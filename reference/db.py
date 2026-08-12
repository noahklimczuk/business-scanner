"""SQLite store for the lead pipeline."""
import sqlite3, json, os, datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "leads.db")

STAGES = ["new", "queued", "contacted", "interested", "sold", "live", "dead"]

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
    score         INTEGER DEFAULT 0,
    stage         TEXT DEFAULT 'new',
    notes         TEXT DEFAULT '',
    last_touch    TEXT,
    found_at      TEXT,
    site_dir      TEXT
);
CREATE INDEX IF NOT EXISTS idx_stage ON leads(stage);
CREATE INDEX IF NOT EXISTS idx_score ON leads(score DESC);

CREATE TABLE IF NOT EXISTS touches (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    place_id  TEXT,
    at        TEXT,
    channel   TEXT,
    outcome   TEXT,
    note      TEXT
);

CREATE TABLE IF NOT EXISTS scans (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TEXT,
    centre     TEXT,
    radius_m   INTEGER,
    types      TEXT,
    calls      INTEGER,
    seen       INTEGER,
    new_leads  INTEGER,
    est_cost   REAL
);
"""


def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def upsert_lead(con, place):
    """Insert a lead if new. Returns True when it is genuinely new."""
    existing = con.execute(
        "SELECT place_id FROM leads WHERE place_id=?", (place["place_id"],)
    ).fetchone()
    if existing:
        # refresh the volatile fields but never clobber pipeline state
        con.execute(
            """UPDATE leads SET phone=?, rating=?, review_count=?, website=?, hours_json=?
               WHERE place_id=?""",
            (place.get("phone"), place.get("rating"), place.get("review_count"),
             place.get("website"), json.dumps(place.get("hours")), place["place_id"]),
        )
        return False
    con.execute(
        """INSERT INTO leads
           (place_id,name,category,address,phone,lat,lng,rating,review_count,
            hours_json,website,score,stage,found_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'new', ?)""",
        (place["place_id"], place["name"], place.get("category"), place.get("address"),
         place.get("phone"), place.get("lat"), place.get("lng"), place.get("rating"),
         place.get("review_count"), json.dumps(place.get("hours")),
         place.get("website"), place.get("score", 0), now()),
    )
    return True


def log_touch(con, place_id, channel, outcome, note=""):
    con.execute(
        "INSERT INTO touches (place_id,at,channel,outcome,note) VALUES (?,?,?,?,?)",
        (place_id, now(), channel, outcome, note),
    )
    con.execute("UPDATE leads SET last_touch=? WHERE place_id=?", (now(), place_id))


def set_stage(con, place_id, stage):
    if stage not in STAGES:
        raise ValueError(f"Unknown stage: {stage}. Use one of {', '.join(STAGES)}")
    con.execute("UPDATE leads SET stage=? WHERE place_id=?", (stage, place_id))


def leads(con, stage=None, limit=None):
    q = "SELECT * FROM leads"
    args = []
    if stage:
        q += " WHERE stage=?"
        args.append(stage)
    q += " ORDER BY score DESC, review_count DESC"
    if limit:
        q += f" LIMIT {int(limit)}"
    return con.execute(q, args).fetchall()


def get(con, place_id):
    return con.execute("SELECT * FROM leads WHERE place_id=?", (place_id,)).fetchone()
