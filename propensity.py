"""Purchase propensity: how likely is this business to actually buy?

Two things separate this from the crude lead score in prospect.py.

1. It is explainable. Every score comes with the reasons that produced it,
   because the reasons are what you say on the call. A number alone tells
   you who to phone; the reasons tell you what to open with.

2. It learns. Rule weights are a guess until you have real outcomes. After
   ~40 closed or dead leads, `recalibrate()` shrinks the hand-set weights
   toward what your own results actually show. Before 40, it refuses --
   a model fitted on twelve data points is superstition with a decimal point.

Nothing here predicts the future. It ranks a call list, which is all it
needs to do.
"""
from __future__ import annotations
import json, math, datetime
from dataclasses import dataclass, field
from typing import Optional

# --------------------------------------------------------------------------
# Category economics
# --------------------------------------------------------------------------
# What one new customer is worth to this business, roughly, in CAD. This is
# the single best predictor of willingness to pay $1,200: a roofer needs one
# extra job a decade to justify it, a nail salon needs forty.
JOB_VALUE = {
    "roof": 9000, "hvac": 6000, "contract": 8000, "renovat": 12000,
    "kitchen": 15000, "landscap": 3500, "paving": 5000, "fence": 4000,
    "plumb": 600, "electric": 700, "garage": 2500, "window": 5000,
    "law": 2500, "account": 900, "dent": 1200, "chiro": 800, "vet": 400,
    "physio": 700, "optom": 500, "moving": 1500, "tow": 300,
    "auto": 800, "car_repair": 800, "detail": 250, "locksmith": 200,
    "restaurant": 45, "cafe": 15, "bakery": 30, "bar": 60,
    "hair": 70, "barber": 35, "nail": 55, "spa": 130, "beauty": 90,
    "gym": 600, "pet": 80, "florist": 100, "cleaning": 200,
}
DEFAULT_JOB_VALUE = 400

# --------------------------------------------------------------------------
# Seasonality: when does this trade have money and motivation?
# --------------------------------------------------------------------------
# Multiplier by calendar month (1-12). Trades buy marketing just before their
# season, not during it -- during it they are too busy to answer the phone,
# and after it they are broke.
SEASON = {
    "landscap":  {2: 1.4, 3: 1.5, 4: 1.3, 11: 0.6, 12: 0.5, 1: 0.7},
    "roof":      {2: 1.3, 3: 1.4, 4: 1.3, 12: 0.6, 1: 0.6},
    "hvac":      {3: 1.3, 4: 1.3, 8: 1.3, 9: 1.4, 1: 0.8},
    "paving":    {3: 1.4, 4: 1.4, 11: 0.5, 12: 0.4, 1: 0.5},
    "pool":      {2: 1.5, 3: 1.5, 9: 0.5, 10: 0.4},
    "account":   {11: 1.4, 12: 1.4, 1: 1.3, 3: 0.6, 4: 0.5},
    "gym":       {11: 1.4, 12: 1.5, 1: 0.7, 6: 0.8},
    "florist":   {12: 0.6, 1: 1.3, 4: 0.8},
    "contract":  {1: 1.3, 2: 1.4, 3: 1.3, 12: 0.7},
}


def job_value(category: str | None) -> int:
    c = (category or "").lower()
    for k, v in JOB_VALUE.items():
        if k in c:
            return v
    return DEFAULT_JOB_VALUE


def season_multiplier(category: str | None, month: int | None = None) -> float:
    month = month or datetime.date.today().month
    c = (category or "").lower()
    for k, table in SEASON.items():
        if k in c:
            return table.get(month, 1.0)
    return 1.0


def _plural(category: str | None) -> str:
    """'Roofing contractor' -> 'roofing contractors'. Good enough for speech."""
    c = (category or "").strip().lower()
    if not c:
        return ""
    if c.endswith(("s", "x", "ch", "sh")):
        return c + "es"
    if c.endswith("y") and c[-2:-1] not in "aeiou":
        return c[:-1] + "ies"
    return c + "s"


# --------------------------------------------------------------------------
# Signals
# --------------------------------------------------------------------------
@dataclass
class Signal:
    key: str
    points: float          # contribution to the raw score
    reason: str            # what to say, or why it matters
    kind: str = "static"   # static | market | behaviour


@dataclass
class Propensity:
    score: int                       # 0-100
    band: str                        # hot | warm | cool | cold
    signals: list[Signal] = field(default_factory=list)
    opener: str = ""                 # suggested first line on the call

    def reasons(self) -> list[str]:
        return [s.reason for s in sorted(self.signals,
                                         key=lambda s: -abs(s.points))]


# Default weights. These are informed guesses, not measurements. Every one
# is a candidate for recalibrate() to overwrite once you have outcomes.
WEIGHTS = {
    "demand_no_capture": 22.0,   # lots of reviews, no site
    "socials_only":      18.0,   # has a Facebook page and nothing else
    "competitors_online": 16.0,  # peers have sites, they don't
    "job_value":         14.0,   # one job pays for the website many times
    "active_listing":    10.0,   # they maintain their Google profile
    "review_recency":    10.0,   # customers still arriving
    "quality":            6.0,   # good rating, worth promoting
    "reachable":          4.0,   # phone present
    "season":             0.0,   # applied as a multiplier, not additive
    # behavioural, only available after first contact
    "preview_opened":    25.0,
    "preview_reshared":  18.0,
    "answered":           8.0,
    "asked_price":       20.0,
    "went_quiet":       -15.0,
    "no_answer_x3":     -25.0,
}


def evaluate(lead: dict,
             market: Optional[dict] = None,
             behaviour: Optional[dict] = None,
             weights: Optional[dict] = None,
             month: Optional[int] = None) -> Propensity:
    """Score one lead.

    lead      -- row from the leads table as a dict
    market    -- {'category_total': int, 'category_with_site': int} for the
                 surrounding area, computed from the market table
    behaviour -- {'preview_views': int, 'unique_ips': int, 'answered': bool,
                  'asked_price': bool, 'days_since_touch': int,
                  'no_answer_count': int}
    """
    w = {**WEIGHTS, **(weights or {})}
    sig: list[Signal] = []

    rc = lead.get("review_count") or 0
    rating = lead.get("rating") or 0.0
    cat = lead.get("category") or ""

    # -- demand with no way to capture it -----------------------------------
    # The core thesis. Reviews prove people are finding and choosing them.
    # No website means every one of those searches ends on a Google listing
    # with no photos, no service list, and nowhere to book.
    if rc >= 15:
        strength = min(1.0, math.log10(rc / 10) / math.log10(15))
        sig.append(Signal("demand_no_capture", w["demand_no_capture"] * strength,
                          f"{rc} Google reviews and no website — proven demand, "
                          f"nothing capturing it", "static"))
    elif rc < 5:
        sig.append(Signal("demand_no_capture", -8.0,
                          f"only {rc} reviews — may be dormant or brand new"))

    # -- Facebook-only is the best segment there is -------------------------
    # They already decided they need a web presence. You are not selling the
    # idea, only a better version of a thing they already bought into.
    if lead.get("facebook_url") or lead.get("instagram_url"):
        sig.append(Signal("socials_only", w["socials_only"],
                          "runs a Facebook/Instagram page but no site — already "
                          "believes in online presence, currently renting it",
                          "static"))

    # -- competitive pressure ----------------------------------------------
    # Computable for free from your own scan data, and the most persuasive
    # single fact you can put in front of an owner.
    if market and market.get("category_total", 0) >= 4:
        total = market["category_total"]
        with_site = market.get("category_with_site", 0)
        share = with_site / total
        if share >= 0.6:
            peers = _plural(cat) or "competitors"
            sig.append(Signal("competitors_online", w["competitors_online"] * share,
                              f"{with_site} of {total} nearby {peers} have websites "
                              f"— they are the exception",
                              "market"))

    # -- what a customer is worth to them ----------------------------------
    jv = job_value(cat)
    if jv >= 1000:
        strength = min(1.0, math.log10(jv / 500) / math.log10(30))
        payback = max(1, round(1200 / jv, 1))
        sig.append(Signal("job_value", w["job_value"] * strength,
                          f"one job is worth about ${jv:,} — the site pays for "
                          f"itself in {payback} job{'s' if payback != 1 else ''}",
                          "static"))
    elif jv < 100:
        sig.append(Signal("job_value", -6.0,
                          f"low ticket (~${jv}/customer) — needs volume to justify spend"))

    # -- do they tend their listing? ---------------------------------------
    # A claimed, maintained profile means someone in there cares about being
    # found. A barren listing often means a business that doesn't want more
    # customers, which is a real thing and unsellable.
    tended = sum([bool(lead.get("hours_json") and lead["hours_json"] != "null"),
                  bool(lead.get("phone")),
                  bool(lead.get("photo_count", 0) >= 3)])
    if tended >= 2:
        sig.append(Signal("active_listing", w["active_listing"] * (tended / 3),
                          "Google profile is claimed and maintained — someone there "
                          "already cares about being found", "static"))
    elif tended == 0:
        sig.append(Signal("active_listing", -12.0,
                          "listing is bare — may not want to be found online at all"))

    # -- are customers still arriving? -------------------------------------
    days = lead.get("days_since_last_review")
    if days is not None:
        if days <= 60:
            sig.append(Signal("review_recency", w["review_recency"],
                              "reviews within the last two months — actively trading",
                              "static"))
        elif days > 365:
            sig.append(Signal("review_recency", -14.0,
                              f"no review in {days // 30} months — may be winding down"))

    if rating >= 4.3 and rc >= 10:
        sig.append(Signal("quality", w["quality"],
                          f"{rating} stars — they are good at the work and have "
                          f"something worth showing", "static"))
    elif 0 < rating < 3.5 and rc >= 10:
        sig.append(Signal("quality", -10.0,
                          f"{rating} stars — a website amplifies a reputation problem"))

    if lead.get("phone"):
        sig.append(Signal("reachable", w["reachable"], "phone number on file"))
    else:
        sig.append(Signal("reachable", -20.0, "no phone — walk-in only"))

    # -- behaviour, once you have made contact ------------------------------
    # These dominate everything above. Nothing you infer from a listing beats
    # knowing they opened the preview four times.
    if behaviour:
        views = behaviour.get("preview_views", 0)
        if views >= 3:
            sig.append(Signal("preview_opened", w["preview_opened"],
                              f"opened their preview {views} times — they are "
                              f"picturing it live", "behaviour"))
        elif views >= 1:
            sig.append(Signal("preview_opened", w["preview_opened"] * 0.4,
                              "opened the preview", "behaviour"))
        if behaviour.get("unique_ips", 0) >= 2:
            sig.append(Signal("preview_reshared", w["preview_reshared"],
                              "preview opened from more than one device — they "
                              "showed someone, usually a partner", "behaviour"))
        if behaviour.get("asked_price"):
            sig.append(Signal("asked_price", w["asked_price"],
                              "asked what it costs", "behaviour"))
        if behaviour.get("answered"):
            sig.append(Signal("answered", w["answered"], "answered the phone",
                              "behaviour"))
        if behaviour.get("no_answer_count", 0) >= 3:
            sig.append(Signal("no_answer_x3", w["no_answer_x3"],
                              "three attempts, no answer", "behaviour"))
        dst = behaviour.get("days_since_touch")
        if dst and dst > 21 and views == 0:
            sig.append(Signal("went_quiet", w["went_quiet"],
                              f"quiet for {dst} days after contact", "behaviour"))

    # -- combine -------------------------------------------------------------
    raw = sum(s.points for s in sig)
    mult = season_multiplier(cat, month)
    if mult != 1.0:
        note = "in season — call now" if mult > 1 else "out of season — park until spring"
        sig.append(Signal("season", 0.0,
                          f"{cat or 'this trade'} {note}", "static"))
    raw *= mult

    # squash to 0-100. Deliberately wide: a great listing alone should top out
    # in the mid-80s so that behavioural evidence still has room to move the
    # number. Someone who opened their preview twice outranks someone who
    # merely looks good on paper.
    score = int(round(100 / (1 + math.exp(-(raw - 34) / 26))))
    band = ("hot" if score >= 72 else
            "warm" if score >= 52 else
            "cool" if score >= 32 else "cold")

    return Propensity(score=score, band=band, signals=sig,
                      opener=_opener(lead, sig))


def _opener(lead: dict, sig: list[Signal]) -> str:
    """The line to lead with. Pick the strongest positive static signal."""
    name = lead.get("name", "there")
    top = max((s for s in sig if s.points > 0 and s.kind in ("static", "market")),
              key=lambda s: s.points, default=None)
    if not top:
        return f"Hi, is the owner of {name} around?"
    lines = {
        "competitors_online":
            f"I build websites for local trades. I noticed most of the other shops "
            f"around here have one and {name} doesn't — so I went ahead and built "
            f"you one. Can I show you?",
        "demand_no_capture":
            f"You've got {lead.get('review_count')} reviews on Google, which is more "
            f"than almost anyone around here, and no website to send those people to. "
            f"I built you one — two minutes?",
        "socials_only":
            f"I saw your Facebook page. You're already doing the work of being online, "
            f"you're just doing it on rented land. I built you a proper site — can I "
            f"show you what it looks like?",
        "job_value":
            f"One job pays for this several times over. I already built the site — "
            f"you can look at it and tell me no.",
    }
    return lines.get(top.key, f"Hi, is the owner of {name} around?")


# --------------------------------------------------------------------------
# Learning from outcomes
# --------------------------------------------------------------------------
MIN_OUTCOMES = 40


def recalibrate(con, shrink: float = 0.5) -> dict:
    """Reweight signals against your actual close rate.

    Logistic regression, pure Python, L2 regularised, on the signals that
    fired for every lead that reached a terminal state. Returns updated
    weights blended with the defaults -- `shrink` is how far to trust the
    data over the priors.

    Refuses under MIN_OUTCOMES. With thirty samples and thirteen features
    you are fitting noise, and a confidently wrong call list is worse than
    an unranked one.
    """
    rows = con.execute(
        """SELECT signals_json, stage FROM leads
           WHERE stage IN ('sold','dead') AND signals_json IS NOT NULL"""
    ).fetchall()
    if len(rows) < MIN_OUTCOMES:
        return {"_status": f"need {MIN_OUTCOMES} outcomes, have {len(rows)}",
                "_weights": WEIGHTS}

    keys = sorted(WEIGHTS)
    X, y = [], []
    for r in rows:
        fired = {s["key"]: s["points"] for s in json.loads(r["signals_json"])}
        X.append([1.0 if k in fired else 0.0 for k in keys])
        y.append(1.0 if r["stage"] == "sold" else 0.0)

    b = [0.0] * len(keys)
    bias, lr, lam = 0.0, 0.08, 0.05
    for _ in range(3000):
        gb = [0.0] * len(keys)
        gbias = 0.0
        for xi, yi in zip(X, y):
            z = bias + sum(bj * xj for bj, xj in zip(b, xi))
            p = 1 / (1 + math.exp(-max(-30, min(30, z))))
            e = p - yi
            gbias += e
            for j, xj in enumerate(xi):
                gb[j] += e * xj
        n = len(X)
        bias -= lr * gbias / n
        for j in range(len(b)):
            b[j] -= lr * (gb[j] / n + lam * b[j])

    # log-odds -> points on the same scale as the hand-set weights
    learned = {k: max(-30.0, min(30.0, coef * 12)) for k, coef in zip(keys, b)}
    blended = {k: WEIGHTS[k] * (1 - shrink) + learned[k] * shrink for k in keys}
    blended["_status"] = f"fitted on {len(rows)} outcomes"
    return blended


def call_list(con, limit: int = 15, band: str = "hot") -> list:
    """Today's calls: highest propensity, not contacted in the last 7 days."""
    rows = con.execute(
        """SELECT * FROM leads
           WHERE stage IN ('new','queued','contacted','interested')
             AND (last_touch IS NULL OR julianday('now') - julianday(last_touch) > 7)
           ORDER BY propensity DESC LIMIT ?""", (limit,)).fetchall()
    return [r for r in rows if not band or r["propensity_band"] == band] or list(rows)
