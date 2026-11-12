# CRM integration

Lead Agent connects to any CRM (HubSpot, Zoho, Pipedrive, Salesforce, Odoo...) in two directions, with no
per-CRM code: a **REST API** (your CRM or automation pushes/pulls leads) and **outgoing webhooks** (Lead Agent tells
your CRM what happened). Use them directly, or through n8n, Zapier or Make.

Nothing here can send a WhatsApp message. Drafts always wait for your approval in the dashboard.

## Setup

Add to `.env` (see `.env.example`), then restart:

```
API_KEY=<long random value>            # empty = REST API disabled
CRM_WEBHOOK_URL=https://...            # empty = no webhooks
CRM_WEBHOOK_SECRET=<long random value> # used to sign webhooks
```

Generate a value with `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`. Keep the API on a private
network (Tailscale, see [REMOTE_ACCESS.md](REMOTE_ACCESS.md)); never port-forward port 8000 to the internet.

## REST API

Base path `/api/v1`. Every request needs `Authorization: Bearer <API_KEY>`. The dashboard login is not needed.
Wrong or missing key gives 401; with `API_KEY` unset the API returns 404. Interactive docs: `/docs` (log in to the
dashboard, or send the Bearer header).

| Method | Path | What |
|---|---|---|
| POST | `/api/v1/leads` | Create or upsert a lead |
| GET | `/api/v1/leads` | List: `stage`, `campaign`, `updated_since`, `limit` (1-200, default 50), `offset` |
| GET | `/api/v1/leads/{id}` | One lead with its `messages` (oldest first) |
| PATCH | `/api/v1/leads/{id}` | Change `stage`, `notes`, `campaign`, `external_id` |

Stages: `new`, `contacted`, `replied`, `won`, `lost`, `opted_out`. Timestamps are UTC `YYYY-MM-DD HH:MM:SS`.
The list is ordered by `updated_at` ascending, so you can sync incrementally: remember the last `updated_at` you
saw and pass it as `updated_since` (inclusive, so de-duplicate on `id`).

**Upsert rules.** A lead matches an existing one by `external_id`, then phone, then website domain, then
name+city. A match only fills fields that are still empty; it never overwrites. Response: `{"created": bool, "lead": {...}}`.
New leads are scored and drafted by the local AI in the background.

```bash
curl -s -X POST http://localhost:8000/api/v1/leads \
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{"name":"Acme Dental","phone":"+44 7700 900123","city":"Leeds","external_id":"hubspot-4821","source":"hubspot"}'

curl -s "http://localhost:8000/api/v1/leads?stage=replied&updated_since=2025-01-31T09:00:00Z" \
  -H "Authorization: Bearer $API_KEY"

curl -s -X PATCH http://localhost:8000/api/v1/leads/12 \
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{"stage":"won","notes":"Signed 3-month plan"}'
```

The old `/api/leads` (dashboard login) still works.

## Webhooks

Lead Agent sends `POST` with JSON to `CRM_WEBHOOK_URL`:

```json
{"event": "lead.stage_changed", "id": "5b0c...uuid", "timestamp": "2025-01-31T09:00:00Z", "data": { } }
```

| Event | When | `data` |
|---|---|---|
| `lead.created` | New lead added (API, import, form, inbound message) | the lead |
| `lead.updated` | Lead fields changed or filled in | the lead |
| `lead.stage_changed` | Stage changed | the lead plus `previous_status` (`status` is the new stage) |
| `message.sent` | You approved a draft and it went out | `{lead, message: {id, body, kind}}` |
| `reply.received` | The lead wrote back; includes the AI tag | `{lead, message: {id, body, tag, next_step}}` |
| `lead.opted_out` | Lead said STOP or was marked opted out | the lead |

`tag` is one of `interested`, `asking_price`, `not_now`, `not_interested`, `other`. Lead objects use the database
column names (`status` is the stage, same values as above).

Headers: `X-LeadAgent-Event`, `X-LeadAgent-Delivery` (the envelope `id`), and, when a secret is set,
`X-LeadAgent-Signature: sha256=<hex HMAC-SHA256 of the raw body>`. Delivery happens in the background; a failed
delivery (network error or non-2xx) is retried 3 more times after 2 s, 10 s and 60 s, then dropped. Return 2xx quickly
and de-duplicate on `id`. Deliveries never block the app and the secret is never logged.

### Verify the signature

Python:

```python
import hashlib, hmac

def valid(secret: str, raw_body: bytes, header: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header or "")
```

Node:

```js
const crypto = require("crypto");

function valid(secret, rawBody, header) {          // rawBody: Buffer/string exactly as received
  const expected = "sha256=" + crypto.createHmac("sha256", secret).update(rawBody).digest("hex");
  const a = Buffer.from(expected), b = Buffer.from(header || "");
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}
```

Always hash the raw bytes, not re-serialised JSON.

## Recipes

Run n8n/Make/Zapier somewhere that can reach Lead Agent (same machine or Tailscale network). Zapier and Make cloud
need a reachable URL for the API (e.g. a Tailscale Funnel/private tunnel you trust); n8n self-hosted can sit next to it.

### n8n

- **Lead Agent to CRM:** Webhook node (POST) with the URL in `CRM_WEBHOOK_URL`. Add a Switch on `{{$json.body.event}}`:
  `lead.created` / `lead.updated` -> HubSpot/Zoho/Pipedrive *Create or Update contact* (match on email or phone, store
  `data.id` in a custom field); `reply.received` -> add a note/activity with `data.message.body` and `data.message.tag`;
  `lead.stage_changed` -> move the deal stage. Check the signature in a Code node (see above) if you set a secret.
- **CRM to Lead Agent:** CRM trigger (new contact / deal stage change) -> HTTP Request node `POST /api/v1/leads` with a
  Bearer header and `external_id` = the CRM record id. A stage change -> find the lead (`GET /api/v1/leads`, filter in
  n8n) and `PATCH /api/v1/leads/{id}`.

### Zapier

- **To CRM:** trigger *Webhooks by Zapier -> Catch Hook*; put the Zap's URL in `CRM_WEBHOOK_URL`. Filter on `event`, then
  *HubSpot / Zoho CRM / Pipedrive -> Create or Update Contact/Person* (and *Create Note* for `reply.received`). Zapier
  cannot verify HMAC without a Code step; use one if you need it.
- **From CRM:** CRM trigger -> *Webhooks by Zapier -> Custom Request* (POST, header `Authorization: Bearer ...`,
  JSON body as above).

### Make

- **To CRM:** *Webhooks -> Custom webhook* as the first module, URL into `CRM_WEBHOOK_URL`; a Router with a filter per
  event feeds *HubSpot / Zoho CRM / Pipedrive* modules (create/update contact, add note, update deal stage).
- **From CRM:** CRM *Watch* module -> *HTTP -> Make a request* to `POST /api/v1/leads` (Bearer header, JSON body).

### Field mapping

| Lead Agent | HubSpot | Zoho CRM | Pipedrive |
|---|---|---|---|
| `name` | Company name / contact name | Account / Last Name | Organization / Person name |
| `phone`, `email`, `website` | phone, email, website | Phone, Email, Website | phone, email, (org) address/website |
| `city`, `niche` | city, industry | City, Industry | custom fields |
| `stage` | lifecycle stage / deal stage | Lead Status / Stage | Deal stage |
| `external_id` | record id (hs_object_id) | record id | person/deal id |
