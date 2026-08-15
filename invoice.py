"""Invoices: raised here, printed here, paid into the operator's own bank.

There is no billing API behind this and that is the point. Stripe is optional
in this tool and always has been — `billing.py` only ever *reads* from it — but
plenty of clients pay by e-transfer or a cheque handed over at the counter, and
until now there was nowhere to record that. An operator with six clients and a
notebook is not going to open a Stripe account to send one $149 invoice, and if
the only way to bill somebody is through a service that takes a percentage and
wants a business number, the invoice does not get sent at all.

So: the numbers live in the same SQLite file as everything else, the document
is an HTML page printed from the browser, and the money arrives however the
operator and the client already agreed. Nothing here talks to the network.

Six decisions hold this together, and each is a bug that would otherwise be
found by a client rather than by a test.

  - **Cents, as integers.** Never floats. A subtotal that comes out a cent
    under the sum of its own lines is an argument with somebody who is holding
    the paper, and 0.1 + 0.2 is exactly how that happens. Dollars exist in this
    module in two places only: what the operator types, and what gets printed.
  - **Tax once, on the taxable subtotal.** Not per line and then summed —
    rounding each line's tax separately drifts a cent or two away from the
    figure a client gets when they check the total themselves.
  - **No registration number, no tax.** You may not collect GST/HST unless you
    are registered for it, and a small supplier under $30k a year usually is
    not. So tax is charged only when a number has been entered, and the number
    is printed on the invoice — which is also what the CRA requires of anyone
    who does charge it.
  - **The client is copied onto the invoice, not joined to it.** `bill_to_name`
    and the address are a snapshot taken the day it was raised. `db.purge_stale`
    blanks a lead's name and address 30 days after Google supplied them, and an
    invoice has to say the same thing in a year's time as it did the day it was
    sent.
  - **Numbers are never reused and invoices are never deleted.** A wrong one is
    voided, keeps its number, and says why. A gap in a numbered run is the
    first thing an auditor asks about.
  - **Overdue is arithmetic, not a column.** A stored flag is wrong every
    morning until something rewrites it. `is_overdue()` compares the due date
    to today, every time it is asked.

Printing is the browser's job, exactly as it is for the leave-behind: every
machine can print a page to PDF, and a PDF library would be a heavy dependency,
a font problem, and a new way for the total to come out in the wrong place.
"""
from __future__ import annotations

import csv
import datetime
import io
import os
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterable, Optional, Sequence

from jinja2 import Environment, FileSystemLoader, select_autoescape

import db

ROOT = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(ROOT, "templates")

# Beside the database and the executable, like `leads.db` and `board.html`, and
# deliberately *not* inside `sites/`: `deploy.py` uploads what is in a site
# directory and `offboard.bundle` zips it for the client. An invoice is neither
# publishable nor theirs to receive a copy of every other client's.
INVOICES_DIR = os.environ.get("LEADSMITH_INVOICES") or os.path.join(ROOT, "invoices")

STATUSES = ["draft", "sent", "paid", "void"]

# How the money arrived. Short on purpose — a list nobody uses consistently is
# a list that tells you nothing at the end of the year.
METHODS = ["e-transfer", "cheque", "cash", "card", "other"]

DEFAULTS: dict[str, Any] = {
    "prefix": "",              # "" gives 2026-001; "LS-" gives LS-2026-001
    "terms_days": 14,          # net 14. Two weeks is long enough to be polite
    "tax_label": "HST",        # what it is called where the operator is
    "tax_rate": 13.0,          # Ontario. Ignored entirely without a number
    "tax_number": "",          # the registration. Empty means: charge no tax
    "currency": "CAD",
    "e_transfer_email": "",
    "cheque_payable_to": "",
    "footer": "",
}


class InvoiceError(RuntimeError):
    """Written for the operator, not for a log file."""


# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------
def to_cents(amount: Any) -> int:
    """Whatever the operator typed -> integer cents.

    Accepts 149, "149", "149.00", "$1,200.50". Decimal rather than float, and
    half-up rather than Python's default half-to-even: a client checking
    $10.005 expects a cent up, and bankers' rounding is a surprise nobody
    invited to a two-line invoice.
    """
    if isinstance(amount, bool):                     # bool is an int; not money
        raise InvoiceError(f"{amount!r} is not an amount.")
    text = str(amount).strip().replace("$", "").replace(",", "").replace(" ", "")
    if not text:
        raise InvoiceError("That amount is empty.")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise InvoiceError(f"'{amount}' is not an amount I can read. "
                           f"Try something like 149 or 1200.50.") from exc
    return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def money(cents: int, currency: str = "") -> str:
    """Cents -> "$1,234.56", negatives as "-$1,234.56"."""
    sign = "-" if cents < 0 else ""
    body = f"${abs(int(cents)) / 100:,.2f}"
    return f"{sign}{body}{' ' + currency if currency else ''}"


def line_cents(quantity: Any, unit_cents: int) -> int:
    """What one line comes to. Quantity may be fractional — 1.5 hours."""
    product = Decimal(str(quantity)) * Decimal(int(unit_cents))
    return int(product.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def tax_on(taxable_cents: int, rate: Any) -> int:
    """Tax on a subtotal, rounded half-up to the cent.

    One function, so the figure the app shows while the operator is typing is
    arrived at exactly the way the figure printed on the paper is.
    """
    rate = Decimal(str(rate or 0))
    if not rate:
        return 0
    return int((Decimal(int(taxable_cents)) * rate / 100).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass
class Totals:
    subtotal_cents: int = 0
    taxable_cents: int = 0
    tax_cents: int = 0
    total_cents: int = 0
    paid_cents: int = 0

    @property
    def balance_cents(self) -> int:
        return self.total_cents - self.paid_cents

    @property
    def settled(self) -> bool:
        """Paid in full, or more. Overpayment counts — the debt is gone."""
        return self.balance_cents <= 0

    @property
    def credit_cents(self) -> int:
        """What they overpaid, if they did. Shown rather than swallowed."""
        return max(0, -self.balance_cents)


def totals(invoice_row: Any, lines: Sequence[Any],
           payments: Sequence[Any] = ()) -> Totals:
    """Add it up. The only place in the tool that decides what is owed."""
    result = Totals()
    for line in lines:
        amount = line_cents(line["quantity"], line["unit_cents"])
        result.subtotal_cents += amount
        if line["taxable"]:
            result.taxable_cents += amount

    result.tax_cents = tax_on(result.taxable_cents, invoice_row["tax_rate"])
    result.total_cents = result.subtotal_cents + result.tax_cents
    result.paid_cents = sum(int(p["amount_cents"]) for p in payments)
    return result


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def settings_from(cfg: Optional[dict[str, Any]]) -> dict[str, Any]:
    """The `invoicing` block of config.json, over the defaults."""
    settings = {**DEFAULTS, **((cfg or {}).get("invoicing") or {})}
    settings["terms_days"] = max(0, int(settings.get("terms_days") or 0))
    settings["tax_rate"] = float(settings.get("tax_rate") or 0)
    return settings


def tax_from(settings: dict[str, Any]) -> tuple[str, float, str]:
    """(label, rate, number) — and the rate is zero without a number.

    Charging GST/HST you are not registered to collect is not a paperwork slip,
    it is collecting somebody else's money. The rate in config is what to
    charge *if* registered; entering the number is what turns it on. Somebody
    who registers later types their number once and every invoice after it is
    correct — including this one, which is why the rate stays in config rather
    than being deleted.
    """
    number = str(settings.get("tax_number") or "").strip()
    if not number:
        return "", 0.0, ""
    return str(settings.get("tax_label") or "Tax"), float(settings["tax_rate"]), number


# ---------------------------------------------------------------------------
# Lines
# ---------------------------------------------------------------------------
def _looks_numeric(text: str) -> bool:
    try:
        Decimal(text.strip().replace("$", "").replace(",", ""))
    except (InvalidOperation, AttributeError):
        return False
    return True


def parse_line(text: str) -> dict[str, Any]:
    """`"Extra page:250"` or `"Photography:120:2.5"` -> a line.

    Split from the right, so a description can contain a colon — "Rebuild:
    kitchen page:400" is something somebody will type eventually. The
    three-part form is only read as one when *both* of the last two fields are
    numbers, which is what stops "Rebuild: kitchen page:400" being read as a
    quantity of 400 of something called "Rebuild".

    A negative amount is allowed and is how a discount or a goodwill credit
    goes on: "Late last month, sorry:-50".
    """
    raw = (text or "").strip()
    if ":" not in raw:
        raise InvoiceError(
            f'"{text}" needs a price. Write it as "what it is for:amount", '
            f'e.g. "Extra page:250" — or "Photography:120:2.5" for 2.5 of them.')

    head, _, last = raw.rpartition(":")
    quantity = 1.0
    if ":" in head:
        before, _, middle = head.rpartition(":")
        if _looks_numeric(middle) and _looks_numeric(last):
            head, amount_cents = before, to_cents(middle)
            quantity = float(last.strip())
        else:
            amount_cents = to_cents(last)
    else:
        amount_cents = to_cents(last)

    description = head.strip()
    if not description:
        raise InvoiceError(f'"{text}" has a price but nothing to say what it '
                           f'is for. The client reads this line.')
    if quantity <= 0:
        raise InvoiceError(f"A quantity of {quantity} on \"{description}\" "
                           f"would bill nothing.")
    return {"description": description, "unit_cents": amount_cents,
            "quantity": quantity, "taxable": True}


def period_label(period: str) -> str:
    """"2026-08" -> "August 2026". The client reads this, not an ISO string."""
    try:
        when = datetime.date.fromisoformat(f"{period}-01")
    except (TypeError, ValueError):
        return period or ""
    return when.strftime("%B %Y")


def period_of(when: Optional[datetime.date] = None) -> str:
    when = when or datetime.date.today()
    return f"{when.year:04d}-{when.month:02d}"


def subscription_lines(con, sub: Any, period: str) -> list[dict[str, Any]]:
    """What a client's month comes to, as lines.

    The setup fee rides on the first invoice a client ever gets and never
    again — `db.has_invoices` counts voided ones too, so re-raising a botched
    first invoice does not charge for the build twice.
    """
    lines: list[dict[str, Any]] = []
    setup = to_cents(sub["setup_fee"] or 0)
    if setup and not db.has_invoices(con, sub["place_id"]):
        lines.append({"description": "Website build — one-off setup",
                      "unit_cents": setup, "quantity": 1.0, "taxable": True})
    monthly = to_cents(sub["monthly"] or 0)
    if monthly:
        lines.append({
            "description": f"Website hosting and care — {period_label(period)}",
            "unit_cents": monthly, "quantity": 1.0, "taxable": True})
    return lines


# ---------------------------------------------------------------------------
# Raising one
# ---------------------------------------------------------------------------
def create(con, place_id: str, lines: Sequence[dict[str, Any]], *,
           cfg: Optional[dict[str, Any]] = None, period: Optional[str] = None,
           note: str = "", bill_to_name: str = "", bill_to_email: str = "",
           terms_days: Optional[int] = None) -> str:
    """Raise a draft invoice against a client. Returns its number.

    Everything about who they are is copied onto it here, once. After this the
    invoice does not care what happens to the lead row.
    """
    if not lines:
        raise InvoiceError("An invoice with no lines on it says the client "
                           "owes nothing. Add at least one line.")

    lead = db.get(con, place_id)
    if lead is None:
        raise InvoiceError(f"No lead with id {place_id}, so there is nobody to "
                           f"invoice.")

    name = (bill_to_name or lead["name"] or "").strip()
    if not name or name.startswith("(purged"):
        # Their name expired out of the cache 30 days after Google gave it to
        # us. That is fine for a lead and useless on an invoice.
        raise InvoiceError(
            f"We no longer hold a business name for {place_id} — Google's "
            f"copy expired. Pass the name to bill explicitly (the CLI takes "
            f"--name), or re-scan the town to refill it.")

    settings = settings_from(cfg)
    label, rate, number_of_ours = tax_from(settings)
    sub = db.subscription(con, place_id)
    currency = (sub["currency"] if sub and sub["currency"]
                else settings["currency"])

    if period and period in db.invoiced_periods(con, place_id):
        raise InvoiceError(
            f"{name} already has an invoice for {period_label(period)}. "
            f"Void that one first if it is wrong — billing the same month "
            f"twice is the mistake this refuses to make for you.")

    email = (bill_to_email or (lead["consent_email"] if "consent_email"
                               in lead.keys() else "") or "").strip()
    number = db.next_invoice_number(con, settings["prefix"])
    db.create_invoice(
        con, number=number, place_id=place_id, bill_to_name=name,
        bill_to_address=(lead["address"] or "") if "address" in lead.keys() else "",
        bill_to_email=email, period=period, currency=currency,
        terms_days=int(settings["terms_days"] if terms_days is None else terms_days),
        tax_label=label, tax_rate=rate, tax_number=number_of_ours,
        note=note, lines=lines)
    return number


def _must_exist(con, number: str) -> Any:
    row = db.invoice(con, number)
    if row is None:
        raise InvoiceError(f"There is no invoice {number}.")
    return row


def send(con, number: str, *, today: Optional[datetime.date] = None) -> Any:
    """Mark a draft as issued and set the clock running on it.

    The due date is worked out once, here, from the terms stored on the invoice
    rather than from whatever config says today — so a client on 30-day terms
    stays on 30-day terms after the default changes.
    """
    row = _must_exist(con, number)
    if row["status"] == "void":
        raise InvoiceError(f"Invoice {number} was voided"
                           f"{' — ' + row['void_reason'] if row['void_reason'] else ''}."
                           f" Raise a new one.")
    if row["status"] != "draft":
        raise InvoiceError(
            f"Invoice {number} was already issued on {row['issued_at']}. "
            f"Print it again with `leadsmith invoice show {number} --open`; "
            f"changing an invoice a client is already holding is how two "
            f"different documents end up with the same number.")

    day = today or datetime.date.today()
    due = day + datetime.timedelta(days=int(row["terms_days"] or 0))
    db.set_invoice_status(con, int(row["id"]), "sent",
                          issued_at=day.isoformat(), due_at=due.isoformat(),
                          sent_at=db.now())
    return db.invoice(con, number)


def editable(row: Any) -> bool:
    """Only a draft. Once it has been sent, the client has a copy of it."""
    return row["status"] == "draft"


def guard_editable(row: Any) -> None:
    if not editable(row):
        raise InvoiceError(
            f"Invoice {row['number']} has already been {row['status']}, so its "
            f"lines cannot change — the client is holding a copy of them. Void "
            f"it and raise a new one.")


def pay(con, number: str, amount: Any, *, method: str = "e-transfer",
        reference: str = "", note: str = "",
        at: Optional[datetime.date] = None) -> Totals:
    """Record money that arrived. Returns the totals as they now stand.

    Partial payments are ordinary: half up front and half on completion is how
    a lot of these are agreed. The status follows the arithmetic rather than
    the operator's memory — the invoice is paid when the balance reaches zero,
    and not before.
    """
    row = _must_exist(con, number)
    if row["status"] == "void":
        raise InvoiceError(f"Invoice {number} was voided, so nothing should "
                           f"have been paid against it. Check what the money "
                           f"was actually for.")
    if row["status"] == "draft":
        raise InvoiceError(
            f"Invoice {number} has not been sent yet. Send it first — a "
            f"payment against a document nobody has seen is a puzzle in a "
            f"year's time.")
    if method and method not in METHODS:
        raise InvoiceError(f"Unknown payment method '{method}'. "
                           f"Use one of: {', '.join(METHODS)}")

    cents = to_cents(amount)
    if cents <= 0:
        raise InvoiceError("A payment has to be more than nothing.")

    db.add_payment(con, int(row["id"]), cents, method=method,
                   reference=reference, note=note,
                   at=(at or datetime.date.today()).isoformat())

    lines = db.invoice_lines(con, int(row["id"]))
    payments = db.invoice_payments(con, int(row["id"]))
    result = totals(row, lines, payments)
    if result.settled:
        db.set_invoice_status(con, int(row["id"]), "paid",
                              paid_at=(at or datetime.date.today()).isoformat())
    return result


def void(con, number: str, reason: str) -> Any:
    """Cancel an invoice without erasing it.

    Deleting would leave a hole in the numbering, and a hole is the first thing
    anybody looking at a year of invoices asks about. A void keeps its number,
    says why, and frees its month so the run can raise it again.
    """
    row = _must_exist(con, number)
    if not (reason or "").strip():
        raise InvoiceError("Say why it is being voided. In a year that "
                           "sentence is the only thing that will explain it.")
    if row["status"] == "void":
        raise InvoiceError(f"Invoice {number} is already void.")
    if db.paid_cents(con, int(row["id"])):
        raise InvoiceError(
            f"Invoice {number} has money against it, so voiding it would "
            f"quietly erase a payment that actually arrived. Refund it and "
            f"record that instead, or leave this one alone and raise a credit.")

    db.set_invoice_status(con, int(row["id"]), "void",
                          voided_at=db.now(), void_reason=reason.strip())
    return db.invoice(con, number)


# ---------------------------------------------------------------------------
# Reading them back
# ---------------------------------------------------------------------------
def is_overdue(row: Any, balance_cents: int,
               today: Optional[datetime.date] = None) -> bool:
    if row["status"] != "sent" or balance_cents <= 0 or not row["due_at"]:
        return False
    return row["due_at"] < (today or datetime.date.today()).isoformat()


def days_overdue(row: Any, today: Optional[datetime.date] = None) -> int:
    if not row["due_at"]:
        return 0
    try:
        due = datetime.date.fromisoformat(row["due_at"])
    except ValueError:
        return 0
    return max(0, ((today or datetime.date.today()) - due).days)


@dataclass
class Owed:
    outstanding_cents: int = 0
    overdue_cents: int = 0
    open_count: int = 0
    overdue_count: int = 0
    oldest: str = ""              # the number of the one that has waited longest


def outstanding(con, today: Optional[datetime.date] = None) -> Owed:
    """What is out there unpaid, for the board and the Money page.

    Computed through `totals()` — the same function that prints the figure onto
    the invoice — rather than re-derived in SQL. Two implementations of "what
    do they owe" is one too many, and the pair disagreeing by a cent is exactly
    the sort of thing nobody notices until a client points at it.
    """
    day = today or datetime.date.today()
    rows = db.invoices(con, status="sent")
    lines = db.lines_by_invoice(con, [int(r["id"]) for r in rows])

    owed = Owed()
    oldest_due: Optional[tuple[str, str]] = None
    for row in rows:
        result = totals(row, lines.get(int(row["id"]), []))
        result.paid_cents = int(row["paid_cents"] or 0)
        balance = result.balance_cents
        if balance <= 0:
            continue
        owed.outstanding_cents += balance
        owed.open_count += 1
        if is_overdue(row, balance, day):
            owed.overdue_cents += balance
            owed.overdue_count += 1
            # Ties broken by number, so a month's worth of invoices sharing one
            # due date names the first one raised rather than whichever the
            # query happened to reach first.
            key = (row["due_at"] or "", row["number"])
            if oldest_due is None or key < oldest_due:
                oldest_due = key
                owed.oldest = row["number"]
    return owed


# ---------------------------------------------------------------------------
# The monthly run
# ---------------------------------------------------------------------------
@dataclass
class RunResult:
    period: str = ""
    created: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    total_cents: int = 0
    sent: bool = False


def run(con, cfg: Optional[dict[str, Any]] = None, *, period: Optional[str] = None,
        issue: bool = True, today: Optional[datetime.date] = None) -> RunResult:
    """Raise this month's invoice for every active client, and issue them.

    `issue=False` leaves them as drafts to look over first. Issuing is the
    default because an invoice nobody sent is an invoice nobody pays, and a
    monthly run that quietly produces twelve drafts is a month of no income
    that looks like a month of work.

    Safe to press twice, which is the entire reason it is a single command: the
    partial unique index in the schema and the check here both refuse a second
    invoice for a client's month, so a nervous operator running it again
    produces nothing rather than double-billing everybody.

    A month is billed whole. There is no proration — the offer is a 12-month
    term that starts when they sign, and inventing a part-month makes the first
    invoice an argument about a fortnight.
    """
    day = today or datetime.date.today()
    result = RunResult(period=period or period_of(day), sent=issue)

    for sub in db.subscriptions(con, status="active"):
        name = (sub["name"] if "name" in sub.keys() else None) or sub["place_id"]
        if result.period in db.invoiced_periods(con, sub["place_id"]):
            result.skipped.append((name, f"already invoiced for "
                                         f"{period_label(result.period)}"))
            continue

        lines = subscription_lines(con, sub, result.period)
        if not lines:
            result.skipped.append((name, "nothing to bill — their plan is $0"))
            continue

        try:
            number = create(con, sub["place_id"], lines, cfg=cfg,
                            period=result.period)
        except InvoiceError as exc:
            # One client with an expired name must not stop the other eleven
            # from being billed.
            result.skipped.append((name, str(exc).split("\n")[0]))
            continue

        if issue:
            send(con, number, today=day)
        row = db.invoice(con, number)
        result.created.append(number)
        result.total_cents += totals(
            row, db.invoice_lines(con, int(row["id"]))).total_cents
    return result


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------
_env: Optional[Environment] = None


def env() -> Environment:
    global _env
    if _env is None:
        _env = Environment(loader=FileSystemLoader(TEMPLATE_DIR),
                           autoescape=select_autoescape(["html"]),
                           trim_blocks=True, lstrip_blocks=True)
    return _env


def document(con, number: str, *, operator: Optional[dict[str, Any]] = None,
             cfg: Optional[dict[str, Any]] = None,
             today: Optional[datetime.date] = None) -> dict[str, Any]:
    """Everything the page needs, already formatted. The template stays dumb.

    Money is turned into strings here rather than in Jinja because a filter
    that formats money is a filter that can be forgotten on one line, and the
    line it gets forgotten on will be the total.
    """
    row = _must_exist(con, number)
    day = today or datetime.date.today()
    invoice_id = int(row["id"])
    lines = db.invoice_lines(con, invoice_id)
    payments = db.invoice_payments(con, invoice_id)
    sums = totals(row, lines, payments)
    settings = settings_from(cfg)
    currency = row["currency"] or settings["currency"]
    balance = sums.balance_cents

    return {
        "number": row["number"],
        "status": row["status"],
        "currency": currency,
        "issued": _long_date(row["issued_at"]) or "not yet issued",
        "due": _long_date(row["due_at"]),
        "period": period_label(row["period"]) if row["period"] else "",
        "note": row["note"] or "",
        "void_reason": row["void_reason"] or "",
        "overdue": is_overdue(row, balance, day),
        "days_overdue": days_overdue(row, day) if is_overdue(row, balance, day) else 0,
        "bill_to": {
            "name": row["bill_to_name"],
            "address": row["bill_to_address"] or "",
            "email": row["bill_to_email"] or "",
        },
        "operator": operator or {},
        "tax": {"label": row["tax_label"] or "", "rate": row["tax_rate"] or 0,
                "number": row["tax_number"] or ""},
        "lines": [{
            "description": line["description"],
            "quantity": _tidy_quantity(line["quantity"]),
            "show_quantity": float(line["quantity"]) != 1.0,
            "unit": money(int(line["unit_cents"])),
            "amount": money(line_cents(line["quantity"], line["unit_cents"])),
            "taxable": bool(line["taxable"]),
        } for line in lines],
        "payments": [{
            "at": _long_date(payment["at"]),
            "amount": money(int(payment["amount_cents"])),
            "method": payment["method"] or "",
            "reference": payment["reference"] or "",
        } for payment in payments],
        "totals": {
            "subtotal": money(sums.subtotal_cents),
            "tax": money(sums.tax_cents),
            "total": money(sums.total_cents, currency),
            "paid": money(sums.paid_cents),
            "balance": money(max(0, balance), currency),
            "credit": money(sums.credit_cents, currency),
            "has_tax": bool(sums.tax_cents) or bool(row["tax_rate"]),
            "has_payments": bool(payments),
            "settled": sums.settled,
            "credit_cents": sums.credit_cents,
        },
        "pay_to": {
            # Only what was configured, never a guess at it. "Payable to" has
            # to match the name on the bank account exactly or the cheque
            # bounces back, and the operator's legal name is close enough to
            # look right and be wrong.
            "e_transfer": settings["e_transfer_email"],
            "cheque": settings["cheque_payable_to"],
            "address": (operator or {}).get("mailing_address", ""),
        },
        "footer": settings["footer"],
        "printed": day.strftime("%-d %B %Y"),
    }


def _tidy_quantity(quantity: Any) -> str:
    """1.0 -> "1", 2.5 -> "2.5". Nobody writes "1.0 site"."""
    value = float(quantity)
    return str(int(value)) if value == int(value) else f"{value:g}"


def _long_date(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        return datetime.date.fromisoformat(str(value)[:10]).strftime("%-d %B %Y")
    except ValueError:
        return str(value)


def render(con, number: str, **kwargs: Any) -> str:
    """The invoice as one self-contained HTML page."""
    return env().get_template("invoice.html").render(
        **document(con, number, **kwargs))


def path_for(number: str, directory: Optional[str] = None) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "-", number)
    return os.path.join(directory or INVOICES_DIR, f"invoice-{safe}.html")


def write(number: str, html: str, directory: Optional[str] = None) -> str:
    path = path_for(number, directory)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return path


# ---------------------------------------------------------------------------
# For the bookkeeper
# ---------------------------------------------------------------------------
LEDGER_HEADER = ["number", "status", "issued", "due", "client", "period",
                 "subtotal", "tax", "total", "paid", "balance", "currency"]


def export_csv(con, *, year: Optional[int] = None,
               rows: Optional[Iterable[Any]] = None) -> str:
    """Every invoice as a CSV an accountant can open without asking questions.

    Dollars here, not cents — this is the one output read by somebody who did
    not write the tool, and a column of 14900s would be read as fourteen
    thousand dollars once and only once.
    """
    rows = list(rows if rows is not None else db.invoices(con, year=year))
    lines = db.lines_by_invoice(con, [int(r["id"]) for r in rows])

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(LEDGER_HEADER)
    for row in rows:
        sums = totals(row, lines.get(int(row["id"]), []))
        sums.paid_cents = int(row["paid_cents"] or 0)
        writer.writerow([
            row["number"], row["status"], row["issued_at"] or "",
            row["due_at"] or "", row["bill_to_name"], row["period"] or "",
            f"{sums.subtotal_cents / 100:.2f}", f"{sums.tax_cents / 100:.2f}",
            f"{sums.total_cents / 100:.2f}", f"{sums.paid_cents / 100:.2f}",
            f"{sums.balance_cents / 100:.2f}", row["currency"] or "",
        ])
    return buffer.getvalue()
