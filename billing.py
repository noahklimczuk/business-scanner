"""What the clients are worth, and whether Stripe agrees.

The division of labour here is deliberate and it is the whole design:

    Stripe holds the money. This tool holds the opinion about the money.

So nothing here creates a subscription, charges a card, or changes a price. You
set the two plans up once in the Stripe dashboard as payment links and send a
client at one. Writing a billing integration means writing the failure cases —
a card declining mid-cycle, a proration, a dispute, a webhook arriving twice —
and getting any of them wrong costs a client rather than a test run.

What the tool does instead is the part Stripe is bad at: telling you, in one
place, that the roofer you launched in March stopped paying in June and nobody
noticed. `sync()` reads subscriptions back out of Stripe and reconciles them
against what we think is true. Every disagreement is a real-world event —
a cancellation, a failed payment, a subscription set up and never recorded.

MRR is computed from the local records because that is what you are owed;
`sync()` is what tells you whether you are being paid it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import requests

import db

STRIPE_ROOT = "https://api.stripe.com/v1"
TIMEOUT = 30

# The guide's two offers. `no-money-down` closes far more often with small
# trades, because the objection is almost never "a website isn't worth $1,200",
# it is "I don't have $1,200 sitting around this month".
PLANS = {
    "no-money-down": {"setup_fee": 0.0, "monthly": 149.0, "term_months": 12},
    "standard": {"setup_fee": 1200.0, "monthly": 60.0, "term_months": 12},
}

# Stripe statuses that mean money is not arriving. `past_due` is the one that
# matters most: the subscription still exists, so nothing looks wrong until you
# read the bank statement.
UNHEALTHY = {"past_due", "unpaid", "canceled", "incomplete_expired", "paused"}


class BillingError(RuntimeError):
    """Written for the operator, not a log file."""


@dataclass
class Money:
    mrr: float = 0.0
    clients: int = 0
    paused: int = 0
    cancelled: int = 0
    setup_collected: float = 0.0
    currency: str = "CAD"

    @property
    def arr(self) -> float:
        return self.mrr * 12

    @property
    def average(self) -> float:
        return self.mrr / self.clients if self.clients else 0.0


@dataclass
class Discrepancy:
    place_id: str
    name: str
    ours: str
    theirs: str
    detail: str


@dataclass
class SyncResult:
    checked: int = 0
    matched: int = 0
    problems: list[Discrepancy] = field(default_factory=list)
    unlinked: list[str] = field(default_factory=list)


def plan_terms(plan: str) -> dict[str, Any]:
    if plan not in PLANS:
        raise BillingError(f"Unknown plan '{plan}'. Use one of: "
                           f"{', '.join(PLANS)}")
    return dict(PLANS[plan])


def summarise(rows: list[Any]) -> Money:
    """Recurring revenue from what we believe we are owed.

    Setup fees are counted separately and never folded into MRR. A $1,200 setup
    is not $100/month of anything — treating it as recurring is how a business
    convinces itself it is twice the size it is.
    """
    money = Money()
    for row in rows:
        status = row["status"]
        if status == "active":
            money.mrr += float(row["monthly"] or 0)
            money.clients += 1
            money.setup_collected += float(row["setup_fee"] or 0)
        elif status == "paused":
            money.paused += 1
        elif status == "cancelled":
            money.cancelled += 1
        if row["currency"]:
            money.currency = row["currency"]
    return money


def unit_costs(con) -> dict[str, float]:
    """What the last 30 days actually cost, against what they earned.

    On the board next to MRR, because the guide's whole claim is that marginal
    cost per client is about a dollar a month, and a number that is never
    checked is a number that quietly stops being true.
    """
    # Not "copy": Jinja resolves `costs.copy` to dict.copy, the method,
    # and renders a bound-method repr or blows up on arithmetic. The key
    # is named so that cannot happen again.
    return {
        "places": db.spend_since(con, 30),
        "generation": db.generation_spend(con, 30),
    }


# ---------------------------------------------------------------------------
# Stripe, read-only
# ---------------------------------------------------------------------------
def fetch_subscriptions(api_key: str, getter=requests.get) -> dict[str, dict[str, Any]]:
    """Every subscription Stripe knows about, keyed by id.

    Read-only, and deliberately the only Stripe call in the codebase. Paginates
    because the operator who needs this most is the one with sixty clients.
    """
    if not api_key:
        raise BillingError(
            "No Stripe key. Put `stripe_secret_key` in config.json — a "
            "restricted key with read access to subscriptions is enough, and "
            "is what you want: nothing here needs write access.")

    out: dict[str, dict[str, Any]] = {}
    starting_after: Optional[str] = None
    for _ in range(20):                       # 2,000 subscriptions is plenty
        params: dict[str, Any] = {"limit": 100, "status": "all"}
        if starting_after:
            params["starting_after"] = starting_after
        response = getter(f"{STRIPE_ROOT}/subscriptions",
                          params=params, auth=(api_key, ""), timeout=TIMEOUT)
        if response.status_code == 401:
            raise BillingError("Stripe rejected that key (401). Check "
                               "`stripe_secret_key` in config.json.")
        if not getattr(response, "ok", False):
            raise BillingError(f"Stripe returned {response.status_code}: "
                               f"{response.text[:200]}")
        payload = response.json()
        for item in payload.get("data", []):
            out[item["id"]] = item
        if not payload.get("has_more"):
            break
        starting_after = payload["data"][-1]["id"] if payload.get("data") else None
        if not starting_after:
            break
    return out


def reconcile(rows: list[Any], remote: dict[str, dict[str, Any]]) -> SyncResult:
    """Every way our records and Stripe's can disagree.

    Each of these is a real event that happened while nobody was looking, which
    is exactly why it is worth a command:

      - we say active, Stripe says past_due   -> a card failed. Silent.
      - we say active, Stripe says canceled   -> they left and did not say.
      - we say cancelled, Stripe says active  -> we are still charging someone
                                                 who thinks they have gone,
                                                 which is worse than the others
      - the price does not match              -> a plan change nobody recorded
      - no Stripe id at all                   -> being paid by e-transfer, or
                                                 not being paid
    """
    result = SyncResult()
    for row in rows:
        name = row["name"] if "name" in row.keys() else row["place_id"]
        stripe_id = row["stripe_id"]
        if not stripe_id:
            result.unlinked.append(name)
            continue

        result.checked += 1
        theirs = remote.get(stripe_id)
        if theirs is None:
            result.problems.append(Discrepancy(
                row["place_id"], name, row["status"], "missing",
                f"Stripe has no subscription {stripe_id}."))
            continue

        their_status = theirs.get("status", "unknown")
        ours_active = row["status"] == "active"

        if ours_active and their_status in UNHEALTHY:
            result.problems.append(Discrepancy(
                row["place_id"], name, "active", their_status,
                "We think they are a client; Stripe is not collecting."
                if their_status != "canceled" else
                "They cancelled in Stripe and we never heard."))
            continue
        if not ours_active and their_status == "active":
            result.problems.append(Discrepancy(
                row["place_id"], name, row["status"], "active",
                "Stripe is still charging them. Cancel it before they notice."))
            continue

        expected = float(row["monthly"] or 0)
        charged = _monthly_amount(theirs)
        if expected and charged and abs(expected - charged) > 0.01:
            result.problems.append(Discrepancy(
                row["place_id"], name, f"${expected:,.2f}", f"${charged:,.2f}",
                "The price in Stripe is not the price we recorded."))
            continue
        result.matched += 1
    return result


def _monthly_amount(subscription: dict[str, Any]) -> float:
    """Stripe amounts are in cents, and yearly plans are not monthly ones."""
    items = (subscription.get("items") or {}).get("data") or []
    total = 0.0
    for item in items:
        price = item.get("price") or {}
        amount = price.get("unit_amount")
        if amount is None:
            continue
        quantity = item.get("quantity", 1) or 1
        interval = ((price.get("recurring") or {}).get("interval") or "month")
        count = (price.get("recurring") or {}).get("interval_count", 1) or 1
        monthly = (amount / 100.0) * quantity
        if interval == "year":
            monthly /= 12 * count
        elif interval == "week":
            monthly *= 52 / 12 / count
        elif interval == "day":
            monthly *= 365 / 12 / count
        elif count > 1:
            monthly /= count
        total += monthly
    return round(total, 2)


def sync(con, api_key: str, getter=requests.get) -> SyncResult:
    remote = fetch_subscriptions(api_key, getter)
    rows = db.subscriptions(con)
    result = reconcile(rows, remote)
    for row in rows:
        if row["stripe_id"] and row["stripe_id"] in remote:
            db.record_sync(con, row["place_id"],
                           remote[row["stripe_id"]].get("status", ""))
    return result
