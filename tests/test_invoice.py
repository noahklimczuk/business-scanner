"""Invoicing: the arithmetic, the rules, and the page that gets handed over.

Money is the one part of this tool where being nearly right is worse than
being broken — a total that is a cent out does not crash, it gets argued about
in front of a client. So the sums are tested at the cent, the tax is tested
against the case where per-line rounding would drift, and the monthly run is
tested by running it twice.
"""
import datetime
import os
import re

import pytest

import db
import invoice


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    connection = db.connect()
    yield connection
    connection.close()


def lead(con, place_id="p1", name="Klimczuk Roofing", **kw):
    db.upsert_lead(con, {"place_id": place_id, "name": name,
                         "category": "Roofing contractor", "website_kind": "none",
                         "address": "12 Main St, Newmarket, ON L3Y 0A0", **kw})
    return place_id


def client(con, place_id="p1", *, monthly=149.0, setup=0.0, **kw):
    lead(con, place_id, **kw)
    db.set_subscription(con, place_id, plan="no-money-down", monthly=monthly,
                        setup_fee=setup)
    return place_id


# A registered operator, so tax is on. See the tax tests for the other case.
CFG = {
    "invoicing": {"tax_label": "HST", "tax_rate": 13, "tax_number": "80012 3456 RT0001",
                  "e_transfer_email": "pay@example.ca",
                  "cheque_payable_to": "N. Klimczuk o/a Leadsmith"},
    "business": {"operator_name": "Noah", "legal_name": "N. Klimczuk o/a Leadsmith",
                 "phone": "+1 905 555 0123", "email": "noah@example.ca",
                 "mailing_address": "1 Yonge St, Newmarket, ON"},
}
NO_TAX_CFG = {"invoicing": {"tax_label": "HST", "tax_rate": 13, "tax_number": ""}}

LINE = [{"description": "Website build", "unit_cents": 120000, "quantity": 1.0,
         "taxable": True}]


def raise_one(con, place_id="p1", lines=None, cfg=CFG, **kw):
    return invoice.create(con, place_id, lines or LINE, cfg=cfg, **kw)


# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("typed, cents", [
    (149, 14900), ("149", 14900), ("149.00", 14900), (149.0, 14900),
    ("$1,200.50", 120050), ("  60 ", 6000), ("-50", -5000), ("0.01", 1),
])
def test_whatever_the_operator_types_becomes_cents(typed, cents):
    assert invoice.to_cents(typed) == cents


def test_half_a_cent_rounds_up_rather_than_to_even():
    # Python's round() would make this 0 — bankers' rounding is a surprise
    # nobody invited to a two-line invoice.
    assert invoice.to_cents("0.005") == 1
    assert invoice.to_cents("0.015") == 2


@pytest.mark.parametrize("junk", ["", "  ", "free", "$", "twelve", None, True])
def test_an_amount_that_is_not_one_says_so(junk):
    with pytest.raises(invoice.InvoiceError):
        invoice.to_cents(junk)


def test_a_fractional_quantity_is_billed_at_the_cent():
    assert invoice.line_cents(2.5, 12000) == 30000
    assert invoice.line_cents(1.005, 10000) == 10050
    assert invoice.line_cents(3, 3333) == 9999


def test_money_reads_like_money():
    assert invoice.money(120050) == "$1,200.50"
    assert invoice.money(-5000) == "-$50.00"
    assert invoice.money(14900, "CAD") == "$149.00 CAD"


# ---------------------------------------------------------------------------
# Tax
# ---------------------------------------------------------------------------
def test_tax_is_worked_out_once_on_the_taxable_subtotal(con):
    # Three 33c lines. Taxing each line and adding up gives 12c (4+4+4 after
    # rounding); taxing the 99c subtotal once gives 13c, which is the number
    # the client gets when they check it themselves.
    lines = [{"description": f"Bit {n}", "unit_cents": 33, "quantity": 1.0,
              "taxable": True} for n in range(3)]
    number = raise_one(con, client(con), lines)
    row = db.invoice(con, number)
    sums = invoice.totals(row, db.invoice_lines(con, int(row["id"])))

    assert sums.subtotal_cents == 99
    assert sums.tax_cents == 13
    assert sums.total_cents == 112


def test_a_line_marked_not_taxable_is_left_out_of_the_tax(con):
    lines = [{"description": "Build", "unit_cents": 100000, "quantity": 1.0,
              "taxable": True},
             {"description": "Disbursement — domain at cost", "unit_cents": 2000,
              "quantity": 1.0, "taxable": False}]
    number = raise_one(con, client(con), lines)
    row = db.invoice(con, number)
    sums = invoice.totals(row, db.invoice_lines(con, int(row["id"])))

    assert sums.subtotal_cents == 102000
    assert sums.tax_cents == 13000                  # 13% of 100000, not 102000
    assert sums.total_cents == 115000


def test_no_registration_number_means_no_tax_is_charged(con):
    # You may not collect GST/HST unless you are registered for it, and a small
    # supplier under $30k a year is not. The rate stays in config so that
    # entering a number later is all it takes.
    number = raise_one(con, client(con), cfg=NO_TAX_CFG)
    row = db.invoice(con, number)
    sums = invoice.totals(row, db.invoice_lines(con, int(row["id"])))

    assert row["tax_rate"] == 0
    assert row["tax_number"] is None
    assert sums.tax_cents == 0
    assert sums.total_cents == 120000


def test_the_number_is_what_turns_tax_on():
    assert invoice.tax_from(invoice.settings_from(NO_TAX_CFG)) == ("", 0.0, "")
    label, rate, number = invoice.tax_from(invoice.settings_from(CFG))
    assert (label, rate) == ("HST", 13.0)
    assert number == "80012 3456 RT0001"


def test_the_rate_on_an_old_invoice_does_not_move_when_config_does(con):
    number = raise_one(con, client(con))
    con.execute("UPDATE invoices SET tax_rate=5, tax_label='GST'")   # a rate change
    row = db.invoice(con, number)
    doc = invoice.document(con, number, cfg=CFG)

    # Read off the invoice, not off today's settings.
    assert doc["tax"]["label"] == "GST"
    assert row["tax_rate"] == 5


# ---------------------------------------------------------------------------
# Numbering
# ---------------------------------------------------------------------------
def test_numbers_run_in_sequence_within_the_year(con):
    place = client(con)
    year = datetime.date.today().year
    first = raise_one(con, place)
    second = raise_one(con, place)

    assert first == f"{year}-001"
    assert second == f"{year}-002"


def test_a_void_keeps_its_number_and_the_run_carries_on(con):
    # A gap in a numbered run is the first thing anybody asks about.
    place = client(con)
    year = datetime.date.today().year
    first = raise_one(con, place)
    invoice.void(con, first, "priced it wrong")
    second = raise_one(con, place)

    assert db.invoice(con, first)["status"] == "void"
    assert second == f"{year}-002"


def test_a_prefix_from_config_goes_on_the_front(con):
    number = raise_one(con, client(con),
                       cfg={"invoicing": {"prefix": "LS-", "tax_number": ""}})
    assert number == f"LS-{datetime.date.today().year}-001"


def test_two_invoices_can_never_share_a_number(con):
    import sqlite3
    place = client(con)
    number = raise_one(con, place)
    with pytest.raises(sqlite3.IntegrityError):
        db.create_invoice(con, number=number, place_id=place,
                          bill_to_name="Someone else", lines=LINE)


# ---------------------------------------------------------------------------
# The client is copied on, not joined to
# ---------------------------------------------------------------------------
def test_the_invoice_keeps_the_name_it_was_raised_with(con):
    place = client(con)
    number = raise_one(con, place)

    con.execute("UPDATE leads SET name='Something Else Entirely', address=NULL "
                "WHERE place_id=?", (place,))
    row = db.invoice(con, number)
    assert row["bill_to_name"] == "Klimczuk Roofing"
    assert "12 Main St" in row["bill_to_address"]


def test_a_purged_lead_still_has_its_old_invoices_intact(con):
    # Google's copy of a business expires after 30 days and `purge_stale`
    # blanks it. An invoice has to say in a year what it said when it was sent.
    place = client(con)
    number = raise_one(con, place)
    con.execute("UPDATE leads SET refreshed_at='2000-01-01', stage='contacted' "
                "WHERE place_id=?", (place,))
    db.purge_stale(con)

    assert db.get(con, place)["name"].startswith("(purged")
    assert db.invoice(con, number)["bill_to_name"] == "Klimczuk Roofing"


def test_raising_one_against_a_purged_lead_asks_for_the_name(con):
    place = client(con)
    con.execute("UPDATE leads SET name='(purged — re-scan to refill)' "
                "WHERE place_id=?", (place,))
    with pytest.raises(invoice.InvoiceError, match="expired"):
        raise_one(con, place)

    # ...and takes it when given one.
    number = raise_one(con, place, bill_to_name="Klimczuk Roofing")
    assert db.invoice(con, number)["bill_to_name"] == "Klimczuk Roofing"


def test_there_is_nobody_to_invoice_without_a_lead(con):
    with pytest.raises(invoice.InvoiceError, match="nobody to invoice"):
        raise_one(con, "not-a-place")


def test_an_invoice_with_no_lines_is_refused(con):
    with pytest.raises(invoice.InvoiceError, match="no lines"):
        invoice.create(con, client(con), [], cfg=CFG)


# ---------------------------------------------------------------------------
# Issuing, paying, voiding
# ---------------------------------------------------------------------------
def test_a_new_invoice_is_a_draft_and_owes_nothing_yet(con):
    row = db.invoice(con, raise_one(con, client(con)))
    assert row["status"] == "draft"
    assert row["issued_at"] is None and row["due_at"] is None
    assert invoice.editable(row)


def test_issuing_sets_the_date_and_the_clock(con):
    number = raise_one(con, client(con))
    row = invoice.send(con, number, today=datetime.date(2026, 8, 14))

    assert row["status"] == "sent"
    assert row["issued_at"] == "2026-08-14"
    assert row["due_at"] == "2026-08-28"             # net 14
    assert not invoice.editable(row)


def test_terms_come_off_the_invoice_not_off_todays_config(con):
    number = raise_one(con, client(con), terms_days=30)
    row = invoice.send(con, number, today=datetime.date(2026, 8, 14))
    assert row["due_at"] == "2026-09-13"


def test_an_invoice_is_only_issued_once(con):
    number = raise_one(con, client(con))
    invoice.send(con, number)
    with pytest.raises(invoice.InvoiceError, match="already issued"):
        invoice.send(con, number)


def test_a_part_payment_leaves_it_owing(con):
    number = raise_one(con, client(con))
    invoice.send(con, number)
    result = invoice.pay(con, number, "600")

    assert result.paid_cents == 60000
    assert result.balance_cents == 75600            # 1200 + 13% - 600
    assert db.invoice(con, number)["status"] == "sent"


def test_the_rest_of_it_settles_the_invoice(con):
    number = raise_one(con, client(con))
    invoice.send(con, number)
    invoice.pay(con, number, "600")
    result = invoice.pay(con, number, "756", method="cheque", reference="0142")

    assert result.settled
    assert db.invoice(con, number)["status"] == "paid"
    assert db.invoice(con, number)["paid_at"]


def test_overpaying_is_recorded_as_a_credit_rather_than_swallowed(con):
    number = raise_one(con, client(con), [{"description": "Month",
                                           "unit_cents": 14900, "quantity": 1.0,
                                           "taxable": False}], cfg=NO_TAX_CFG)
    invoice.send(con, number)
    result = invoice.pay(con, number, "200")

    assert result.settled and result.credit_cents == 5100
    assert "credit" in invoice.render(con, number, cfg=NO_TAX_CFG)


def test_money_cannot_be_recorded_against_a_draft(con):
    number = raise_one(con, client(con))
    with pytest.raises(invoice.InvoiceError, match="not been sent"):
        invoice.pay(con, number, "149")


@pytest.mark.parametrize("amount", ["0", "-20"])
def test_a_payment_has_to_be_more_than_nothing(con, amount):
    number = raise_one(con, client(con))
    invoice.send(con, number)
    with pytest.raises(invoice.InvoiceError):
        invoice.pay(con, number, amount)


def test_an_unknown_payment_method_is_refused(con):
    number = raise_one(con, client(con))
    invoice.send(con, number)
    with pytest.raises(invoice.InvoiceError, match="Unknown payment method"):
        invoice.pay(con, number, "10", method="bitcoin")


def test_voiding_needs_a_reason(con):
    number = raise_one(con, client(con))
    with pytest.raises(invoice.InvoiceError, match="why"):
        invoice.void(con, number, "  ")


def test_an_invoice_with_money_against_it_cannot_be_voided(con):
    # Voiding it would quietly erase a payment that actually arrived.
    number = raise_one(con, client(con))
    invoice.send(con, number)
    invoice.pay(con, number, "100")
    with pytest.raises(invoice.InvoiceError, match="money against it"):
        invoice.void(con, number, "changed my mind")


def test_nothing_is_paid_against_a_voided_invoice(con):
    number = raise_one(con, client(con))
    invoice.send(con, number)
    invoice.void(con, number, "wrong client")
    with pytest.raises(invoice.InvoiceError, match="voided"):
        invoice.pay(con, number, "50")


def test_a_sent_invoice_can_no_longer_be_edited(con):
    number = raise_one(con, client(con))
    invoice.send(con, number)
    with pytest.raises(invoice.InvoiceError, match="cannot change"):
        invoice.guard_editable(db.invoice(con, number))


# ---------------------------------------------------------------------------
# Overdue
# ---------------------------------------------------------------------------
def test_overdue_is_worked_out_from_today_rather_than_stored(con):
    number = raise_one(con, client(con))
    invoice.send(con, number, today=datetime.date(2026, 8, 1))
    row = db.invoice(con, number)

    assert not invoice.is_overdue(row, 100, datetime.date(2026, 8, 15))
    assert invoice.is_overdue(row, 100, datetime.date(2026, 8, 16))
    assert invoice.days_overdue(row, datetime.date(2026, 8, 20)) == 5


def test_something_paid_is_never_overdue(con):
    number = raise_one(con, client(con))
    invoice.send(con, number, today=datetime.date(2020, 1, 1))
    row = db.invoice(con, number)
    assert not invoice.is_overdue(row, 0, datetime.date(2026, 8, 20))


def test_what_is_owed_is_added_up_the_same_way_the_paper_says(con):
    place = client(con)
    old = raise_one(con, place)
    invoice.send(con, old, today=datetime.date(2026, 7, 1))
    recent = raise_one(con, place)
    invoice.send(con, recent, today=datetime.date(2026, 8, 10))
    invoice.pay(con, recent, "200")

    owed = invoice.outstanding(con, today=datetime.date(2026, 8, 15))
    assert owed.open_count == 2
    assert owed.outstanding_cents == 135600 + 115600      # 1356 each, less $200
    assert owed.overdue_cents == 135600                   # only the July one
    assert owed.overdue_count == 1 and owed.oldest == old


def test_the_oldest_overdue_is_the_first_one_raised_when_dates_tie(con):
    # A monthly run gives every client the same due date, so "the oldest" has
    # to mean something better than whichever row the query reached first.
    place = client(con)
    first = raise_one(con, place)
    second = raise_one(con, place)
    for number in (first, second):
        invoice.send(con, number, today=datetime.date(2026, 7, 1))

    owed = invoice.outstanding(con, today=datetime.date(2026, 8, 1))
    assert owed.overdue_count == 2
    assert owed.oldest == first


def test_a_paid_invoice_is_not_money_owed(con):
    number = raise_one(con, client(con))
    invoice.send(con, number)
    invoice.pay(con, number, "1356")
    assert invoice.outstanding(con).outstanding_cents == 0


# ---------------------------------------------------------------------------
# Lines from what they pay
# ---------------------------------------------------------------------------
def test_a_month_of_their_plan_reads_like_a_month(con):
    place = client(con, monthly=149)
    lines = invoice.subscription_lines(con, db.subscription(con, place), "2026-08")
    assert lines == [{"description": "Website hosting and care — August 2026",
                      "unit_cents": 14900, "quantity": 1.0, "taxable": True}]


def test_the_setup_fee_goes_on_the_first_invoice_only(con):
    place = client(con, monthly=60, setup=1200)
    sub = db.subscription(con, place)

    first = invoice.subscription_lines(con, sub, "2026-08")
    assert [line["unit_cents"] for line in first] == [120000, 6000]

    raise_one(con, place, first, period="2026-08")
    second = invoice.subscription_lines(con, sub, "2026-09")
    assert [line["unit_cents"] for line in second] == [6000]


def test_a_botched_first_invoice_does_not_bill_the_build_twice(con):
    # Voided invoices still count as "they have been invoiced before" — the
    # setup fee was on the one that was cancelled and reissued.
    place = client(con, monthly=60, setup=1200)
    sub = db.subscription(con, place)
    number = raise_one(con, place, invoice.subscription_lines(con, sub, "2026-08"),
                       period="2026-08")
    invoice.void(con, number, "wrong month")

    again = invoice.subscription_lines(con, sub, "2026-08")
    assert [line["unit_cents"] for line in again] == [6000]


# ---------------------------------------------------------------------------
# The monthly run
# ---------------------------------------------------------------------------
def test_the_run_invoices_every_active_client(con):
    client(con, "p1", monthly=149)
    client(con, "p2", monthly=60, name="Second Client")
    result = invoice.run(con, CFG, period="2026-08")

    assert len(result.created) == 2
    assert result.total_cents == invoice.to_cents(149 * 1.13) + invoice.to_cents(60 * 1.13)
    assert all(db.invoice(con, n)["status"] == "sent" for n in result.created)


def test_running_it_twice_bills_nobody_twice(con):
    # The one thing this command must never do, and the only reason it is a
    # command rather than a careful morning.
    client(con, "p1", monthly=149)
    invoice.run(con, CFG, period="2026-08")
    second = invoice.run(con, CFG, period="2026-08")

    assert second.created == []
    assert second.skipped and "already invoiced" in second.skipped[0][1]
    assert len(db.invoices(con)) == 1


def test_the_database_refuses_a_double_bill_even_if_the_check_is_skipped(con):
    import sqlite3
    place = client(con, monthly=149)
    raise_one(con, place, period="2026-08")
    with pytest.raises(sqlite3.IntegrityError):
        db.create_invoice(con, number="ZZZ-1", place_id=place,
                          bill_to_name="Klimczuk Roofing", period="2026-08",
                          lines=LINE)


def test_voiding_a_month_lets_it_be_raised_again(con):
    place = client(con, monthly=149)
    first = invoice.run(con, CFG, period="2026-08")
    invoice.void(con, first.created[0], "billed the wrong amount")

    again = invoice.run(con, CFG, period="2026-08")
    assert len(again.created) == 1


def test_a_cancelled_client_is_not_invoiced(con):
    place = client(con, monthly=149)
    db.end_subscription(con, place, "cancelled")
    assert invoice.run(con, CFG, period="2026-08").created == []


def test_a_client_on_nothing_a_month_is_skipped_with_a_reason(con):
    client(con, monthly=0)
    result = invoice.run(con, CFG, period="2026-08")
    assert result.created == []
    assert "plan is $0" in result.skipped[0][1]


def test_one_bad_client_does_not_stop_the_rest_of_the_run(con):
    client(con, "p1", monthly=149)
    client(con, "p2", monthly=99, name="Second Client")
    con.execute("UPDATE leads SET name='(purged — re-scan to refill)' "
                "WHERE place_id='p1'")

    result = invoice.run(con, CFG, period="2026-08")
    assert len(result.created) == 1
    assert len(result.skipped) == 1


def test_the_run_can_leave_them_as_drafts(con):
    client(con, monthly=149)
    result = invoice.run(con, CFG, period="2026-08", issue=False)
    assert db.invoice(con, result.created[0])["status"] == "draft"


def test_a_month_reads_as_a_month(con):
    assert invoice.period_label("2026-08") == "August 2026"
    assert invoice.period_of(datetime.date(2026, 8, 14)) == "2026-08"


# ---------------------------------------------------------------------------
# Typed lines
# ---------------------------------------------------------------------------
def test_a_typed_line_becomes_a_line():
    assert invoice.parse_line("Extra page:250") == {
        "description": "Extra page", "unit_cents": 25000, "quantity": 1.0,
        "taxable": True}


def test_a_quantity_can_be_typed_after_the_price():
    line = invoice.parse_line("Photography:120:2.5")
    assert (line["unit_cents"], line["quantity"]) == (12000, 2.5)


def test_a_description_may_contain_a_colon():
    # "Rebuild: kitchen page:400" must not be read as 400 of something.
    line = invoice.parse_line("Rebuild: kitchen page:400")
    assert line["description"] == "Rebuild: kitchen page"
    assert (line["unit_cents"], line["quantity"]) == (40000, 1.0)


def test_a_discount_is_a_negative_line():
    assert invoice.parse_line("Late last month, sorry:-50")["unit_cents"] == -5000


@pytest.mark.parametrize("junk", ["Extra page", "", ":250", "Thing:free"])
def test_a_line_that_cannot_be_read_says_how_to_write_it(junk):
    with pytest.raises(invoice.InvoiceError):
        invoice.parse_line(junk)


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------
def page(con, number, cfg=CFG, **kw):
    return invoice.render(con, number, operator=CFG["business"], cfg=cfg, **kw)


def test_the_page_says_who_owes_what_to_whom(con):
    number = raise_one(con, client(con))
    invoice.send(con, number, today=datetime.date(2026, 8, 14))
    html = page(con, number, today=datetime.date(2026, 8, 15))

    assert number in html
    assert "Klimczuk Roofing" in html and "12 Main St" in html
    assert "N. Klimczuk o/a Leadsmith" in html
    assert "$1,200.00" in html                       # the line
    assert "$156.00" in html                         # the HST
    assert "$1,356.00 CAD" in html                   # the total, and the balance
    assert "14 August 2026" in html and "28 August 2026" in html


def test_the_registration_number_is_printed_when_tax_is_charged(con):
    # Required of anyone charging GST/HST, and the first thing a client's
    # bookkeeper looks for.
    number = raise_one(con, client(con))
    invoice.send(con, number)
    assert "80012 3456 RT0001" in page(con, number)


def test_a_page_with_no_tax_shows_no_tax_line(con):
    number = raise_one(con, client(con), cfg=NO_TAX_CFG)
    invoice.send(con, number)
    html = page(con, number, cfg=NO_TAX_CFG)
    assert "HST" not in html
    assert "$1,200.00 CAD" in html


def test_how_to_pay_is_on_it(con):
    number = raise_one(con, client(con))
    invoice.send(con, number)
    html = page(con, number)
    assert "pay@example.ca" in html
    assert "e-Transfer" in html and "Cheque" in html
    assert number in html          # asked for in the e-transfer message


def test_nothing_offers_a_way_to_pay_that_was_never_configured(con):
    number = raise_one(con, client(con), cfg=NO_TAX_CFG)
    invoice.send(con, number)
    html = page(con, number, cfg=NO_TAX_CFG)
    assert "How to pay" not in html


def test_a_settled_invoice_says_paid_and_stops_asking(con):
    number = raise_one(con, client(con))
    invoice.send(con, number)
    invoice.pay(con, number, "1356", method="cash")
    html = page(con, number)

    assert "Paid in full" in html
    assert "How to pay" not in html
    assert "by cash" in html                          # the receipt half of it


def test_an_overdue_invoice_says_so_in_days(con):
    number = raise_one(con, client(con))
    invoice.send(con, number, today=datetime.date(2026, 8, 1))
    html = page(con, number, today=datetime.date(2026, 8, 20))
    assert "5 days overdue" in html


def test_a_voided_page_says_nothing_is_owed(con):
    number = raise_one(con, client(con))
    invoice.send(con, number)
    invoice.void(con, number, "priced it wrong")
    html = page(con, number)

    assert "Void" in html and "priced it wrong" in html
    assert "Balance due" not in html
    assert "How to pay" not in html


def test_a_part_paid_invoice_shows_both_halves(con):
    number = raise_one(con, client(con))
    invoice.send(con, number)
    invoice.pay(con, number, "600", method="e-transfer", reference="CA12345")
    html = page(con, number)

    assert "$600.00" in html and "CA12345" in html
    assert "$756.00 CAD" in html                      # what is still owing


def test_a_hostile_business_name_cannot_inject_markup(con):
    place = lead(con, name="<script>alert(1)</script> Roofing")
    number = raise_one(con, place)
    html = page(con, number)

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_the_page_is_self_contained(con):
    # Printed on a laptop at a kitchen table with no internet, and emailed to
    # people whose mail client blocks remote content.
    number = raise_one(con, client(con))
    invoice.send(con, number)
    html = page(con, number)

    assert "<script" not in html
    assert not re.findall(r'(?:src|href)="https?://', html)


def test_a_draft_can_still_be_printed_but_says_it_is_not_issued(con):
    number = raise_one(con, client(con))
    assert "not yet issued" in page(con, number)


# ---------------------------------------------------------------------------
# Where it is written
# ---------------------------------------------------------------------------
def test_the_page_is_written_somewhere_no_client_will_ever_be_sent(con, tmp_path):
    # Not in sites/: `deploy` uploads what is in a site directory and
    # `offboard.bundle` zips it for the client. Somebody else's invoice must
    # not be able to end up in either.
    import generate
    assert not invoice.INVOICES_DIR.startswith(generate.SITES_DIR)

    number = raise_one(con, client(con))
    path = invoice.write(number, page(con, number), directory=str(tmp_path))
    assert os.path.basename(path) == f"invoice-{number}.html"
    assert os.path.exists(path)


def test_a_number_with_awkward_characters_still_makes_a_filename():
    assert invoice.path_for("2026/001").endswith("invoice-2026-001.html")


# ---------------------------------------------------------------------------
# For the bookkeeper
# ---------------------------------------------------------------------------
def test_the_export_is_in_dollars_because_an_accountant_reads_it(con):
    number = raise_one(con, client(con))
    invoice.send(con, number, today=datetime.date(2026, 8, 14))
    invoice.pay(con, number, "356")

    header, row = invoice.export_csv(con).strip().splitlines()
    assert header.startswith("number,status,issued,due,client")
    fields = row.split(",")
    assert fields[0] == number
    assert fields[4] == "Klimczuk Roofing"
    assert fields[6:11] == ["1200.00", "156.00", "1356.00", "356.00", "1000.00"]


def test_the_export_can_be_asked_for_one_year(con):
    place = client(con)
    old = raise_one(con, place)
    invoice.send(con, old, today=datetime.date(2025, 12, 30))
    new = raise_one(con, place)
    invoice.send(con, new, today=datetime.date(2026, 1, 2))

    assert old in invoice.export_csv(con, year=2025)
    assert new not in invoice.export_csv(con, year=2025)
