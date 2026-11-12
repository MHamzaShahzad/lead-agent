"""Dashboard HTML. Server-rendered, phone-first app shell (bottom tabs on phones, sidebar on desktop).
Small JS handles tabs, lead search/filter/sort, selection bars, draft checks and keyboard shortcuts."""
from datetime import datetime, timezone
import json
import re as _re
from html import escape

from . import config, db, emailer, whatsapp


def e(v):
    return escape(str(v if v is not None else ""))


def when(ts):
    """'2026-11-15 14:50:41' (UTC) -> 'in 3 days' / '2 hours ago'."""
    if not ts:
        return ""
    t = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    s = (t - datetime.now(timezone.utc)).total_seconds()
    a = abs(s)
    n, unit = ((int(a // 86400), "day") if a >= 86400 else (int(a // 3600), "hour") if a >= 3600
               else (int(a // 60), "minute") if a >= 60 else (0, ""))
    if not unit:
        return "just now"
    txt = f"{n} {unit}{'s' if n != 1 else ''}"
    return f"in {txt}" if s > 0 else f"{txt} ago"


_ERR = _re.compile(r"(Not |Re-link failed|Search failed|Unknown|Tick at least|That draft|The AI is offline|Lead not found)")


def flash_html(msg):
    """Toast: shows at the bottom of the screen, fades after a few seconds (JS); plain bar if JS is off."""
    if not msg:
        return ""
    err = bool(_ERR.match(msg))
    return (f'<div class="toast{" err" if err else ""}" role="{"alert" if err else "status"}">{e(msg)}'
            '<button class="x" onclick="this.parentNode.remove()" aria-label="Close message">×</button></div>')


def dshort(d):
    """'2026-11-10' -> '10 Nov' (and 'today' / 'overdue' hints)."""
    try:
        x = datetime.strptime(d, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return e(d)
    delta = (x - datetime.now().date()).days
    txt = f"{x.day} {x.strftime('%b')}"
    return f"{txt} · today" if delta == 0 else f"{txt} · overdue" if delta < 0 else txt


def eta(ts):
    """'~14:05' (or '~14:05 Mon' on another day) from a stored UTC time."""
    from . import sendqueue
    try:
        loc = sendqueue.to_local(sendqueue.parse(ts))
    except (TypeError, ValueError):
        return "soon"
    return "~" + loc.strftime("%H:%M") + ("" if loc.date() == whatsapp._local_now().date() else loc.strftime(" %a"))


def t(ts):
    """Relative time; the browser adds the exact local time as a tooltip (stored times are UTC)."""
    if not ts:
        return ""
    return f'<time datetime="{e(ts).replace(" ", "T")}Z">{e(when(ts))}</time>'


def score_badge(score):
    if score is None:
        return '<span class="badge grey" title="AI is still scoring">Scoring…</span>'
    cls, label = ("hot", "Hot") if score >= 70 else ("warm", "Warm") if score >= 40 else ("cold", "Cold")
    return f'<span class="badge {cls}" title="AI score out of 100">{label} · {score}</span>'


STATUS_LABEL = {"new": "New", "contacted": "Messaged", "replied": "Replied", "won": "Won",
                "lost": "Lost", "opted_out": "Opted out"}
KIND_LABEL = {"outreach": "First message", "reply": "Reply to their message", "followup": "Follow-up"}

PLACEHOLDER = _re.compile(r"\[[^\]]{1,40}\]")


def chan_badge(m):
    return '<span class="tag chan">Email</span>' if (m.get("channel") or "whatsapp") == "email" else '<span class="tag chan wa">WhatsApp</span>'


def draft_warning(body):
    if "[fallback]" in body:
        return "The AI was offline when this was written, so it is a plain template. Check it carefully."
    if PLACEHOLDER.search(body):
        return "This still has a placeholder like [Name]. Replace it before sending."
    return ""


ICONS = {
    "today": '<path d="M4 13h4l2 3h4l2-3h4"/><path d="M5.5 5h13L21 13v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-5z"/>',
    "chat": '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/>',
    "leads": '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7M18 14.2a6.5 6.5 0 0 1 3.5 5.8"/>',
    "add": '<path d="M12 5v14M5 12h14"/>',
    "more": '<circle cx="5" cy="12" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="19" cy="12" r="1.4"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "phone": '<path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z"/>',
    "gear": '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.9 4.9 7 7M17 17l2.1 2.1M2 12h3M19 12h3M4.9 19.1 7 17M17 7l2.1-2.1"/>',
    "out": '<path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 17l5-5-5-5M15 12H3"/>',
    "send": '<path d="M21 3 10 14M21 3l-6.5 18-4-7.5L3 9.5z"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    "back": '<path d="M15 18l-6-6 6-6"/>',
    "chev": '<path d="M9 18l6-6-6-6"/>',
    "spark": '<path d="M12 3l1.8 4.7 4.7 1.8-4.7 1.8L12 16l-1.8-4.7-4.7-1.8 4.7-1.8z"/><path d="M19 15l.8 2.2 2.2.8-2.2.8L19 21l-.8-2.2-2.2-.8 2.2-.8z"/>',
    "file": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M12 18v-6M9 15l3 3 3-3"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    "download": '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
    "refresh": '<path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
}


def brand_html(tagline=False):
    """Logo letter + app name (+ optional tagline), from BRAND_NAME / BRAND_TAGLINE."""
    n = config.BRAND_NAME
    sub = f'<span class="sub">{e(config.BRAND_TAGLINE)}</span>' if tagline and config.BRAND_TAGLINE else ""
    return f'<div class="brand"><div class="logo">{e(n[:1].upper())}</div><div><b>{e(n)}</b>{sub}</div></div>'


def ic(name, size=20):
    return (f'<svg class="ic" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{ICONS[name]}</svg>')


def av(name, cls="av"):
    """Initials avatar with a stable colour per name."""
    name = name or "?"
    words = [w for w in _re.split(r"\W+", name) if w]
    ini = "".join(w[0] for w in words[:2]).upper() or "?"
    hue = sum(map(ord, name)) * 37 % 360
    return f'<span class="{cls}" style="--h:{hue}" aria-hidden="true">{e(ini)}</span>'


def stage_select(lead, extra=""):
    opts = "".join(f'<option value="{k}" {"selected" if k == lead["status"] else ""}>{v}</option>'
                   for k, v in STATUS_LABEL.items())
    return (f'<form method="post" action="/leads/{lead["id"]}/status" class="stf">{extra}'
            f'<select name="status" class="stage s-{e(lead["status"])}" onchange="this.form.submit()" aria-label="Stage">{opts}</select></form>')


def empty(title, text, icon="check"):
    return f'<div class="empty card"><div class="ib">{ic(icon, 22)}</div><h3>{title}</h3><p class="sub">{text}</p></div>'


def week_hours():
    rows = []
    for i, (day, w) in enumerate(zip(whatsapp.DAYS, whatsapp.week_windows())):
        a, b = w or (config.SEND_START, config.SEND_END)
        rows.append(f'''<div class="wkd"><label class="wkon"><input type="checkbox" name="day{i}_on" value="1" {'checked' if w else ''}> {day}</label>
            <input name="day{i}_start" type="time" value="{escape(a)}" aria-label="{day} from"><input name="day{i}_end" type="time" value="{escape(b)}" aria-label="{day} until"></div>''')
    return ('<div class="wk"><div class="wkd wkh"><span></span><span class="lbl">From</span><span class="lbl">Until</span></div>'
            + "".join(rows) + "</div>")


def page(*, results=None, working=0, replies_week=0, msg, ai_status, wa_status, wa_number, last_backup=None, campaigns=(), reminders=(), sent_today, leads, drafts, inbox, followups, queued=(), paused=False):
    ai_ok = ai_status["up"] and ai_status["model_ready"]
    wa_ok = wa_status == "WORKING"
    limit = config.DAILY_SEND_LIMIT
    pct = min(100, int(100 * sent_today / max(1, limit)))
    res = results or {"rate": None, "replied": 0, "messaged": 0, "won": 0, "sent_week": 0}

    # ---------- Today (overview + approvals) ----------
    stage_n = {}
    for l in leads:
        stage_n[l["status"]] = stage_n.get(l["status"], 0) + 1
    total = max(1, len(leads))
    stack = "".join(f'<i class="s-{k}" style="width:{100 * stage_n[k] / total:.1f}%" title="{v}: {stage_n[k]}"></i>'
                    for k, v in STATUS_LABEL.items() if stage_n.get(k))
    legend = "".join(f'<div class="lg{"" if stage_n.get(k) else " z"}"><i class="s-{k}"></i><span>{v}</span><b>{stage_n.get(k, 0)}</b></div>'
                     for k, v in STATUS_LABEL.items())
    n_drafts = sum(1 for d in drafts if d["status"] == "draft")
    n_rem = len(reminders)
    rem_html = (f'<div class="sh2"><h3>Reminders <span class="cnt alert">{n_rem}</span></h3></div><div class="card flush clist">' + "".join(
        f"""<div class="frow"><a class="lt" href="/leads/{r['lead_id']}?from=today"><b>{e(r['lead_name'])}</b><span class="sub">{e(r['note'] or 'Follow up')}</span><span class="sub due">{dshort(r['due_date'])}</span></a>
        <form method="post" action="/reminders/{r['id']}/done"><button class="secondary small" aria-label="Mark reminder done for {e(r['lead_name'])}">Done</button></form></div>""" for r in reminders)
        + '</div>') if reminders else ""
    rate = f"{res['rate']}%" if res["rate"] is not None else "–"
    camp_card = ('<div class="card"><div class="ph"><h3>Reply rate by campaign</h3></div><ul class="list">' + "".join(
        f'<li><span>{e(c["name"])}</span><span class="tag">{(str(c["rate"]) + "%") if c["rate"] is not None else "–"} · '
        f'{c["replied"]} of {c["messaged"]} replied · {c["leads"]} leads</span></li>' for c in campaigns) + '</ul></div>') if campaigns else ""
    overview = f"""
    <div class="kpis">
      <a class="kpi hero{' zero' if not n_drafts else ''}" href="#todo"><span class="k">Waiting for approval</span><b>{n_drafts}</b><small>{'Check them below' if n_drafts else 'All caught up'}</small></a>
      <div class="kpi"><span class="k">Sent today</span><b>{sent_today}<small> / {limit}</small></b>
        <div class="meter" title="Daily limit protects your number"><i style="width:{pct}%"></i></div><small>{res['sent_week']} in the last 7 days</small></div>
      <div class="kpi"><span class="k">Replies this week</span><b>{replies_week}</b><small>from leads</small></div>
      <div class="kpi"><span class="k" title="Of the leads we messaged, how many wrote back">Reply rate</span><b>{rate}</b><small>{res['replied']} of {res['messaged']} replied</small></div>
    </div>"""
    q_rows = "".join(
        f"""<div class="frow"><span class="lt"><b>{e(q['lead_name'])}</b><span class="sub">Queued, sends at {eta(q['scheduled_at'])} · {e(q['body'][:60])}</span></span>
        <form method="post" action="/messages/{q['id']}/cancel"><button class="secondary small" aria-label="Cancel queued message to {e(q['lead_name'])}">Cancel</button></form></div>"""
        for q in queued)
    queue_html = (f"""<div class="sh2"><h3>Queue <span class="cnt">{len(queued)}</span></h3></div>
      <div class="card flush clist">{q_rows or '<div class="frow"><span class="sub">Nothing queued.</span></div>'}</div>
      {'<p class="sub">The queue is paused. Resume it in Settings.</p>' if paused else ''}""") if (queued or paused) else ""
    insights = f"""<div class="sh2"><h3>Results</h3></div>
    {camp_card}
    <div class="card pipe"><div class="ph"><h3>Leads by stage</h3><span class="sub">{len(leads)} total · {res['won']} won</span></div>
      <div class="stack">{stack}</div><div class="legend">{legend}</div></div>"""

    cards = []
    _ord = {"reply": 0, "followup": 1, "outreach": 2}
    drafts = sorted(drafts, key=lambda d: _ord.get(d["kind"], 3))
    for n_card, d in enumerate(drafts):
        ctx_items = d.get("context", [])
        ctx = "".join(f'<div class="b {"in" if c["direction"] == "in" else "out"}">{e(c["body"])}<span class="m">{"They" if c["direction"] == "in" else "You"} · {t(c["sent_at"] or c["created_at"])}</span></div>'
                      for c in ctx_items)
        their = d.get("their_msg") if d["kind"] == "reply" else None
        if their and not (ctx_items and ctx_items[-1]["direction"] == "in" and ctx_items[-1]["body"] == their):
            ctx += f'<div class="b in hi">{e(their)}<span class="m">They wrote {t(d["their_at"])}</span></div>'
        ctx = f'<div class="thread"><div class="tl">Recent conversation</div>{ctx}</div>' if ctx else ""
        tag_box = (f'<p class="sub dmeta">AI read their message as {reply_tag(d.get("their_tag"))} · Suggested next step: '
                   f'{e(d.get("their_step") or "")}</p>' if d["kind"] == "reply" and d.get("their_tag") else "")
        failed = d["status"] == "failed"
        is_mail = d.get("channel") == "email"
        other = "whatsapp" if is_mail else "email"
        switch = (f'<form method="post" action="/messages/{d["id"]}/channel" class="chsw"><input type="hidden" name="channel" value="{other}">'
                  f'<button class="tool">Switch to {"WhatsApp" if is_mail else "Email"} (writes a new version)</button></form>'
                  if (d.get("phone") and d.get("email")) else "")
        subj = (f'<label class="lbl">Subject</label><input name="subject" value="{e(d.get("subject") or "")}" maxlength="200" form="send{d["id"]}">'
                if is_mail else "")
        optout = d.get("lead_status") == "opted_out"
        warn = "This person asked us to stop. Sending is blocked; discard this draft." if optout else draft_warning(d["body"])
        if not warn and not optout:
            warn = "" if d.get("channel") == "email" else whatsapp.window_note(d.get("phone"))      # Cloud API: 24-hour window note ('' on WAHA)
        fail_box = f'<div class="warn bad">Not sent: {e(d.get("error") or "unknown error")}</div>' if failed else ""
        pick = (f'<label class="pick" title="Select for Send selected"><input type="checkbox" name="ids" value="{d["id"]}" form="bulksend" '
                f'aria-label="Select draft for {e(d["lead_name"])}"><span>Select</span></label>'
                if d["status"] == "draft" and not optout else "")
        tools = "" if failed else f"""<div class="tools" role="group" aria-label="Change the message">
              <button class="tool" formaction="/messages/{d['id']}/regenerate">{ic('refresh', 15)} Write a new one</button>
              <button class="tool" formaction="/messages/{d['id']}/rewrite" name="mode" value="shorten">Shorter</button>
              <button class="tool" formaction="/messages/{d['id']}/rewrite" name="mode" value="formal">More formal</button>
              <button class="tool" formaction="/messages/{d['id']}/rewrite" name="mode" value="roman_urdu">Roman Urdu</button></div>"""
        cards.append(f"""
        <div class="draft" tabindex="-1" data-id="{d['id']}" data-kind="{e(d['kind'])}" data-failed="{1 if failed else 0}" aria-label="Draft for {e(d['lead_name'])}"><details name="drafts">
          <summary class="dh">{av(d['lead_name'])}<div class="who"><span class="name">{e(d['lead_name'])}</span>
            <span class="sub">{chan_badge(d)} {'Not sent · ' if failed else ''}{e(KIND_LABEL.get(d['kind'], d['kind']))}</span>
            <span class="pv">{e(d['body'])}</span></div>
            <div class="dr">{score_badge(d.get('score'))}{pick}<span class="tog">{ic('chev', 18)}</span></div></summary>
          <div class="dbody"><p class="sub dmeta">{e(d.get('email') if is_mail else d['phone'])} · <a href="/leads/{d['lead_id']}?from=today">Open lead</a></p>
          {switch}
          {tag_box}{ctx}{fail_box}
          <form method="post" action="/messages/{d['id']}/send" id="send{d['id']}">
            {subj}<label class="lbl">Your message <span class="sub">· written by AI, edit freely</span></label>
            <textarea class="compose" name="body" rows="5" data-count>{e(d['body'])}</textarea>
            <div class="under"><div class="warn" {'' if warn else 'hidden'}>{e(warn)}</div><span class="cnt sub"><span class="n">{len(d['body'])}</span> characters</span></div>
            {tools}
            <div class="acts">
              <button class="primary wa" {'disabled' if optout else ''}>{ic('send', 17)} {'Try again' if failed else ('Send as email' if is_mail else 'Send on WhatsApp')}</button>
              <button class="ghost" formaction="/messages/{d['id']}/reject">Discard</button>
            </div>
          </form></div>
        </details></div>""")
    dk = {"reply": 0, "followup": 0, "outreach": 0, "failed": 0}
    for d in drafts:
        if d["status"] == "failed":
            dk["failed"] += 1
        if d["kind"] in dk:
            dk[d["kind"]] += 1
    dchips = f'<button class="chip on" data-dfilter="all">All <b>{len(drafts)}</b></button>' + "".join(
        f'<button class="chip" data-dfilter="{k}">{lbl} <b>{dk[k]}</b></button>'
        for k, lbl in (("reply", "Replies"), ("followup", "Follow-ups"), ("outreach", "First messages"), ("failed", "Not sent")) if dk[k])
    approvals = (f'<div class="chips dchips">{dchips}</div><div class="card flush dlist" id="dlist">{"".join(cards)}</div>'
                 '<button class="secondary more" id="dmore" hidden>Show 10 more</button>') if cards else empty(
        "Nothing to approve", "When the AI writes a message (a first message, a reply or a follow-up) it waits here for you. "
        "<a href='#add' data-go='add'>Add leads</a> to get started.")

    bulk_bar = """<form id="bulksend" method="post" action="/messages/bulk-send" class="floatbar" hidden>
      <b><span id="nsend">0</span> selected</b>
      <button class="primary wa small" onclick="return confirm('Send the selected drafts now? They go out as saved (unsaved edits in the boxes are not included), spaced apart, and stop at your daily limit. Drafts with warnings are skipped.')">Send selected</button></form>""" if any(d["status"] == "draft" for d in drafts) else ""

    # ---------- Chats ----------
    replies = "".join(f"""
        <a class="crow" data-text="{e((m['lead_name'] or '') + ' ' + (m['body'] or '')).lower()}" href="/leads/{m['lead_id']}?from=chats">{av(m['lead_name'])}
          <div class="cm"><div class="ct"><b>{e(m['lead_name'])}</b><span class="sub">{t(m['sent_at'] or m['created_at'])}</span></div>
          <div class="cp"><p>{reply_tag(m.get('tag'), m.get('next_step'))} {e(m['body'])}</p>{f'<span class="unread" title="{m["n"]} messages">{m["n"]}</span>' if m.get('n', 1) > 1 else ''}</div></div></a>""" for m in inbox)
    replies = (f'<label class="search chatsearch">{ic("search", 18)}<input type="search" id="cq" aria-label="Search chats" placeholder="Search name or message"></label>'
               f'<div class="card flush clist" id="clist">{replies}</div><p class="sub" id="cnone" hidden>No chats match.</p>'
               '<button class="secondary more" id="cmore" hidden>Show more</button>') if replies else empty(
        "No messages yet", "When someone writes to your WhatsApp, they show up here, become a lead automatically, "
        "and the AI drafts a reply for you to approve.", "chat")

    # ---------- Leads ----------
    counts = {}
    for l in leads:
        counts[l["status"]] = counts.get(l["status"], 0) + 1
    chips = f'<button class="chip on" data-filter="all">All <b>{len(leads)}</b></button>' + "".join(
        f'<button class="chip" data-filter="{k}">{v} <b>{counts[k]}</b></button>'
        for k, v in STATUS_LABEL.items() if counts.get(k))
    camps = sorted({(l.get("campaign") or "").strip() for l in leads} - {""})
    cchips = ('<div class="chips">' + "".join(f'<button class="chip cchip" data-campaign="{e(c)}" title="Campaign">{e(c)}</button>'
                                              for c in camps) + '</div>') if camps else ""
    rows = "".join(f"""
        <div class="lrow" data-campaign="{e((l.get('campaign') or '').strip())}" data-status="{e(l['status'])}" data-score="{-1 if l['score'] is None else l['score']}" data-id="{l['id']}" data-stage="{list(STATUS_LABEL).index(l['status']) if l['status'] in STATUS_LABEL else 9}" data-text="{e((l['name'] or '') + ' ' + (l['niche'] or '') + ' ' + (l['city'] or '') + ' ' + (l['phone'] or '')).lower()}">
          <input type="checkbox" class="sel" name="ids" value="{l['id']}" form="bulk" aria-label="Select {e(l['name'])}">
          <a class="lm" href="/leads/{l['id']}?from=leads">{av(l['name'])}<span class="lt"><b>{e(l['name'])}</b>{' <span class="tag bad">Opted out</span>' if l['status'] == 'opted_out' else ''}
            <span class="sub">{(e(l['campaign']) + ' · ') if l.get('campaign') else ''}{e(' · '.join(x for x in (l['niche'], l['city']) if x)) or 'No details'}{' · ' + e(l['phone']) if l['phone'] else ' · no phone'}</span></span></a>
          <div class="ls">{score_badge(l['score'])}<span class="why sub">{e((l['score_reason'] or '').replace('[fallback] ', ''))}</span></div>
          {stage_select(l)}
          <div class="la"><form method="post" action="/leads/{l['id']}/draft"><button class="secondary small" {'' if l['phone'] else 'disabled title="Needs a phone number"'}>Write message</button></form>
            <form method="post" action="/leads/{l['id']}/rescore"><button class="ghost small" title="Ask the AI to score again">Re-score</button></form></div>
        </div>""" for l in leads)
    leads_html = f"""
      {f'<div class="notice working" role="status"><span class="spin"></span>AI is working on {working} lead{"s" if working != 1 else ""}… this page refreshes by itself.</div>' if working else ''}
      <div class="toolbar">
        <label class="search">{ic('search', 18)}<input type="search" id="q" aria-label="Search leads" placeholder="Search name, niche, city, phone"></label>
        {cchips}
        <div class="trow"><div class="chips">{chips}</div>
          <div class="tr"><select id="sort" aria-label="Sort leads"><option value="score">Best score</option>
            <option value="new">Newest</option><option value="stage">Stage</option></select>
            <a class="iconbtn" href="/leads.csv" download title="Export CSV" aria-label="Export CSV">{ic('download', 18)}</a></div></div></div>
      <form id="bulk" method="post" action="/leads/bulk" class="floatbar" hidden>
        <b><span id="nsel">0</span> selected</b>
        <button class="primary small" name="action" value="draft">Write messages</button>
        <span class="grp"><select name="status" aria-label="New stage">{''.join(f'<option value="{k}">{v}</option>' for k, v in STATUS_LABEL.items())}</select>
        <button class="secondary small" name="action" value="stage">Set stage</button></span>
        <button class="danger small" name="action" value="delete" onclick="return confirm('Permanently delete the selected leads and their messages? This cannot be undone.')">Delete</button>
      </form>
      <div class="card flush leads"><div class="lhead"><label class="sa"><input type="checkbox" id="all" aria-label="Select all shown"> <span>Select all</span></label>
        <span class="h-b">Business</span><span class="h-s">AI score</span><span class="h-st">Stage</span><span></span></div>
        <div id="leadlist">{rows}</div></div>
      <p class="more"><button type="button" class="secondary" id="more" hidden></button></p>""" if leads else empty(
        "No leads yet", "<a href='#add' data-go='add'>Add leads</a> by searching for businesses or importing a list.", "leads")

    # ---------- Follow-ups ----------
    due = sum(1 for f in followups if when(f['due_at']).endswith('ago'))
    fu = "".join(f"""<a class="frow" href="/leads/{f['lead_id']}?from=followups">{av(f['lead_name'])}<span class="lt"><b>{e(f['lead_name'])}</b><span class="sub">Follow-up #{f['step']}</span></span>
        <span class="tag {'due' if when(f['due_at']).endswith('ago') else ''}"><span>{'Due ' if when(f['due_at']).endswith('ago') else ''}{t(f['due_at'])}</span></span></a>""" for f in followups)
    fu_html = f"""
      <p class="hint">After you message a lead, the agent plans polite reminders ({', '.join(f'day {d}' for d in config.FOLLOWUP_DAYS) or 'none set'}).
      If they write back, the reminders are cancelled. When one is due, the AI writes it and it appears in <b>Today</b>.</p>
      {f'<div class="card flush clist">{fu}</div>' if fu else empty("No follow-ups planned", "They appear here after you send a message to a lead.", "clock")}
      <form method="post" action="/followups/run" class="mt"><button class="secondary">Write due follow-ups now</button></form>"""

    # ---------- WhatsApp ----------
    cloud = whatsapp.is_cloud()
    old_html = f"""<div class="card"><h3>Link WhatsApp again</h3>
        <p class="sub">Only needed if WhatsApp logged out. You'll get an 8-character code to enter in
        WhatsApp → Linked devices → Link a device → <i>Link with phone number instead</i>.</p>
        <form method="post" action="/whatsapp/relink" class="inline">
          <input name="phone" inputmode="tel" placeholder="With country code, e.g. 9715…" required aria-label="Phone number">
          <button class="primary" {'disabled' if wa_ok else ''}>Get pairing code</button></form>
        {'<p class="sub">Disabled because you are already connected.</p>' if wa_ok else ''}
      </div>"""
    new_cloud = f"""<div class="card"><h3>Official WhatsApp Cloud API</h3>
        <p class="sub">There is no QR code or pairing: this uses your Meta business number. Free text can only go to leads who
        messaged you in the last 24 hours; first contact uses your approved template{f" <code>{e(config.WA_CLOUD_TEMPLATE)}</code>" if config.WA_CLOUD_TEMPLATE else " (<b>not set</b>: add WA_CLOUD_TEMPLATE in .env)"}.
        Webhook URL for Meta: <code>/webhook/cloud</code>.</p></div>"""
    es = emailer.status()
    if es["smtp"]:
        email_card = (f'''<div class="card" id="emailstat"><div class="ph"><h3>Email channel</h3><b>{es["sent_today"]} <span class="sub">of {es["limit"]} today</span></b></div>
        <p class="sub">Sending from <b>{e(es["from"])}</b>. Replies: {("read from your inbox" + (f", last check {t(es['poll_at'])}" if es["poll_at"] else ", first check soon")) if es["imap"] else "not read (add IMAP settings in <a href='/setup'>Setup</a>)"}.</p>
        {f'<div class="warn bad">Last inbox check failed: {e(es["poll_error"])}</div>' if es["poll_error"] else ''}</div>''')
    else:
        email_card = '<div class="card" id="emailstat"><h3>Email channel</h3><p class="sub">Off. Add your SMTP details in <a href="/setup">Setup</a> to also write to leads by email.</p></div>'
    wa_html = f"""
      <div class="card wastat {'ok' if wa_ok else 'bad'}"><div class="ib">{ic('phone', 22)}</div>
        <div><h3>{'Connected' if wa_ok else 'Not connected'}{' (official Cloud API)' if cloud else ''}</h3>
        <p class="sub">{f'{"Business number" if cloud else "Linked number"}: +{e(wa_number)}' if wa_number else 'Status: ' + e(wa_status)}</p></div></div>
      <div class="card"><div class="ph"><h3>Sent today</h3><b>{sent_today} <span class="sub">of {limit}</span></b></div>
        <div class="meter big"><i style="width:{pct}%"></i></div>
        <p class="sub">The limit protects your number from being banned. {config.SEND_GAP_MIN}-{config.SEND_GAP_MAX}s between messages. Sending hours: {e(whatsapp.schedule_summary())}.</p></div>
      {email_card}
      <div class="card"><h3>Backup</h3>
        <p class="sub">{('Last automatic backup: ' + last_backup.strftime('%d %b %Y, %H:%M') + '.') if last_backup else 'No backup yet. The first one is made within 10 minutes of the app running.'}
        A copy of your leads is saved once a day in the <code>backups</code> folder (the last 14 are kept).</p></div>
{new_cloud if cloud else old_html}"""

    # ---------- Settings ----------
    settings_html = f"""
      <div class="card"><h3>Sending queue</h3>
        <p class="sub">{len(queued)} queued. {'Paused: nothing is sent.' if paused else 'Running: sends one message at a time inside your sending hours.'}</p>
        <form method="post" action="/queue/{'resume' if paused else 'pause'}"><button class="{'primary' if paused else 'secondary'}">{'Resume queue' if paused else 'Pause queue'}</button></form></div>
      <form method="post" action="/settings">
        <div class="card"><h3>Branding</h3>
          <p class="sub">Shown in the header and browser tab. Only you see it.</p>
          <div class="two"><div><label class="lbl">App name</label><input name="brand_name" maxlength="40" value="{e(config.BRAND_NAME)}" placeholder="Lead Agent"></div>
          <div><label class="lbl">Tagline (optional)</label><input name="brand_tagline" maxlength="60" value="{e(config.BRAND_TAGLINE)}" placeholder="Your company name"></div></div></div>
        <div class="card"><h3>About your business</h3>
          <p class="sub">The AI reads this to score leads and write messages. Be specific about what you sell.</p>
          <textarea name="my_business" rows="6" required maxlength="1500" aria-label="About your business">{e(config.MY_BUSINESS)}</textarea></div>
        <div class="card"><h3>Sending limits</h3>
          <p class="sub">These protect your WhatsApp number from being banned. Keep them low.</p>
          <div class="two"><div><label class="lbl">Messages per day (max)</label><input name="daily_limit" type="number" min="1" max="500" value="{limit}" required></div>
          <div><label class="lbl">Seconds between messages (min)</label><input name="min_seconds" type="number" min="0" max="3600" value="{config.SEND_GAP_MIN}" required></div></div>
          <label class="lbl">Seconds between messages (max)</label><input name="max_seconds" type="number" min="0" max="3600" value="{config.SEND_GAP_MAX}" required>
          <p class="sub">Each gap is a random number between min and max, so sending looks less robotic.</p>
          <label class="lbl">Follow-up reminders after how many days?</label>
          <input name="followup_days" value="{e(','.join(map(str, config.FOLLOWUP_DAYS)))}" placeholder="3,7">
          <p class="sub">Separate with commas: 3,7 means a reminder on day 3 and day 7. Leave empty for none. Applies to leads you message from now on.</p></div>
        <div class="card"><h3>Sending hours</h3>
          <p class="sub">Messages only go out on the days ticked, between that day's times (your local time). Outside them, queued messages wait for the next window.</p>
          {week_hours()}</div>
        <div class="card"><h3>Opt-out line</h3>
          <label class="chk"><input type="checkbox" name="optout_on" value="1" {'checked' if config.OPTOUT_ENABLED else ''}> Add this line to the <b>first</b> message to each lead</label>
          <input name="optout_line" value="{e(config.OPTOUT_LINE)}" maxlength="120" aria-label="Opt-out line">
          <p class="sub">Not added to replies or follow-ups. Anyone who writes STOP is never messaged again.</p></div>
        <div class="card"><h3>AI model</h3>
          <label class="lbl">Ollama model name</label><input name="ollama_model" value="{e(config.OLLAMA_MODEL)}" required>
          <p class="sub">Must already be downloaded in Ollama. The status at the top turns red if it is not.</p></div>
        <button class="primary block">Save settings</button></form>
      <p class="sub" style="text-align:center;margin-top:14px"><a href="/setup">Run setup again</a> (password, WhatsApp, API keys)</p>"""

    # ---------- Add leads ----------
    add_html = f"""
      <div class="seg addtabs" role="tablist" aria-label="How to add leads">
        <button type="button" class="on" data-addtab="0">{ic('search', 16)} Search</button>
        <button type="button" data-addtab="1">{ic('file', 16)} Import</button>
        <button type="button" data-addtab="2">{ic('user', 16)} One lead</button></div>
      <div class="grid3">
        <form class="card addc on" method="post" action="/find"><div class="ib">{ic('search', 22)}</div><h3>Find businesses</h3>
          <p class="sub">Free search on OpenStreetMap. The AI scores each result and drafts a first message.</p>
          <label class="lbl">Type of business</label><input name="niche" id="niche" list="niches" placeholder="e.g. school, cafe" required>
          <div class="qp" aria-label="Quick picks">{''.join(f'<button type="button" class="chip" data-fill="{n}">{n}</button>' for n in ('school', 'clinic', 'gym', 'restaurant', 'real estate'))}</div>
          <datalist id="niches"><option>school<option>college<option>software house<option>clinic<option>gym<option>restaurant<option>real estate<option>hotel</datalist>
          <label class="lbl">City</label><input name="city" placeholder="e.g. Lahore, Dubai" required>
          {'<label class="lbl">Source</label><select name="source"><option value="osm">OpenStreetMap (free)</option><option value="google">Google Places</option></select>' if config.GOOGLE_PLACES_API_KEY else ''}
          <button class="primary block">Search</button></form>
        <form class="card addc" method="post" action="/import" enctype="multipart/form-data"><div class="ib">{ic('file', 22)}</div><h3>Import a list</h3>
          <p class="sub">A CSV file with columns: name, phone, website, niche, city, email. Duplicates are merged automatically.</p>
          <label class="lbl">CSV file</label><input type="file" name="file" accept=".csv" required>
          <button class="primary block">Import</button></form>
        <form class="card addc" method="post" action="/leads"><div class="ib">{ic('user', 22)}</div><h3>Add one lead</h3>
          <p class="sub">Type in a business you already know.</p>
          <label class="lbl">Business name</label><input name="name" required>
          <label class="lbl">WhatsApp number</label><input name="phone" inputmode="tel" placeholder="With country code">
          <div class="two"><div><label class="lbl">Niche</label><input name="niche"></div>
          <div><label class="lbl">City</label><input name="city"></div></div>
          <label class="lbl">Website</label><input name="website">
          <button class="primary block">Add lead</button></form>
      </div>"""

    # ---------- More (phone menu) ----------
    def mrow(go, icon, label, detail=""):
        return f'<a class="mrow" href="#{go}" data-go="{go}"><span class="ib sm">{ic(icon, 18)}</span><span>{label}</span><span class="sub">{detail}</span>{ic("chev", 18)}</a>'
    more_html = f"""<div class="card flush menu">
      {mrow('followups', 'clock', 'Follow-ups', f'{due} due' if due else f'{len(followups)} planned')}
      {mrow('whatsapp', 'phone', 'WhatsApp', 'Connected' if wa_ok else 'Not connected')}
      {mrow('settings', 'gear', 'Settings')}</div>
      {f'<div class="card flush menu"><a class="mrow" href="/logout"><span class="ib sm">{ic("out", 18)}</span><span>Sign out</span><span></span>{ic("chev", 18)}</a></div>' if config.DASHBOARD_PASSWORD else ''}"""

    # ---------- Shell ----------
    problems = [p for p, ok in (("AI offline", ai_ok), ("WhatsApp offline", wa_ok)) if not ok]
    health_title = f"AI ({e(config.OLLAMA_MODEL)}): {'ready' if ai_ok else 'offline'} · WhatsApp: {e(wa_status)}"
    health = (f'<a class="health {"bad" if problems else "ok"}" href="#whatsapp" data-go="whatsapp" title="{health_title}">'
              f'<i></i>{" · ".join(problems) if problems else "All systems on"}</a>')

    def cnt(n, alert=False):
        return f'<span class="cnt{" alert" if alert else ""}">{n}</span>' if n else ""

    def nv(go, icon, label, n=None, alert=False):
        return f'<a href="#{go}" data-go="{go}" class="nv">{ic(icon)}<span>{label}</span>{cnt(n, alert)}</a>'

    def bn(go, icon, label, n=None):
        dot = f'<span class="dot">{n}</span>' if n else ""
        return f'<a href="#{go}" data-go="{go}" class="bn">{ic(icon, 23)}{dot}<span>{label}</span></a>'

    def sh(title, sub="", back=False):
        b = f'<a class="backlink" href="#menu" data-go="menu">{ic("back", 18)}More</a>' if back else ""
        return f'<div class="sh">{b}<h2>{title}</h2>{f"<p class=sub>{sub}</p>" if sub else ""}</div>'

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#f6f6f3" media="(prefers-color-scheme: light)"><meta name="theme-color" content="#0e0f12" media="(prefers-color-scheme: dark)">
<title>{e(config.BRAND_NAME)}</title><style>{CSS}</style></head><body>
<a class="skip" href="#main">Skip to content</a>
<div class="app">
<aside class="side" aria-label="Sections">
  {brand_html(True)}
  <nav>{nv('approvals', 'today', 'Today', n_drafts + n_rem, True)}{nv('replies', 'chat', 'Chats', len(inbox))}{nv('leads', 'leads', 'Leads', len(leads))}{nv('add', 'add', 'Add leads')}
    <div class="navsep"></div>{nv('followups', 'clock', 'Follow-ups', due or None, True)}{nv('whatsapp', 'phone', 'WhatsApp')}{nv('settings', 'gear', 'Settings')}</nav>
  <div class="sidefoot">{health}
    <div class="sent"><span class="sub">Sent today</span><b>{sent_today} / {limit}</b><div class="meter"><i style="width:{pct}%"></i></div></div>
    {f'<a class="nv" href="/logout">{ic("out")}<span>Sign out</span></a>' if config.DASHBOARD_PASSWORD else ''}</div>
</aside>
<header class="topbar">{brand_html()}{health}</header>
<main class="main" id="main">
{flash_html(msg)}
<section id="approvals"><div class="sh"><p class="sub" id="hello">Today</p><h2>Today</h2></div>{overview}{rem_html}
  <div class="sh2" id="todo"><h3>Messages to check {cnt(n_drafts, True)}</h3>
  <p class="sub">The AI wrote these. <b>Nothing is sent until you press Send.</b> <span class="kbd">Keys: <kbd>j</kbd>/<kbd>k</kbd> move, <kbd>⌘</kbd>+<kbd>Enter</kbd> sends, <kbd>Esc</kbd> leaves the box.</span></p></div>
  {queue_html}{bulk_bar}{approvals}{insights}</section>
<section id="replies">{sh('Chats', 'The latest message from each lead. Tap one to open the whole conversation.')}{replies}</section>
<section id="leads">{sh('Leads', f'{len(leads)} businesses')}{leads_html}</section>
<section id="add">{sh('Add leads', 'Three ways to bring in new businesses.')}{add_html}</section>
<section id="menu">{sh('More')}{more_html}</section>
<section id="followups">{sh('Follow-ups', back=True)}{fu_html}</section>
<section id="whatsapp">{sh('WhatsApp', back=True)}{wa_html}</section>
<section id="settings">{sh('Settings', back=True)}{settings_html}</section>
</main>
</div>
<nav class="bnav" aria-label="Sections">{bn('approvals', 'today', 'Today', (n_drafts + n_rem) or None)}{bn('replies', 'chat', 'Chats')}<a href="#add" data-go="add" class="bn add" aria-label="Add leads"><span class="plus">{ic('add', 24)}</span><span>Add</span></a>{bn('leads', 'leads', 'Leads')}{bn('menu', 'more', 'More', due or None)}</nav>
<script>{JS}</script></body></html>"""


def reply_tag(tag, step=None):
    """Small coloured label for the AI's reading of a reply, with the suggested next step as hover text."""
    if not tag or tag not in REPLY_TAG_UI:
        return ""
    label, cls = REPLY_TAG_UI[tag]
    return f'<span class="tag {cls}" title="{e(step or "")}">{label}</span>'


REPLY_TAG_UI = {"interested": ("Interested", "ok"), "asking_price": ("Asking price", "due"), "not_now": ("Not now", ""),
                "not_interested": ("Not interested", "bad"), "other": ("Other", "")}


# Where the lead page's back link goes: ?from=<key> -> (dashboard section id, label). Unknown -> Leads.
BACK_TO = {"today": ("approvals", "Today"), "chats": ("replies", "Chats"), "leads": ("leads", "Leads"),
           "followups": ("followups", "Follow-ups")}


def lead_page(lead, *, msg, convo, followups, notes=(), reminders=(), came_from=""):
    i = lead["id"]
    bkey = came_from if came_from in BACK_TO else "leads"
    bsec, blabel = BACK_TO[bkey]
    nxt = f'<input type="hidden" name="next" value="/leads/{i}">'
    ST = {"draft": "Draft, waiting for approval", "approved": "Sending", "sending": "Sending", "queued": "", "cancelled": "Cancelled", "sent": "Sent", "rejected": "Discarded",
          "failed": "Failed", "received": ""}
    bubbles = "".join(f"""
      <div class="bub {'in' if m['direction'] == 'in' else 'out'} {e(m['status'])}">
        {f'<div class="meta"><b>{e(m["subject"])}</b></div>' if m.get('subject') else ''}<div>{e(m['body'])}</div><div class="meta">{chan_badge(m)} {t(m['sent_at'] or m['created_at'])}{(' · ' + e(ST.get(m['status'], m['status']))) if ST.get(m['status'], m['status']) else ''}{(' · Queued, sends at ' + eta(m['scheduled_at'])) if m['status'] == 'queued' else ''}</div>
        {f'<form method="post" action="/messages/{m["id"]}/cancel">{nxt}<button class="ghost small">Cancel</button></form>' if m['status'] == 'queued' else ''}
        {f'<div class="meta err">{e(m["error"])}</div>' if m.get('error') else ''}
        {f'<div class="meta">{reply_tag(m.get("tag"))} Next step: {e(m["next_step"])}</div>' if m.get('tag') else ''}
      </div>""" for m in convo)
    chat = f'<div class="chat">{bubbles}</div>' if bubbles else empty(
        "No messages yet", "Press <b>Write message</b> and the AI drafts a first message for your approval.", "chat")
    waiting = next((m for m in reversed(convo) if m["status"] == "draft" and m["direction"] == "out"), None)
    pending = waiting is not None
    compose = (f"""<form method="post" action="/messages/{waiting['id']}/send" class="mt">{nxt}
      <label class="lbl">Your reply <span class="sub">· written by AI, edit freely</span></label>
      {f'<label class="lbl">Subject</label><input name="subject" value="{e(waiting.get("subject") or "")}" maxlength="200">' if waiting.get('channel') == 'email' else ''}
      <textarea name="body" rows="5">{e(waiting['body'])}</textarea>
      <div class="acts"><button class="primary wa">{ic('send', 17)} Send {'as email' if waiting.get('channel') == 'email' else 'on WhatsApp'}</button>
      <button class="ghost" formaction="/messages/{waiting['id']}/reject">Discard</button></div></form>""") if waiting else ""
    fu = "".join(f"<li><span>Follow-up #{f['step']}</span><span class='tag'>{t(f['due_at'])}</span></li>"
                 for f in followups)
    fu_html = (f'<ul class="list">{fu}</ul><form method="post" action="/leads/{i}/cancel-followups">{nxt}'
               '<button class="ghost small">Cancel planned follow-ups</button></form>'
               if fu else '<p class="sub">No follow-ups planned.</p>')
    rem_list = ('<ul class="list">' + "".join(
        f"""<li><span>Remind: {e(r['note'] or '')} <span class="tag due">{dshort(r['due_date'])}</span></span>
        <form method="post" action="/reminders/{r['id']}/done"><input type="hidden" name="next" value="/leads/{i}"><button class="ghost small">Done</button></form></li>"""
        for r in reminders) + '</ul>') if reminders else ""
    note_list = ('<ul class="list">' + "".join(
        f"<li><span>{e(n['body'])}</span><span class='tag'>{t(n['created_at'])}</span></li>" for n in notes) + '</ul>'
        ) if notes else '<p class="sub">No notes yet.</p>'
    can_msg = db.pick_channel(lead) and lead["status"] != "opted_out"
    cur_ch = lead.get("channel") or ""
    chsel = (f'<form method="post" action="/leads/{i}/channel" class="chsel">{nxt}<label class="sub" for="lch">Channel</label>'
             f'<select id="lch" name="channel" onchange="this.form.submit()"><option value="">Automatic (WhatsApp if phone, else email)</option>'
             f'<option value="whatsapp"{" selected" if cur_ch == "whatsapp" else ""}>WhatsApp</option>'
             f'<option value="email"{" selected" if cur_ch == "email" else ""}>Email</option></select></form>')
    digits = "".join(ch for ch in (lead["phone"] or "") if ch.isdigit())
    site = lead["website"] or ""
    site_url = site if "://" in site else "https://" + site
    meta = " · ".join(x for x in (lead["niche"], lead["city"]) if x)

    def fld(name, label, val, **kw):
        return f'<label class="lbl">{label}</label><input name="{name}" value="{e(val)}" {kw.get("x", "")}>'
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>{e(lead['name'])} · {e(config.BRAND_NAME)}</title>
<style>{CSS}</style></head><body class="solo">
<a class="skip" href="#main">Skip to content</a>
<header class="topbar show"><a class="backlink" id="backlink" href="/#{bsec}" data-from="{bkey}" data-known="{1 if came_from in BACK_TO else 0}">{ic('back', 18)}<span>{blabel}</span></a>{brand_html()}</header>
<main class="main detail" id="main">
{flash_html(msg)}
<div class="card lhero">{av(lead['name'], 'av xl')}
  <div class="lhi"><h2>{e(lead['name'])}</h2><p class="sub">{e(meta) + ' · ' if meta else ''}Added {t(lead['created_at'])}</p>
    <div class="lhb">{score_badge(lead['score'])}{stage_select(lead, nxt)}</div>{chsel}</div>
  <div class="acts">
    <form method="post" action="/leads/{i}/draft">{nxt}<button class="primary" {'' if can_msg else 'disabled title="Needs a phone number (or an email with the email channel set up) and a lead who has not opted out"'}>{ic('spark', 17)} Write message</button></form>
    {f'<a class="btn secondary" href="https://wa.me/{digits}" target="_blank" rel="noopener">{ic("phone", 17)} WhatsApp</a>' if digits and lead['status'] != 'opted_out' else ''}
    <form method="post" action="/leads/{i}/rescore">{nxt}<button class="ghost">{ic('refresh', 17)} Re-score</button></form></div>
</div>
{'<div class="warn bad">This lead asked us to stop. Messages cannot be sent to them.</div>' if lead['status'] == 'opted_out' else ''}
<div class="dgrid"><div>
  <div class="card"><h3>Conversation</h3>{chat}
    {compose}</div></div>
<div>
  <div class="card"><h3>Why this score</h3>
    <p>{e((lead['score_reason'] or '').replace('[fallback] ', '')) or '<span class="sub">Not scored yet.</span>'}</p>
    {f'<p class="sub">Website: <a href="{e(site_url)}" target="_blank" rel="noopener">{e(site)}</a></p>' if site else ''}</div>
  <div class="card"><h3>Notes and reminders</h3>
    <form method="post" action="/leads/{i}/notes">
      <textarea name="body" rows="3" maxlength="2000" placeholder="e.g. Spoke to the owner, call back after Eid" aria-label="New note" required></textarea>
      <label class="lbl">Remind me on (optional)</label><input type="date" name="remind_on">
      <button class="primary block">Add note</button></form>
    {rem_list}{note_list}</div>
  <div class="card"><h3>Find contact details</h3>
    <p class="sub">Looks on the lead's own website (home page and /contact) and fills the email and phone only if they are empty.</p>
    <form method="post" action="/leads/{i}/find-contact"><button class="secondary" {'' if site else 'disabled title="Add a website first"'}>Find email/phone on website</button></form></div>
  <div class="card"><h3>Planned follow-ups</h3>{fu_html}</div>
  <form class="card" method="post" action="/leads/{i}"><h3>Details</h3>
    {fld('name', 'Business name', lead['name'], x='required')}{fld('phone', 'WhatsApp number', lead['phone'], x='inputmode="tel"')}
    {fld('website', 'Website', lead['website'])}{fld('email', 'Email', lead['email'], x='inputmode="email"')}
    {fld('campaign', 'Campaign (free text, e.g. Dentists Karachi Nov)', lead.get('campaign'), x='maxlength="60"')}
    <div class="two"><div>{fld('niche', 'Niche', lead['niche'])}</div><div>{fld('city', 'City', lead['city'])}</div></div>
    <label class="lbl">Other notes</label><textarea name="notes" rows="4">{e(lead['notes'])}</textarea>
    <button class="primary block">Save changes</button></form>
  <div class="card dz"><details><summary>Delete this lead…</summary>
      <p class="sub">This permanently deletes the lead, its messages and follow-ups. It cannot be undone.</p>
      <form method="post" action="/leads/{i}/delete"><button class="danger">Yes, delete permanently</button></form></details></div>
</div></div></main><script>{LOCALIZE}{BACKJS.replace('LEADID', str(i))}</script></body></html>"""


# Keep the back link pointing where the user came from after form posts redirect to /leads/<id> (no ?from=).
BACKJS = ("const BK=" + json.dumps({k: v for k, v in BACK_TO.items()}) + ";" + """
(()=>{const a=document.getElementById('backlink');if(!a)return;const key='leadFrom:LEADID';let f=a.dataset.from;
 try{if(a.dataset.known==='1')sessionStorage.setItem(key,f);else f=sessionStorage.getItem(key)||f}catch(e){}
 const t=BK[f];if(t){a.href='/#'+t[0];a.querySelector('span').textContent=t[1]}})();
""")


LOCALIZE = """
document.querySelectorAll('time[datetime]').forEach(x=>{const d=new Date(x.getAttribute('datetime'));if(!isNaN(d))x.title=d.toLocaleString([],{dateStyle:'medium',timeStyle:'short'})});
document.querySelectorAll('.lbl').forEach((l,i)=>{const c=l.nextElementSibling;if(c&&c.matches('input,textarea,select')){if(!c.id)c.id='f'+i+'-'+(c.name||'x');l.htmlFor=c.id}});
document.addEventListener('submit',ev=>{const b=ev.submitter||ev.target.querySelector('button.primary');
 if(b&&b.tagName==='BUTTON')setTimeout(()=>{b.disabled=true;b.setAttribute('aria-busy','true');b.textContent='Working…'},0)});
addEventListener('pageshow',ev=>{if(ev.persisted)location.reload()});
document.querySelectorAll('.toast').forEach(t=>setTimeout(()=>t.remove(),t.classList.contains('err')?12000:6000));
"""


def login_page(error, nxt):
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>Sign in · {e(config.BRAND_NAME)}</title><style>{CSS}</style></head><body class="solo">
<main class="login" id="main"><form class="card" method="post" action="/login">
<div class="logo big">{e(config.BRAND_NAME[:1].upper())}</div><h1>{e(config.BRAND_NAME)}</h1><p class="sub">{e(config.BRAND_TAGLINE) + ' · ' if config.BRAND_TAGLINE else ''}sign in to continue</p>
{f'<div class="warn bad" role="alert">{e(error)}</div>' if error else ''}
<label class="lbl" for="pw">Password</label><input id="pw" name="password" type="password" autocomplete="current-password" autofocus required>
<input type="hidden" name="next" value="{e(nxt)}"><button class="primary block">Sign in</button></form></main></body></html>"""


CSS = """
:root{color-scheme:light dark;--bg:#f6f6f3;--card:#fff;--fg:#16181d;--sub:#6b7079;--faint:#9ba0a8;--line:#ebebe6;--fill:#f1f1ed;
--ink:#16181d;--ink-fg:#fff;--acc:#4f46e5;--acc-soft:#eeeefe;--wa:#16a34a;--wa-fg:#fff;--wa-soft:#e9f8ee;
--ok:#0f7a4b;--ok-bg:#e3f5ec;--bad:#c0362c;--bad-bg:#fdecea;--warm:#9a5800;--warm-bg:#fff2d9;--cold:#5d6470;--cold-bg:#eff0f2;
--s-new:#a3a8b0;--s-contacted:#5b7cfa;--s-replied:#9b6cf2;--s-won:#16a34a;--s-lost:#d4d6da;--s-opted_out:#e5484d;
--sh:0 1px 2px rgba(20,22,30,.04),0 2px 8px rgba(20,22,30,.05);--r:18px}
@media (prefers-color-scheme:dark){:root{--bg:#0e0f12;--card:#17191d;--fg:#ececee;--sub:#9a9fa8;--faint:#6b7079;--line:#25282e;--fill:#202328;
--ink:#ececee;--ink-fg:#0e0f12;--acc:#8f8cff;--acc-soft:#25244a;--wa:#25c464;--wa-fg:#04170a;--wa-soft:#163222;
--ok:#4fd197;--ok-bg:#13291f;--bad:#ff8a80;--bad-bg:#3a1a18;--warm:#ffc061;--warm-bg:#33270f;--cold:#a3a8b0;--cold-bg:#23262c;
--s-lost:#454a52;--sh:none}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",system-ui,sans-serif;-webkit-font-smoothing:antialiased}
a{color:var(--acc);text-decoration:none}a:hover{text-decoration:underline}
h1,h2,h3{margin:0;letter-spacing:-.01em}h3{font-size:16px;font-weight:650}
.sub{color:var(--sub);font-size:13px;font-weight:400}.hint{color:var(--sub);font-size:14px;margin:0 0 14px}
.ic{flex:none;display:block}
/* ---------- shell ---------- */
.side{display:none}
.topbar{position:sticky;top:0;z-index:20;display:flex;align-items:center;justify-content:space-between;gap:10px;
 padding:calc(8px + env(safe-area-inset-top)) 16px 8px;background:color-mix(in srgb,var(--bg) 82%,transparent);backdrop-filter:saturate(1.6) blur(18px);-webkit-backdrop-filter:saturate(1.6) blur(18px)}
.brand{display:flex;align-items:center;gap:9px}.brand b{font-size:15px;font-weight:700}
.logo{width:30px;height:30px;border-radius:9px;background:var(--ink);color:var(--ink-fg);display:grid;place-items:center;font-weight:800;font-size:15px}
.health{display:inline-flex;align-items:center;gap:7px;font-size:12.5px;font-weight:600;padding:5px 11px;border-radius:99px;background:var(--ok-bg);color:var(--ok);white-space:nowrap}
.health i{width:7px;height:7px;border-radius:50%;background:currentColor;box-shadow:0 0 0 3px color-mix(in srgb,currentColor 25%,transparent)}
.health.bad{background:var(--bad-bg);color:var(--bad)}.health:hover{text-decoration:none}
.main{width:100%;max-width:920px;margin:0 auto;padding:4px 16px calc(96px + env(safe-area-inset-bottom))}
section{display:none;scroll-margin-top:200px}section.on{display:block;animation:fade .18s ease-out}@keyframes fade{from{opacity:.6}}
.sh{margin:6px 0 16px}.sh h2{font-size:30px;font-weight:760;letter-spacing:-.025em;line-height:1.15}.sh p{margin:4px 0 0}
#hello{font-size:13px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;margin:0 0 2px;color:var(--faint)}
.sh2{margin:26px 0 12px}.sh2 h3{font-size:19px;font-weight:700;display:flex;align-items:center;gap:8px}.sh2 p{margin:4px 0 0}
.backlink{display:inline-flex;align-items:center;gap:2px;font-size:15px;font-weight:500;margin:0 0 6px -4px}
.bnav{position:fixed;left:0;right:0;bottom:0;z-index:20;display:flex;justify-content:space-around;
 padding:6px 6px calc(6px + env(safe-area-inset-bottom));background:color-mix(in srgb,var(--card) 86%,transparent);
 backdrop-filter:saturate(1.6) blur(18px);-webkit-backdrop-filter:saturate(1.6) blur(18px);border-top:1px solid var(--line)}
.bn{flex:1;max-width:96px;display:flex;flex-direction:column;align-items:center;gap:2px;padding:4px 0;color:var(--faint);font-size:11px;font-weight:600;position:relative}
.bn:hover{text-decoration:none}.bn.on{color:var(--fg)}
.bn .dot{position:absolute;top:0;left:calc(50% + 6px);min-width:18px;height:18px;padding:0 5px;border-radius:99px;background:var(--bad);color:#fff;font-size:11px;line-height:18px;text-align:center;border:2px solid var(--card)}
.bn.add .plus{width:44px;height:30px;border-radius:12px;background:var(--ink);color:var(--ink-fg);display:grid;place-items:center;margin-bottom:1px}
.bn.add{color:var(--sub)}.bn.add.on{color:var(--fg)}
@media (min-width:900px){
 .app{display:grid;grid-template-columns:248px minmax(0,1fr);min-height:100vh}
 .side{display:flex;flex-direction:column;gap:18px;position:sticky;top:0;height:100vh;padding:20px 14px;border-right:1px solid var(--line);background:var(--bg)}
 .side .brand{padding:0 8px}.side .brand>div:last-child{display:flex;flex-direction:column;line-height:1.25}.side .brand .sub{font-size:12px}
 .side nav{display:flex;flex-direction:column;gap:2px}.navsep{height:1px;background:var(--line);margin:10px 8px}
 .nv{display:flex;align-items:center;gap:11px;padding:9px 10px;border-radius:11px;color:var(--sub);font-weight:550;font-size:14.5px}
 .nv:hover{background:var(--fill);color:var(--fg);text-decoration:none}.nv.on{background:var(--card);color:var(--fg);box-shadow:var(--sh)}
 .nv .cnt{margin-left:auto}.sidefoot{margin-top:auto;display:flex;flex-direction:column;gap:12px}.sidefoot .health{align-self:flex-start}
 .sent{padding:12px;border-radius:14px;background:var(--card);box-shadow:var(--sh);display:grid;grid-template-columns:1fr auto;gap:6px;font-size:13px}.sent .meter{grid-column:1/-1}
 .topbar,.bnav,.backlink,.bn{display:none}.solo .topbar{display:flex}.solo .backlink{display:inline-flex;margin:0}
 .main{padding:28px 36px 60px}.sh h2{font-size:32px}.sh{margin-top:4px}
 .toast{bottom:24px}.floatbar{bottom:24px}
}
/* ---------- basics ---------- */
.card{background:var(--card);border-radius:var(--r);padding:18px;margin-bottom:12px;box-shadow:var(--sh);border:1px solid var(--line)}
@media (prefers-color-scheme:light){.card{border-color:transparent}}
.card.flush{padding:0;overflow:hidden}.card h3+.sub,.card h3+p{margin-top:4px}
.ib{width:42px;height:42px;border-radius:13px;background:var(--acc-soft);color:var(--acc);display:grid;place-items:center;margin-bottom:12px}.ib.sm{width:32px;height:32px;border-radius:10px;margin:0}
.lbl{display:block;font-size:13px;font-weight:600;margin:14px 0 6px}
input,textarea,select{width:100%;font:inherit;font-size:16px;color:var(--fg);background:var(--fill);border:1px solid transparent;border-radius:12px;padding:11px 13px;transition:border-color .15s,background .15s}
input:focus,textarea:focus,select:focus{outline:none;border-color:var(--acc);background:var(--card);box-shadow:0 0 0 3px var(--acc-soft)}
input[type=checkbox]{width:20px;height:20px;accent-color:var(--acc);margin:0;flex:none}input[type=file]{padding:9px}
textarea{resize:vertical;line-height:1.5}select{width:auto;padding:8px 30px 8px 12px;font-size:14px;appearance:none;-webkit-appearance:none;
 background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' fill='none' stroke='%23888' stroke-width='2'%3E%3Cpath d='M3 4.5l3 3 3-3'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 10px center}
button,.btn{font:inherit;font-weight:600;font-size:15px;border-radius:12px;padding:11px 18px;cursor:pointer;border:1px solid transparent;display:inline-flex;align-items:center;justify-content:center;gap:7px;transition:transform .08s,filter .15s,background .15s}
button:active:not(:disabled),.btn:active{transform:scale(.97)}button:disabled{opacity:.4;cursor:not-allowed}.btn:hover{text-decoration:none}
.primary{background:var(--ink);color:var(--ink-fg)}.primary:hover:not(:disabled){filter:brightness(1.15)}
.primary.wa{background:var(--wa);color:var(--wa-fg)}.secondary{background:var(--fill);color:var(--fg)}.secondary:hover:not(:disabled){background:var(--line)}
.ghost{background:none;color:var(--sub)}.ghost:hover:not(:disabled){background:var(--fill);color:var(--fg)}
.danger{background:var(--bad);color:#fff}.small{padding:7px 12px;font-size:13.5px;border-radius:10px}.block{width:100%;margin-top:18px}
.iconbtn{width:38px;height:38px;border-radius:11px;background:var(--fill);color:var(--fg);display:grid;place-items:center;flex:none}
.tag.chan{background:var(--acc-bg,var(--cold-bg));color:var(--acc,var(--cold))}.tag.chan.wa{background:var(--ok-bg);color:var(--ok)}.tag.chan{padding:2px 8px;font-size:11px}
.chsel{margin-top:8px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}.chsel select{max-width:100%}.chsw{margin:6px 0}
.badge,.tag{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:650;padding:4px 10px;border-radius:99px;white-space:nowrap}
.badge:before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor}
.badge.hot{background:var(--hot-bg,var(--ok-bg));color:var(--ok)}.badge.warm{background:var(--warm-bg);color:var(--warm)}
.chk{display:flex;gap:10px;align-items:center;margin:4px 0 10px;font-size:15px}.chk input{width:auto}
.tag.ok{background:var(--ok-bg);color:var(--ok)}
.badge.cold,.badge.grey,.tag{background:var(--cold-bg);color:var(--cold)}.tag.due{background:var(--warm-bg);color:var(--warm)}.tag.bad{background:var(--bad-bg);color:var(--bad)}
.cnt{font-size:12px;font-weight:650;min-width:22px;text-align:center;padding:1px 7px;border-radius:99px;background:var(--fill);color:var(--sub)}.cnt.alert{background:var(--bad);color:#fff}
.av{--h:220;flex:none;width:42px;height:42px;border-radius:50%;display:grid;place-items:center;font-weight:700;font-size:14.5px;letter-spacing:.02em;
 background:hsl(var(--h) 70% 92%);color:hsl(var(--h) 45% 32%)}.av.xl{width:64px;height:64px;font-size:22px}
@media (prefers-color-scheme:dark){.av{background:hsl(var(--h) 28% 22%);color:hsl(var(--h) 70% 78%)}}
.warn{background:var(--warm-bg);color:var(--warm);border-radius:12px;padding:9px 13px;font-size:13.5px;font-weight:500}.warn.bad{background:var(--bad-bg);color:var(--bad);margin:0 0 12px}.warn[hidden]{display:none}
.notice{display:flex;align-items:center;gap:10px;background:var(--acc-soft);color:var(--acc);border-radius:14px;padding:12px 14px;margin-bottom:12px;font-weight:550;font-size:14px}
.spin{width:16px;height:16px;border-radius:50%;border:2px solid currentColor;border-right-color:transparent;animation:sp 0.8s linear infinite;flex:none}@keyframes sp{to{transform:rotate(360deg)}}
.meter{height:6px;border-radius:99px;background:var(--fill);overflow:hidden}.meter i{display:block;height:100%;background:var(--wa);border-radius:99px}.meter.big{height:10px;margin:10px 0}
.empty{text-align:center;padding:40px 22px}.empty .ib{margin:0 auto 14px}.empty p{max-width:420px;margin:6px auto 0}
.two{display:grid;grid-template-columns:1fr 1fr;gap:10px}.wk{display:grid;gap:6px;max-width:520px}.wkd{display:grid;grid-template-columns:56px minmax(0,1fr) minmax(0,1fr);gap:6px;align-items:center}.wkd input[type=time]{min-width:0;width:100%;padding-left:9px;padding-right:6px}@media (max-width:420px){.wkd input[type=time]::-webkit-calendar-picker-indicator{display:none}}.wkh .lbl{margin:8px 0 0}.wkon{display:flex;align-items:center;gap:10px;min-height:44px;font-weight:600;font-size:15px;cursor:pointer}.wkd:has(.wkon input:not(:checked)) input[type=time]{opacity:.35}.mt{margin-top:14px}
.ph{display:flex;justify-content:space-between;align-items:baseline;gap:10px}
/* ---------- today ---------- */
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:10px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:var(--r);padding:14px 15px;box-shadow:var(--sh);display:flex;flex-direction:column;gap:2px;min-width:0;color:var(--fg)}
@media (prefers-color-scheme:light){.kpi{border-color:transparent}}
.kpi .k{font-size:12.5px;color:var(--sub);font-weight:550}.kpi b{font-size:28px;font-weight:750;letter-spacing:-.02em;line-height:1.15;font-variant-numeric:tabular-nums}
.kpi b small{font-size:15px;color:var(--sub);font-weight:600}.kpi>small{font-size:12px;color:var(--sub)}.kpi .meter{margin:4px 0 2px}
.kpi.hero{background:var(--ink);color:var(--ink-fg);border-color:transparent}.kpi.hero .k,.kpi.hero small{color:inherit;opacity:.7}.kpi.hero:hover{text-decoration:none;filter:brightness(1.12)}
.kpi.hero.zero{background:var(--card);color:var(--fg);border-color:var(--line)}
.pipe .stack{display:flex;gap:3px;height:12px;margin:14px 0 14px}.pipe .stack i{border-radius:4px;min-width:6px}
.s-new{background:var(--s-new)}.s-contacted{background:var(--s-contacted)}.s-replied{background:var(--s-replied)}.s-won{background:var(--s-won)}.s-lost{background:var(--s-lost)}.s-opted_out{background:var(--s-opted_out)}
.legend{display:grid;grid-template-columns:repeat(3,1fr);gap:8px 18px}.lg{display:flex;align-items:center;gap:8px;font-size:13.5px}.lg i{width:9px;height:9px;border-radius:3px;flex:none}
.lg b{margin-left:auto;font-variant-numeric:tabular-nums}.lg.z{opacity:.45}
/* draft card */
.dlist{overflow:hidden}.draft{padding:0;border-bottom:1px solid var(--line);scroll-margin-top:72px}.draft:last-child{border:0}.draft summary{list-style:none;cursor:pointer;padding:10px 16px;margin:0;min-height:64px;display:flex;align-items:center;gap:12px}.dchips{margin:0 0 10px}.more{display:block;margin:12px auto}button[hidden],.more[hidden]{display:none!important}.chatsearch{margin-bottom:10px}.dlist .dh .who{min-width:0;flex:1;display:flex;flex-direction:row;flex-wrap:wrap;align-items:baseline;column-gap:8px}.dlist .dh .who .name{max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.dlist .dh .who>.name,.dlist .dh .who>.sub{display:inline-block;width:auto;flex:0 1 auto;line-height:1.3}.dlist .dh .who .sub{font-size:12.5px;white-space:nowrap}.dlist .dh .who>.pv{display:block;width:100%;line-height:1.35}.dlist .dh .pv{flex-basis:100%}.dlist summary{padding:8px 16px}.dlist .dh .dr{display:flex;align-items:center;gap:8px;flex:none}.draft summary::-webkit-details-marker{display:none}
.draft details[open] summary{padding-bottom:6px}.dbody{padding:0 16px 16px}.dmeta{margin:0 0 12px 54px}
.pv{font-size:13.5px;color:var(--sub);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.draft details[open] .pv{display:none}
.tog{color:var(--faint);transition:transform .15s;display:grid}.draft details[open] .tog{transform:rotate(90deg)}.draft summary:hover .name{color:var(--acc)}.draft.cur{box-shadow:inset 0 0 0 2px var(--acc)}.draft:focus{outline:none}
.dh{display:flex;align-items:center;gap:12px}.who{min-width:0;flex:1;display:flex;flex-direction:column}
.who .name{color:var(--fg);font-weight:700;font-size:16px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.who .sub{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.dr{display:flex;align-items:center;gap:8px;flex:none}
.pick{display:flex;align-items:center;gap:6px;font-size:13px;color:var(--sub);cursor:pointer;padding:10px 6px;margin:-10px -6px}
.thread{display:flex;flex-direction:column;gap:6px;background:var(--bg);border-radius:14px;padding:10px 12px;margin-bottom:12px}
.tl{font-size:11.5px;font-weight:650;color:var(--faint);text-transform:uppercase;letter-spacing:.06em}
.b{max-width:86%;padding:7px 11px;border-radius:14px;font-size:14px;white-space:pre-wrap;word-break:break-word}.b .m{display:block;font-size:11px;color:var(--sub);margin-top:2px;white-space:normal}
.b.in{align-self:flex-start;background:var(--card);border-bottom-left-radius:5px}.b.out{align-self:flex-end;background:var(--wa-soft);border-bottom-right-radius:5px}
.b.hi{box-shadow:0 0 0 1.5px var(--acc) inset}
.draft .lbl{margin-top:0}.compose{background:var(--wa-soft);border-radius:16px;padding:13px 14px;min-height:120px}
.under{display:flex;justify-content:space-between;gap:10px;align-items:flex-start;margin-top:6px}.under .warn{flex:1}.cnt.sub,.under .cnt{background:none;padding:0;margin-left:auto;white-space:nowrap;font-size:12px;font-weight:500}
.tools{display:flex;gap:6px;margin-top:10px;flex-wrap:wrap}
.tool{flex:none;font-size:13px;font-weight:600;padding:7px 12px;border-radius:99px;background:var(--fill);color:var(--fg);gap:5px}.tool:hover:not(:disabled){background:var(--line)}
.acts{display:flex;gap:8px;margin-top:14px;flex-wrap:wrap;align-items:center}.acts form{display:contents}
.draft .acts .wa{flex:1;max-width:340px;padding:13px 18px}
.kbd{display:none}kbd{font:12px ui-monospace,Menlo,monospace;background:var(--fill);border-radius:5px;padding:1px 5px}
@media (min-width:900px){.kbd{display:inline}}
.floatbar{position:fixed;left:50%;transform:translateX(-50%);bottom:calc(78px + env(safe-area-inset-bottom));z-index:25;display:flex;align-items:center;gap:10px;flex-wrap:wrap;justify-content:center;
 background:var(--ink);color:var(--ink-fg);padding:8px 8px 8px 16px;border-radius:18px;box-shadow:0 10px 30px rgba(0,0,0,.25);width:max-content;max-width:calc(100vw - 24px)}
.floatbar[hidden]{display:none}.floatbar .secondary{background:color-mix(in srgb,var(--ink-fg) 15%,transparent);color:inherit}
.floatbar select{background-color:color-mix(in srgb,var(--ink-fg) 15%,transparent);color:inherit;padding:7px 28px 7px 10px;font-size:13.5px}.floatbar .grp{display:flex;gap:6px}
.floatbar .primary:not(.wa){background:var(--ink-fg);color:var(--ink)}
/* chats + lists */
.clist>a{display:flex;gap:12px;align-items:center;padding:13px 16px;color:var(--fg);border-bottom:1px solid var(--line)}.clist>a:last-child{border:0}
.clist>a:hover{background:var(--fill);text-decoration:none}
.cm{flex:1;min-width:0}.ct{display:flex;justify-content:space-between;gap:10px;align-items:baseline}.ct b{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.ct .sub{flex:none;font-size:12px}
.cp{display:flex;gap:10px;align-items:center}.cp p{margin:1px 0 0;color:var(--sub);font-size:14px;flex:1;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.unread{flex:none;min-width:22px;height:22px;padding:0 6px;border-radius:99px;background:var(--wa);color:var(--wa-fg);font-size:12px;font-weight:700;display:grid;place-items:center}
.lt{display:flex;flex-direction:column;min-width:0;flex:1}.lt b{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.lt .sub{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.frow .tag{flex:none}.clist>.frow{display:flex;gap:10px;align-items:center;padding:12px 16px;border-bottom:1px solid var(--line)}.clist>.frow:last-child{border:0}.clist>.frow .lt{color:var(--fg)}.clist>.frow .lt:hover{text-decoration:none}.frow form{flex:none}.lt .sub.due{color:var(--warm);font-weight:600}
.menu{margin-bottom:14px}.mrow{display:grid;grid-template-columns:auto 1fr auto auto;gap:12px;align-items:center;padding:12px 14px;color:var(--fg);font-weight:550;border-bottom:1px solid var(--line)}
.mrow:last-child{border:0}.mrow:hover{background:var(--fill);text-decoration:none}.mrow>.ic{color:var(--faint)}
/* leads */
.toolbar{display:flex;flex-direction:column;gap:10px;margin-bottom:12px}
.search{display:flex;align-items:center;gap:8px;background:var(--card);border-radius:14px;padding:0 14px;box-shadow:var(--sh);border:1px solid var(--line);color:var(--faint)}
.search:focus-within{border-color:var(--acc);box-shadow:0 0 0 3px var(--acc-soft)}.search input{background:none;border:0;padding:12px 0;box-shadow:none}.search input:focus{box-shadow:none;background:none}
.trow{display:flex;gap:10px;align-items:center;justify-content:space-between}.tr{display:flex;gap:8px;align-items:center;flex:none}.tr select{background-color:var(--card);border:1px solid var(--line)}
.chips{display:flex;gap:6px;overflow-x:auto;scrollbar-width:none;min-width:0}.chips::-webkit-scrollbar{display:none}
.chip{flex:none;font-size:13.5px;font-weight:600;padding:7px 13px;border-radius:99px;background:var(--card);color:var(--sub);border:1px solid var(--line)}
.chip b{font-weight:600;opacity:.6;margin-left:2px}.chip.on{background:var(--ink);color:var(--ink-fg);border-color:var(--ink)}
.lhead{display:flex;align-items:center;gap:12px;padding:10px 16px;border-bottom:1px solid var(--line);font-size:12px;font-weight:650;color:var(--faint);text-transform:uppercase;letter-spacing:.05em}
.lhead .sa{display:flex;align-items:center;gap:10px;cursor:pointer}.lhead>span{display:none}
.lrow{display:grid;grid-template-columns:20px minmax(0,1fr) auto;grid-template-areas:"c m m" "c st s";gap:8px 12px;align-items:center;padding:12px 16px;border-bottom:1px solid var(--line)}
.lrow:last-child{border:0}.lrow .sel{grid-area:c;align-self:start;margin-top:11px;width:22px;height:22px}.lrow{grid-template-columns:22px minmax(0,1fr) auto}
.lm{grid-area:m;display:flex;gap:12px;align-items:center;color:var(--fg);min-width:0}.lm:hover{text-decoration:none}.lm:hover b{color:var(--acc)}
.ls{grid-area:s;display:flex;flex-direction:column;align-items:flex-end;gap:4px;justify-self:end}.ls .why{display:none}
.lrow .stf{grid-area:st;padding-left:54px}.la{display:none}
.stage{font-size:12.5px;font-weight:650;padding:4px 26px 4px 10px;border-radius:99px;border:0;background-color:var(--fill);color:var(--fg);background-position:right 8px center}
.stage.s-new{background-color:var(--cold-bg)}.stage.s-contacted{background-color:color-mix(in srgb,var(--s-contacted) 16%,var(--card))}
.stage.s-replied{background-color:color-mix(in srgb,var(--s-replied) 18%,var(--card))}.stage.s-won{background-color:var(--ok-bg);color:var(--ok)}
.stage.s-lost{background-color:var(--fill);color:var(--sub)}.stage.s-opted_out{background-color:var(--bad-bg);color:var(--bad)}
.more{text-align:center}
@media (min-width:700px){
 .lhead>span{display:block}.lhead .sa span{display:none}
 .lhead,.lrow{display:grid;grid-template-columns:20px minmax(0,1.6fr) minmax(0,1.4fr) 116px auto;grid-template-areas:"c m s st a";gap:14px}
 .lrow .stf{padding:0}.lrow .sel{align-self:center;margin:0}.ls{align-items:flex-start}.ls .why{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;font-size:12.5px}
 .la{grid-area:a;display:flex;gap:4px;justify-content:flex-end}.la button{white-space:nowrap}.la form{display:contents}
}
/* add / settings / whatsapp */
.grid3{display:grid;gap:14px}.grid3 .card{margin:0}
.addc .two{row-gap:0}
@media (min-width:900px){.addc{display:flex;flex-direction:column}.addc>.primary{margin-top:auto}.addc>:nth-last-child(2){margin-bottom:18px}.addc>.sub{margin-bottom:0}
 .grid3{grid-template-columns:1fr 1fr}.grid3>:first-child{grid-column:1/-1}}
@media (min-width:1180px){.grid3{grid-template-columns:repeat(3,minmax(0,1fr))}.grid3>:first-child{grid-column:auto}.addc>.sub{min-height:3lh}}
.addc h3{font-size:17px}
.seg{display:none;gap:4px;padding:4px;background:var(--fill);border-radius:14px;margin-bottom:12px}
.seg button{flex:1;padding:9px 6px;font-size:14px;background:none;color:var(--sub);border-radius:11px;gap:6px}.seg button.on{background:var(--card);color:var(--fg);box-shadow:var(--sh)}
.qp{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}.qp .chip{padding:6px 14px;min-height:40px}
.addc input[type=file]{padding:14px 12px;border:1.5px dashed var(--faint);background:none}
.addc .ib{display:none}
@media (max-width:899px){.js .seg{display:flex}.js .addc:not(.on){display:none}}
@media (min-width:900px){.addc .ib{display:grid}}
.wastat{display:flex;gap:14px;align-items:center}.wastat .ib{margin:0}.wastat.ok .ib{background:var(--ok-bg);color:var(--ok)}.wastat.bad .ib{background:var(--bad-bg);color:var(--bad)}.wastat h3{font-size:18px}
.inline{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}.inline input{flex:1;min-width:200px}
#settings .card h3{margin-bottom:2px}#settings textarea{margin-top:12px}#settings .card>.sub:last-child{margin:6px 0 0}
/* lead page */
.lhero{display:grid;grid-template-columns:auto 1fr;gap:4px 16px;align-items:center;padding:20px}
.lhi h2{font-size:24px;font-weight:750;letter-spacing:-.02em;line-height:1.2}.lhi .sub{margin:2px 0 0}
.lhb{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:8px}.lhb .stf{display:inline}
.lhero .acts{grid-column:1/-1;margin-top:16px}.lhero .acts .primary{flex:1;max-width:260px}
.dgrid{display:grid;grid-template-columns:1.35fr 1fr;gap:12px;align-items:start}.dgrid>div>.card:last-child{margin-bottom:0}
.chat{display:flex;flex-direction:column;gap:6px;margin-top:12px;background:var(--bg);border-radius:14px;padding:12px}
.bub{max-width:85%;padding:8px 12px;border-radius:16px;word-break:break-word;font-size:14.5px}.bub>div:first-child{white-space:pre-wrap}
.bub.in{align-self:flex-start;background:var(--card);border-bottom-left-radius:5px}.bub.out{align-self:flex-end;background:var(--wa-soft);border-bottom-right-radius:5px}
.bub.draft{background:var(--card);border:1.5px dashed var(--faint)}.bub.failed{background:var(--bad-bg);color:var(--bad)}.bub.rejected{opacity:.5}
.bub .meta{font-size:11px;color:var(--sub);margin-top:3px}.bub.failed .meta{color:inherit}
.list{list-style:none;padding:0;margin:8px 0 10px}.list li{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:9px 0;border-bottom:1px solid var(--line)}.list li:last-child{border:0}
.dz summary{cursor:pointer;color:var(--bad);font-weight:600;list-style:none}.dz summary::-webkit-details-marker{display:none}.dz form{margin-top:10px}
.solo .topbar{display:flex}.solo .topbar .brand{display:none}.solo .main{padding-bottom:48px}
.detail{max-width:1040px}
.login{min-height:100vh;display:grid;place-items:center;padding:24px 16px}.login form{width:100%;max-width:380px;padding:28px 24px;text-align:center}
.login h1{font-size:24px;font-weight:760;margin-top:14px}.login .sub{margin-top:4px}.login .lbl{text-align:left;margin-top:22px}.login .warn{margin:16px 0 0;text-align:left}
.logo.big{width:56px;height:56px;border-radius:16px;font-size:26px;margin:0 auto}
@media (max-width:899px){.kpis{grid-template-columns:1fr 1fr;gap:8px}.kpi{padding:11px 13px}.kpi b{font-size:24px}.legend{grid-template-columns:1fr 1fr}}
@media (max-width:699px){.pick span{display:none}.draft .dr .badge{display:none}.draft .acts .ghost{padding:11px 12px}.draft .acts .wa{white-space:nowrap}.dgrid{grid-template-columns:1fr}.two{grid-template-columns:1fr}.draft .acts .wa{max-width:none}.sh h2{font-size:28px}
 .lhero .acts .primary{max-width:none;flex-basis:100%}.lhero .acts>*{flex:1}.card{padding:16px}}
/* feedback */
.toast{position:fixed;left:50%;bottom:calc(84px + env(safe-area-inset-bottom));transform:translateX(-50%);z-index:30;width:max-content;max-width:min(92vw,520px);background:var(--ink);color:var(--ink-fg);border-radius:14px;padding:12px 44px 12px 16px;font-weight:550;box-shadow:0 10px 30px rgba(0,0,0,.25);animation:rise .25s ease-out}
.solo .toast{bottom:24px}.toast.err{background:var(--bad);color:#fff}
.x{position:absolute;right:6px;top:50%;transform:translateY(-50%);background:none;color:inherit;font-size:20px;padding:4px 10px}.x:active{transform:translateY(-50%)}
@keyframes rise{from{opacity:0;transform:translate(-50%,12px)}}
@media (prefers-reduced-motion:reduce){.toast,section.on,.spin{animation:none}html{scroll-behavior:auto}}
:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
.skip{position:absolute;left:8px;top:-60px;background:var(--ink);color:var(--ink-fg);padding:8px 12px;border-radius:10px;z-index:40}.skip:focus{top:8px}
"""

JS = LOCALIZE + """
document.documentElement.classList.add('js');
document.querySelectorAll('[data-addtab]').forEach(b=>b.addEventListener('click',()=>{const i=+b.dataset.addtab;
 document.querySelectorAll('[data-addtab]').forEach(x=>x.classList.toggle('on',x===b));
 document.querySelectorAll('.addc').forEach((c,j)=>c.classList.toggle('on',j===i))}));
document.querySelectorAll('[data-fill]').forEach(b=>b.addEventListener('click',()=>{const n=document.getElementById('niche');n.value=b.dataset.fill;document.getElementById('niche').closest('form').querySelector('input[name=city]').focus()}));
const hello=document.getElementById('hello');if(hello){const h=new Date().getHours();
 hello.textContent=(h<12?'Good morning':h<17?'Good afternoon':'Good evening')+' · '+new Date().toLocaleDateString([],{weekday:'long',day:'numeric',month:'long'})}
const secs=[...document.querySelectorAll('main>section')],MORE=['menu','followups','whatsapp','settings'];
function show(id,scroll){if(!secs.some(s=>s.id===id))id='approvals';secs.forEach(s=>s.classList.toggle('on',s.id===id));
 const grp=MORE.includes(id)?'menu':id;
 document.querySelectorAll('.nv[data-go],.bn').forEach(t=>{const on=t.classList.contains('bn')?t.dataset.go===grp:t.dataset.go===id;
  t.classList.toggle('on',on);on?t.setAttribute('aria-current','page'):t.removeAttribute('aria-current')});
 try{localStorage.setItem('tab',id)}catch(e){};if(scroll)scrollTo(0,0)}
document.addEventListener('click',ev=>{const t=ev.target.closest('[data-go]');if(!t)return;ev.preventDefault();show(t.dataset.go,true);history.replaceState(null,'','#'+t.dataset.go)});
document.querySelector('.kpi.hero')?.addEventListener('click',ev=>{ev.preventDefault();(document.querySelector('#approvals .draft')||document.getElementById('todo')).scrollIntoView({behavior:'smooth',block:'start'})});
addEventListener('hashchange',()=>show(location.hash.slice(1),true));
let start=location.hash.slice(1);if(!start){try{start=localStorage.getItem('tab')}catch(e){}}show(start||'approvals');
const q=document.getElementById('q'),sortSel=document.getElementById('sort'),moreBtn=document.getElementById('more'),LIMIT=50;let filter='all',camp='',shown=LIMIT;
const cmp={score:(a,b)=>b.dataset.score-a.dataset.score||b.dataset.id-a.dataset.id,new:(a,b)=>b.dataset.id-a.dataset.id,stage:(a,b)=>a.dataset.stage-b.dataset.stage||b.dataset.score-a.dataset.score};
function apply(){const box=document.getElementById('leadlist');if(!box)return;const s=(q&&q.value||'').toLowerCase();
 const rows=[...box.querySelectorAll('.lrow')].sort(cmp[sortSel.value]);rows.forEach(r=>box.appendChild(r));let n=0;
 rows.forEach(r=>{const ok=(filter==='all'||r.dataset.status===filter)&&(!camp||r.dataset.campaign===camp)&&r.dataset.text.includes(s);if(ok)n++;r.style.display=ok&&n<=shown?'':'none';r.dataset.vis=ok&&n<=shown?'1':''});
 moreBtn.hidden=n<=shown;moreBtn.textContent='Show more ('+Math.max(0,n-shown)+' left)';sel()}
function sel(){const c=document.querySelectorAll('#leadlist input[name=ids]:checked').length,bar=document.getElementById('bulk');
 if(bar){bar.hidden=!c;document.getElementById('nsel').textContent=c}}
function reset(){shown=LIMIT;apply()}
if(q){q.addEventListener('input',reset);sortSel.addEventListener('change',reset);moreBtn.addEventListener('click',()=>{shown+=LIMIT;apply()});
 document.getElementById('all').addEventListener('change',ev=>{document.querySelectorAll('.lrow[data-vis="1"] input[name=ids]').forEach(b=>b.checked=ev.target.checked);sel()});
 document.addEventListener('change',ev=>{if(ev.target.matches('#leadlist input[name=ids]'))sel()});
 document.querySelectorAll('.cchip').forEach(c=>c.addEventListener('click',()=>{camp=c.classList.contains('on')?'':c.dataset.campaign;document.querySelectorAll('.cchip').forEach(x=>x.classList.toggle('on',x===c&&camp!==''));reset()}));
 document.querySelectorAll('.chip:not(.cchip)').forEach(c=>c.addEventListener('click',()=>{filter=c.dataset.filter;
  document.querySelectorAll('.chip:not(.cchip)').forEach(x=>x.classList.toggle('on',x===c));reset()}));apply()}
document.querySelectorAll('textarea[data-count]').forEach(t=>{const f=t.form,n=f.querySelector('.n'),w=f.querySelector('.warn');
 const chk=()=>{n.textContent=t.value.length;let m='';if(t.value.includes('[fallback]'))m='The AI was offline when this was written, so it is a plain template. Check it carefully.';
  else if(/\\[[^\\]]{1,40}\\]/.test(t.value))m='This still has a placeholder like [Name]. Replace it before sending.';w.textContent=m;w.hidden=!m};
 t.addEventListener('input',chk)});
let dirty=false;document.addEventListener('input',ev=>{if(ev.target.matches('textarea,input'))dirty=true});
document.addEventListener('change',ev=>{if(ev.target.matches('input[form=bulksend]')){const n=document.querySelectorAll('input[form=bulksend]:checked').length,b=document.getElementById('bulksend');b.hidden=!n;document.getElementById('nsend').textContent=n}});
const allD=()=>[...document.querySelectorAll('#dlist .draft')],cards=()=>allD().filter(x=>x.style.display!=='none');let cur=-1;
let dF='all',dShown=10;const dmore=document.getElementById('dmore');
function dApply(openFirst){let n=0;allD().forEach(r=>{const k=r.dataset.kind,ok=dF==='all'||(dF==='failed'?r.dataset.failed==='1':k===dF);if(ok)n++;const v=ok&&n<=dShown;r.style.display=v?'':'none'});
 if(dmore){dmore.hidden=n<=dShown;dmore.textContent='Show '+Math.max(0,Math.min(10,n-dShown))+' more'}
 if(openFirst){const c=cards();allD().forEach(x=>x.querySelector('details').open=false);if(c[0])c[0].querySelector('details').open=true;cur=0}}
document.querySelectorAll('[data-dfilter]').forEach(c=>c.addEventListener('click',()=>{dF=c.dataset.dfilter;dShown=10;
 document.querySelectorAll('[data-dfilter]').forEach(x=>x.classList.toggle('on',x===c));dApply(true)}));
dmore&&dmore.addEventListener('click',()=>{dShown+=10;dApply(false)});
if(document.getElementById('dlist')){let nx=null;try{nx=sessionStorage.getItem('nextDraft');sessionStorage.removeItem('nextDraft')}catch(e){}
 const t=nx&&allD().findIndex(x=>x.dataset.id===nx);if(t>=10)dShown=t+1;dApply(false);
 const c=cards(),tgt=(t>=0&&allD()[t])||c[0];if(tgt){tgt.querySelector('details').open=true;cur=cards().indexOf(tgt);if(nx&&t>=0)setTimeout(()=>tgt.scrollIntoView({block:'start'}),50)}}
document.addEventListener('submit',ev=>{const d=ev.target.closest&&ev.target.closest('.draft');if(!d)return;const a=allD(),i=a.indexOf(d),nn=a.slice(i+1).find(x=>x.style.display!=='none')||a.slice(0,i).reverse().find(x=>x.style.display!=='none');
 try{nn?sessionStorage.setItem('nextDraft',nn.dataset.id):sessionStorage.removeItem('nextDraft')}catch(e){}});
const cq=document.getElementById('cq'),cmore=document.getElementById('cmore');let cShown=20;
function cApply(){const s=(cq.value||'').toLowerCase().trim(),rows=[...document.querySelectorAll('#clist .crow')];let n=0;
 rows.forEach(r=>{const ok=!s||r.dataset.text.includes(s);if(ok)n++;r.style.display=ok&&n<=cShown?'':'none'});
 cmore.hidden=n<=cShown;cmore.textContent='Show more ('+Math.max(0,n-cShown)+' left)';document.getElementById('cnone').hidden=n>0}
if(cq){cq.addEventListener('input',()=>{cShown=20;cApply()});cmore.addEventListener('click',()=>{cShown+=20;cApply()});cApply()}
let ready=false;setTimeout(()=>ready=true,600);
document.querySelectorAll('#approvals details[name=drafts]').forEach(x=>x.addEventListener('toggle',()=>{if(x.open&&ready){document.querySelectorAll('#approvals details[name=drafts]').forEach(o=>{if(o!==x)o.open=false});cur=cards().indexOf(x.closest('.draft'));
 const r=x.getBoundingClientRect();if(r.top<60||r.top>innerHeight*.6)x.closest('.draft').scrollIntoView({block:'start',behavior:'smooth'})}}));
function go(d){const c=cards();if(!c.length)return;cur=Math.max(0,Math.min(c.length-1,cur+d));c[cur].querySelector('details').open=true;c.forEach((x,i)=>x.classList.toggle('cur',i===cur));c[cur].focus({preventScroll:true});c[cur].scrollIntoView({block:'center',behavior:'smooth'})}
document.addEventListener('keydown',ev=>{if(!document.getElementById('approvals').classList.contains('on'))return;
 const typing=ev.target.matches('input,textarea,select');
 if((ev.metaKey||ev.ctrlKey)&&ev.key==='Enter'){const f=ev.target.closest('form')||(cards()[cur]||{}).querySelector&&cards()[cur].querySelector('form');
  const b=f&&f.querySelector('button.primary');if(b&&!b.disabled){ev.preventDefault();f.requestSubmit(b)}return}
 if(ev.key==='Escape'&&typing){ev.target.blur();return}
 if(typing||ev.metaKey||ev.ctrlKey||ev.altKey)return;
 if(ev.key==='j')go(1);else if(ev.key==='k')go(-1)});
setInterval(()=>{if(!dirty&&!document.activeElement.matches('textarea,input,select')&&!document.querySelector('input[name=ids]:checked'))location.reload()},document.querySelector('.working')?5000:60000);
"""
