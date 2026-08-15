"""The dashboard. One local HTML file, opened in a browser, answering the four
questions the operator actually has on a Tuesday morning:

    who do I call today
    where is everything in the pipeline
    are the live sites still up
    what is this worth, against what it costs

It is a file, not a server. There is no login to build, no port to remember,
nothing to leave running, and nothing that can be reached from outside the
laptop — which matters, because this page has every client's revenue on it and
is the single most sensitive artefact the tool produces. `leadsmith board`
writes it and prints the path.

The call list is the part that gets used. Sorted by score for now; Phase 2.5
replaces that sort with propensity, which is a different and better question —
score says who *qualifies*, propensity says who *buys*. Everything else on the
page is a number that should make an uncomfortable trend obvious before it
becomes a surprise.
"""
from __future__ import annotations

import datetime
import os
from typing import Any, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

import billing
import db

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "templates")
DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "board.html")

# Long enough not to pester someone who said "not this week", short enough that
# a lead does not quietly rot. The three-strikes rule in `touch` handles the
# ones who are never in; this is about not being the person who calls twice in
# two days.
COOLDOWN_DAYS = 7
CALL_LIST = 15

_env: Optional[Environment] = None


def env() -> Environment:
    global _env
    if _env is None:
        _env = Environment(loader=FileSystemLoader(TEMPLATE_DIR),
                           autoescape=select_autoescape(["html"]),
                           trim_blocks=True, lstrip_blocks=True)
    return _env


def call_list(con, limit: int = CALL_LIST, cooldown_days: int = COOLDOWN_DAYS
              ) -> list[dict[str, Any]]:
    """Today's calls: workable leads, best first, nobody called this week.

    Anyone sold, live or dead is out by definition. So is anyone touched
    recently — a call list that keeps offering you the same person you spoke to
    yesterday is a list you stop trusting, and then stop opening.
    """
    rows = con.execute(
        """SELECT * FROM leads
           WHERE stage NOT IN ('sold', 'live', 'dead')
             -- Belt and braces on top of the stage: a paying client must never
             -- appear on a call list, whatever else went wrong upstream.
             AND place_id NOT IN (SELECT place_id FROM subscriptions
                                  WHERE status = 'active')
             AND duplicate_of IS NULL
             AND is_chain = 0
             AND (last_touch IS NULL
                  OR julianday('now') - julianday(last_touch) >= ?)
           ORDER BY score DESC, review_count DESC
           LIMIT ?""", (cooldown_days, limit)).fetchall()

    out = []
    for row in rows:
        item = dict(row)
        item["why"] = _reasons(row)
        item["last_touch_days"] = _days_since(row["last_touch"])
        out.append(item)
    return out


def _days_since(stamp: Optional[str]) -> Optional[int]:
    if not stamp:
        return None
    try:
        then = datetime.datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return (datetime.datetime.now() - then).days


def _reasons(row) -> list[str]:
    """Why this one is on the list, in the words you would use on the phone.

    A board that prints a score and nothing else has done about a third of the
    job: the number tells you who to ring, the reasons tell you what to say
    when they pick up.
    """
    reasons = []
    kind = row["website_kind"]
    if kind == "social":
        # The best segment there is: they already decided they need a web
        # presence and are currently renting one from Facebook.
        reasons.append("Facebook page, no website of their own")
    elif kind == "defunct":
        reasons.append("their website link is dead")
    elif kind == "none":
        reasons.append("no website at all")

    count = row["review_count"] or 0
    rating = row["rating"]
    if count >= 20 and rating and rating >= 4.0:
        reasons.append(f"{count} reviews at {rating} — proven demand, nowhere to send it")
    elif count >= 20:
        reasons.append(f"{count} reviews")
    elif count <= 3:
        reasons.append("barely reviewed; may not want more customers")

    if rating and rating < 3.5:
        # Worth saying out loud rather than burying: a website amplifies a
        # reputation problem, and this is not the one you want in a portfolio.
        reasons.append(f"rated {rating} — a site would amplify that")
    if not row["phone"]:
        reasons.append("no phone listed — walk in")
    return reasons


def pipeline(con) -> list[dict[str, Any]]:
    counts = db.counts_by_stage(con)
    return [{"stage": stage, "count": counts.get(stage, 0)} for stage in db.STAGES]


def live_sites(con) -> list[dict[str, Any]]:
    """Launched sites and how they were last seen.

    Guarded on the column existing: the phases land in whatever order they get
    merged, and a board that crashes because Phase 5 has not landed yet is
    worse than one that shows an empty panel.
    """
    if not db.has_column(con, "leads", "live_url"):
        return []
    rows = con.execute(
        "SELECT * FROM leads WHERE stage='live' ORDER BY name").fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["checked_days"] = _days_since(row["last_checked_at"])
        out.append(item)
    return out


def stale_previews(con, days: int = 30) -> list[dict[str, Any]]:
    """Previews still on the internet for a lead that went nowhere.

    Each one is a page with a stranger's name on it, published by us, that
    nobody is going to buy. They should come down, and nothing else in the tool
    would ever mention them again.
    """
    if not db.has_column(con, "leads", "preview_url"):
        return []
    rows = con.execute(
        """SELECT * FROM leads
           WHERE preview_url IS NOT NULL
             AND stage IN ('dead', 'new', 'queued', 'contacted')
             AND julianday('now') - julianday(preview_at) >= ?
           ORDER BY preview_at""", (days,)).fetchall()
    return [dict(r) for r in rows]


def _stamp(when: datetime.datetime) -> str:
    """"Friday 14 August 2026, 9:05PM" — without a glibc-only directive."""
    return (f"{when:%A} {when.day} {when:%B %Y}, "
            f"{when.hour % 12 or 12}:{when:%M}{when:%p}")


def build(con, *, operator: Optional[dict[str, Any]] = None) -> str:
    subs = db.subscriptions(con)
    money = billing.summarise(subs)
    costs = billing.unit_costs(con)
    live = live_sites(con)

    import invoice as invoice_mod

    return env().get_template("board.html").render(
        # Assembled rather than strftime("%A %-d %B %Y, %-I:%M%p"): both %-d
        # and %-I are glibc extensions and raise ValueError on Windows, which
        # is where the packaged app runs — the board would not build at all.
        generated=_stamp(datetime.datetime.now()),
        # Money asked for but not yet arrived. On the same page as MRR because
        # the two answer different questions and the second one is the one that
        # goes quiet — nothing else in the tool would ever mention an invoice
        # that has been sitting unpaid since June.
        owed=invoice_mod.outstanding(con),
        owed_money=invoice_mod.money,
        operator=operator or {},
        calls=call_list(con),
        pipeline=pipeline(con),
        total_leads=sum(db.counts_by_stage(con).values()),
        live=live,
        down=[s for s in live if s.get("last_check_ok") == 0],
        unchecked=[s for s in live
                   if s.get("checked_days") is None or s["checked_days"] > 2],
        stale=stale_previews(con),
        subs=[dict(r) for r in subs if r["status"] == "active"],
        money=money,
        costs=costs,
        margin=money.mrr - (costs["places"] + costs["generation"]),
    )


def write(html: str, path: str = DEFAULT_PATH) -> str:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return path
