"""First-run setup page (/setup): one mobile-friendly form that writes the main settings to .env and applies them live."""
import re

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import auth, config, emailer, ui, wa_cloud, whatsapp

router = APIRouter()
e = ui.e

# form field -> .env key, for secrets (blank = keep the saved value, so nothing secret is ever echoed into the page)
SECRET_FIELDS = {"password": "DASHBOARD_PASSWORD", "cloud_token": "WA_CLOUD_TOKEN", "cloud_verify": "WA_CLOUD_VERIFY_TOKEN",
                 "cloud_secret": "WA_CLOUD_APP_SECRET", "places_key": "GOOGLE_PLACES_API_KEY", "api_key": "API_KEY",
                 "crm_secret": "CRM_WEBHOOK_SECRET", "smtp_password": "SMTP_PASSWORD", "imap_password": "IMAP_PASSWORD"}


def _secret_input(name, label, key, gen=False):
    cur = getattr(config, key)
    ph = f"saved: {config.mask(cur)} (leave blank to keep)" if cur else "not set"
    g = f'<button type="button" class="secondary" onclick="gen(\'{name}\')">Generate random key</button>' if gen else ""
    return (f'<label class="lbl" for="{name}">{e(label)}</label><input id="{name}" name="{name}" type="password" autocomplete="new-password" '
            f'placeholder="{e(ph)}">{g}')


def _page(request, error="", ok=""):
    c = config
    cloud = c.WHATSAPP_PROVIDER == "cloud"
    base = str(request.base_url).rstrip("/")
    wa_status = "—" if cloud else whatsapp.status()
    hhmm = lambda t: t if re.fullmatch(r"\d\d:\d\d", t or "") else "09:00"
    body = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>Setup · {e(c.BRAND_NAME)}</title><style>{ui.CSS}
.setup{{max-width:560px;margin:0 auto;padding:16px 16px 60px}}.setup .card{{margin-bottom:14px}}.setup input,.setup textarea,.setup select{{width:100%}}
.setup .row{{display:flex;gap:10px}}.setup .row>div{{flex:1}}.setup button{{margin-top:10px}}.setup h3 span{{color:var(--acc)}}</style></head><body class="solo">
<main class="setup" id="main"><h1>Welcome to {e(c.BRAND_NAME)}</h1>
<p class="sub">Five quick steps. Everything is saved to your <code>.env</code> and applies right away. You can run this again any time from Settings.</p>
{f'<div class="warn bad" role="alert">{e(error)}</div>' if error else ''}{f'<div class="warn" role="status">{e(ok)}</div>' if ok else ''}
<form method="post" action="/setup">
<div class="card"><h3><span>1</span> Dashboard password</h3>
<p class="sub">{'A password is set. Enter a new one to change it.' if c.DASHBOARD_PASSWORD else 'No password yet: anyone who can reach this page can use the dashboard. Set one (6+ characters).'}</p>
{_secret_input('password', 'Password', 'DASHBOARD_PASSWORD')}</div>
<div class="card"><h3><span>2</span> Your business</h3>
<label class="lbl" for="biz">What do you sell? (the AI uses this to score leads and write messages)</label>
<textarea id="biz" name="my_business" rows="4" maxlength="1500" required>{e('' if c.needs_setup() else c.MY_BUSINESS)}</textarea>
<label class="lbl" for="bn">Brand name</label><input id="bn" name="brand_name" value="{e(c.BRAND_NAME)}" maxlength="40">
<label class="lbl" for="cc">Default country code (for local numbers starting with 0, e.g. 44, 92, 1)</label>
<input id="cc" name="country_code" value="{e(c.DEFAULT_COUNTRY_CODE)}" inputmode="numeric" maxlength="4"></div>
<div class="card"><h3><span>3</span> WhatsApp</h3>
<label class="lbl" for="prov">Provider</label><select id="prov" name="provider" onchange="prov()">
<option value="waha"{'' if cloud else ' selected'}>WAHA (links your normal WhatsApp, free)</option>
<option value="cloud"{' selected' if cloud else ''}>Official Cloud API (Meta)</option></select>
<div id="p-waha"><p class="sub">Status: <b>{e(wa_status)}</b>. Pair your phone in the <a href="/#whatsapp">WhatsApp tab</a> after saving.</p></div>
<div id="p-cloud">{_secret_input('cloud_token', 'Access token', 'WA_CLOUD_TOKEN')}
<label class="lbl" for="pid">Phone number ID</label><input id="pid" name="cloud_phone_id" value="{e(c.WA_CLOUD_PHONE_NUMBER_ID)}" inputmode="numeric">
{_secret_input('cloud_verify', 'Webhook verify token', 'WA_CLOUD_VERIFY_TOKEN', gen=True)}
{_secret_input('cloud_secret', 'App secret', 'WA_CLOUD_APP_SECRET')}
<label class="lbl" for="tpl">Approved template name (optional, one &#123;&#123;1&#125;&#125; variable)</label><input id="tpl" name="cloud_template" value="{e(c.WA_CLOUD_TEMPLATE)}">
<p class="sub">Webhook URL for Meta: <code>{e(base)}/webhook/cloud</code></p></div>
<button type="button" class="secondary" onclick="testConn()">Test connection</button> <span id="tres" class="sub" role="status"></span>
<p class="sub">The test only reads status from WhatsApp. It never sends a message.</p></div>
<div class="card"><h3><span>4</span> Optional extras</h3>
{_secret_input('places_key', 'Google Places API key (adds a Google lead source)', 'GOOGLE_PLACES_API_KEY')}
{_secret_input('api_key', 'CRM API key (enables the REST API)', 'API_KEY', gen=True)}
<label class="lbl" for="wh">CRM webhook URL</label><input id="wh" name="crm_webhook_url" type="url" value="{e(c.CRM_WEBHOOK_URL)}" placeholder="https://…">
{_secret_input('crm_secret', 'Webhook signing secret', 'CRM_WEBHOOK_SECRET', gen=True)}</div>
<div class="card"><h3><span>5</span> Sending</h3>
<div class="row"><div><label class="lbl" for="s1">Start</label><input id="s1" name="send_start" type="time" value="{e(hhmm(c.SEND_START))}"></div>
<div><label class="lbl" for="s2">End</label><input id="s2" name="send_end" type="time" value="{e(hhmm(c.SEND_END))}"></div></div>
<label class="lbl" for="dl">Daily send limit</label><input id="dl" name="daily_limit" type="number" min="1" max="500" value="{c.DAILY_SEND_LIMIT}">
<div class="row"><div><label class="lbl" for="g1">Gap min (seconds)</label><input id="g1" name="gap_min" type="number" min="0" max="3600" value="{c.SEND_GAP_MIN}"></div>
<div><label class="lbl" for="g2">Gap max (seconds)</label><input id="g2" name="gap_max" type="number" min="0" max="3600" value="{c.SEND_GAP_MAX}"></div></div>
<p class="sub">Hours apply to every day; fine-tune per day later in Settings. Start slow: new WhatsApp numbers get banned for bulk sending.</p></div>
<div class="card"><h3><span>6</span> Email channel (optional)</h3>
<p class="sub">Lets the agent also write to leads by email. Leave the host empty to keep email off. Use a dedicated mailbox and an app password; start with a low daily limit.</p>
<div class="row"><div><label class="lbl" for="sh">SMTP host</label><input id="sh" name="smtp_host" value="{e(c.SMTP_HOST)}" placeholder="smtp.example.com"></div>
<div><label class="lbl" for="sp">Port</label><input id="sp" name="smtp_port" type="number" min="1" max="65535" value="{c.SMTP_PORT}"></div></div>
<label class="lbl" for="st">Security</label><select id="st" name="smtp_tls">{''.join(f'<option value="{v}"{" selected" if c.SMTP_TLS == v else ""}>{l}</option>' for v, l in (("starttls", "STARTTLS (port 587)"), ("ssl", "SSL/TLS (port 465)"), ("none", "None (local test server only)")))}</select>
<label class="lbl" for="su">SMTP username</label><input id="su" name="smtp_user" value="{e(c.SMTP_USER)}" autocomplete="off">
{_secret_input('smtp_password', 'SMTP password', 'SMTP_PASSWORD')}
<label class="lbl" for="sf">From (e.g. Sam &lt;sam@example.com&gt;)</label><input id="sf" name="smtp_from" value="{e(c.SMTP_FROM)}">
<label class="lbl" for="ih">IMAP host (to read replies; optional)</label><input id="ih" name="imap_host" value="{e(c.IMAP_HOST)}" placeholder="imap.example.com">
<label class="lbl" for="iu">IMAP username (blank = same as SMTP)</label><input id="iu" name="imap_user" value="{e(c.IMAP_USER)}" autocomplete="off">
{_secret_input('imap_password', 'IMAP password (blank = same as SMTP)', 'IMAP_PASSWORD')}
<label class="lbl" for="el">Emails per day</label><input id="el" name="email_limit" type="number" min="1" max="500" value="{c.EMAIL_DAILY_LIMIT}">
<button type="button" class="secondary" onclick="testMail()">Test email login</button> <span id="eres" class="sub" role="status"></span>
<p class="sub">The test only logs in. It never sends or reads any mail. Replies are checked every few minutes; mail is never deleted.</p></div>
<button class="primary block">Save and finish</button></form></main>
<script>
function prov(){{var c=document.getElementById('prov').value=='cloud';document.getElementById('p-cloud').hidden=!c;document.getElementById('p-waha').hidden=c}}prov();
function gen(id){{var a=new Uint8Array(24);crypto.getRandomValues(a);var v=Array.from(a,function(b){{return b.toString(16).padStart(2,'0')}}).join('');
var i=document.getElementById(id);i.type='text';i.value=v}}
function testMail(){{var f=new FormData(document.querySelector('form')),r=document.getElementById('eres');r.textContent='Testing…';
fetch('/setup/test-email',{{method:'POST',body:f}}).then(function(x){{return x.json()}}).then(function(j){{r.textContent=(j.ok?'✓ ':'✗ ')+j.text}}).catch(function(){{r.textContent='✗ Test failed'}})}}
function testConn(){{var f=new FormData(document.querySelector('form')),r=document.getElementById('tres');r.textContent='Testing…';
fetch('/setup/test',{{method:'POST',body:f}}).then(function(x){{return x.json()}}).then(function(j){{r.textContent=(j.ok?'✓ ':'✗ ')+j.text}}).catch(function(){{r.textContent='✗ Test failed'}})}}
</script></body></html>"""
    return HTMLResponse(body)


@router.get("/setup", response_class=HTMLResponse)
def setup_get(request: Request):
    return _page(request)


@router.post("/setup/test")
def setup_test(provider: str = Form("waha"), cloud_token: str = Form(""), cloud_phone_id: str = Form("")):
    """Read-only connection check. Never sends a WhatsApp message."""
    if provider == "cloud":
        tok = cloud_token.strip() or config.WA_CLOUD_TOKEN
        pid = cloud_phone_id.strip() or config.WA_CLOUD_PHONE_NUMBER_ID
        if not tok or not pid:
            return JSONResponse({"ok": False, "text": "Enter the access token and phone number ID first."})
        ok, text = wa_cloud.check(tok, pid)
        return JSONResponse({"ok": ok, "text": text})
    st = whatsapp.status()
    return JSONResponse({"ok": st == "WORKING", "text": f"WAHA session status: {st}" + ("" if st == "WORKING" else " (pair your phone in the WhatsApp tab)")})


@router.post("/setup/test-email")
async def setup_test_email(request: Request):
    """Read-only: logs in to SMTP (and IMAP) with the form values (blank secrets = saved ones). Sends and reads nothing."""
    f = await request.form()
    g = lambda k, d="": str(f.get(k, "")).strip() or d
    saved = {k: getattr(config, k) for k in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "SMTP_TLS", "IMAP_HOST", "IMAP_USER", "IMAP_PASSWORD")}
    try:
        port = int(g("smtp_port", str(config.SMTP_PORT)))
    except ValueError:
        return JSONResponse({"ok": False, "text": "Port must be a number."})
    try:
        config.SMTP_HOST, config.SMTP_PORT, config.SMTP_USER = g("smtp_host"), port, g("smtp_user")
        config.SMTP_PASSWORD, config.SMTP_FROM = g("smtp_password", saved["SMTP_PASSWORD"]), g("smtp_from", g("smtp_user"))
        config.SMTP_TLS, config.IMAP_HOST, config.IMAP_USER = g("smtp_tls", "starttls"), g("imap_host"), g("imap_user")
        config.IMAP_PASSWORD = g("imap_password", saved["IMAP_PASSWORD"])
        ok, text = emailer.check_smtp()
        if ok and config.IMAP_HOST:
            ok2, t2 = emailer.check_imap()
            ok, text = ok and ok2, text + " " + t2
    finally:
        for k, v in saved.items():
            setattr(config, k, v)
    return JSONResponse({"ok": ok, "text": text})


@router.post("/setup")
async def setup_save(request: Request):
    f = await request.form()
    g = lambda k: " ".join(str(f.get(k, "")).split())
    biz, cc, provider = g("my_business"), g("country_code").lstrip("+"), g("provider")
    try:
        limit, lo = int(g("daily_limit")), int(g("gap_min") or 0)
        hi = int(g("gap_max") or lo)
    except ValueError:
        return _page(request, "Not saved: limit and gaps must be whole numbers.")
    pw = str(f.get("password", ""))
    err = None
    if not biz or len(biz) > 1500:
        err = "describe your business in 1 to 1500 characters."
    elif cc and not re.fullmatch(r"\d{1,4}", cc):
        err = "country code must be digits only, like 44."
    elif pw and len(pw) < 6:
        err = "password must be at least 6 characters."
    elif not 1 <= limit <= 500 or not 0 <= lo <= hi <= 3600:
        err = "daily limit must be 1-500 and gaps 0-3600 seconds (max not below min)."
    elif not whatsapp.valid_window(g("send_start"), g("send_end")):
        err = "sending start must be earlier than end, like 09:00 and 20:00."
    elif provider not in ("waha", "cloud"):
        err = "pick a WhatsApp provider."
    values = {"MY_BUSINESS": biz, "BRAND_NAME": g("brand_name")[:40] or "Lead Agent", "DEFAULT_COUNTRY_CODE": cc,
              "WHATSAPP_PROVIDER": provider, "WA_CLOUD_PHONE_NUMBER_ID": g("cloud_phone_id"), "WA_CLOUD_TEMPLATE": g("cloud_template"),
              "CRM_WEBHOOK_URL": g("crm_webhook_url"), "DAILY_SEND_LIMIT": limit, "MIN_SECONDS_BETWEEN_SENDS": lo, "SEND_GAP_MIN": lo,
              "SEND_GAP_MAX": hi, "SEND_START": g("send_start"), "SEND_END": g("send_end"),
              "SEND_SCHEDULE": whatsapp.format_schedule([(g("send_start"), g("send_end"))] * 7), "SETUP_DONE": "1"}
    try:
        smtp_port, elimit = int(g("smtp_port") or 587), int(g("email_limit") or 30)
    except ValueError:
        return _page(request, "Not saved: the email port and daily email limit must be whole numbers.")
    tls = g("smtp_tls") or "starttls"
    if not err and (tls not in ("starttls", "ssl", "none") or not 1 <= smtp_port <= 65535 or not 1 <= elimit <= 500):
        err = "email: port 1-65535, security one of the list, daily limit 1-500."
    elif not err and g("smtp_host") and not emailer.valid_address(emailer.parseaddr(g("smtp_from") or g("smtp_user"))[1]):
        err = "email needs a From address like sam@example.com."
    values.update({"SMTP_HOST": g("smtp_host"), "SMTP_PORT": smtp_port, "SMTP_USER": g("smtp_user"), "SMTP_FROM": g("smtp_from"), "SMTP_TLS": tls,
                   "IMAP_HOST": g("imap_host"), "IMAP_USER": g("imap_user"), "EMAIL_DAILY_LIMIT": elimit})
    for field, key in SECRET_FIELDS.items():
        v = str(f.get(field, "")).strip()
        if v:
            values[key] = v
    if not err and provider == "cloud" and not ((values.get("WA_CLOUD_TOKEN") or config.WA_CLOUD_TOKEN) and values["WA_CLOUD_PHONE_NUMBER_ID"]):
        err = "the Cloud API needs an access token and phone number ID."
    if err:
        return _page(request, "Not saved: " + err)
    config.save_settings(values)
    r = RedirectResponse("/", status_code=303)
    if pw:                                    # the login cookie is signed with the password: issue a fresh one
        r.set_cookie(auth.COOKIE, auth.make_token(), max_age=auth.DAYS * 86400, httponly=True, samesite="lax")
    return r
