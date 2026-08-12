"""Find businesses in a radius that have no website.

Places API (New) `searchNearby`. One call returns up to 20 businesses
*including* `websiteUri`, which is the whole cost story: we never make a
per-business Place Details call, so qualifying a business costs about a fifth
of a cent instead of two cents.

Presentation lives in cli.py. This module talks to Google, parses, filters and
scores, and reports progress through a callback.
"""
from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional, Sequence
from urllib.parse import urlparse

import requests

import db

NEARBY_ENDPOINT = "https://places.googleapis.com/v1/places:searchNearby"
TEXT_ENDPOINT = "https://places.googleapis.com/v1/places:searchText"

# ---------------------------------------------------------------------------
# FROZEN FIELD MASK — do not edit without pricing the change first.
# ---------------------------------------------------------------------------
# Places (New) bills a call at the tier of the most expensive field requested.
# Every field below is Essentials or Pro except four — nationalPhoneNumber,
# websiteUri, rating, userRatingCount, regularOpeningHours — which put this
# call in the Enterprise tier. That is a deliberate trade: websiteUri is what
# qualifies the lead in the search response, and buying it here is far cheaper
# than a Details call per business. Adding one more Enterprise-or-worse field
# is free; adding anything from the Atmosphere tier (reviews, etc.) roughly
# doubles the bill for the entire scan. Never build this mask dynamically.
FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.nationalPhoneNumber",
    "places.websiteUri",
    "places.rating",
    "places.userRatingCount",
    "places.location",
    "places.primaryTypeDisplayName",
    "places.regularOpeningHours",
    "places.businessStatus",
])

# Geocoding the scan centre. Deliberately a smaller mask than the one above —
# one cheap call per scan, and it is cached for 30 days in `geocache`.
GEOCODE_FIELD_MASK = "places.id,places.location,places.formattedAddress"

# USD per call at the Enterprise tier for the mask above. Published rates move;
# check your own billing after the first scan and correct this constant. The
# estimate the CLI prints is only as good as this number.
COST_PER_CALL = 0.040
COST_PER_GEOCODE = 0.032

MAX_RESULTS = 20        # hard ceiling on searchNearby (New); there is no paging
EARTH_R = 6371000.0

# Square lattice of circular cells. Two cells whose centres are `s` apart leave
# a gap at the diagonal unless s <= r * sqrt(2). 1.4 is just inside that; the
# obvious-looking 1.5 leaves diamond-shaped holes where leads disappear.
CELL_SPACING = 1.4

# The API accepts far more than eight included types per call, but a cell in a
# commercial strip will blow past the 20-result cap and silently truncate. Small
# batches trade money for recall — with 24 types this triples the call count and
# is the single biggest cost lever in the tool.
TYPE_BATCH_SIZE = 8

RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES = 4
TIMEOUT = 30

# Trades that genuinely convert. A business here without a website is leaving
# money on the table, which is the entire pitch.
DEFAULT_TYPES = [
    "plumber", "electrician", "roofing_contractor", "general_contractor",
    "painter", "moving_company", "hair_salon", "barber_shop", "nail_salon",
    "beauty_salon", "spa", "dentist", "veterinary_care", "physiotherapist",
    "restaurant", "cafe", "bakery", "florist", "car_repair", "car_wash",
    "locksmith", "landscaper", "pet_grooming", "storage", "gym",
]

# A "website" that is really a social page. These are the near-miss segment:
# they already decided they need a web presence and are currently renting one.
SOCIAL_HOSTS = (
    "facebook.com", "fb.com", "fb.me", "instagram.com", "linktr.ee",
    "linktree.com", "beacons.ai", "twitter.com", "x.com", "tiktok.com",
    "yelp.ca", "yelp.com", "yellowpages.ca", "youtube.com",
)

# Google's own site builder shut down in 2024 and these links now go nowhere,
# so the owner believes they have a website and does not. Best segment there is.
DEFUNCT_HOSTS = ("business.site", "negocio.site", "empresa.site")

# A Tim Hortons without a website is not a lead — head office owns that
# decision. Match is on a normalised name with word boundaries.
FRANCHISES = [
    "tim hortons", "mcdonalds", "starbucks", "subway", "wendys", "burger king",
    "kfc", "popeyes", "harveys", "a w", "dairy queen", "mary browns",
    "pizza pizza", "pizza hut", "dominos", "papa johns", "little caesars",
    "pizza nova", "panago", "boston pizza", "swiss chalet", "montanas",
    "kelseys", "east side marios", "the keg", "jack astors", "moxies",
    "milestones", "chipotle", "taco bell", "mucho burrito", "osmows",
    "second cup", "country style", "coffee culture", "booster juice",
    "jugo juice", "freshii", "quiznos", "mr sub", "extreme pita", "pita pit",
    "bulk barn", "m m food market", "cobs bread", "menchies", "yogen fruz",
    "baskin robbins", "dq grill", "circle k", "7 eleven", "macs convenience",
    "petro canada", "esso", "shell", "husky", "ultramar",
    "great clips", "first choice haircutters", "magicuts", "supercuts",
    "sport clips", "tommy guns", "fantastic sams",
    "midas", "mr lube", "jiffy lube", "speedy auto", "active green ross",
    "mister transmission", "minute muffler", "canadian tire", "napa auto",
    "u haul", "budget rent", "enterprise rent", "hertz", "avis",
    "goodlife fitness", "planet fitness", "anytime fitness", "orangetheory",
    "f45 training", "snap fitness", "curves", "world gym",
    "molly maid", "merry maids", "jani king", "servicemaster", "chemdry",
    "mr rooter", "roto rooter", "drain rescue", "mr handyman",
    "1 800 got junk", "junk king", "college pro", "student works",
    "weed man", "nutri lawn", "greenlawn", "orkin", "terminix", "abell pest",
    "shoppers drug mart", "rexall", "gnc", "pet valu", "petsmart", "pet smart",
]

HIGH_VALUE_CATEGORY = (
    "plumb", "electric", "roof", "contract", "renovat", "hvac", "heating",
    "landscap", "paving", "fence", "window", "dent", "law", "vet", "chiro",
    "physio", "garage", "excavat", "concrete",
)


class PlacesError(RuntimeError):
    """Written for the operator at 9pm: what broke and what to do next."""


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------
def haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(h))


def grid(lat: float, lng: float, radius_m: float, cell_m: float) -> list[tuple[float, float]]:
    """Cover a circle of `radius_m` with overlapping cells of `cell_m`.

    Nearby Search caps at 20 results with no pagination, so one call can never
    enumerate a town — the radius has to be tiled.
    """
    if cell_m <= 0:
        raise ValueError("cell size must be positive")
    step = cell_m * CELL_SPACING
    dlat = (step / EARTH_R) * (180 / math.pi)
    dlng = dlat / max(0.05, math.cos(math.radians(lat)))
    n = int(math.ceil(radius_m / step))
    pts: list[tuple[float, float]] = []
    for i in range(-n, n + 1):
        for j in range(-n, n + 1):
            clat = lat + i * dlat
            clng = lng + j * dlng
            # Keep a cell if any part of it can reach inside the radius.
            if haversine(lat, lng, clat, clng) <= radius_m + cell_m:
                pts.append((round(clat, 6), round(clng, 6)))
    return pts


# ---------------------------------------------------------------------------
# Classification and scoring
# ---------------------------------------------------------------------------
def normalise_name(name: str | None) -> str:
    # Apostrophes close up rather than split, so "McDonald's" matches "mcdonalds"
    # in the blocklist and "Joe's" stays one word for chain detection.
    cleaned = re.sub(r"['’]", "", (name or "").lower())
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", cleaned)).strip()


def classify_website(url: str | None) -> str:
    """none | social | defunct | real — see db.WEBSITE_KINDS."""
    if not url or not url.strip():
        return "none"
    raw = url.strip()
    if "//" not in raw:                      # bare "example.com/foo"
        raw = "//" + raw
    host = (urlparse(raw).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return "none"
    if any(host == h or host.endswith("." + h) for h in DEFUNCT_HOSTS):
        return "defunct"
    if any(host == h or host.endswith("." + h) for h in SOCIAL_HOSTS):
        return "social"
    return "real"


def is_franchise(name: str | None) -> bool:
    n = normalise_name(name)
    if not n:
        return False
    return any(re.search(rf"(^|\s){re.escape(f)}(\s|$)", n) for f in FRANCHISES)


def chain_names(records: Iterable[dict[str, Any]], min_locations: int = 3) -> set[str]:
    """Names appearing at several distinct addresses in one scan are chains.

    Catches the regional chains no blocklist will ever have — three locations of
    the same name in one town is head-office marketing, not a prospect.
    """
    seen: dict[str, set[str]] = {}
    for r in records:
        n = normalise_name(r.get("name"))
        if n:
            seen.setdefault(n, set()).add(r["place_id"])
    return {n for n, ids in seen.items() if len(ids) >= min_locations}


def score(rec: dict[str, Any]) -> int:
    """How worth calling is this lead? 0-100.

    Crude on purpose — Phase 2.5 replaces it with the propensity model. Reviews
    dominate because they are the signal that matters: a business with 80
    reviews and no website has demand and nowhere to put it.
    """
    s = 0
    rc = rec.get("review_count") or 0
    rating = rec.get("rating") or 0.0

    s += min(45, rc // 2)                                  # proven demand
    if rating >= 4.2 and rc >= 5:                          # good at the job
        s += 20
    elif 0 < rating < 3.5 and rc >= 10:                    # a site amplifies this
        s -= 15
    if rec.get("phone"):
        s += 15
    if rec.get("hours"):                                   # tended listing
        s += 10
    cat = (rec.get("category") or "").lower()
    if any(k in cat for k in HIGH_VALUE_CATEGORY):
        s += 10

    kind = rec.get("website_kind", "none")
    if kind == "social":                                   # near-miss: easier sell
        s += 8
    elif kind == "defunct":
        s += 5

    # Phase 2 enrichment. All absent at scan time, so scanning is unchanged.
    if rec.get("phone") and rec.get("phone_valid") is not None and not rec["phone_valid"]:
        s -= 10                                            # the listed number does not dial
    if rec.get("phone_type") == "toll_free":
        s -= 5                                             # 1-800 is a chain or an agency front
    if kind == "none" and (rec.get("facebook_url") or rec.get("instagram_url")):
        s += 12                                            # near-miss, found by search
    if rec.get("is_chain"):
        s -= 40                                            # head office owns this decision

    return max(0, min(100, s))


def parse(place: dict[str, Any]) -> dict[str, Any]:
    loc = place.get("location") or {}
    rec = {
        "place_id": place.get("id"),
        "name": (place.get("displayName") or {}).get("text", "").strip(),
        "category": (place.get("primaryTypeDisplayName") or {}).get("text"),
        "address": place.get("formattedAddress"),
        "phone": place.get("nationalPhoneNumber"),
        "lat": loc.get("latitude"),
        "lng": loc.get("longitude"),
        "rating": place.get("rating"),
        "review_count": place.get("userRatingCount"),
        "hours": (place.get("regularOpeningHours") or {}).get("weekdayDescriptions"),
        "website": place.get("websiteUri"),
        "status": place.get("businessStatus"),
    }
    rec["website_kind"] = classify_website(rec["website"])
    rec["score"] = score(rec)
    return rec


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def _post(url: str, api_key: str, body: dict[str, Any], mask: str) -> dict[str, Any]:
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": mask,
    }
    delay = 2.0
    last = ""
    for attempt in range(MAX_RETRIES + 1):
        try:
            r = requests.post(url, json=body, headers=headers, timeout=TIMEOUT)
        except requests.Timeout:
            last = f"timed out after {TIMEOUT}s"
        except requests.RequestException as exc:
            last = f"network error: {exc}"
        else:
            if r.ok:
                return r.json()
            if r.status_code in RETRY_STATUS:
                last = f"HTTP {r.status_code}"
                retry_after = r.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    delay = max(delay, float(retry_after))
            else:
                raise PlacesError(_explain(r.status_code, r.text))
        if attempt < MAX_RETRIES:
            time.sleep(delay)
            delay *= 2
    raise PlacesError(
        f"Google Places kept failing ({last}) after {MAX_RETRIES} retries.\n"
        "Check https://status.cloud.google.com and your daily quota cap, then re-run "
        "the scan — leads already found are saved."
    )


def _explain(status: int, text: str) -> str:
    snippet = text[:300].replace("\n", " ")
    if status == 400:
        return ("Google Places rejected the request (400). Almost always the field mask "
                f"or an unknown place type.\n{snippet}")
    if status in (401, 403):
        return ("Google Places refused the key (403). Check, in order: Places API (New) "
                "is enabled on the project, the key's API restriction includes it, and "
                "the key's IP restriction matches the machine you are on right now "
                f"(it will not, if you are on a different network).\n{snippet}")
    if status == 404:
        return f"Google Places endpoint not found (404) — wrong URL in the code.\n{snippet}"
    return f"Google Places returned {status}.\n{snippet}"


def geocode(api_key: str, address: str, region_code: str = "CA",
            con: Optional[Any] = None) -> tuple[float, float, str]:
    """Text address -> (lat, lng, formatted). Cached for 30 days."""
    if con is not None:
        hit = db.geocode_cached(con, address)
        if hit:
            return hit["lat"], hit["lng"], hit["formatted"] or address

    body: dict[str, Any] = {"textQuery": address}
    if region_code:
        body["regionCode"] = region_code
    data = _post(TEXT_ENDPOINT, api_key, body, GEOCODE_FIELD_MASK)
    places = data.get("places") or []
    if not places:
        raise PlacesError(
            f"Google could not find '{address}'.\n"
            "Try a more specific address, or pass --lat/--lng directly."
        )
    loc = places[0].get("location") or {}
    lat, lng = loc.get("latitude"), loc.get("longitude")
    if lat is None or lng is None:
        raise PlacesError(f"Google returned no coordinates for '{address}'.")
    formatted = places[0].get("formattedAddress") or address
    if con is not None:
        db.geocode_store(con, address, lat, lng, formatted)
        con.commit()
    return lat, lng, formatted


def search_nearby(api_key: str, lat: float, lng: float, radius_m: float,
                  types: Sequence[str]) -> list[dict[str, Any]]:
    body = {
        "includedTypes": list(types),
        "maxResultCount": MAX_RESULTS,
        # Distance ranking makes truncation predictable: when a cell returns the
        # full 20, we know exactly how far out it covered and can say so, rather
        # than getting 20 popular places scattered anywhere in the circle.
        "rankPreference": "DISTANCE",
        "locationRestriction": {
            "circle": {"center": {"latitude": lat, "longitude": lng},
                       "radius": float(radius_m)}
        },
    }
    return _post(NEARBY_ENDPOINT, api_key, body, FIELD_MASK).get("places", [])


# ---------------------------------------------------------------------------
# Planning and running a scan
# ---------------------------------------------------------------------------
@dataclass
class ScanPlan:
    lat: float
    lng: float
    radius_m: float
    cell_m: float
    types: list[str]
    cells: list[tuple[float, float]]
    batches: list[list[str]]
    label: str = ""
    geocoded: bool = True      # False when the plan used an assumed latitude

    @property
    def calls(self) -> int:
        return len(self.cells) * len(self.batches)

    @property
    def est_cost(self) -> float:
        return self.calls * COST_PER_CALL


@dataclass
class ScanResult:
    calls_made: int = 0
    calls_failed: int = 0
    seen: int = 0                 # place rows returned, including duplicates
    unique: int = 0
    new_leads: int = 0
    refreshed: int = 0
    near_miss: int = 0
    market_rows: int = 0
    franchises_dropped: int = 0
    closed_dropped: int = 0
    saturated_cells: int = 0
    min_coverage_m: Optional[float] = None
    errors: list[str] = field(default_factory=list)
    aborted: str = ""             # why we stopped early, empty if we finished

    @property
    def actual_cost(self) -> float:
        # Failed calls still bill in the general case, so count every attempt.
        return (self.calls_made + self.calls_failed) * COST_PER_CALL


def plan(lat: float, lng: float, radius_km: float, cell_m: float,
         types: Optional[Sequence[str]] = None, label: str = "") -> ScanPlan:
    chosen = list(types or DEFAULT_TYPES)
    radius_m = radius_km * 1000
    cells = grid(lat, lng, radius_m, cell_m)
    batches = [chosen[i:i + TYPE_BATCH_SIZE]
               for i in range(0, len(chosen), TYPE_BATCH_SIZE)]
    return ScanPlan(lat=lat, lng=lng, radius_m=radius_m, cell_m=cell_m,
                    types=chosen, cells=cells, batches=batches, label=label)


ProgressFn = Callable[[int, int, int], None]


def run(api_key: str, con: Any, sp: ScanPlan,
        progress: Optional[ProgressFn] = None) -> ScanResult:
    """Execute a planned scan. Results are written once, at the end.

    Ctrl-C stops calling Google but still saves everything already paid for.
    """
    res = ScanResult()
    records: dict[str, dict[str, Any]] = {}
    done = 0
    consecutive_failures = 0

    # Whatever went wrong, we have already paid for the calls that succeeded —
    # break out and save them rather than losing the scan.
    try:
        for clat, clng in sp.cells:
            for batch in sp.batches:
                done += 1
                try:
                    places = search_nearby(api_key, clat, clng, sp.cell_m, batch)
                    res.calls_made += 1
                    consecutive_failures = 0
                except PlacesError as exc:
                    res.calls_failed += 1
                    consecutive_failures += 1
                    res.errors.append(str(exc).splitlines()[0])
                    if consecutive_failures >= 5:
                        raise PlacesError(
                            "Five calls in a row failed — stopping before this gets "
                            f"expensive.\n{exc}"
                        ) from exc
                    continue

                if len(places) >= MAX_RESULTS:
                    res.saturated_cells += 1
                    far = max((haversine(clat, clng, (p.get("location") or {}).get("latitude", clat),
                                         (p.get("location") or {}).get("longitude", clng))
                               for p in places), default=sp.cell_m)
                    res.min_coverage_m = far if res.min_coverage_m is None \
                        else min(res.min_coverage_m, far)

                for raw in places:
                    res.seen += 1
                    rec = parse(raw)
                    if not rec["place_id"] or not rec["name"]:
                        continue
                    records[rec["place_id"]] = rec

                if progress:
                    progress(done, sp.calls, len(records))
    except KeyboardInterrupt:
        res.aborted = f"stopped by you after {done} of {sp.calls} calls"
    except PlacesError as exc:
        res.aborted = str(exc)

    res.unique = len(records)
    chains = chain_names(records.values())

    for rec in records.values():
        if rec.get("status") not in (None, "OPERATIONAL"):
            res.closed_dropped += 1
            continue

        # Everything operational goes into `market`, websites included. The
        # competitive-density number in Phase 2.5 is built from this, and the
        # data is already paid for.
        db.upsert_market(con, rec)
        res.market_rows += 1

        if rec["website_kind"] == "real":
            continue
        if is_franchise(rec["name"]) or normalise_name(rec["name"]) in chains:
            res.franchises_dropped += 1
            continue

        if db.upsert_lead(con, rec):
            res.new_leads += 1
        else:
            res.refreshed += 1
        if rec["website_kind"] in ("social", "defunct"):
            res.near_miss += 1

    db.record_scan(
        con, centre=f"{sp.lat},{sp.lng}", radius_m=int(sp.radius_m),
        cell_m=int(sp.cell_m), types=sp.types, calls_planned=sp.calls,
        calls_made=res.calls_made, calls_failed=res.calls_failed,
        seen=res.seen, new_leads=res.new_leads, est_cost=sp.est_cost,
        actual_cost=res.actual_cost, saturated=res.saturated_cells,
    )
    con.commit()
    return res
