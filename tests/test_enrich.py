import pytest
import requests

import db
import enrich
import search


@pytest.fixture(autouse=True)
def no_real_http(monkeypatch):
    def explode(*a, **kw):
        raise AssertionError("enrichment must not make network calls in tests")
    monkeypatch.setattr(requests.Session, "get", explode)
    monkeypatch.setattr(requests, "get", explode)
    monkeypatch.setattr(requests, "post", explode)


@pytest.fixture()
def con(tmp_path):
    c = db.connect(str(tmp_path / "enrich.db"))
    yield c
    c.close()


class FakeProvider:
    """Returns canned hits; can be told to fail or to block."""
    name = "fake"
    automatic = True

    def __init__(self, hits=None, raises=None):
        self.hits = hits or []
        self.raises = raises
        self.queries = []

    def search(self, query):
        self.queries.append(query)
        if self.raises:
            raise self.raises
        return list(self.hits)


def add(con, place_id, name, **kw):
    place = {"place_id": place_id, "name": name, "category": kw.get("category", "Plumber"),
             "address": "1 Main St", "phone": kw.get("phone", "(905) 555-0123"),
             "lat": kw.get("lat", 44.0), "lng": kw.get("lng", -79.4),
             "rating": kw.get("rating", 4.5), "review_count": kw.get("review_count", 30),
             "hours": ["Monday: 8-5"], "website": kw.get("website"),
             "website_kind": kw.get("website_kind", "none"), "score": kw.get("score", 60)}
    db.upsert_lead(con, place)
    con.commit()


# ---------------------------------------------------------------------------
def test_enrich_normalises_phones_offline(con):
    add(con, "p1", "Joe's Plumbing", phone="(905) 555-0123")
    res = enrich.run(con, search.NullProvider(), city="Newmarket, ON")
    row = db.get(con, "p1")
    assert row["phone_e164"] == "+19055550123"
    assert row["phone_valid"] == 1
    assert row["enriched_at"] is not None
    assert res.phones_normalised == 1


def test_a_number_that_does_not_dial_costs_the_lead_points(con):
    add(con, "good", "Alpha Plumbing", phone="(905) 555-0123")
    add(con, "bad", "Beta Plumbing", phone="905-555-012")     # one digit short
    enrich.run(con, search.NullProvider())
    assert db.get(con, "bad")["phone_valid"] == 0
    assert db.get(con, "bad")["score"] < db.get(con, "good")["score"]


def test_finding_a_facebook_page_improves_the_score(con):
    add(con, "p1", "Klimczuk Roofing", category="Roofing contractor")
    before = db.get(con, "p1")["score"]
    provider = FakeProvider([
        search.SearchHit("https://www.facebook.com/klimczukroofing",
                         "Klimczuk Roofing | Facebook")])
    res = enrich.run(con, provider, city="Newmarket, ON")
    row = db.get(con, "p1")
    assert row["facebook_url"] == "https://www.facebook.com/klimczukroofing"
    assert row["score"] > before
    assert res.socials_found == 1
    assert res.improved == 1


def test_a_wrong_page_is_not_saved(con):
    add(con, "p1", "Joe's Plumbing")
    provider = FakeProvider([
        search.SearchHit("https://www.facebook.com/BigBoxPlumbing", "Big Box Plumbing")])
    enrich.run(con, provider, city="Newmarket, ON")
    row = db.get(con, "p1")
    assert row["facebook_url"] is None
    assert row["social_checked_at"] is not None      # we looked, and found nothing


def test_a_known_social_page_is_used_without_paying_for_a_search(con):
    add(con, "p1", "Bayview Nails", website="https://facebook.com/bayviewnails",
        website_kind="social")
    provider = FakeProvider()
    enrich.run(con, provider, city="Newmarket, ON")
    assert provider.queries == []                    # the scan already told us
    assert db.get(con, "p1")["facebook_url"] == "https://www.facebook.com/bayviewnails"


def test_a_failed_lookup_is_not_recorded_as_absence(con):
    # Otherwise a broken scraper silently downgrades the whole pipeline.
    add(con, "p1", "Joe's Plumbing")
    provider = FakeProvider(raises=search.SearchError("boom"))
    res = enrich.run(con, provider, city="Newmarket, ON")
    row = db.get(con, "p1")
    assert row["social_checked_at"] is None
    assert row["phone_e164"] is not None             # offline work still happened
    assert res.search_failed == 1


def test_being_rate_limited_stops_searching_but_saves_the_rest(con):
    for i in range(5):
        add(con, f"p{i}", f"Shop {i} Plumbing")
    provider = FakeProvider(raises=search.SearchBlocked("429"))
    res = enrich.run(con, provider)
    assert res.stopped
    assert len(provider.queries) == 1                # gave up after the first refusal
    assert res.processed == 5
    assert all(db.get(con, f"p{i}")["phone_e164"] for i in range(5))


def test_a_dead_search_path_gives_up_instead_of_grinding(con):
    for i in range(9):
        add(con, f"p{i}", f"Shop {i} Plumbing")
    provider = FakeProvider(raises=search.SearchError("connection refused"))
    res = enrich.run(con, provider)
    assert len(provider.queries) == enrich.MAX_CONSECUTIVE_SEARCH_FAILURES
    assert res.stopped
    assert res.processed == 9
    assert all(db.get(con, f"p{i}")["social_checked_at"] is None for i in range(9))


def test_one_flaky_lookup_does_not_stop_the_run(con):
    for i in range(3):
        add(con, f"p{i}", f"Shop {i} Plumbing")
    calls = {"n": 0}

    class Flaky(FakeProvider):
        def search(self, query):
            calls["n"] += 1
            if calls["n"] == 1:
                raise search.SearchError("hiccup")
            return []

    res = enrich.run(con, Flaky())
    assert res.search_failed == 1
    assert not res.stopped
    assert calls["n"] == 3


def test_enrich_skips_leads_already_done_unless_forced(con):
    add(con, "p1", "Joe's Plumbing")
    enrich.run(con, search.NullProvider())
    assert enrich.run(con, search.NullProvider()).processed == 0
    assert enrich.run(con, search.NullProvider(), force=True).processed == 1


def test_manual_provider_collects_queries_and_touches_nothing(con):
    add(con, "p1", "Joe's Plumbing")
    provider = search.ManualProvider()
    res = enrich.run(con, provider, city="Newmarket, ON")
    assert res.manual_queries and "duckduckgo.com" in res.manual_queries[0]
    assert db.get(con, "p1")["social_checked_at"] is None


def test_limit_takes_the_best_leads_first(con):
    add(con, "low", "Low Score Plumbing", score=10)
    add(con, "high", "High Score Plumbing", score=90)
    enrich.run(con, search.NullProvider(), limit=1)
    assert db.get(con, "high")["enriched_at"] is not None
    assert db.get(con, "low")["enriched_at"] is None


# ---------------------------------------------------------------------------
def test_chain_needs_separated_locations():
    same_spot = [{"place_id": f"p{i}", "name": "Joe's Plumbing", "lat": 44.0, "lng": -79.4}
                 for i in range(4)]
    assert enrich.chain_groups(same_spot) == {}

    spread = [{"place_id": f"p{i}", "name": "Newmarket Wings",
               "lat": 44.0 + i * 0.02, "lng": -79.4} for i in range(3)]
    assert "newmarket wings" in enrich.chain_groups(spread)


def test_two_locations_are_not_a_chain():
    rows = [{"place_id": f"p{i}", "name": "Two Shop Diner",
             "lat": 44.0 + i * 0.02, "lng": -79.4} for i in range(2)]
    assert enrich.chain_groups(rows) == {}


def test_enrich_kills_untouched_chain_locations(con):
    for i in range(3):
        add(con, f"c{i}", "Newmarket Wings", lat=44.0 + i * 0.02, category="Restaurant")
    res = enrich.run(con, search.NullProvider())
    assert res.chains_marked == 3
    assert all(db.get(con, f"c{i}")["stage"] == "dead" for i in range(3))
    assert db.get(con, "c0")["is_chain"] == 1


def test_a_chain_location_you_have_already_worked_is_left_alone(con):
    for i in range(3):
        add(con, f"c{i}", "Newmarket Wings", lat=44.0 + i * 0.02)
    db.set_stage(con, "c1", "interested")
    con.commit()
    enrich.run(con, search.NullProvider())
    assert db.get(con, "c1")["stage"] == "interested"
    assert db.get(con, "c1")["is_chain"] == 1        # flagged, not deleted


def test_chains_are_detected_from_market_rows_too(con):
    # The other branches usually have websites, so they only exist in `market`.
    add(con, "lead", "Newmarket Wings", lat=44.0)
    for i in range(2):
        db.upsert_market(con, {"place_id": f"m{i}", "name": "Newmarket Wings",
                               "category": "Restaurant", "lat": 44.0 + (i + 1) * 0.02,
                               "lng": -79.4, "website_kind": "real",
                               "rating": 4.0, "review_count": 50})
    con.commit()
    enrich.run(con, search.NullProvider())
    assert db.get(con, "lead")["is_chain"] == 1


# ---------------------------------------------------------------------------
def test_same_name_same_spot_is_a_duplicate():
    rows = [
        {"place_id": "a", "name": "Joe's Plumbing", "lat": 44.0, "lng": -79.4,
         "review_count": 60, "phone_e164": None, "found_at": "2026-01-01"},
        {"place_id": "b", "name": "Joes Plumbing", "lat": 44.0002, "lng": -79.4,
         "review_count": 3, "phone_e164": None, "found_at": "2026-01-01"},
    ]
    dupes = enrich.find_duplicates(rows)
    assert dupes["b"][0] == "a"          # the one with the reviews wins


def test_same_name_far_apart_is_not_a_duplicate():
    rows = [
        {"place_id": "a", "name": "Joe's Plumbing", "lat": 44.0, "lng": -79.4,
         "review_count": 60, "phone_e164": None, "found_at": ""},
        {"place_id": "b", "name": "Joe's Plumbing", "lat": 44.1, "lng": -79.4,
         "review_count": 3, "phone_e164": None, "found_at": ""},
    ]
    assert enrich.find_duplicates(rows) == {}


def test_shared_phone_alone_is_not_enough():
    # A trade and a salon run out of the same house share a landline.
    rows = [
        {"place_id": "a", "name": "Joe's Plumbing", "lat": 44.0, "lng": -79.4,
         "review_count": 60, "phone_e164": "+19055550123", "found_at": ""},
        {"place_id": "b", "name": "Bayview Nails", "lat": 44.0, "lng": -79.4,
         "review_count": 3, "phone_e164": "+19055550123", "found_at": ""},
    ]
    assert enrich.find_duplicates(rows) == {}


def test_shared_phone_with_an_overlapping_name_is_a_duplicate():
    rows = [
        {"place_id": "a", "name": "Joe's Plumbing", "lat": 44.0, "lng": -79.4,
         "review_count": 60, "phone_e164": "+19055550123", "found_at": ""},
        {"place_id": "b", "name": "Joe's Plumbing and Heating", "lat": 44.9,
         "lng": -79.4, "review_count": 3, "phone_e164": "+19055550123", "found_at": ""},
    ]
    assert enrich.find_duplicates(rows)["b"][0] == "a"


def test_duplicate_chains_collapse_to_one_survivor():
    rows = [
        {"place_id": "a", "name": "Joe's Plumbing", "lat": 44.0, "lng": -79.4,
         "review_count": 60, "phone_e164": None, "found_at": ""},
        {"place_id": "b", "name": "Joe's Plumbing", "lat": 44.0001, "lng": -79.4,
         "review_count": 10, "phone_e164": None, "found_at": ""},
        {"place_id": "c", "name": "Joe's Plumbing", "lat": 44.0002, "lng": -79.4,
         "review_count": 1, "phone_e164": None, "found_at": ""},
    ]
    dupes = enrich.find_duplicates(rows)
    assert {k: v[0] for k, v in dupes.items()} == {"b": "a", "c": "a"}


def test_enrich_folds_duplicates_and_hides_them_from_the_list(con):
    add(con, "a", "Joe's Plumbing", review_count=60)
    add(con, "b", "Joe's Plumbing", review_count=2, lat=44.0002)
    res = enrich.run(con, search.NullProvider())
    assert res.duplicates_marked == 1
    assert db.get(con, "b")["duplicate_of"] == "a"
    visible = db.leads(con, exclude_dead=True, exclude_duplicates=True)
    assert [r["place_id"] for r in visible] == ["a"]


# ---------------------------------------------------------------------------
def test_consent_is_recorded_only_when_an_address_is_actually_published(con):
    add(con, "p1", "Klimczuk Roofing", category="Roofing contractor")
    provider = FakeProvider([
        search.SearchHit("https://www.facebook.com/klimczukroofing",
                         "Klimczuk Roofing | Facebook",
                         "Call or email info@klimczukroofing.ca")])
    res = enrich.run(con, provider, city="Newmarket, ON")
    row = db.get(con, "p1")
    assert res.consent_found == 1
    assert row["consent_basis"] == "conspicuous_publication"
    assert row["consent_email"] == "info@klimczukroofing.ca"
    assert db.may_email(row) is True


def test_no_published_address_means_no_consent_basis(con):
    add(con, "p1", "Klimczuk Roofing")
    enrich.run(con, FakeProvider([
        search.SearchHit("https://www.facebook.com/klimczukroofing",
                         "Klimczuk Roofing | Facebook", "412 likes")]),
        city="Newmarket, ON")
    row = db.get(con, "p1")
    assert row["consent_basis"] is None
    assert db.may_email(row) is False


def test_a_withdrawal_closes_the_door_again(con):
    add(con, "p1", "Joe's Plumbing")
    db.record_consent(con, "p1", "conspicuous_publication", "a@b.ca", "their page")
    assert db.may_email(db.get(con, "p1")) is True
    db.withdraw_consent(con, "p1")
    assert db.may_email(db.get(con, "p1")) is False


def test_an_invented_consent_basis_is_refused(con):
    add(con, "p1", "Joe's Plumbing")
    with pytest.raises(ValueError):
        db.record_consent(con, "p1", "existing_business_relationship", "a@b.ca", "hope")
