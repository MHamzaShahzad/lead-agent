"""Find businesses for free from OpenStreetMap (Overpass API) or import a CSV."""
import csv
import io
import re
from urllib.parse import urljoin, urlparse

import httpx

from . import config

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


PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
PLACES_FIELDS = ("places.displayName,places.nationalPhoneNumber,places.internationalPhoneNumber,"
                 "places.websiteUri,places.formattedAddress,places.rating,places.types,nextPageToken")
PLACES_MAX = 60                                   # 3 pages of 20: keeps API cost low


class PlacesError(Exception):
    pass


def find_google(niche, city, limit=PLACES_MAX):
    """Official Google Places API (New) Text Search. Needs GOOGLE_PLACES_API_KEY. No page scraping."""
    key = config.GOOGLE_PLACES_API_KEY
    if not key:
        raise PlacesError("Google Places is not set up. Add GOOGLE_PLACES_API_KEY to .env.")
    limit = min(int(limit), PLACES_MAX)
    headers = {"X-Goog-Api-Key": key, "X-Goog-FieldMask": PLACES_FIELDS, "Content-Type": "application/json"}
    body = {"textQuery": f"{niche.strip()} in {city.strip()}", "pageSize": min(20, limit)}
    leads = []
    while len(leads) < limit:
        try:
            r = httpx.post(PLACES_URL, json=body, headers=headers, timeout=30)
        except httpx.HTTPError:
            raise PlacesError("Could not reach Google Places. Check your internet connection.") from None
        if r.status_code != 200:
            if r.status_code in (400, 401, 403):
                msg = "Google Places key was rejected or the API is not enabled for it. Check GOOGLE_PLACES_API_KEY."
            elif r.status_code == 429:
                msg = "Google Places quota exceeded. Try again later or check your billing quota."
            else:
                msg = f"Google Places error {r.status_code}."
            raise PlacesError(msg)
        data = r.json()
        for p in data.get("places", []):
            name = (p.get("displayName") or {}).get("text")
            if not name:
                continue
            types = [t.replace("_", " ") for t in (p.get("types") or []) if t not in ("point_of_interest", "establishment")]
            notes = " | ".join(x for x in (p.get("formattedAddress"),
                                           f"Google rating {p['rating']}" if p.get("rating") else None) if x)
            leads.append({
                "name": name, "niche": niche, "city": city, "source": "google_places",
                "website": p.get("websiteUri"),
                "phone": p.get("internationalPhoneNumber") or p.get("nationalPhoneNumber"),
                "email": None, "notes": notes or (types[0] if types else None),
            })
        token = data.get("nextPageToken")
        if not token:
            break
        body["pageToken"] = token
    return leads[:limit]


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
