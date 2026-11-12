"""REST API for CRM integration, under /api/v1. Auth: `Authorization: Bearer <API_KEY>` (API_KEY in .env).
The API never sends WhatsApp messages: drafts still need approval in the dashboard."""
import hmac
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from . import config, db

STAGES = ("new", "contacted", "replied", "won", "lost", "opted_out")
Stage = Literal["new", "contacted", "replied", "won", "lost", "opted_out"]
score_hook = None        # set by main: called in the background for newly created leads (AI score + draft)


def key_ok(token):
    """Constant-time check of a presented key. False whenever API_KEY is unset."""
    return bool(config.API_KEY) and hmac.compare_digest((token or "").encode(), config.API_KEY.encode())


def bearer(request: Request):
    h = request.headers.get("authorization", "")
    return h[7:].strip() if h[:7].lower() == "bearer " else ""


def require_key(request: Request):
    if not config.API_KEY:
        raise HTTPException(404, "API is disabled. Set API_KEY in .env to enable it.")
    if not key_ok(bearer(request)):
        raise HTTPException(401, "Invalid or missing API key.", headers={"WWW-Authenticate": "Bearer"})


router = APIRouter(prefix="/api/v1", tags=["CRM API"], dependencies=[Depends(require_key)])


class LeadIn(BaseModel):
    name: Optional[str] = Field(None, description="Business/person name. Falls back to website or phone.")
    phone: Optional[str] = None
    website: Optional[str] = None
    email: Optional[str] = None
    niche: Optional[str] = None
    city: Optional[str] = None
    notes: Optional[str] = None
    campaign: Optional[str] = None
    source: Optional[str] = Field(None, description="Where the lead came from, e.g. 'hubspot'. Default 'api'.")
    external_id: Optional[str] = Field(None, description="Your CRM's record id. Upserts match on it first.")


class LeadPatch(BaseModel):
    stage: Optional[Stage] = None
    notes: Optional[str] = None
    campaign: Optional[str] = None
    external_id: Optional[str] = None


class Lead(BaseModel):
    id: int
    name: str
    phone: Optional[str] = None
    website: Optional[str] = None
    email: Optional[str] = None
    niche: Optional[str] = None
    city: Optional[str] = None
    notes: Optional[str] = None
    campaign: Optional[str] = None
    source: Optional[str] = None
    external_id: Optional[str] = None
    stage: str = Field(description="new, contacted, replied, won, lost or opted_out")
    score: Optional[int] = None
    score_reason: Optional[str] = None
    created_at: Optional[str] = Field(None, description="UTC, YYYY-MM-DD HH:MM:SS")
    updated_at: Optional[str] = Field(None, description="UTC, YYYY-MM-DD HH:MM:SS")


class Message(BaseModel):
    id: int
    direction: str = Field(description="out or in")
    body: str
    kind: Optional[str] = None
    status: Optional[str] = Field(None, description="draft, approved, sent, rejected, failed, received")
    tag: Optional[str] = Field(None, description="AI reply tag for inbound messages")
    next_step: Optional[str] = None
    created_at: Optional[str] = None
    sent_at: Optional[str] = None


class LeadDetail(Lead):
    messages: list[Message]


class UpsertOut(BaseModel):
    created: bool
    lead: Lead


class LeadPage(BaseModel):
    items: list[Lead]
    total: int
    limit: int
    offset: int


def lead_out(r):
    d = dict(r)
    d["stage"] = d.pop("status", None) or "new"
    return d


def _ts(value):
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        raise HTTPException(422, "updated_since must be an ISO date/time, e.g. 2025-01-31T09:00:00Z")
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


@router.post("/leads", response_model=UpsertOut, summary="Create or upsert a lead")
def create_lead(body: LeadIn, tasks: BackgroundTasks):
    """Matches an existing lead by external_id, phone, website or name+city. A match only fills fields that are
    still empty (nothing is overwritten); otherwise a new lead is created and the AI scores/drafts it in the
    background. Drafts are never sent without dashboard approval."""
    data = body.model_dump()
    data["source"] = data["source"] or "api"
    lead_id, is_new = db.add_lead(data)
    if is_new and score_hook:
        tasks.add_task(score_hook, lead_id)
    return {"created": is_new, "lead": lead_out(db.get_lead(lead_id))}


@router.get("/leads", response_model=LeadPage, summary="List leads (oldest update first)")
def list_leads(stage: Optional[Stage] = None, campaign: Optional[str] = None,
               updated_since: Optional[str] = Query(None, description="ISO timestamp; only leads changed at/after it"),
               limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    where, args = ["1=1"], []
    if stage:
        where.append("status=?"); args.append(stage)
    if campaign:
        where.append("campaign=?"); args.append(campaign)
    if updated_since:
        where.append("updated_at>=?"); args.append(_ts(updated_since))
    w = " AND ".join(where)
    with db.conn() as c:
        total = c.execute(f"SELECT COUNT(*) FROM leads WHERE {w}", args).fetchone()[0]
        rows = c.execute(f"SELECT * FROM leads WHERE {w} ORDER BY updated_at, id LIMIT ? OFFSET ?",
                         (*args, limit, offset)).fetchall()
    return {"items": [lead_out(r) for r in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/leads/{lead_id}", response_model=LeadDetail, summary="One lead with its messages")
def get_lead(lead_id: int):
    lead = db.get_lead(lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found.")
    msgs = [m for m in reversed(db.messages(lead_id=lead_id))]       # oldest first
    return {**lead_out(lead), "messages": msgs}


@router.patch("/leads/{lead_id}", response_model=Lead, summary="Change stage, notes, campaign or external_id")
def patch_lead(lead_id: int, body: LeadPatch):
    if not db.get_lead(lead_id):
        raise HTTPException(404, "Lead not found.")
    fields = body.model_dump(exclude_unset=True)
    stage = fields.pop("stage", None)
    if fields:
        db.edit_lead(lead_id, **fields)
    if stage:
        db.update_lead(lead_id, status=stage)
        if stage in ("won", "lost", "opted_out"):
            db.cancel_followups(lead_id)
    return lead_out(db.get_lead(lead_id))
