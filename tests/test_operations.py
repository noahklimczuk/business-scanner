"""Phase 6: the board, the money, and giving a client their site back."""
import json
import os
import re
import zipfile
from types import SimpleNamespace

import pytest

import billing
import board
import db
import generate
import offboard


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    connection = db.connect()
    yield connection
    connection.close()


def lead(con, place_id="p1", name="Klimczuk Roofing", **kw):
    db.upsert_lead(con, {"place_id": place_id, "name": name,
                         "category": "Roofing contractor", "website_kind": "none",
                         **kw})
    if "score" in kw:
        con.execute("UPDATE leads SET score=? WHERE place_id=?",
                    (kw["score"], place_id))
    return place_id


def stripe_sub(sub_id, status="active", amount=14900, interval="month"):
    return {"id": sub_id, "status": status,
            "items": {"data": [{"quantity": 1, "price": {
                "unit_amount": amount,
                "recurring": {"interval": interval, "interval_count": 1}}}]}}


def stub_get(payload, status=200):
    def getter(url, **kw):
        return SimpleNamespace(ok=status == 200, status_code=status,
                               text=json.dumps(payload),
                               json=lambda: payload)
    return getter


# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------
def test_mrr_counts_only_active_subscriptions(con):
    lead(con, "p1"); lead(con, "p2", name="B"); lead(con, "p3", name="C")
    db.set_subscription(con, "p1", plan="no-money-down", monthly=149)
    db.set_subscription(con, "p2", plan="standard", monthly=60, setup_fee=1200)
    db.set_subscription(con, "p3", plan="standard", monthly=60)
    db.end_subscription(con, "p3", "cancelled")

    money = billing.summarise(db.subscriptions(con))
    assert money.mrr == 209
    assert money.clients == 2 and money.cancelled == 1
    assert money.arr == 209 * 12


def test_a_setup_fee_is_never_folded_into_recurring_revenue(con):
    # A $1,200 setup is not $100/month of anything, and treating it as
    # recurring is how a business convinces itself it is twice its size.
    lead(con, "p1")
    db.set_subscription(con, "p1", plan="standard", monthly=60, setup_fee=1200)
    money = billing.summarise(db.subscriptions(con))
    assert money.mrr == 60
    assert money.setup_collected == 1200


def test_the_plans_are_the_ones_the_guide_specifies():
    assert billing.plan_terms("no-money-down") == {
        "setup_fee": 0.0, "monthly": 149.0, "term_months": 12}
    assert billing.plan_terms("standard")["setup_fee"] == 1200.0
    with pytest.raises(billing.BillingError):
        billing.plan_terms("mates-rates")


# ---------------------------------------------------------------------------
# Stripe reconciliation — every disagreement is a real event
# ---------------------------------------------------------------------------
def test_a_failed_card_is_surfaced(con):
    lead(con, "p1")
    db.set_subscription(con, "p1", plan="no-money-down", monthly=149,
                        stripe_id="sub_1")
    result = billing.reconcile(db.subscriptions(con),
                               {"sub_1": stripe_sub("sub_1", "past_due")})
    assert len(result.problems) == 1
    assert result.problems[0].theirs == "past_due"
    assert "not collecting" in result.problems[0].detail


def test_a_client_who_cancelled_without_saying_is_surfaced(con):
    lead(con, "p1")
    db.set_subscription(con, "p1", plan="no-money-down", monthly=149,
                        stripe_id="sub_1")
    result = billing.reconcile(db.subscriptions(con),
                               {"sub_1": stripe_sub("sub_1", "canceled")})
    assert "never heard" in result.problems[0].detail


def test_still_charging_someone_who_left_is_surfaced(con):
    # The worst of the four: they think they have gone and their card is still
    # being taken.
    lead(con, "p1")
    db.set_subscription(con, "p1", plan="standard", monthly=60, stripe_id="sub_1")
    db.end_subscription(con, "p1", "cancelled")
    result = billing.reconcile(db.subscriptions(con),
                               {"sub_1": stripe_sub("sub_1", "active", 6000)})
    assert "before they notice" in result.problems[0].detail


def test_a_price_that_drifted_is_surfaced(con):
    lead(con, "p1")
    db.set_subscription(con, "p1", plan="no-money-down", monthly=149,
                        stripe_id="sub_1")
    result = billing.reconcile(db.subscriptions(con),
                               {"sub_1": stripe_sub("sub_1", "active", 9900)})
    assert result.problems[0].ours == "$149.00"
    assert result.problems[0].theirs == "$99.00"


def test_agreement_produces_no_noise(con):
    lead(con, "p1")
    db.set_subscription(con, "p1", plan="no-money-down", monthly=149,
                        stripe_id="sub_1")
    result = billing.reconcile(db.subscriptions(con),
                               {"sub_1": stripe_sub("sub_1", "active", 14900)})
    assert result.problems == [] and result.matched == 1


def test_a_subscription_with_no_stripe_id_is_listed_not_flagged(con):
    # Being paid by e-transfer is a choice, not a fault. It is worth knowing
    # that it cannot be checked.
    lead(con, "p1")
    db.set_subscription(con, "p1", plan="standard", monthly=60)
    result = billing.reconcile(db.subscriptions(con), {})
    assert result.problems == []
    assert result.unlinked == ["Klimczuk Roofing"]


@pytest.mark.parametrize("interval,amount,expected", [
    ("month", 14900, 149.0),
    ("year", 178800, 149.0),        # a yearly plan is not a monthly one
    ("week", 3400, 147.33),
])
def test_stripe_intervals_are_normalised_to_a_month(interval, amount, expected):
    got = billing._monthly_amount(stripe_sub("s", amount=amount, interval=interval))
    assert got == pytest.approx(expected, abs=0.5)


def test_a_missing_stripe_key_says_which_key_and_that_read_only_is_enough():
    with pytest.raises(billing.BillingError) as excinfo:
        billing.fetch_subscriptions("")
    assert "stripe_secret_key" in str(excinfo.value)
    assert "write access" in str(excinfo.value)


def test_a_rejected_stripe_key_is_not_reported_as_zero_clients():
    # Silently returning nothing would read as "everyone cancelled".
    with pytest.raises(billing.BillingError, match="401"):
        billing.fetch_subscriptions("sk_bad", stub_get({"error": {}}, status=401))


def test_stripe_pagination_is_followed():
    pages = [
        {"data": [stripe_sub("sub_1")], "has_more": True},
        {"data": [stripe_sub("sub_2")], "has_more": False},
    ]
    calls = []

    def getter(url, **kw):
        calls.append(kw.get("params", {}))
        payload = pages[min(len(calls) - 1, len(pages) - 1)]
        return SimpleNamespace(ok=True, status_code=200, text="",
                               json=lambda: payload)

    got = billing.fetch_subscriptions("sk_test", getter)
    assert set(got) == {"sub_1", "sub_2"}
    assert calls[1]["starting_after"] == "sub_1"


# ---------------------------------------------------------------------------
# The call list
# ---------------------------------------------------------------------------
def test_the_call_list_is_best_first_and_skips_the_unworkable(con):
    lead(con, "good", name="Good", score=80)
    lead(con, "better", name="Better", score=95)
    lead(con, "dead", name="Dead", score=99)
    db.set_stage(con, "dead", "dead")
    names = [item["name"] for item in board.call_list(con)]
    assert names == ["Better", "Good"]


def test_someone_rung_this_week_is_not_offered_again(con):
    lead(con, "p1", score=90)
    db.log_touch(con, "p1", "phone", "no-answer")
    assert board.call_list(con) == []


def test_a_paying_client_never_appears_on_the_call_list(con):
    # The single most embarrassing thing this tool could do to its operator.
    lead(con, "p1", score=90)
    db.set_subscription(con, "p1", plan="standard", monthly=60)
    assert board.call_list(con) == []


def test_each_lead_arrives_with_the_reason_to_ring_it(con):
    # A board that prints a score and nothing else has done a third of the job.
    lead(con, "p1", score=70, website_kind="social", rating=4.6, review_count=48)
    why = board.call_list(con)[0]["why"]
    assert any("Facebook" in reason for reason in why)
    assert any("48 reviews" in reason for reason in why)


def test_a_bad_rating_is_called_out_rather_than_buried(con):
    lead(con, "p1", score=60, rating=2.9, review_count=30)
    why = " ".join(board.call_list(con)[0]["why"])
    assert "amplify" in why


# ---------------------------------------------------------------------------
# The board
# ---------------------------------------------------------------------------
def test_the_board_renders_with_nothing_in_the_database(con):
    html = board.build(con)
    assert "Leadsmith" in html
    assert "Nobody to call" in html


def test_the_board_survives_phase_5_not_having_landed(con):
    # The phases land in whatever order they get merged. A board that crashes
    # because a later phase's column is missing is worse than an empty panel.
    assert not db.has_column(con, "leads", "live_url")
    assert board.live_sites(con) == []
    assert board.stale_previews(con) == []
    assert "No launched sites yet" in board.build(con)


def test_the_board_shows_the_money_and_what_it_cost(con):
    lead(con, "p1")
    db.set_subscription(con, "p1", plan="no-money-down", monthly=149)
    html = board.build(con)
    assert "$149" in html
    assert "MRR" in html and "API cost" in html


def test_the_board_is_not_indexable_and_loads_nothing_remote(con):
    # It has every client's revenue on it.
    html = board.build(con)
    assert 'content="noindex,nofollow"' in html
    assert not re.findall(r'(?:src|href)="https?://', html)


def test_writing_the_board_returns_the_path(con, tmp_path):
    path = board.write(board.build(con), str(tmp_path / "b.html"))
    assert os.path.exists(path)


# ---------------------------------------------------------------------------
# Offboarding — the promise the leave-behind makes in print
# ---------------------------------------------------------------------------
@pytest.fixture
def built_site(tmp_path, monkeypatch):
    monkeypatch.setattr(generate, "SITES_DIR", str(tmp_path / "sites"))
    directory = generate.site_dir("p1")
    os.makedirs(directory)
    for name, body in (("index.html", "<html>their site</html>"),
                       ("robots.txt", "User-agent: *\nAllow: /\n"),
                       ("content.rejected.json", '{"flagged": "family owned"}'),
                       ("leave-behind.html", "<html>$149/mo, our margin</html>")):
        with open(os.path.join(directory, name), "w") as fh:
            fh.write(body)
    generate.save_content("p1", {"version": 1, "copy": {"hero_headline": "Roofing"}})
    return directory


def test_the_zip_contains_the_whole_site(built_site):
    data = offboard.bundle({"place_id": "p1", "name": "Klimczuk Roofing"})
    with zipfile.ZipFile(__import__("io").BytesIO(data)) as archive:
        names = set(archive.namelist())
    assert {"index.html", "robots.txt", "content.json",
            "READ-ME-FIRST.txt", "your-words.json"} <= names


def test_the_zip_contains_nothing_that_was_ours(built_site):
    # One is a list of claims a model tried to make about them; the other has
    # our pricing and margin on it. Neither would land well.
    data = offboard.bundle({"place_id": "p1", "name": "Klimczuk Roofing"})
    with zipfile.ZipFile(__import__("io").BytesIO(data)) as archive:
        names = set(archive.namelist())
        assert "content.rejected.json" not in names
        assert "leave-behind.html" not in names
        assert b"margin" not in b"".join(archive.read(n) for n in names)


def test_the_note_tells_them_what_they_are_holding(built_site):
    note = offboard.handover_note(
        {"place_id": "p1", "name": "Klimczuk Roofing",
         "domain": "klimczukroofing.ca", "form_enabled": 1},
        {"operator_name": "Noah", "email": "noah@example.ca"})
    # The note is hard-wrapped for a terminal and an email, so phrases span
    # newlines; compare against the flowed text.
    flowed = " ".join(note.split())
    assert "klimczukroofing.ca" in flowed
    assert "registered in your name" in flowed
    assert "there is no database, no WordPress" in flowed
    # The point of the promise: they can hand this to anyone.
    assert "rebuilding from scratch" in flowed
    assert "noah@example.ca" in flowed


def test_the_note_is_honest_when_there_was_no_domain_or_form(built_site):
    flowed = " ".join(offboard.handover_note({"place_id": "p1", "name": "X"}).split())
    assert "never launched on a domain" in flowed
    assert "had no contact form" in flowed


def test_the_zip_is_written_with_a_recognisable_name(built_site, tmp_path):
    path = offboard.write({"place_id": "p1", "name": "Klimczuk Roofing",
                           "domain": "klimczukroofing.ca"}, str(tmp_path))
    assert os.path.basename(path) == "klimczukroofing-ca-website.zip"


def test_a_lead_whose_site_was_never_built_still_gets_the_note(tmp_path, monkeypatch):
    monkeypatch.setattr(generate, "SITES_DIR", str(tmp_path / "none"))
    data = offboard.bundle({"place_id": "nope", "name": "X"})
    with zipfile.ZipFile(__import__("io").BytesIO(data)) as archive:
        assert archive.namelist() == ["READ-ME-FIRST.txt"]
