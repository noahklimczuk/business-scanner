"""Find businesses in a radius that have no website.

Uses Places API (New) searchNearby. One call returns up to 20 businesses
*including* the website field, so there is no per-business Details call.
The field mask below is deliberately frozen: adding a field can silently
move the whole request into a pricier SKU.
"""
import math, time, json, requests
from db import connect, upsert_lead, now

ENDPOINT = "https://places.googleapis.com/v1/places:searchNearby"

# Frozen field mask. Every field here is needed to qualify or contact a lead.
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

# Rough published rate for a Nearby Search call carrying contact + rating
# fields, in USD per call. Verify against your own billing before trusting it.
COST_PER_CALL = 0.040
MAX_RESULTS = 20          # hard ceiling on searchNearby (New)
EARTH_R = 6371000.0

# Trades that genuinely convert. A business here without a website is
# leaving money on the table, which is the entire pitch.
DEFAULT_TYPES = [
    "plumber", "electrician", "roofing_contractor", "general_contractor",
    "painter", "moving_company", "hair_salon", "barber_shop", "nail_salon",
    "beauty_salon", "spa", "dentist", "veterinary_care", "physiotherapist",
    "restaurant", "cafe", "bakery", "florist", "car_repair", "car_wash",
    "locksmith", "landscaper", "pet_grooming", "storage", "gym",
]


def grid(lat, lng, radius_m, cell_m):
    """Cover a circle with overlapping cells. Returns list of (lat,lng)."""
    pts = []
    step = cell_m * 1.5          # overlap so nothing falls between cells
    dlat = (step / EARTH_R) * (180 / math.pi)
    dlng = dlat / math.cos(math.radians(lat))
    n = int(math.ceil(radius_m / step))
    for i in range(-n, n + 1):
        for j in range(-n, n + 1):
            clat = lat + i * dlat
            clng = lng + j * dlng
            if haversine(lat, lng, clat, clng) <= radius_m + cell_m:
                pts.append((clat, clng))
    return pts


def haversine(a1, o1, a2, o2):
    p1, p2 = math.radians(a1), math.radians(a2)
    dp = p2 - p1
    dl = math.radians(o2 - o1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(h))


def score(p):
    """How worth calling is this lead? 0-100.

    Reviews are the signal that matters: a business with 80 reviews and no
    website has demand and no way to capture it. That is the whole pitch.
    """
    s = 0
    rc = p.get("review_count") or 0
    s += min(45, rc // 2)                       # demand
    if (p.get("rating") or 0) >= 4.2: s += 20   # they are good at the job
    if p.get("phone"): s += 15                  # reachable
    if p.get("hours"): s += 10                  # actively maintained listing
    high_value = ("plumb", "electric", "roof", "contract", "dent",
                  "law", "hvac", "landscap", "vet")
    cat = (p.get("category") or "").lower()
    if any(k in cat for k in high_value): s += 10
    return min(100, s)


def parse(place):
    loc = place.get("location", {})
    hours = place.get("regularOpeningHours", {}).get("weekdayDescriptions")
    rec = {
        "place_id": place.get("id"),
        "name": place.get("displayName", {}).get("text", "").strip(),
        "category": place.get("primaryTypeDisplayName", {}).get("text"),
        "address": place.get("formattedAddress"),
        "phone": place.get("nationalPhoneNumber"),
        "lat": loc.get("latitude"),
        "lng": loc.get("longitude"),
        "rating": place.get("rating"),
        "review_count": place.get("userRatingCount"),
        "hours": hours,
        "website": place.get("websiteUri"),
        "status": place.get("businessStatus"),
    }
    rec["score"] = score(rec)
    return rec


def call(api_key, lat, lng, radius, types):
    body = {
        "includedTypes": types,
        "maxResultCount": MAX_RESULTS,
        "locationRestriction": {
            "circle": {"center": {"latitude": lat, "longitude": lng},
                       "radius": float(radius)}
        },
    }
    r = requests.post(ENDPOINT, json=body, timeout=30, headers={
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": FIELD_MASK,
    })
    if r.status_code == 429:
        time.sleep(2)
        return call(api_key, lat, lng, radius, types)
    if not r.ok:
        raise RuntimeError(f"Places API returned {r.status_code}: {r.text[:300]}")
    return r.json().get("places", [])


def scan(api_key, lat, lng, radius_km=10, cell_m=900, types=None,
         dry_run=False, verbose=True):
    types = types or DEFAULT_TYPES
    radius_m = radius_km * 1000
    cells = grid(lat, lng, radius_m, cell_m)

    # Places caps includedTypes per request, so types are batched.
    batches = [types[i:i + 8] for i in range(0, len(types), 8)]
    total_calls = len(cells) * len(batches)
    est = total_calls * COST_PER_CALL

    print(f"  {len(cells)} cells x {len(batches)} type batches = {total_calls} calls")
    print(f"  estimated spend: ${est:,.2f} USD")
    if dry_run:
        print("  dry run, nothing sent")
        return
    if est > 25 and input("  continue? [y/N] ").strip().lower() != "y":
        print("  cancelled")
        return

    con = connect()
    seen, added, done = 0, 0, 0
    for clat, clng in cells:
        for batch in batches:
            done += 1
            try:
                places = call(api_key, clat, clng, cell_m, batch)
            except RuntimeError as e:
                print(f"  ! {e}")
                continue
            for raw in places:
                p = parse(raw)
                seen += 1
                if p["website"]:
                    continue                      # already has a site
                if p.get("status") not in (None, "OPERATIONAL"):
                    continue                      # closed or temporarily shut
                if not p["name"]:
                    continue
                if upsert_lead(con, p):
                    added += 1
            if verbose and done % 10 == 0:
                print(f"  {done}/{total_calls} calls · {added} leads", end="\r")
        con.commit()

    con.execute(
        """INSERT INTO scans (at,centre,radius_m,types,calls,seen,new_leads,est_cost)
           VALUES (?,?,?,?,?,?,?,?)""",
        (now(), f"{lat},{lng}", radius_m, json.dumps(types), total_calls,
         seen, added, est))
    con.commit()
    print(f"\n  done. {seen} businesses seen, {added} new leads without a website.")
    print(f"  spent roughly ${est:,.2f}")
    con.close()
