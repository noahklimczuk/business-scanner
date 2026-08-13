"""SQLite store for the lead pipeline.

One file, one operator, no migration framework. `connect()` creates the schema
and back-fills any column added by a later phase, so upgrading is `git pull`.

Two tables carry Google Places content and therefore carry an expiry:
`leads` and `market`. Both have `refreshed_at`; see CACHE_TTL_DAYS.
"""
from __future__ import annotations

import datetime
import json
import os
import sqlite3
from typing import Any, Optional, Sequence

DB_PATH = os.environ.get("LEADSMITH_DB") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "leads.db"
)

STAGES = ["new", "queued", "contacted", "interested", "sold", "live", "dead"]

# How we classify whatever Google has in `websiteUri`:
#   none     - no website at all, the core target
#   social   - a Facebook/Instagram/Linktree page standing in for a site
#   defunct  - a Google `business.site` page (that builder shut down in 2024,
#              so the link is dead), or similar
#   real     - an actual website; not a lead, but useful market data
WEBSITE_KINDS = ["none", "social", "defunct", "real"]

# Google Places terms let us keep `place_id` forever, but every other field is
# cached content that must be refreshed or discarded. 30 days is the ceiling we
# hold ourselves to. See `stale_leads()` and `purge_stale()`.
CACHE_TTL_DAYS = 30

# Fields on `leads` that came from Google and therefore expire. Pipeline state
# (stage, notes, score, touches) is ours and survives a purge. `phone_e164` is
# derived from Google's phone number, so it expires with it; the social URLs we
# found ourselves and they stay.
CACHED_LEAD_FIELDS = [
    "name", "category", "address", "phone", "lat", "lng",
    "rating", "review_count", "hours_json", "website", "website_kind",
    "phone_e164", "phone_valid", "phone_type",
]

# CASL consent bases we will actually record. There is no implied-consent
# "existing business relationship" option here on purpose: we have no prior
# relationship with a cold lead, and inventing one is how people get fined.
CONSENT_BASES = ["conspicuous_publication", "express"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    place_id      TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    category      TEXT,
    address       TEXT,
    phone         TEXT,
    lat           REAL,
    lng           REAL,
    rating        REAL,
    review_count  INTEGER,
    hours_json    TEXT,
    website       TEXT,
    website_kind  TEXT DEFAULT 'none',
    score         INTEGER DEFAULT 0,
    stage         TEXT DEFAULT 'new',
    notes         TEXT DEFAULT '',
    last_touch    TEXT,
    found_at      TEXT,
    refreshed_at  TEXT,
    site_dir      TEXT,
    -- CASL: null means we have no consent basis and must not send electronic
    -- commercial messages. Phone, walk-in and physical mail are unaffected.
    consent_basis     TEXT,
    consent_at        TEXT,
    consent_note      TEXT,
    consent_email     TEXT,
    consent_withdrawn_at TEXT,
    -- Phase 2 enrichment.
    phone_e164        TEXT,
    phone_valid       INTEGER,
    phone_type        TEXT,
    facebook_url      TEXT,
    instagram_url     TEXT,
    social_confidence REAL,
    social_checked_at TEXT,
    enriched_at       TEXT,
    is_chain          INTEGER DEFAULT 0,
    duplicate_of      TEXT,
    -- Phase 4. `preview_url` is a real business's name on the public internet
    -- before they have agreed to anything, so it is recorded rather than
    -- remembered: it is what `unpreview` takes down and what the leave-behind
    -- points a QR code at.
    preview_url       TEXT,
    preview_at        TEXT,
    preview_project   TEXT,
    -- Phase 5. A launched site is the client's actual web presence: it is
    -- indexable, it is on their domain, and it is the thing that breaks at
    -- 2am. `monitor` reads these.
    domain            TEXT,
    live_url          TEXT,
    live_project      TEXT,
    launched_at       TEXT,
    form_enabled      INTEGER DEFAULT 0,
    last_checked_at   TEXT,
    last_check_ok     INTEGER,
    last_check_note   TEXT
);
CREATE INDEX IF NOT EXISTS idx_stage ON leads(stage);
CREATE INDEX IF NOT EXISTS idx_score ON leads(score DESC);
CREATE INDEX IF NOT EXISTS idx_refreshed ON leads(refreshed_at);

-- Every operational business the scan saw, including the ones that already
-- have websites. The scan pays for this data whether or not we keep it, and
-- Phase 2.5 needs it to say "9 of 11 nearby roofers have a site and you
-- don't" — the most persuasive sentence available. Not a lead list.
CREATE TABLE IF NOT EXISTS market (
    place_id      TEXT PRIMARY KEY,
    name          TEXT,
    category      TEXT,
    lat           REAL,
    lng           REAL,
    has_site      INTEGER DEFAULT 0,
    website_kind  TEXT,
    rating        REAL,
    review_count  INTEGER,
    refreshed_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_market_cat ON market(category);

CREATE TABLE IF NOT EXISTS touches (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    place_id  TEXT,
    at        TEXT,
    channel   TEXT,
    outcome   TEXT,
    note      TEXT
);
CREATE INDEX IF NOT EXISTS idx_touch_place ON touches(place_id);

CREATE TABLE IF NOT EXISTS scans (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    at            TEXT,
    centre        TEXT,
    radius_m      INTEGER,
    cell_m        INTEGER,
    types         TEXT,
    calls_planned INTEGER,
    calls_made    INTEGER,
    calls_failed  INTEGER,
    seen          INTEGER,
    new_leads     INTEGER,
    est_cost      REAL,
    actual_cost   REAL,
    saturated     INTEGER
);

-- One row per paid copy generation, so the Claude bill is as visible as the
-- Places bill. Re-rendering an existing content.json writes nothing here
-- because it costs nothing.
CREATE TABLE IF NOT EXISTS generations (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    place_id           TEXT,
    at                 TEXT,
    model              TEXT,
    template           TEXT,
    attempts           INTEGER,
    input_tokens       INTEGER,
    output_tokens      INTEGER,
    cache_write_tokens INTEGER,
    cache_read_tokens  INTEGER,
    cost               REAL
);

-- Geocoding a town costs a call. Same 30-day expiry as everything else from
-- Google, so re-scanning "Newmarket, ON" this week is free.
CREATE TABLE IF NOT EXISTS geocache (
    query        TEXT PRIMARY KEY,
    lat          REAL,
    lng          REAL,
    formatted    TEXT,
    refreshed_at TEXT
);
"""

# Columns added after the first release. Adding one here is the whole migration.
_ADDED_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "leads": [
        ("website_kind", "TEXT DEFAULT 'none'"),
        ("refreshed_at", "TEXT"),
        ("consent_basis", "TEXT"),
        ("consent_at", "TEXT"),
        ("consent_note", "TEXT"),
        ("consent_email", "TEXT"),
        ("consent_withdrawn_at", "TEXT"),
        ("phone_e164", "TEXT"),
        ("phone_valid", "INTEGER"),
        ("phone_type", "TEXT"),
        ("facebook_url", "TEXT"),
        ("instagram_url", "TEXT"),
        ("social_confidence", "REAL"),
        ("social_checked_at", "TEXT"),
        ("enriched_at", "TEXT"),
        ("is_chain", "INTEGER DEFAULT 0"),
        ("duplicate_of", "TEXT"),
        ("preview_url", "TEXT"),
        ("preview_at", "TEXT"),
        ("preview_project", "TEXT"),
        ("domain", "TEXT"),
        ("live_url", "TEXT"),
        ("live_project", "TEXT"),
        ("launched_at", "TEXT"),
        ("form_enabled", "INTEGER DEFAULT 0"),
        ("last_checked_at", "TEXT"),
        ("last_check_ok", "INTEGER"),
        ("last_check_note", "TEXT"),
    ],
    "market": [],
    "scans": [],
}


def connect(path: Optional[str] = None) -> sqlite3.Connection:
    con = sqlite3.connect(path or DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA)
    _migrate(con)
    con.commit()
    return con


def _migrate(con: sqlite3.Connection) -> None:
    for table, columns in _ADDED_COLUMNS.items():
        have = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns:
            if name not in have:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Leads
# ---------------------------------------------------------------------------
def upsert_lead(con: sqlite3.Connection, place: dict[str, Any]) -> bool:
    """Insert a lead, or refresh the volatile fields of one we already have.

    Returns True only when the lead is genuinely new, so the scan can report an
    honest count. Pipeline state (stage, notes, site_dir) is never clobbered.
    """
    existing = con.execute(
        "SELECT stage FROM leads WHERE place_id=?", (place["place_id"],)
    ).fetchone()
    hours = json.dumps(place.get("hours")) if place.get("hours") else None

    if existing:
        con.execute(
            """UPDATE leads SET name=?, category=?, address=?, phone=?, lat=?, lng=?,
                      rating=?, review_count=?, hours_json=?, website=?,
                      website_kind=?, score=?, refreshed_at=?
               WHERE place_id=?""",
            (place["name"], place.get("category"), place.get("address"),
             place.get("phone"), place.get("lat"), place.get("lng"),
             place.get("rating"), place.get("review_count"), hours,
             place.get("website"), place.get("website_kind", "none"),
             place.get("score", 0), now(), place["place_id"]),
        )
        # They built their own site while we weren't looking. If we have never
        # spoken to them there is nothing to sell; if we have, that is a
        # conversation for the operator, so leave the stage alone.
        if place.get("website_kind") == "real" and existing["stage"] in ("new", "queued"):
            con.execute(
                """UPDATE leads SET stage='dead',
                          notes = TRIM(COALESCE(notes,'') || ' [auto] has a website now')
                   WHERE place_id=?""",
                (place["place_id"],),
            )
        return False

    con.execute(
        """INSERT INTO leads
           (place_id, name, category, address, phone, lat, lng, rating,
            review_count, hours_json, website, website_kind, score, stage,
            found_at, refreshed_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, 'new', ?, ?)""",
        (place["place_id"], place["name"], place.get("category"),
         place.get("address"), place.get("phone"), place.get("lat"),
         place.get("lng"), place.get("rating"), place.get("review_count"),
         hours, place.get("website"), place.get("website_kind", "none"),
         place.get("score", 0), now(), now()),
    )
    return True


def upsert_market(con: sqlite3.Connection, place: dict[str, Any]) -> None:
    con.execute(
        """INSERT INTO market
           (place_id, name, category, lat, lng, has_site, website_kind,
            rating, review_count, refreshed_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(place_id) DO UPDATE SET
             name=excluded.name, category=excluded.category,
             lat=excluded.lat, lng=excluded.lng,
             has_site=excluded.has_site, website_kind=excluded.website_kind,
             rating=excluded.rating, review_count=excluded.review_count,
             refreshed_at=excluded.refreshed_at""",
        (place["place_id"], place.get("name"), place.get("category"),
         place.get("lat"), place.get("lng"),
         1 if place.get("website_kind") == "real" else 0,
         place.get("website_kind"), place.get("rating"),
         place.get("review_count"), now()),
    )


# How a touch happened. `preview` is not an attempt to reach anyone — it is the
# record that a page carrying their name went onto the public internet, which
# is worth having in the same timeline as the visits.
CHANNELS = ["walk-in", "phone", "preview", "mail", "email", "other"]

# What came of it. Deliberately short: a list of twenty outcomes is a list
# nobody uses consistently, and the automatic rules below depend on the
# operator picking the same word twice.
OUTCOMES = {
    "no-answer": "Nobody there, or nobody who could talk.",
    "gatekeeper": "Spoke to someone who is not the decision maker.",
    "callback": "Come back later — they said when, or they didn't.",
    "interested": "Wants the site, or wants to think about it.",
    "not-interested": "A no. Believe it the first time.",
    "sold": "Paid, or committed to pay.",
}

# Three attempts with nobody home and the lead is closed. The scarce resource
# is the operator's afternoon, not leads — a scan produces more than can ever
# be worked, so the default has to be to move on rather than to persist.
NO_ANSWER_LIMIT = 3

_OUTCOME_STAGE = {
    "gatekeeper": "contacted",
    "callback": "contacted",
    "interested": "interested",
    "not-interested": "dead",
    "sold": "sold",
}


def log_touch(con: sqlite3.Connection, place_id: str, channel: str,
              outcome: str, note: str = "") -> str:
    """Record an attempt and move the lead to wherever the outcome puts it.

    Returns the stage the lead ended up in, so the caller can tell the operator
    what just happened to it rather than making them run `show` to find out.

    The stage moves are here rather than in the CLI on purpose: a touch that
    does not change the pipeline is a touch the operator has to remember to
    follow up with a second command, and that is the step that gets skipped at
    the eleventh door of the afternoon.
    """
    if outcome not in OUTCOMES:
        raise ValueError(f"Unknown outcome: {outcome}. "
                         f"Use one of {', '.join(OUTCOMES)}")
    if channel not in CHANNELS:
        raise ValueError(f"Unknown channel: {channel}. "
                         f"Use one of {', '.join(CHANNELS)}")

    stamp = now()
    con.execute(
        "INSERT INTO touches (place_id, at, channel, outcome, note) VALUES (?,?,?,?,?)",
        (place_id, stamp, channel, outcome, note),
    )
    con.execute("UPDATE leads SET last_touch=? WHERE place_id=?", (stamp, place_id))

    row = con.execute("SELECT stage FROM leads WHERE place_id=?",
                      (place_id,)).fetchone()
    if row is None:
        return ""
    stage = row["stage"]

    # A closed lead stays closed. Logging a touch against something already
    # sold or live is a note on an existing client, not a pipeline move.
    if stage in ("sold", "live", "dead"):
        if outcome == "sold" and stage == "dead":
            set_stage(con, place_id, "sold")     # they changed their mind
            return "sold"
        return stage

    target = _OUTCOME_STAGE.get(outcome)
    if outcome == "no-answer" and consecutive_no_answers(con, place_id) >= NO_ANSWER_LIMIT:
        target = "dead"
        con.execute(
            "UPDATE leads SET notes = TRIM(COALESCE(notes,'') || ?) WHERE place_id=?",
            (f"\nClosed automatically after {NO_ANSWER_LIMIT} attempts with no "
             f"answer ({stamp[:10]}).", place_id))
    if target:
        set_stage(con, place_id, target)
        return target
    # "no-answer" below the limit: they have been contacted, in the sense that
    # the operator spent a trip on them.
    if stage == "new":
        set_stage(con, place_id, "queued")
        return "queued"
    return stage


def consecutive_no_answers(con: sqlite3.Connection, place_id: str) -> int:
    """No-answers since the last touch that was anything else.

    Consecutive rather than total: someone who was interested in March and then
    missed three times in June is a different lead from one who has never once
    been in, and closing them on a lifetime tally would be wrong.
    """
    count = 0
    for row in con.execute(
            "SELECT outcome FROM touches WHERE place_id=? ORDER BY id DESC",
            (place_id,)):
        if row["outcome"] != "no-answer":
            break
        count += 1
    return count


def set_live(con: sqlite3.Connection, place_id: str, *, domain: str,
             url: str, project: str, form: bool = False) -> None:
    """Record a launch and move the lead to `live`.

    `live` rather than `sold`: sold is a promise, live is a running website
    with the operator's name attached to whether it stays up.
    """
    con.execute(
        """UPDATE leads SET domain=?, live_url=?, live_project=?, launched_at=?,
           form_enabled=?, stage='live' WHERE place_id=?""",
        (domain or None, url or None, project or None, now(),
         1 if form else 0, place_id))


def live_sites(con: sqlite3.Connection) -> list[sqlite3.Row]:
    """Everything `monitor` is responsible for."""
    return con.execute(
        "SELECT * FROM leads WHERE stage='live' AND live_url IS NOT NULL "
        "ORDER BY name").fetchall()


def record_check(con: sqlite3.Connection, place_id: str, ok: bool,
                 note: str = "") -> None:
    con.execute(
        "UPDATE leads SET last_checked_at=?, last_check_ok=?, last_check_note=? "
        "WHERE place_id=?", (now(), 1 if ok else 0, note or None, place_id))


def set_preview(con: sqlite3.Connection, place_id: str, url: str,
                project: str = "") -> None:
    con.execute(
        "UPDATE leads SET preview_url=?, preview_at=?, preview_project=? "
        "WHERE place_id=?", (url or None, now() if url else None,
                             project or None, place_id))


def set_stage(con: sqlite3.Connection, place_id: str, stage: str) -> None:
    if stage not in STAGES:
        raise ValueError(f"Unknown stage: {stage}. Use one of {', '.join(STAGES)}")
    con.execute("UPDATE leads SET stage=? WHERE place_id=?", (stage, place_id))


def leads(con: sqlite3.Connection, stage: Optional[str] = None,
          kind: Optional[str] = None, min_score: int = 0,
          limit: Optional[int] = None, exclude_dead: bool = False,
          exclude_duplicates: bool = False) -> list[sqlite3.Row]:
    q = ["SELECT * FROM leads WHERE score >= ?"]
    args: list[Any] = [min_score]
    if stage:
        q.append("AND stage = ?")
        args.append(stage)
    elif exclude_dead:
        q.append("AND stage != 'dead'")
    if kind:
        q.append("AND website_kind = ?")
        args.append(kind)
    if exclude_duplicates:
        q.append("AND duplicate_of IS NULL")
    q.append("ORDER BY score DESC, review_count DESC")
    if limit:
        q.append("LIMIT ?")
        args.append(int(limit))
    return con.execute(" ".join(q), args).fetchall()


def get(con: sqlite3.Connection, place_id: str) -> Optional[sqlite3.Row]:
    return con.execute("SELECT * FROM leads WHERE place_id=?", (place_id,)).fetchone()


def touches(con: sqlite3.Connection, place_id: str) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT * FROM touches WHERE place_id=? ORDER BY at DESC", (place_id,)
    ).fetchall()


# ---------------------------------------------------------------------------
# Enrichment (Phase 2)
# ---------------------------------------------------------------------------
def leads_to_enrich(con: sqlite3.Connection, limit: int = 50,
                    force: bool = False) -> list[sqlite3.Row]:
    """Best leads first — enrichment costs wall-clock time, so spend it well."""
    q = ["SELECT * FROM leads WHERE stage != 'dead' AND duplicate_of IS NULL"]
    if not force:
        q.append("AND enriched_at IS NULL")
    q.append("ORDER BY score DESC, review_count DESC LIMIT ?")
    return con.execute(" ".join(q), (int(limit),)).fetchall()


def save_enrichment(con: sqlite3.Connection, place_id: str, **fields: Any) -> None:
    allowed = {"phone_e164", "phone_valid", "phone_type", "facebook_url",
               "instagram_url", "social_confidence", "social_checked_at",
               "score", "enriched_at"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"save_enrichment does not write {', '.join(sorted(unknown))}")
    sets = ", ".join(f"{k}=?" for k in fields)
    con.execute(f"UPDATE leads SET {sets} WHERE place_id=?",
                (*fields.values(), place_id))


def mark_chain(con: sqlite3.Connection, place_id: str, note: str) -> bool:
    """Flag a chain location. Kills it only if we have never worked it."""
    row = con.execute("SELECT stage, is_chain FROM leads WHERE place_id=?",
                      (place_id,)).fetchone()
    if not row or row["is_chain"]:
        return False
    con.execute("UPDATE leads SET is_chain=1 WHERE place_id=?", (place_id,))
    if row["stage"] in ("new", "queued"):
        con.execute(
            """UPDATE leads SET stage='dead',
                      notes = TRIM(COALESCE(notes,'') || ' [auto] ' || ?)
               WHERE place_id=?""", (note, place_id))
    return True


def mark_duplicate(con: sqlite3.Connection, place_id: str, canonical: str,
                   note: str) -> bool:
    row = con.execute("SELECT stage, duplicate_of FROM leads WHERE place_id=?",
                      (place_id,)).fetchone()
    if not row or row["duplicate_of"]:
        return False
    con.execute("UPDATE leads SET duplicate_of=? WHERE place_id=?",
                (canonical, place_id))
    if row["stage"] in ("new", "queued"):
        con.execute(
            """UPDATE leads SET stage='dead',
                      notes = TRIM(COALESCE(notes,'') || ' [auto] ' || ?)
               WHERE place_id=?""", (note, place_id))
    return True


def duplicates(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute(
        """SELECT d.place_id, d.name, d.duplicate_of, c.name AS canonical_name
           FROM leads d LEFT JOIN leads c ON c.place_id = d.duplicate_of
           WHERE d.duplicate_of IS NOT NULL ORDER BY c.name""").fetchall()


def record_consent(con: sqlite3.Connection, place_id: str, basis: str,
                   email: str, source: str) -> None:
    """CASL: log the basis and the date we established it, per lead.

    Nothing in this tool sends email. This exists so that if the operator ever
    does write to someone, there is a dated record of why that was allowed.
    """
    if basis not in CONSENT_BASES:
        raise ValueError(f"Unknown consent basis '{basis}'. "
                         f"Use one of: {', '.join(CONSENT_BASES)}")
    con.execute(
        """UPDATE leads SET consent_basis=?, consent_at=?, consent_email=?,
                  consent_note=?, consent_withdrawn_at=NULL
           WHERE place_id=?""", (basis, now(), email, source, place_id))


def withdraw_consent(con: sqlite3.Connection, place_id: str) -> None:
    # CASL gives you 10 business days to honour a withdrawal. Recording it the
    # moment it happens is the only way that clock is ever met.
    con.execute("UPDATE leads SET consent_withdrawn_at=? WHERE place_id=?",
                (now(), place_id))


def may_email(row: Any) -> bool:
    """The one gate. False unless a basis was recorded and not withdrawn."""
    return bool(row["consent_basis"]) and not row["consent_withdrawn_at"]


def chain_input(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every business we know of, leads and market alike, for chain detection.

    A chain's other locations usually *do* have websites, so they only exist in
    `market` — detecting chains from the lead table alone misses most of them.
    """
    rows = con.execute(
        """SELECT place_id, name, lat, lng FROM leads
           UNION
           SELECT place_id, name, lat, lng FROM market""").fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Market density (the input Phase 2.5 needs)
# ---------------------------------------------------------------------------
def market_density(con: sqlite3.Connection, category: Optional[str]) -> dict[str, int]:
    """How many businesses in this category have a website, and how many exist."""
    if not category:
        return {"category_total": 0, "category_with_site": 0}
    row = con.execute(
        """SELECT COUNT(*) AS total, COALESCE(SUM(has_site), 0) AS with_site
           FROM market WHERE category = ?""", (category,)
    ).fetchone()
    return {"category_total": row["total"], "category_with_site": row["with_site"]}


# ---------------------------------------------------------------------------
# Scans and cost logging
# ---------------------------------------------------------------------------
def record_scan(con: sqlite3.Connection, **kw: Any) -> int:
    cur = con.execute(
        """INSERT INTO scans
           (at, centre, radius_m, cell_m, types, calls_planned, calls_made,
            calls_failed, seen, new_leads, est_cost, actual_cost, saturated)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (now(), kw.get("centre"), kw.get("radius_m"), kw.get("cell_m"),
         json.dumps(kw.get("types")), kw.get("calls_planned"),
         kw.get("calls_made"), kw.get("calls_failed", 0), kw.get("seen"),
         kw.get("new_leads"), kw.get("est_cost"), kw.get("actual_cost"),
         kw.get("saturated", 0)),
    )
    return int(cur.lastrowid or 0)


def record_generation(con: sqlite3.Connection, place_id: str, **kw: Any) -> None:
    con.execute(
        """INSERT INTO generations
           (place_id, at, model, template, attempts, input_tokens, output_tokens,
            cache_write_tokens, cache_read_tokens, cost)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (place_id, now(), kw.get("model"), kw.get("template"), kw.get("attempts"),
         kw.get("input_tokens"), kw.get("output_tokens"),
         kw.get("cache_write_tokens"), kw.get("cache_read_tokens"), kw.get("cost")),
    )


def generation_spend(con: sqlite3.Connection, days: int = 30) -> float:
    row = con.execute(
        """SELECT COALESCE(SUM(cost), 0) AS spent FROM generations
           WHERE julianday('now') - julianday(at) <= ?""", (days,)
    ).fetchone()
    return float(row["spent"])


def set_site_dir(con: sqlite3.Connection, place_id: str, path: str) -> None:
    con.execute("UPDATE leads SET site_dir=? WHERE place_id=?", (path, place_id))


def spend_since(con: sqlite3.Connection, days: int = 30) -> float:
    row = con.execute(
        """SELECT COALESCE(SUM(actual_cost), 0) AS spent FROM scans
           WHERE julianday('now') - julianday(at) <= ?""", (days,)
    ).fetchone()
    return float(row["spent"])


# ---------------------------------------------------------------------------
# Geocode cache
# ---------------------------------------------------------------------------
def geocode_cached(con: sqlite3.Connection, query: str) -> Optional[sqlite3.Row]:
    return con.execute(
        """SELECT * FROM geocache
           WHERE query = ? AND julianday('now') - julianday(refreshed_at) <= ?""",
        (query.strip().lower(), CACHE_TTL_DAYS),
    ).fetchone()


def geocode_store(con: sqlite3.Connection, query: str, lat: float, lng: float,
                  formatted: Optional[str]) -> None:
    con.execute(
        """INSERT INTO geocache (query, lat, lng, formatted, refreshed_at)
           VALUES (?,?,?,?,?)
           ON CONFLICT(query) DO UPDATE SET
             lat=excluded.lat, lng=excluded.lng,
             formatted=excluded.formatted, refreshed_at=excluded.refreshed_at""",
        (query.strip().lower(), lat, lng, formatted, now()),
    )


# ---------------------------------------------------------------------------
# 30-day cache expiry
# ---------------------------------------------------------------------------
def stale_leads(con: sqlite3.Connection, days: int = CACHE_TTL_DAYS) -> list[sqlite3.Row]:
    return con.execute(
        """SELECT * FROM leads
           WHERE refreshed_at IS NOT NULL
             AND julianday('now') - julianday(refreshed_at) > ?
           ORDER BY refreshed_at""", (days,)
    ).fetchall()


def purge_stale(con: sqlite3.Connection, days: int = CACHE_TTL_DAYS) -> int:
    """Scrub expired Google content, keeping place_id and our own pipeline data.

    We cannot legally hold Places content past 30 days, and we cannot refresh it
    cheaply either — refreshing one business means a Place Details call, which
    is the thing the whole cost model exists to avoid. So the compliant move is
    to scrub and re-scan; a re-scan refills every field for the price of the
    scan. Live and sold clients are left alone: by then the business data comes
    from the client, not from Google.
    """
    sets = ", ".join(f"{f}=NULL" for f in CACHED_LEAD_FIELDS if f != "name")
    cur = con.execute(
        f"""UPDATE leads
            SET name='(purged — re-scan to refill)', {sets}, refreshed_at=NULL
            WHERE refreshed_at IS NOT NULL
              AND julianday('now') - julianday(refreshed_at) > ?
              AND stage NOT IN ('sold', 'live')""", (days,)
    )
    con.execute(
        """DELETE FROM market
           WHERE refreshed_at IS NOT NULL
             AND julianday('now') - julianday(refreshed_at) > ?""", (days,)
    )
    con.execute(
        """DELETE FROM geocache
           WHERE julianday('now') - julianday(refreshed_at) > ?""", (days,)
    )
    return cur.rowcount


def counts_by_stage(con: sqlite3.Connection) -> dict[str, int]:
    rows = con.execute("SELECT stage, COUNT(*) AS n FROM leads GROUP BY stage").fetchall()
    return {r["stage"]: r["n"] for r in rows}
