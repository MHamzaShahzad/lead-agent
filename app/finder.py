"""Find businesses for free from OpenStreetMap (Overpass API) or import a CSV."""
import csv
import io
import re
from urllib.parse import urljoin, urlparse

import httpx

OVERPASS = "https://overpass-api.de/api/interpreter"
OVERPASS_MIRRORS = (OVERPASS, "https://overpass.kumi.systems/api/interpreter")


class SearchBusy(Exception):
    pass


def _overpass(q):
    """Run an Overpass query, trying a mirror if the main server is busy. Raises SearchBusy with a plain message."""
    for url in OVERPASS_MIRRORS:
        try:
            r = httpx.post(url, data={"data": q}, timeout=90, headers={"User-Agent": "lead-gen-agent/1.0"})
        except httpx.HTTPError:
            continue
        if r.status_code != 200:
            continue                              # 429/502/504: server busy, try the next one
        data = r.json()
        if "runtime error" in (data.get("remark") or "") and not data.get("elements"):
            continue                              # query timed out on a busy server: not a real "0 found"
        return data
    raise SearchBusy("OpenStreetMap servers are busy right now. Try again in a few minutes, or import a CSV.")

# Friendly niche names -> OpenStreetMap tags. You can also pass a raw tag like "shop=bakery".
NICHES = {
    "restaurant": "amenity=restaurant", "cafe": "amenity=cafe", "dentist": "amenity=dentist",
    "clinic": "amenity=clinic", "pharmacy": "amenity=pharmacy", "gym": "leisure=fitness_centre",
    "salon": "shop=hairdresser", "beauty": "shop=beauty", "bakery": "shop=bakery",
    "clothes": "shop=clothes", "real estate": "office=estate_agent", "school": "amenity=school",
    "hotel": "tourism=hotel", "car repair": "shop=car_repair", "furniture": "shop=furniture",
}


def find_osm(niche, city, limit=50):
    tag = NICHES.get(niche.lower().strip(), niche.strip())
    if "=" not in tag:
        raise ValueError(f"Unknown niche '{niche}'. Use one of {', '.join(NICHES)} or a tag like shop=bakery")
    k, v = tag.split("=", 1)
    q = (f'[out:json][timeout:60];area["name"="{city}"]["boundary"="administrative"]->.a;'
         f'nwr["{k}"="{v}"]["name"](area.a);out center {int(limit)};')
    data = _overpass(q)
    leads = []
    for el in data.get("elements", []):
        t = el.get("tags", {})
        leads.append({
            "name": t.get("name"), "niche": niche, "city": city, "source": "openstreetmap",
            "website": t.get("website") or t.get("contact:website"),
            "phone": t.get("phone") or t.get("contact:phone") or t.get("contact:whatsapp"),
            "email": t.get("email") or t.get("contact:email"),
            "notes": t.get("addr:street"),
        })
    return leads


def parse_csv(text):
    """CSV columns (any case): name, website, niche, phone, email, city, notes."""
    reader = csv.DictReader(io.StringIO(text))
    alias = {"business": "name", "business name": "name", "category": "niche", "mobile": "phone",
             "whatsapp": "phone", "url": "website", "location": "city"}
    rows = []
    for row in reader:
        clean = {}
        for k, v in row.items():
            key = (k or "").strip().lower()
            clean[alias.get(key, key)] = v
        clean.setdefault("source", "csv")
        rows.append(clean)
    return rows


_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_BAD_EMAIL_END = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js")
_TEL = re.compile(r'href=["\']tel:([^"\']+)', re.I)
_PHONE = re.compile(r"(?:\+|00)\d[\d\s\-().]{8,16}\d|\b0\d{2,3}[\s-]?\d{7}\b")


def _host(url):
    h = (urlparse(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def extract_contacts(html):
    """Best email and phone found in a page: mailto:/tel: links first, then plain text."""
    emails = [m for m in re.findall(r"mailto:([^\"'?\s>]+)", html, re.I)] + _EMAIL.findall(html)
    emails = [x.strip().lower() for x in emails if not x.lower().endswith(_BAD_EMAIL_END)]
    phones = _TEL.findall(html) + _PHONE.findall(re.sub(r"<[^>]+>", " ", html))
    phones = [p.strip() for p in phones if 10 <= len(re.sub(r"\D", "", p)) <= 14]
    return (emails[0] if emails else None), (phones[0] if phones else None)


def find_contacts_on_website(website):
    """Fetch the lead's own site (home page + /contact, same host only, 8s timeout). Returns (email, phone)."""
    base = website if "://" in website else "https://" + website
    host = _host(base)
    if not host:
        return None, None
    email = phone = None
    for url in (base, urljoin(base, "/contact")):
        try:
            r = httpx.get(url, timeout=8, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
        except Exception:
            continue
        if r.status_code >= 400 or _host(str(r.url)) != host:
            continue                                   # redirected to another site: ignore it
        e, p = extract_contacts(r.text[:500_000])
        email, phone = email or e, phone or p
        if email and phone:
            break
    return email, phone
