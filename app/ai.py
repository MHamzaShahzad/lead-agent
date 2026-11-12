"""Local AI via Ollama: score leads, draft outreach, replies and follow-ups.

If Ollama is not running, simple rule-based fallbacks keep the app usable
(results are marked "[fallback]" so you know the AI did not write them).
"""
import json
import re

import httpx

from . import config


class OllamaDown(Exception):
    pass


def _chat(system, user, as_json=False, timeout=120):
    body = {"model": config.OLLAMA_MODEL, "stream": False,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "options": {"temperature": 0.3}}
    if as_json:
        body["format"] = "json"
    if config.DEMO_MODE:
        timeout = min(timeout, 20)      # demo: use a local Ollama if there is one, else the canned fallbacks right away
    try:
        r = httpx.post(f"{config.OLLAMA_URL}/api/chat", json=body, timeout=timeout)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise OllamaDown(str(e)) from e
    return r.json()["message"]["content"].strip()


def status():
    try:
        tags = httpx.get(f"{config.OLLAMA_URL}/api/tags", timeout=3).json()
        names = [m["name"] for m in tags.get("models", [])]
        return {"up": True, "model_ready": any(n.startswith(config.OLLAMA_MODEL) for n in names), "models": names}
    except Exception:
        return {"up": False, "model_ready": False, "models": []}


def _describe(lead):
    keys = ("name", "niche", "city", "website", "phone", "email", "notes")
    return "\n".join(f"{k}: {lead[k]}" for k in keys if lead.get(k))


_FACT_RULES = ("FACTS: only mention services that are clearly listed in 'About us'. Never invent services, partners, "
               "clients, prices, numbers, locations, awards or experience. Never call us 'leading', 'best' or 'top'. ")
_FIT_RULES = ("Pick ONE of our services that fits this prospect: for a school, college or university that can be "
              "courses, internships or study guidance for their students (only if we offer them); for any other business "
              "it is our IT services for THEIR business (for example a website, online booking or WhatsApp automation). "
              "Do NOT offer training or courses to a business's staff unless they asked for it. ")

def score_lead(lead):
    """Returns (score 0-100, reason)."""
    system = ("You are a sales analyst. Score how likely this prospect is to need at least ONE of our services. "
              f"What we sell: {config.MY_BUSINESS}\n"
              "Rules: small and local businesses ARE our customers, never score them down for being small or local. "
              "No website or a weak online presence is a REASON TO SCORE HIGHER (they need help). "
              + _FIT_RULES +
              "Scale: 0-30 clearly no need or no way to contact; 40-59 possible need; 60-79 good fit; "
              "80-100 obvious need, for example a business with NO website. A business that already has a website "
              "usually scores 55-75. The reason must name the ONE service of ours that fits best, chosen by the rule above.\n"
              'Reply ONLY as JSON: {"score": <0-100 integer>, "reason": "<one short sentence>"}')
    try:
        out = json.loads(_chat(system, _describe(lead), as_json=True))
        return max(0, min(100, int(out.get("score", 0)))), str(out.get("reason", ""))[:300]
    except OllamaDown:
        pass
    except (ValueError, TypeError):
        return 50, "[fallback] AI reply was not valid JSON"
    s, why = 30, []
    if lead.get("phone"): s += 20; why.append("has phone")
    if not lead.get("website"): s += 25; why.append("no website (needs one)")
    else: s += 5
    if lead.get("niche"): s += 10; why.append("niche known")
    if lead.get("email"): s += 5
    return min(s, 100), "[fallback] " + ", ".join(why or ["basic info only"])


_SALES_RULES = (_FACT_RULES + "Write a short WhatsApp message (max 60 words). Friendly, specific to this business, "
                "no hype, no emojis spam, one clear question at the end. Plain text only, no quotes, "
                "no subject line, no placeholders like [Name]. Output ONLY the message itself, no intro line. "
                "LANGUAGE: always write in simple English, even if the customer wrote in Roman Urdu or Hindi.")


def draft_outreach(lead):
    system = (f"You are a salesperson for OUR company. About us: {config.MY_BUSINESS}\n"
              "You write a first-contact WhatsApp message FROM us TO a prospect business. "
              "Never write as the prospect or describe their services back to them. Introduce us in one line, "
              + _FIT_RULES + "Say in one line how it helps them.\n" + _SALES_RULES)
    try:
        return _clean(_chat(system, "Write our message TO this prospect:\n" + _describe(lead)))
    except OllamaDown:
        return (f"[fallback] Hi {lead['name']} team! I came across your "
                f"{lead.get('niche') or 'business'}{' in ' + lead['city'] if lead.get('city') else ''}. "
                f"{config.MY_BUSINESS} Would you be open to a quick chat this week?")


def draft_reply(lead, history):
    convo = "\n".join(f"{'Customer' if m['direction'] == 'in' else 'Us'}: {m['body']}" for m in history[-10:])
    system = (f"You reply to a potential customer on WhatsApp for our business. What we sell: {config.MY_BUSINESS}\n"
              "Answer their question honestly. NEVER state prices, timelines or guarantees; say we will share "
              "exact details on a quick call. If they ask about something that is NOT clearly in our list "
              "(for example SEO, ads or a specific software), do not say yes or no: say our team will confirm on a quick call. "
              "Answer ONLY what they asked, in one or two sentences. Do NOT list our other services. "
              "Move toward a call or meeting. "
              + _SALES_RULES)
    try:
        last = next((m["body"] for m in reversed(history) if m["direction"] == "in"), "")
        task = (f"Their latest message: \"{last}\"\nWrite our next reply. Answer exactly that message in one or two "
                "sentences, then suggest a quick call. Do NOT mention any other service of ours (no list of services).")
        return _clean(_chat(system, f"Customer details:\n{_describe(lead)}\n\nConversation:\n{convo}\n\n{task}"))
    except OllamaDown:
        return "[fallback] Thanks for your message! Could you tell me a bit more about what you need? Happy to set up a quick call."


def draft_followup(lead, step):
    situation = ("chatted with us on WhatsApp earlier but has gone quiet after our last reply"
                 if lead.get("status") == "replied" else "has not replied to our WhatsApp message")
    system = (f"You write polite follow-up #{step} to a business that {situation}. We messaged them first; "
              "never say we came across their message. "
              f"What we sell: {config.MY_BUSINESS}\n" + _FIT_RULES + _FACT_RULES +
              "Keep it under 40 words, add one new useful point about "
              "how that service helps a business like theirs, make it easy to say no. No greeting line breaks, no signature, no placeholders. Output ONLY the message.")
    try:
        return _clean(_chat(system, _describe(lead) + "\n\nWrite follow-up #%d. We wrote to them first; do not say "
                             "you came across their message. Mention ONE service only." % step))
    except OllamaDown:
        return f"[fallback] Hi {lead['name']}, just following up on my last message. Is this something worth a 10-minute chat? No worries if not."


def draft_email(lead, kind="outreach", history=None, step=1):
    """An email draft: returns (subject, plain-text body). kind: outreach, reply or followup."""
    task = {"outreach": "a first-contact cold email TO a prospect business. Introduce us in one line, "
                        + _FIT_RULES + "say in one line how it helps them, and end with one clear question.",
            "reply": "a reply to the customer's latest email. Answer exactly what they asked in two or three sentences, "
                     "NEVER state prices, timelines or guarantees (say we will share details on a quick call), then suggest a quick call.",
            "followup": f"polite follow-up #{step} to a business that has not replied to our earlier email. Under 50 words, one new useful point, "
                        "easy to say no. Do not say you came across their message."}[kind]
    system = (f"You write {task}\nAbout us: {config.MY_BUSINESS}\n" + _FACT_RULES +
              "Plain text only: no markdown, no HTML, no placeholders like [Name], no signature block, no unsubscribe line (added automatically). "
              "Max 110 words. Simple English. Friendly and specific, no hype. Never write as the prospect.\n"
              'Reply with JSON only: {"subject": "<under 8 words, no clickbait>", "body": "<the email body>"}')
    convo = "\n".join(f"{'Customer' if m['direction'] == 'in' else 'Us'}: {m['body']}" for m in (history or [])[-8:])
    user = f"Prospect details:\n{_describe(lead)}" + (f"\n\nConversation so far:\n{convo}" if convo else "")
    try:
        d = json.loads(_chat(system, user, as_json=True))
        body, subj = _clean(str(d.get("body", ""))), " ".join(str(d.get("subject", "")).split())[:120]
        if body and subj:
            return subj, body
    except (OllamaDown, ValueError, AttributeError):
        pass
    n = lead["name"]
    if kind == "reply":
        return "Re: your message", "[fallback] Thanks for your email! Could you tell me a bit more about what you need? Happy to set up a quick call."
    if kind == "followup":
        return f"Following up, {n}", f"[fallback] Hi {n} team, just following up on my last email. Is this worth a 10-minute chat? No worries if not."
    return (f"Quick idea for {n}",
            f"[fallback] Hi {n} team,\n\nI came across your {lead.get('niche') or 'business'}"
            f"{' in ' + lead['city'] if lead.get('city') else ''}. {config.MY_BUSINESS}\n\nWould you be open to a quick chat this week?")


REWRITE_MODES = {
    "shorten": "Make it noticeably shorter (about half the length) but keep the key point and the closing question.",
    "formal": "Make it more formal and professional in tone, still warm and brief.",
    "roman_urdu": "Rewrite it in natural Roman Urdu (Urdu written in English letters), same meaning.",
}


def rewrite(text, mode):
    """Rewrite a draft. Raises OllamaDown when the AI is offline (nothing sensible to fall back to)."""
    system = ("You edit WhatsApp messages. " + REWRITE_MODES[mode] +
              " Keep facts unchanged, add no prices or promises, no placeholders, plain text. Output ONLY the message.")
    return _clean(_chat(system, text))


REPLY_TAGS = {
    "interested": ("Interested", "Reply soon and suggest a short call."),
    "asking_price": ("Asking price", "Don't quote a number in chat; offer a quick call to share details."),
    "not_now": ("Not now", "Say thanks and check back in a few weeks."),
    "not_interested": ("Not interested", "Thank them politely and don't message again."),
    "other": ("Other", "Read it and answer their question."),
}
_TAG_RULES = (
    ("not_interested", r"not interested|no thanks|no thank you|don'?t (contact|message|text)|nahi chahiy|nahin chahiy|remove me|unsubscribe|\bstop\b|mat bhejo|band karo"),
    ("not_now", r"not now|later|busy|next (month|week|year)|maybe (later|next)|abhi nahi|abhi nahin|baad mein|baad me|some other time"),
    ("asking_price", r"price|pricing|cost|how much|rates?\b|charges|quote|quotation|fee|kitne|kitna|budget|package"),
    ("interested", r"interested|tell me more|more (info|details)|send (details|info|portfolio)|sounds good|let'?s (talk|connect|do)|call me|\bcall\b|meeting|sure\b|\byes\b|\bok(ay)?\b|haan|ji haan|zaroor|details"),
)


def classify_reply(text):
    """Returns (tag, next_step). AI first; simple keyword rules when Ollama is down or answers badly."""
    system = ("Classify a customer's WhatsApp reply to our sales message. Reply ONLY as JSON: "
              '{"tag": "interested|asking_price|not_now|not_interested|other"}')
    try:
        tag = str(json.loads(_chat(system, text[:1000], as_json=True, timeout=30)).get("tag", "")).strip().lower()
        if tag in REPLY_TAGS:
            return tag, REPLY_TAGS[tag][1]
    except (OllamaDown, ValueError, TypeError, AttributeError):
        pass
    low = text.lower()
    for tag, pat in _TAG_RULES:
        if re.search(pat, low):
            return tag, REPLY_TAGS[tag][1]
    return "other", REPLY_TAGS["other"][1]


def extract_inbound(text):
    """Pull business details from an inbound WhatsApp message (unknown sender)."""
    system = ('Extract details from this customer message. Reply ONLY JSON: '
              '{"name": "", "niche": "", "city": "", "website": "", "email": "", "intent": "<short>"}. '
              'Use empty strings when unknown.')
    try:
        out = json.loads(_chat(system, text, as_json=True))
        return {k: str(out.get(k) or "") for k in ("name", "niche", "city", "website", "email", "intent")}
    except (OllamaDown, ValueError, TypeError):
        email = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
        site = re.search(r"\b(?:https?://)?(?:www\.)?[\w-]+\.(?:com|pk|net|org|io|co)\S*", text)
        return {"name": "", "niche": "", "city": "", "website": site.group(0) if site else "",
                "email": email.group(0) if email else "", "intent": text[:80]}


_PREAMBLE = re.compile(r"^\s*(here'?s|here is|sure|below is|draft|message|whatsapp message)[^\n]{0,80}:\s*\n+", re.I)


def _clean(t):
    t = _PREAMBLE.sub("", t.strip()).strip().strip('"“”')
    t = re.sub(r"\n+\s*(note|this message)\b.*$", "", t, flags=re.I | re.S)   # trailing explanations
    t = re.sub(r"\n+\s*(best|regards|thanks|cheers)[^\n]*\[[^\]]*\]\s*$", "", t, flags=re.I)  # "Best, [Your Name]"
    t = re.sub(r"\s*\[(your|my) [^\]]*\]", "", t, flags=re.I)
    t = re.sub(r"\n+\s*(best|regards|thanks|cheers)\s*,?\s*$", "", t, flags=re.I)
    t = re.sub(r"\n{2,}", "\n", t)
    return t.strip().strip('"“”').strip()
