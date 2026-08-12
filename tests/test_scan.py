"""The scan loop, with Google mocked out. Nothing here may touch a paid API."""
import pytest
import requests

import db
import prospect


@pytest.fixture(autouse=True)
def no_real_http(monkeypatch):
    def explode(*a, **kw):
        raise AssertionError("a test tried to call a paid API")
    monkeypatch.setattr(requests, "post", explode)


@pytest.fixture()
def con(tmp_path):
    c = db.connect(str(tmp_path / "scan.db"))
    yield c
    c.close()


def raw(pid, name, *, website=None, status="OPERATIONAL", reviews=40,
        category="Plumber", lat=44.0, lng=-79.4):
    place = {
        "id": pid,
        "displayName": {"text": name},
        "primaryTypeDisplayName": {"text": category},
        "formattedAddress": "1 Main St, Newmarket, ON",
        "nationalPhoneNumber": "(905) 555-0123",
        "location": {"latitude": lat, "longitude": lng},
        "rating": 4.5,
        "userRatingCount": reviews,
        "businessStatus": status,
        "regularOpeningHours": {"weekdayDescriptions": ["Monday: 8:00 AM – 5:00 PM"]},
    }
    if website:
        place["websiteUri"] = website
    return place


# ---------------------------------------------------------------------------
def test_plan_is_cells_times_batches():
    sp = prospect.plan(44.05, -79.46, radius_km=5, cell_m=900,
                       types=["plumber", "electrician"] * 6)   # 12 types -> 2 batches
    assert len(sp.batches) == 2
    assert sp.calls == len(sp.cells) * 2
    assert sp.est_cost == pytest.approx(sp.calls * prospect.COST_PER_CALL)


def test_plan_batches_types_to_protect_recall():
    sp = prospect.plan(44.05, -79.46, 5, 900)
    assert all(len(b) <= prospect.TYPE_BATCH_SIZE for b in sp.batches)
    assert sum(len(b) for b in sp.batches) == len(prospect.DEFAULT_TYPES)


def test_field_mask_stays_frozen():
    # If this fails someone changed the billing tier of every call in the tool.
    assert prospect.FIELD_MASK == (
        "places.id,places.displayName,places.formattedAddress,"
        "places.nationalPhoneNumber,places.websiteUri,places.rating,"
        "places.userRatingCount,places.location,places.primaryTypeDisplayName,"
        "places.regularOpeningHours,places.businessStatus"
    )


# ---------------------------------------------------------------------------
def run_with(monkeypatch, con, pages, cells=1):
    """Run a scan whose every call returns `pages`."""
    monkeypatch.setattr(prospect, "search_nearby", lambda *a, **kw: pages)
    sp = prospect.plan(44.05, -79.46, radius_km=0.5, cell_m=2000, types=["plumber"])
    return sp, prospect.run("fake-key", con, sp)


def test_scan_keeps_the_sites_it_wants_and_drops_the_rest(monkeypatch, con):
    pages = [
        raw("keep1", "Joe's Plumbing"),
        raw("keep2", "Sam's Electric", website="https://facebook.com/sams"),
        raw("drop_site", "Big Co", website="https://bigco.ca"),
        raw("drop_chain", "Tim Hortons"),
        raw("drop_closed", "Gone Fishing", status="CLOSED_PERMANENTLY"),
    ]
    sp, res = run_with(monkeypatch, con, pages)

    ids = {r["place_id"] for r in db.leads(con)}
    assert ids == {"keep1", "keep2"}
    assert res.new_leads == 2
    assert res.near_miss == 1
    assert res.franchises_dropped == 1
    assert res.closed_dropped == 1


def test_businesses_with_websites_are_kept_as_market_data(monkeypatch, con):
    # The scan already paid for these. Phase 2.5 needs them for "9 of 11 nearby
    # roofers have a website and you don't".
    pages = [raw(f"r{i}", f"Roofer {i}", category="Roofing contractor",
                 website="https://r.ca" if i < 9 else None) for i in range(11)]
    run_with(monkeypatch, con, pages)
    assert db.market_density(con, "Roofing contractor") == {
        "category_total": 11, "category_with_site": 9}


def test_a_closed_business_is_not_market_data_either(monkeypatch, con):
    run_with(monkeypatch, con, [raw("x", "Gone", status="CLOSED_TEMPORARILY")])
    assert db.market_density(con, "Plumber")["category_total"] == 0


def test_the_same_place_seen_in_overlapping_cells_counts_once(monkeypatch, con):
    sp = prospect.plan(44.05, -79.46, radius_km=2, cell_m=900, types=["plumber"])
    assert len(sp.cells) > 1
    monkeypatch.setattr(prospect, "search_nearby",
                        lambda *a, **kw: [raw("dup", "Joe's Plumbing")])
    res = prospect.run("fake-key", con, sp)
    assert res.seen == sp.calls          # returned once per call
    assert res.unique == 1
    assert res.new_leads == 1


def test_a_saturated_cell_is_reported_not_silently_truncated(monkeypatch, con):
    pages = [raw(f"p{i}", f"Shop {i}") for i in range(prospect.MAX_RESULTS)]
    sp, res = run_with(monkeypatch, con, pages)
    assert res.saturated_cells == sp.calls


def test_actual_spend_is_logged_per_run(monkeypatch, con):
    sp, res = run_with(monkeypatch, con, [raw("a", "Joe's Plumbing")])
    row = con.execute("SELECT * FROM scans ORDER BY id DESC LIMIT 1").fetchone()
    assert row["calls_made"] == sp.calls
    assert row["actual_cost"] == pytest.approx(res.actual_cost)
    assert row["est_cost"] == pytest.approx(sp.est_cost)


def test_failed_calls_do_not_lose_the_leads_already_paid_for(monkeypatch, con):
    calls = {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] % 2:
            raise prospect.PlacesError("HTTP 503")
        return [raw(f"p{calls['n']}", f"Shop {calls['n']}")]

    monkeypatch.setattr(prospect, "search_nearby", flaky)
    sp = prospect.plan(44.05, -79.46, radius_km=2, cell_m=900, types=["plumber"])
    res = prospect.run("fake-key", con, sp)
    assert res.calls_failed > 0
    assert res.new_leads > 0
    assert len(db.leads(con)) == res.new_leads


def test_five_failures_in_a_row_stops_the_scan(monkeypatch, con):
    def always_fail(*a, **kw):
        raise prospect.PlacesError("HTTP 403 — key rejected")

    monkeypatch.setattr(prospect, "search_nearby", always_fail)
    sp = prospect.plan(44.05, -79.46, radius_km=5, cell_m=900, types=["plumber"])
    res = prospect.run("fake-key", con, sp)
    assert res.aborted
    assert res.calls_failed == 5          # not the hundreds the plan called for
    assert res.calls_failed < sp.calls


def test_costed_at_the_calls_actually_attempted(monkeypatch, con):
    sp, res = run_with(monkeypatch, con, [raw("a", "Joe's Plumbing")])
    assert res.actual_cost == pytest.approx(
        (res.calls_made + res.calls_failed) * prospect.COST_PER_CALL)


# ---------------------------------------------------------------------------
def test_geocode_uses_the_cache_instead_of_paying_twice(con):
    db.geocode_store(con, "Newmarket, ON", 44.05, -79.46, "Newmarket, ON, Canada")
    # requests.post is rigged to explode, so a cache miss would fail this test.
    lat, lng, label = prospect.geocode("fake-key", "Newmarket, ON", "CA", con)
    assert (lat, lng) == (44.05, -79.46)
    assert label == "Newmarket, ON, Canada"
