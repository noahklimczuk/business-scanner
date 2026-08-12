import datetime

import pytest

import db


@pytest.fixture()
def con(tmp_path):
    c = db.connect(str(tmp_path / "test.db"))
    yield c
    c.close()


def place(place_id="p1", **kw):
    base = {"place_id": place_id, "name": "Joe's Plumbing", "category": "Plumber",
            "address": "1 Main St", "phone": "905-555-0123", "lat": 44.0, "lng": -79.4,
            "rating": 4.7, "review_count": 62, "hours": ["Monday: 8-5"],
            "website": None, "website_kind": "none", "score": 71}
    base.update(kw)
    return base


def test_insert_then_refresh(con):
    assert db.upsert_lead(con, place()) is True
    assert db.upsert_lead(con, place(review_count=70, score=75)) is False
    row = db.get(con, "p1")
    assert row["review_count"] == 70
    assert row["score"] == 75
    assert row["refreshed_at"] is not None


def test_refresh_never_clobbers_pipeline_state(con):
    db.upsert_lead(con, place())
    db.set_stage(con, "p1", "interested")
    con.execute("UPDATE leads SET notes='spoke to the owner' WHERE place_id='p1'")
    db.upsert_lead(con, place(review_count=99))
    row = db.get(con, "p1")
    assert row["stage"] == "interested"
    assert row["notes"] == "spoke to the owner"


def test_an_untouched_lead_that_builds_a_site_goes_dead(con):
    db.upsert_lead(con, place())
    db.upsert_lead(con, place(website="https://joes.ca", website_kind="real"))
    assert db.get(con, "p1")["stage"] == "dead"


def test_a_worked_lead_that_builds_a_site_is_left_for_the_human(con):
    db.upsert_lead(con, place())
    db.set_stage(con, "p1", "contacted")
    db.upsert_lead(con, place(website="https://joes.ca", website_kind="real"))
    assert db.get(con, "p1")["stage"] == "contacted"


def test_unknown_stage_is_rejected(con):
    db.upsert_lead(con, place())
    with pytest.raises(ValueError):
        db.set_stage(con, "p1", "probably_maybe")


def test_touch_updates_last_touch(con):
    db.upsert_lead(con, place())
    db.log_touch(con, "p1", "phone", "no answer", "tried at 9am")
    row = db.get(con, "p1")
    assert row["last_touch"] is not None
    assert db.touches(con, "p1")[0]["outcome"] == "no answer"


def test_market_density_counts_websites(con):
    for i in range(11):
        db.upsert_market(con, {"place_id": f"m{i}", "name": f"Roofer {i}",
                               "category": "Roofing contractor", "lat": 44.0, "lng": -79.4,
                               "website_kind": "real" if i < 9 else "none",
                               "rating": 4.2, "review_count": 10})
    assert db.market_density(con, "Roofing contractor") == {
        "category_total": 11, "category_with_site": 9}


def test_market_density_of_an_unknown_category_is_empty(con):
    assert db.market_density(con, None)["category_total"] == 0


def test_listing_filters(con):
    db.upsert_lead(con, place("a", score=90))
    db.upsert_lead(con, place("b", score=40, website_kind="social"))
    db.set_stage(con, "b", "queued")
    assert [r["place_id"] for r in db.leads(con)] == ["a", "b"]          # best first
    assert [r["place_id"] for r in db.leads(con, min_score=50)] == ["a"]
    assert [r["place_id"] for r in db.leads(con, kind="social")] == ["b"]
    assert [r["place_id"] for r in db.leads(con, stage="queued")] == ["b"]


def _age(con, place_id, days):
    old = (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat(timespec="seconds")
    con.execute("UPDATE leads SET refreshed_at=? WHERE place_id=?", (old, place_id))


def test_stale_finds_content_past_the_caching_limit(con):
    db.upsert_lead(con, place("old"))
    db.upsert_lead(con, place("fresh"))
    _age(con, "old", 45)
    stale = db.stale_leads(con)
    assert [r["place_id"] for r in stale] == ["old"]


def test_purge_scrubs_google_content_but_keeps_our_own(con):
    db.upsert_lead(con, place("old"))
    con.execute("UPDATE leads SET notes='left a card' WHERE place_id='old'")
    db.set_stage(con, "old", "contacted")
    _age(con, "old", 45)

    assert db.purge_stale(con) == 1
    row = db.get(con, "old")
    assert row["phone"] is None
    assert row["rating"] is None
    assert row["address"] is None
    assert row["refreshed_at"] is None
    assert row["stage"] == "contacted"        # ours
    assert row["notes"] == "left a card"      # ours
    assert row["place_id"] == "old"           # storable indefinitely


def test_purge_leaves_paying_clients_alone(con):
    db.upsert_lead(con, place("client"))
    db.set_stage(con, "client", "live")
    _age(con, "client", 90)
    assert db.purge_stale(con) == 0
    assert db.get(con, "client")["phone"] == "905-555-0123"


def test_geocache_expires(con):
    db.geocode_store(con, "Newmarket, ON", 44.05, -79.46, "Newmarket, ON, Canada")
    assert db.geocode_cached(con, "newmarket, on")["lat"] == 44.05
    old = (datetime.datetime.now() - datetime.timedelta(days=40)).isoformat(timespec="seconds")
    con.execute("UPDATE geocache SET refreshed_at=?", (old,))
    assert db.geocode_cached(con, "Newmarket, ON") is None


def test_spend_since_sums_actual_not_estimated_cost(con):
    db.record_scan(con, centre="44,-79", radius_m=10000, cell_m=900, types=["plumber"],
                   calls_planned=100, calls_made=90, seen=500, new_leads=40,
                   est_cost=4.0, actual_cost=3.6)
    assert db.spend_since(con, 30) == pytest.approx(3.6)


def test_connect_is_idempotent_and_migrates(tmp_path):
    path = str(tmp_path / "twice.db")
    c1 = db.connect(path)
    db.upsert_lead(c1, place())
    c1.commit()
    c1.close()
    c2 = db.connect(path)          # re-running the schema must not lose data
    assert db.get(c2, "p1") is not None
    c2.close()
