"""Enrichment: everything we can learn about a lead without paying Google again.

Four passes, in increasing order of how much they can hurt if wrong:

  phones      offline, exact. E.164 plus whether the number on the listing
              actually dials.
  socials     one web search per lead. Finding a Facebook page is worth real
              points, so a wrong match is worth real negative points — the
              matcher is deliberately hard to satisfy and abstains often.
  chains      offline. Same name at three separated locations is head office.
  duplicates  offline. Same business listed twice under two place_ids.

Nothing here calls a paid API. Nothing here sends anything to a lead.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional
from urllib.parse import urlparse

import phonenumbers

import db
import search
from prospect import haversine, normalise_name, score

# Words that identify nobody. Stripped before matching a business to a page.
GENERIC_TOKENS = {
    "the", "and", "of", "for", "inc", "ltd", "llc", "corp", "corporation",
    "company", "limited", "group", "holdings", "enterprises", "enterprise",
    "co", "son", "sons", "bros", "brothers", "official", "page", "home",
}

# Trade words are real signal for scoring but useless for telling two
# businesses apart — "Newmarket Plumbing" and "Aurora Plumbing" share one.
TRADE_TOKENS = {
    "plumbing", "plumber", "plumbers", "electric", "electrical", "electrician",
    "electricians", "roofing", "roofer", "roofers", "hvac", "heating",
    "cooling", "air", "contracting", "contractor", "contractors",
    "construction", "renovation", "renovations", "landscaping", "landscape",
    "lawn", "painting", "painter", "painters", "salon", "spa", "nails", "nail",
    "hair", "barber", "barbershop", "beauty", "bakery", "cafe", "coffee",
    "restaurant", "pizza", "grill", "kitchen", "auto", "automotive", "garage",
    "repair", "repairs", "service", "services", "cleaning", "cleaners",
    "dental", "dentistry", "clinic", "fitness", "gym", "florist", "flowers",
    "pet", "pets", "grooming", "moving", "movers", "storage", "locksmith",
    "towing", "tow", "veterinary", "physio", "physiotherapy", "massage",
}

FACEBOOK_HOSTS = {"facebook.com", "fb.com", "fb.me"}
INSTAGRAM_HOSTS = {"instagram.com"}

# Paths that are Facebook itself, not somebody's page.
FB_NON_PAGE = {
    "login", "logout", "sharer", "share", "share.php", "groups", "group",
    "events", "event", "marketplace", "watch", "help", "policies", "policy",
    "privacy", "terms", "business", "ads", "gaming", "messages", "photo",
    "photos", "media", "profile.php", "people", "public", "story.php",
    "permalink.php", "reel", "reels", "search", "hashtag", "home.php",
    "dialog", "notes", "video", "settings", "recover", "legal", "careers",
}
IG_NON_PAGE = {
    "p", "reel", "reels", "explore", "accounts", "stories", "tv", "direct",
    "about", "legal", "developer", "privacy", "terms", "challenge",
}

# How much of a business name has to appear on a page before we believe it.
SOCIAL_THRESHOLD = 0.6

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Addresses that appear on pages but belong to nobody you would write to.
_EMAIL_JUNK = ("example.com", "sentry.io", "wixpress.com", "domain.com",
               "email.com", "yourdomain", "sentry-next", "wordpress.com")

PHONE_TYPES = {
    phonenumbers.PhoneNumberType.MOBILE: "mobile",
    phonenumbers.PhoneNumberType.FIXED_LINE: "fixed_line",
    phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE: "fixed_or_mobile",
    phonenumbers.PhoneNumberType.TOLL_FREE: "toll_free",
    phonenumbers.PhoneNumberType.VOIP: "voip",
    phonenumbers.PhoneNumberType.PREMIUM_RATE: "premium_rate",
    phonenumbers.PhoneNumberType.SHARED_COST: "shared_cost",
}


# ---------------------------------------------------------------------------
# Phones
# ---------------------------------------------------------------------------
@dataclass
class PhoneInfo:
    e164: Optional[str] = None
    valid: bool = False
    kind: str = "unknown"


def normalise_phone(raw: str | None, region: str = "CA") -> PhoneInfo:
    """Listing phone -> E.164. Invalid is a finding, not an error.

    In Canada mobile and landline share the same numbering plan, so `kind` is
    almost always "fixed_or_mobile" — do not read anything into that. Toll-free
    and VOIP are the two that actually tell you something.
    """
    if not raw or not raw.strip():
        return PhoneInfo()
    try:
        parsed = phonenumbers.parse(raw, region)
    except phonenumbers.NumberParseException:
        return PhoneInfo()
    valid = phonenumbers.is_valid_number(parsed)
    return PhoneInfo(
        e164=phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164),
        valid=valid,
        kind=PHONE_TYPES.get(phonenumbers.number_type(parsed), "unknown"),
    )


# ---------------------------------------------------------------------------
# Social matching
# ---------------------------------------------------------------------------
def tokens_of(name: str | None, city: str = "") -> set[str]:
    city_tokens = set(normalise_name(city).split())
    return {t for t in normalise_name(name).split()
            if len(t) >= 2 and t not in GENERIC_TOKENS and t not in city_tokens}


def _host_of(url: str) -> str:
    """Bare host, with the mobile and country prefixes Facebook hands out
    (m., web., ca.) folded away so one page has one identity."""
    parsed = urlparse(url if "//" in url else "//" + url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if host.count(".") > 1:
        host = re.sub(r"^(m|web|business|touch|[a-z]{2})\.", "", host)
    return host


def social_handle(url: str) -> Optional[tuple[str, str]]:
    """(network, handle) for a page URL, or None if it is not one."""
    parsed = urlparse(url if "//" in url else "//" + url)
    host = _host_of(url)
    parts = [p for p in (parsed.path or "").split("/") if p]
    if not parts:
        return None

    if host in FACEBOOK_HOSTS:
        first = parts[0].lower()
        if first in FB_NON_PAGE:
            return None
        # /pages/Joes-Plumbing/123456 and /p/Joes-Plumbing-123456
        if first in ("pages", "p") and len(parts) >= 2:
            return "facebook", parts[1]
        return "facebook", parts[0]

    if host in INSTAGRAM_HOSTS:
        if parts[0].lower() in IG_NON_PAGE:
            return None
        return "instagram", parts[0]

    return None


def clean_social_url(url: str) -> str:
    parsed = urlparse(url if "//" in url else "https://" + url)
    host = _host_of(url)
    if host in FACEBOOK_HOSTS:
        host = "www.facebook.com"
    elif host in INSTAGRAM_HOSTS:
        host = "www.instagram.com"
    return f"https://{host}{(parsed.path or '/').rstrip('/')}"


def social_confidence(name: str | None, url: str, text: str = "",
                      city: str = "") -> float:
    """0-1: how sure are we this page belongs to this business?

    Returns 0 for anything short of a real match. A false positive here adds
    points to the wrong lead and puts a stranger's Facebook page in front of an
    owner during a pitch, so this abstains rather than guesses.
    """
    handle = social_handle(url)
    if not handle:
        return 0.0
    wanted = tokens_of(name, city)
    if not wanted:
        return 0.0

    slug = re.sub(r"[._\-]+", " ", handle[1])
    hay = f"{normalise_name(slug)} {normalise_name(text)}"
    hay_tokens = set(hay.split())
    hay_compact = hay.replace(" ", "")

    # Handles are usually run together ("joesplumbingnewmarket"), so a token
    # also counts if it appears inside the compacted text — but only if it is
    # long enough that the match cannot be an accident.
    matched = {t for t in wanted
               if t in hay_tokens or (len(t) >= 4 and t in hay_compact)}
    if not matched:
        return 0.0

    distinctive = wanted - TRADE_TOKENS
    if distinctive and not (distinctive & matched):
        # Matched only the trade words: this is some other plumber.
        return 0.0
    if not distinctive:
        # The name is nothing but trade words ("Newmarket Plumbing"). Only a
        # whole-name appearance is good enough.
        compact_name = normalise_name(name).replace(" ", "")
        return len(matched) / len(wanted) if compact_name in hay_compact else 0.0

    return len(matched) / len(wanted)


def pick_socials(hits: Iterable[search.SearchHit], name: str | None, city: str = "",
                 threshold: float = SOCIAL_THRESHOLD) -> dict[str, tuple[str, float]]:
    """Best Facebook and Instagram page among the search results, if any."""
    best: dict[str, tuple[str, float]] = {}
    for hit in hits:
        handle = social_handle(hit.url)
        if not handle:
            continue
        network = handle[0]
        conf = social_confidence(name, hit.url, hit.text, city)
        if conf >= threshold and conf > best.get(network, ("", 0.0))[1]:
            best[network] = (clean_social_url(hit.url), conf)
    return best


def emails_in(text: str) -> list[str]:
    out = []
    for candidate in _EMAIL.findall(text or ""):
        low = candidate.lower()
        if not any(junk in low for junk in _EMAIL_JUNK) and low not in out:
            out.append(low)
    return out


# ---------------------------------------------------------------------------
# Chains
# ---------------------------------------------------------------------------
def _distinct_sites(points: list[tuple[float, float]], min_separation_m: float) -> int:
    """Count locations that are genuinely apart, so one shop listed twice in
    overlapping cells does not read as two branches."""
    anchors: list[tuple[float, float]] = []
    for lat, lng in points:
        if lat is None or lng is None:
            continue
        if all(haversine(lat, lng, a, b) > min_separation_m for a, b in anchors):
            anchors.append((lat, lng))
    return len(anchors)


def chain_groups(rows: Iterable[dict[str, Any]], min_locations: int = 3,
                 min_separation_m: float = 200.0) -> dict[str, list[str]]:
    """Normalised name -> place_ids, for names that operate several locations."""
    by_name: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = normalise_name(row.get("name"))
        if key:
            by_name.setdefault(key, []).append(row)

    chains: dict[str, list[str]] = {}
    for key, group in by_name.items():
        if len(group) < min_locations:
            continue
        points = [(r.get("lat"), r.get("lng")) for r in group]
        if _distinct_sites(points, min_separation_m) >= min_locations:
            chains[key] = [r["place_id"] for r in group]
    return chains


# ---------------------------------------------------------------------------
# Duplicates
# ---------------------------------------------------------------------------
def _canonical(group: list[dict[str, Any]]) -> dict[str, Any]:
    # Most reviews wins; ties go to the listing we found first.
    return max(group, key=lambda r: ((r.get("review_count") or 0),
                                     r.get("found_at") or ""))


def find_duplicates(rows: Iterable[dict[str, Any]], max_distance_m: float = 150.0,
                    skip_names: Optional[set[str]] = None) -> dict[str, tuple[str, str]]:
    """place_id -> (canonical place_id, why). Conservative on purpose.

    Two rules, both needing agreement on more than one axis: same name within
    150m, or same phone with an overlapping name. A single shared phone is not
    enough — a trade and a salon run from the same house share a landline.
    """
    rows = [r for r in rows if not (skip_names and normalise_name(r.get("name")) in skip_names)]
    dupes: dict[str, tuple[str, str]] = {}

    by_name: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = normalise_name(row.get("name"))
        if key:
            by_name.setdefault(key, []).append(row)

    for group in by_name.values():
        if len(group) < 2:
            continue
        for cluster in _cluster_by_distance(group, max_distance_m):
            if len(cluster) < 2:
                continue
            keep = _canonical(cluster)
            for row in cluster:
                if row["place_id"] != keep["place_id"]:
                    dupes.setdefault(row["place_id"],
                                     (keep["place_id"], "same name, same spot"))

    by_phone: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("phone_e164"):
            by_phone.setdefault(row["phone_e164"], []).append(row)

    for group in by_phone.values():
        if len(group) < 2:
            continue
        keep = _canonical(group)
        keep_tokens = tokens_of(keep.get("name"))
        for row in group:
            if row["place_id"] == keep["place_id"] or row["place_id"] in dupes:
                continue
            if keep_tokens & tokens_of(row.get("name")):
                dupes[row["place_id"]] = (keep["place_id"], "same phone, same name")

    return _resolve_chains(dupes)


def _cluster_by_distance(group: list[dict[str, Any]],
                         max_distance_m: float) -> list[list[dict[str, Any]]]:
    clusters: list[list[dict[str, Any]]] = []
    for row in group:
        lat, lng = row.get("lat"), row.get("lng")
        for cluster in clusters:
            head = cluster[0]
            if (lat is None or lng is None or head.get("lat") is None
                    or haversine(lat, lng, head["lat"], head["lng"]) <= max_distance_m):
                cluster.append(row)
                break
        else:
            clusters.append([row])
    return clusters


def _resolve_chains(dupes: dict[str, tuple[str, str]]) -> dict[str, tuple[str, str]]:
    """If A points at B and B points at C, point A at C."""
    out: dict[str, tuple[str, str]] = {}
    for place_id, (canonical, why) in dupes.items():
        seen = {place_id}
        while canonical in dupes and canonical not in seen:
            seen.add(canonical)
            canonical = dupes[canonical][0]
        if canonical != place_id:
            out[place_id] = (canonical, why)
    return out


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
@dataclass
class EnrichResult:
    processed: int = 0
    phones_normalised: int = 0
    phones_invalid: int = 0
    socials_found: int = 0
    searches: int = 0
    search_failed: int = 0
    consent_found: int = 0
    chains_marked: int = 0
    duplicates_marked: int = 0
    improved: int = 0
    score_delta: int = 0
    stopped: str = ""
    manual_queries: list[str] = field(default_factory=list)
    _misses: int = 0          # consecutive failed lookups


# Enough consecutive failures to conclude the search path is down rather than
# flaky. Each one costs a second of waiting, so this is not worth 50 of them.
MAX_CONSECUTIVE_SEARCH_FAILURES = 5


ProgressFn = Callable[[int, int, str], None]


def _row_to_rec(row: Any) -> dict[str, Any]:
    rec = dict(row)
    rec["hours"] = json.loads(row["hours_json"]) if row["hours_json"] else None
    return rec


def run(con: Any, provider: Any, limit: int = 50, force: bool = False,
        city: str = "", region: str = "CA",
        progress: Optional[ProgressFn] = None) -> EnrichResult:
    res = EnrichResult()
    rows = db.leads_to_enrich(con, limit, force)

    for i, row in enumerate(rows, 1):
        rec = _row_to_rec(row)
        before = row["score"] or 0
        updates: dict[str, Any] = {}

        phone = normalise_phone(row["phone"], region)
        if phone.e164:
            res.phones_normalised += 1
        if row["phone"] and not phone.valid:
            res.phones_invalid += 1
        updates.update(phone_e164=phone.e164, phone_valid=int(phone.valid),
                       phone_type=phone.kind)
        rec.update(updates)

        if not res.stopped:
            try:
                found = _find_socials(con, row, rec, provider, city, res)
            except search.SearchBlocked as exc:
                # Stop searching, keep everything already learned.
                res.stopped = str(exc)
                found = {}
            updates.update(found)
            rec.update(found)

        after = score(rec)
        updates["score"] = after
        updates["enriched_at"] = db.now()
        if after > before:
            res.improved += 1
        res.score_delta += after - before

        db.save_enrichment(con, row["place_id"], **updates)
        res.processed += 1
        if progress:
            progress(i, len(rows), row["name"] or row["place_id"])

    con.commit()
    _mark_chains_and_duplicates(con, res)
    con.commit()

    if isinstance(provider, search.ManualProvider):
        res.manual_queries = provider.queries
    return res


def _find_socials(con: Any, row: Any, rec: dict[str, Any], provider: Any,
                  city: str, res: EnrichResult) -> dict[str, Any]:
    # The scan already caught the case where Google's websiteUri *is* the
    # social page. No point paying a search for something we were told.
    if row["website_kind"] == "social" and row["website"]:
        handle = social_handle(row["website"])
        if handle:
            res.socials_found += 1
            return {f"{handle[0]}_url": clean_social_url(row["website"]),
                    "social_confidence": 1.0,
                    "social_checked_at": db.now()}

    if not getattr(provider, "automatic", True) or isinstance(provider, search.NullProvider):
        if isinstance(provider, search.ManualProvider):
            provider.search(_query(row["name"], city))
        return {}

    try:
        hits = provider.search(_query(row["name"], city))
        res.searches += 1
        res._misses = 0
    except search.SearchBlocked:
        raise
    except search.SearchError as exc:
        # A single failed lookup is not evidence of no Facebook page, so leave
        # social_checked_at unset and let the next run try again.
        res.search_failed += 1
        res._misses += 1
        if res._misses >= MAX_CONSECUTIVE_SEARCH_FAILURES:
            raise search.SearchBlocked(
                f"{res._misses} lookups in a row failed, so social lookup is off for "
                f"the rest of this run — the last one said: {exc}\n"
                "Phone, chain and duplicate work continues offline. Re-run enrich when "
                "the connection is back; nothing was marked as 'no page found'."
            ) from exc
        return {}

    best = pick_socials(hits, row["name"], city)
    out: dict[str, Any] = {"social_checked_at": db.now()}
    if best:
        res.socials_found += 1
        confidence = max(c for _, c in best.values())
        out["social_confidence"] = confidence
        for network, (url, _) in best.items():
            out[f"{network}_url"] = url

    _maybe_record_consent(con, row, hits, res)
    return out


def _query(name: str | None, city: str) -> str:
    return " ".join(p for p in (f'"{name}"', city.strip(), "facebook instagram") if p)


def _maybe_record_consent(con: Any, row: Any, hits: Iterable[search.SearchHit],
                          res: EnrichResult) -> None:
    """CASL: only conspicuous publication, only when we actually saw one.

    Rare by design — a business with no website usually has no published
    address, which is exactly why this tool has no email feature. When one does
    turn up we log it with the date and where it came from, and nothing more
    happens automatically.
    """
    if row["consent_basis"]:
        return
    for hit in hits:
        handle = social_handle(hit.url)
        if not handle:
            continue
        if social_confidence(row["name"], hit.url, hit.text) < SOCIAL_THRESHOLD:
            continue
        found = emails_in(hit.text)
        if found:
            db.record_consent(con, row["place_id"], "conspicuous_publication",
                              found[0], f"published on {hit.url}")
            res.consent_found += 1
            return


def _mark_chains_and_duplicates(con: Any, res: EnrichResult) -> None:
    everything = db.chain_input(con)
    chains = chain_groups(everything)
    for name, place_ids in chains.items():
        note = f"chain: {len(place_ids)} locations under the same name"
        for place_id in place_ids:
            if db.mark_chain(con, place_id, note):
                res.chains_marked += 1

    lead_rows = [dict(r) for r in con.execute(
        "SELECT place_id, name, lat, lng, phone_e164, review_count, found_at "
        "FROM leads WHERE duplicate_of IS NULL").fetchall()]
    for place_id, (canonical, why) in find_duplicates(
            lead_rows, skip_names=set(chains)).items():
        if db.mark_duplicate(con, place_id, canonical, f"duplicate: {why}"):
            res.duplicates_marked += 1
