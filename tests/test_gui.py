"""The desktop app.

Qt is tested offscreen — every page is constructed against a real database and
a real stylesheet, which is what catches the class of bug that only appears
when a widget meets data: a column that does not exist yet, an empty state that
divides by zero, a signal wired to a method that was renamed.

These skip cleanly when PySide6 is not installed, so the CLI-only checkout is
unaffected.
"""
import os

import pytest

# QtWidgets, not PySide6. The top-level package imports fine from the wheel
# alone; it is QtWidgets that pulls in the Qt shared objects, so a machine with
# PySide6 installed but no libEGL passes the first check and then dies on the
# import below. Guarding on the module that actually has the dependency is what
# makes "the desktop app is optional" true rather than aspirational.
pytest.importorskip("PySide6.QtWidgets", reason="the desktop app is optional")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel              # noqa: E402

import db                                                       # noqa: E402
import design                                                   # noqa: E402
from gui import theme                                           # noqa: E402
from gui.work import Job, Runner, _human                        # noqa: E402
from types import SimpleNamespace                               # noqa: E402


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "gui.db"))
    monkeypatch.setenv("LEADSMITH_CONFIG", str(tmp_path / "config.json"))
    theme.apply(app, dark=True)
    from gui.app import Window
    win = Window()
    yield win
    win.con.close()


# ---------------------------------------------------------------------------
# Colour
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("dark", [True, False])
def test_every_text_colour_in_the_app_is_readable(dark):
    """The same 4.5:1 bar the generated client sites are held to.

    The operator reads this for hours in a room with a window. Holding our own
    tool to a lower standard than the pages it produces would be an odd place
    to draw the line.
    """
    p = theme.palette_for(dark)
    pairs = {
        "ink on bg": (p.ink, p.bg),
        "ink on surface": (p.ink, p.surface),
        "ink on surface_alt": (p.ink, p.surface_alt),
        "muted on surface": (p.muted, p.surface),
        "muted on bg": (p.muted, p.bg),
        "accent_ink on accent": (p.accent_ink, p.accent),
        "good on surface": (p.good, p.surface),
        "warn on surface": (p.warn, p.surface),
        "bad on surface": (p.bad, p.surface),
        "sidebar text": (p.sidebar_ink, p.sidebar),
        "sidebar muted": (p.sidebar_muted, p.sidebar),
    }
    for name, (fg, bg) in pairs.items():
        ratio = design.contrast(fg, bg)
        assert ratio >= design.AA_TEXT, f"{name} = {ratio:.2f} ({'dark' if dark else 'light'})"


def test_both_themes_define_every_colour():
    for palette in (theme.DARK, theme.LIGHT):
        for field, value in vars(palette).items():
            assert value.startswith("#") and len(value) == 7, field


def test_the_stylesheet_renders_for_both_themes():
    for dark in (True, False):
        css = theme.stylesheet(theme.palette_for(dark))
        assert "QPushButton" in css and "QTableWidget" in css
        assert "{p." not in css, "an unformatted placeholder leaked through"


# ---------------------------------------------------------------------------
# The worker
# ---------------------------------------------------------------------------
def test_a_job_that_raises_does_not_take_the_window_with_it(app):
    seen = {}
    job = Job(lambda: 1 / 0)
    job.signals.failed.connect(lambda msg, detail: seen.update(msg=msg, detail=detail))
    job.run()
    assert "ZeroDivisionError" in seen["msg"]
    assert "Traceback" in seen["detail"]


def test_our_own_errors_keep_the_sentence_they_were_written_with():
    import generate
    message = _human(generate.ContentError("This copy cannot go on someone's website"))
    assert message == "This copy cannot go on someone's website"


def test_an_anonymous_error_still_says_what_kind_it_was():
    assert _human(KeyError("place_id")).startswith("KeyError")


def test_a_job_reports_progress_and_stops_when_cancelled(app):
    job = Job(lambda: None)
    ticks = []
    job.signals.progress.connect(lambda d, t, m: ticks.append((d, t)))
    assert job.report(1, 10, "working") is True
    job.cancel()
    # False is how a long loop learns to stop at its next checkpoint rather
    # than being killed mid-write.
    assert job.report(2, 10, "working") is False
    assert ticks == [(1, 10), (2, 10)]


def test_only_one_job_runs_at_a_time(app):
    """Two scans at once would double-spend against the Places API."""
    runner = Runner()
    import threading
    gate = threading.Event()
    first = Job(gate.wait)
    assert runner.start(first) is True
    assert runner.start(Job(lambda: None)) is False, "second job should be refused"
    gate.set()
    runner.wait(5000)


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------
def test_every_page_builds_against_an_empty_database(window, app):
    assert window.stack.count() == 6
    for index in range(window.stack.count()):
        window.go(index)
        app.processEvents()
        assert window.stack.currentIndex() == index


def test_every_page_builds_against_a_populated_one(window, app):
    for i in range(6):
        db.upsert_lead(window.con, {
            "place_id": f"g{i}", "name": f"Business {i}",
            "category": "Roofing contractor", "phone": "(905) 555-0100",
            "address": "1 Main St, Newmarket, ON", "website_kind": "none",
            "rating": 4.5, "review_count": 30})
        window.con.execute("UPDATE leads SET score=? WHERE place_id=?", (60 + i, f"g{i}"))
    db.set_subscription(window.con, "g0", plan="no-money-down", monthly=149)
    window.con.commit()

    for index in range(window.stack.count()):
        window.go(index)
        app.processEvents()
    window.update_sidebar_foot()
    assert "client" in window.sidebar.foot.text()


def test_the_call_list_page_shows_a_card_per_lead(window, app):
    db.upsert_lead(window.con, {"place_id": "g1", "name": "Halstead Roofing",
                                "category": "Roofing contractor",
                                "phone": "(905) 555-0142", "website_kind": "none"})
    window.con.commit()
    window.go(0)
    app.processEvents()
    today = window.pages[0]
    assert len(today.cards) == 1
    assert "Halstead Roofing" in today.cards[0].lead["name"]


def test_a_paying_client_is_not_offered_as_a_lead(window, app):
    db.upsert_lead(window.con, {"place_id": "g1", "name": "Client",
                                "category": "Roofing", "website_kind": "none"})
    db.set_subscription(window.con, "g1", plan="standard", monthly=60)
    window.con.commit()
    window.go(0)
    app.processEvents()
    assert window.pages[0].cards == []


def test_the_scan_cost_is_computed_from_the_same_grid_the_scan_uses(window, app):
    import prospect
    find = window.pages[1]
    find.radius.setValue(10.0)
    find.density.setCurrentIndex(1)          # 1500m
    app.processEvents()

    cells = prospect.grid(44.0, -79.5, 10000, 1500)
    batches = -(-len(prospect.DEFAULT_TYPES) // prospect.TYPE_BATCH_SIZE)
    expected = len(cells) * batches * prospect.COST_PER_CALL
    # The number in the button is the number that will be charged, not an
    # approximation of it.
    assert f"${expected:,.2f}" in find.scan_button.text()
    assert find.cells.value.text() == f"{len(cells):,}"


def test_the_cost_updates_when_the_form_changes(window, app):
    find = window.pages[1]
    find.radius.setValue(5.0)
    app.processEvents()
    small = find.scan_button.text()
    find.radius.setValue(20.0)
    app.processEvents()
    assert find.scan_button.text() != small


def test_scanning_is_refused_without_a_key(window, app):
    window.cfg = {"business": {}, "defaults": {}}
    find = window.pages[1]
    find.refresh()
    assert not find.scan_button.isEnabled()
    assert find.banner.isVisibleTo(find)


def test_busy_disables_the_buttons_that_spend_money(window, app):
    window._busy_changed(True)
    app.processEvents()
    assert not window.pages[1].scan_button.isEnabled()
    window._busy_changed(False)


def test_settings_never_shows_a_placeholder_as_if_it_were_a_key(window, app):
    import config
    config.save({"google_api_key": "PASTE_YOUR_PLACES_API_KEY_HERE"},
                os.environ["LEADSMITH_CONFIG"])
    settings = window.pages[5]
    settings.refresh()
    assert settings.google.text() == ""


def test_settings_round_trips_through_disk(window, app):
    settings = window.pages[5]
    settings.google.set_text("AIzaTest")
    settings.operator.set_text("Noah")
    settings.nmd_monthly.set_text("199")
    settings.save()

    import config
    saved = config.load()
    assert saved["google_api_key"] == "AIzaTest"
    assert saved["business"]["operator_name"] == "Noah"
    assert saved["pricing"]["nmd_monthly"] == 199.0


def test_settings_refuses_a_price_with_a_dollar_sign(window, app):
    settings = window.pages[5]
    settings.google.set_text("AIzaTest")
    settings.nmd_monthly.set_text("$199")
    settings.save()
    assert settings.nmd_monthly.error.isVisibleTo(settings.nmd_monthly)


# ---------------------------------------------------------------------------
# Packaging
# ---------------------------------------------------------------------------
def test_the_frozen_build_keeps_its_data_beside_the_executable(monkeypatch, tmp_path):
    """A one-file build unpacks to a temp directory that is deleted on exit.
    Writing the database there would lose every lead when the app closed."""
    import leadsmith_app
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr("sys.executable", str(tmp_path / "Leadsmith.exe"))
    for name in ("LEADSMITH_DB", "LEADSMITH_CONFIG", "LEADSMITH_SITES"):
        monkeypatch.delenv(name, raising=False)

    leadsmith_app._frozen_paths()
    assert os.path.dirname(os.environ["LEADSMITH_DB"]) == str(tmp_path)
    assert os.path.dirname(os.environ["LEADSMITH_CONFIG"]) == str(tmp_path)


def test_the_spec_ships_the_templates_the_app_reads_at_runtime():
    spec = open("build/leadsmith.spec", encoding="utf-8").read()
    assert '"templates"' in spec
    # PyInstaller raises on a data path that does not exist, so anything that
    # only some branches have must be added conditionally. `functions/` arrives
    # with Phase 5 and this spec has to build without it.
    assert "os.path.isdir(_functions)" in spec
    # Imported by name, so PyInstaller's static analysis misses them.
    for module in ("segno", "phonenumbers", "anthropic"):
        assert module in spec
    assert "console=False" in spec, "a GUI build must not flash a terminal"


def workflow() -> str:
    return open(".github/workflows/windows-app.yml", encoding="utf-8").read()


def windows_job() -> str:
    job = workflow().split("\n  build:", 1)
    assert len(job) == 2, "the Windows job is no longer called `build`"
    return job[1]


def test_the_windows_job_installs_the_things_it_then_runs():
    """A run failed here for a whole day: the job pip-installed PySide6 and
    PyInstaller by name, then ran `python -m pytest`, which was never installed.
    It died in one second, and because the build step needs it, every step after
    — including the one that uploads the .exe — was skipped. The symptom was an
    absent artifact rather than an error anyone would look at.

    Naming the extras instead of restating their contents is what keeps the two
    from drifting apart again.
    """
    job = windows_job()
    assert "python -m pytest" in job
    assert '".[dev,build]"' in job, "install the declared extras, not a copy of them"

    import tomllib
    extras = tomllib.load(open("pyproject.toml", "rb"))["project"]["optional-dependencies"]
    assert any(d.startswith("pytest") for d in extras["dev"])
    assert any(d.startswith("pyinstaller") for d in extras["build"])
    assert any(d.startswith("PySide6") for d in extras["build"])


def test_the_exe_launch_check_actually_waits_for_the_exe():
    """`console=False` makes this a GUI-subsystem binary, and PowerShell does
    not block on one — it returns as soon as the process is spawned. Calling the
    exe directly would therefore pass while the app crashed on launch, which is
    the single thing this step exists to catch.
    """
    job = windows_job()
    launch = job.split("Check it starts", 1)[1].split("Report size", 1)[0]
    assert "Start-Process" in launch and "WaitForExit" in launch
    assert "ExitCode" in launch, "a non-zero exit has to fail the job"


# ---------------------------------------------------------------------------
# Choosing who writes the copy
# ---------------------------------------------------------------------------
def test_the_copywriter_choice_round_trips_through_disk(window, app):
    settings = window.pages[5]
    settings.google.set_text("AIzaTest")
    settings.provider.setCurrentIndex(settings.provider.findData("gemini"))
    settings.copy_key.set_text("AIzaGeminiKey")
    settings.save()

    import config
    saved = config.load()
    assert saved["copy"]["provider"] == "gemini"
    assert saved["copy"]["api_key"] == "AIzaGeminiKey"

    settings.refresh()
    assert settings.provider.currentData() == "gemini"
    assert settings.copy_key.text() == "AIzaGeminiKey"


def test_a_key_saved_before_there_was_a_choice_still_works(window, app):
    """The old top-level anthropic_api_key must not strand an operator."""
    from gui import actions
    window.cfg = {"anthropic_api_key": "sk-ant-old", "business": {}, "defaults": {}}

    spec = actions.copywriter(window)
    assert spec["provider"] == "anthropic"
    assert spec["api_key"] == "sk-ant-old"


def test_a_placeholder_from_the_example_config_is_not_treated_as_a_key(window, app):
    from gui import actions
    window.cfg = {"anthropic_api_key": "PASTE_YOUR_ANTHROPIC_API_KEY_HERE",
                  "business": {}, "defaults": {}}
    assert not actions.copywriter(window)["api_key"]


def test_checking_a_key_does_not_wipe_what_was_typed(window, app, monkeypatch):
    """Check is a job, and every finished job refreshes the current page.

    That refresh reloaded Settings from config.json, so pressing Check to
    confirm a copywriter key blanked the key, put the service back to
    Anthropic, and took the unsaved Google Places key with it — on the one
    page where nothing has been written to disk yet.
    """
    import generate
    monkeypatch.setattr(generate, "list_models", lambda spec: ["gemini-2.5-flash"])

    settings = window.pages[5]
    window.go(5)
    settings.provider.setCurrentIndex(settings.provider.findData("gemini"))
    settings.copy_key.set_text("AIzaGeminiKey")
    settings.google.set_text("AIzaPlacesKey")

    settings.check_models()
    assert window.runner.wait(20000), "the model check never finished"
    app.processEvents()

    assert settings.provider.currentData() == "gemini"
    assert settings.copy_key.text() == "AIzaGeminiKey"
    assert settings.google.text() == "AIzaPlacesKey"
    assert "accepts this key" in settings.provider_note.text()


def test_unsaved_settings_survive_a_refresh_but_not_a_reload(window, app):
    """Navigation and F5 must not eat a half-filled form; Reload must.

    Reload is the operator asking for the saved values back, so it is the one
    place discarding what they typed is what they meant.
    """
    settings = window.pages[5]
    settings.google.set_text("AIzaTest")
    settings.provider.setCurrentIndex(settings.provider.findData("gemini"))
    settings.copy_key.set_text("AIzaGeminiKey")
    settings.save()

    settings.provider.setCurrentIndex(settings.provider.findData("groq"))
    settings.copy_key.set_text("gsk_unsaved")

    window.go(0)
    window.go(5)
    app.processEvents()
    assert settings.provider.currentData() == "groq"
    assert settings.copy_key.text() == "gsk_unsaved"

    window.refresh()                      # F5
    assert settings.copy_key.text() == "gsk_unsaved"

    settings.load()                       # the Reload button
    assert settings.provider.currentData() == "gemini"
    assert settings.copy_key.text() == "AIzaGeminiKey"


def test_a_custom_service_needs_an_address_before_it_will_save(window, app):
    settings = window.pages[5]
    settings.google.set_text("AIzaTest")
    settings.provider.setCurrentIndex(settings.provider.findData("custom"))
    settings.copy_key.set_text("some-key")
    settings.copy_url.set_text("")
    settings.save()
    assert settings.copy_url.error.isVisibleTo(settings.copy_url)


def test_a_custom_service_cannot_be_saved_without_an_address(window, app):
    """Saving one produces a config that can only fail later, in the middle of
    building a client's site."""
    settings = window.pages[5]
    settings.google.set_text("AIzaTest")
    settings.provider.setCurrentIndex(settings.provider.findData("custom"))
    settings.copy_key.set_text("")          # no key either — still not saveable
    settings.copy_url.set_text("")
    settings.save()
    assert settings.copy_url.error.isVisibleTo(settings.copy_url)


# ---------------------------------------------------------------------------
# The buttons actually run
#
# Every action is written `def work(job)` and started as `Job(work)` — the job
# cannot be passed at construction because it does not exist yet. That
# injection was documented but never implemented, so Scan, Build site, Publish
# and Check Stripe all raised TypeError the instant their thread started. The
# page tests missed it because building a page never presses its buttons.
# ---------------------------------------------------------------------------
def test_a_job_hands_itself_to_work_that_asked_for_it(app):
    seen = {}

    def work(job):
        seen["job"] = job
        return "done"

    job = Job(work)
    job.signals.failed.connect(lambda m, d: seen.update(failed=m))
    job.run()
    assert seen.get("failed") is None, seen.get("failed")
    assert seen["job"] is job


def test_a_job_hands_over_the_progress_callback_when_asked(app):
    seen = {}
    job = Job(lambda report: seen.update(report=report))
    job.run()
    assert seen["report"] == job.report


def test_a_plain_function_is_left_alone(app):
    """`Job(fn, arg)` must keep working — nothing is injected uninvited."""
    job = Job(lambda value: value * 2, 21)
    got = {}
    job.signals.finished.connect(lambda r: got.update(result=r))
    job.run()
    assert got["result"] == 42


def _place(pid, name):
    """One business as the Places API hands it over."""
    return {
        "id": pid,
        "displayName": {"text": name},
        "primaryTypeDisplayName": {"text": "Plumber"},
        "formattedAddress": "1 Main St, Newmarket, ON",
        "nationalPhoneNumber": "(905) 555-0123",
        "location": {"latitude": 44.05, "longitude": -79.46},
        "rating": 4.5,
        "userRatingCount": 40,
        "businessStatus": "OPERATIONAL",
    }


def _scan_job(window, monkeypatch, *, search=None, radius_km=0.5, cell_m=2500,
              page=None):
    """Press Scan and hand back the job it queued, plus its done callback.

    The one thing this deliberately does not stub is `prospect.run`. That
    function decides what it hands the progress callback, and a hand-written
    stand-in agreeing with the callback proves only that the two stubs agree —
    which is exactly how `progress() takes 2 positional arguments but 3 were
    given` reached a release with a green suite. Only Google is faked out.
    """
    import prospect
    from gui import actions

    monkeypatch.setattr(config := __import__("config"), "api_key", lambda cfg: "AIzaTest")
    monkeypatch.setattr(window, "confirm", lambda *a, **k: True)

    def fake_geocode(key, town, region, con):
        db.geocode_cached(con, town)          # the call in the real traceback
        return 44.05, -79.46, "Newmarket, ON"

    monkeypatch.setattr(prospect, "geocode", fake_geocode)
    monkeypatch.setattr(prospect, "search_nearby",
                        search or (lambda *a, **kw: [_place("p1", "Joe's Plumbing")]))

    captured = {}
    monkeypatch.setattr(window, "run_job",
                        lambda job, **kw: captured.update(job=job, kw=kw) or True)
    actions.scan(window, "Newmarket", radius_km, cell_m, dry_run=False, page=page)
    return captured["job"], captured["kw"]["on_done"]


def test_pressing_scan_runs_the_scan(window, app, monkeypatch):
    """Press the button, then run the real scan on a real worker thread.

    Three things this has to do that an easier version of it does not. It runs
    through the Runner rather than calling `job.run()` inline, because the bug
    class here is cross-thread use and running on the calling thread cannot
    reproduce it. It touches the database on the connection the job was handed,
    because a scan that never queries anything proves nothing about which
    connection it would have used. And it drives the genuine `prospect.run`, so
    the progress callback the app supplies is called the way the scan really
    calls it.
    """
    job, _ = _scan_job(window, monkeypatch)

    failed, progressed = {}, []
    job.signals.failed.connect(lambda m, d: failed.update(message=m, detail=d))
    job.signals.progress.connect(lambda d, t, m: progressed.append((d, t, m)))

    runner = Runner()
    assert runner.start(job) is True
    assert runner.wait(20000), "the scan job never finished"
    app.processEvents()
    assert not failed, failed.get("message")

    assert db.leads(window.con), "the scan saved nothing"
    # The last update carries the running count, which is the third argument
    # the app used to refuse.
    assert any("1 businesses so far" in message for _, _, message in progressed)


def test_stop_actually_stops_a_scan(window, app, monkeypatch):
    """The Stop button is wired to `Job.cancel`; this is the other half of it.

    Cancelling set a flag that `job.report` returned and `prospect.run` threw
    away, so the button was decoration: a 400-call scan carried on spending
    after it was pressed. The scan is cancelled from inside the fake search, so
    the flag is already set when the first progress report goes back.
    """
    import prospect

    calls = []

    def search(*a, **kw):
        calls.append(1)
        job.cancel()                         # as if Stop were pressed
        return [_place(f"p{len(calls)}", f"Shop {len(calls)}")]

    job, _ = _scan_job(window, monkeypatch, search=search)
    plan = prospect.plan(44.05, -79.46, 0.5, 2500)
    assert plan.calls > 1, "a one-call scan cannot show that it stopped short"

    result = {}
    job.signals.finished.connect(lambda payload: result.update(payload=payload))
    job.signals.failed.connect(lambda m, d: result.update(failed=m))

    runner = Runner()
    assert runner.start(job) is True
    assert runner.wait(20000)
    app.processEvents()

    assert "failed" not in result, result.get("failed")
    _, res = result["payload"]
    assert len(calls) == 1, "the scan kept spending after Stop"
    assert "stopped by you" in res.aborted
    # Stopping is not losing: what was already paid for is saved.
    assert db.leads(window.con), "the cancelled scan threw away paid-for leads"


def test_a_scan_that_stopped_early_says_so(window, app, monkeypatch):
    """Silence here is the expensive kind.

    The app used to report a scan that died after five straight API failures
    with the same "Found 0 new leads" as a scan that worked, and then move the
    operator to another page — so the only account of what happened to the
    money was a card they had navigated away from.
    """
    import prospect

    def always_fail(*a, **kw):
        raise prospect.PlacesError("HTTP 403 — key rejected")

    find = window.pages[1]
    window.go(1)
    # Wide enough to plan more than the five calls it takes to give up, so a
    # scan that stopped short is distinguishable from one that simply ended.
    job, on_done = _scan_job(window, monkeypatch, search=always_fail,
                             radius_km=2, cell_m=900, page=find)

    runner = Runner()
    assert runner.start(job, on_done=on_done) is True
    assert runner.wait(20000)
    app.processEvents()

    shown = find.result.findChildren(QLabel)
    text = " ".join(label.text() for label in shown)
    assert "stopped early" in text.lower()
    assert "403" in text, "the reason it stopped is not on screen"
    assert window.stack.currentIndex() == 1, \
        "the operator was moved away from the only record of the failure"


# ---------------------------------------------------------------------------
# The other three buttons that spend money or touch the network. Each runs the
# real action on a real worker thread with only the outside world faked, for
# the reason spelled out on `_scan_job`: stubs that agree with each other prove
# nothing, and these three shipped broken once already.
# ---------------------------------------------------------------------------
COPY = {
    "hero_headline": "Roofing done right in Newmarket",
    "hero_sub": "Repairs, replacements and flat roofing across York Region.",
    "about": ["We reroof houses in Newmarket and the towns around it.",
              "Most jobs start with a look at the roof and a plain answer."],
    "services": [
        {"name": "Roof replacement", "description": "Full tear-off and new shingles."},
        {"name": "Leak repair", "description": "Finding the leak and stopping it."},
        {"name": "Flat roofing", "description": "Modified bitumen on low-slope roofs."},
    ],
    "why_us": ["We answer the phone", "Written scope before work starts",
               "We clean up the yard"],
    "cta_line": "Call and we will take a look",
    "meta_title": "Halstead Roofing — Roofing in Newmarket",
    "meta_description": "Roof replacement, leak repair and flat roofing in Newmarket.",
}


def _seed_lead(window):
    lead = {"place_id": "g1", "name": "Halstead Roofing",
            "category": "Roofing contractor", "phone": "(905) 555-0142",
            "address": "1 Main St, Newmarket, ON", "website_kind": "none",
            "rating": 4.6, "review_count": 31}
    db.upsert_lead(window.con, lead)
    window.con.commit()
    return dict(db.get(window.con, "g1"))


def _run(job, app, timeout=20000):
    """Run a job to completion on a worker thread and hand back the outcome."""
    outcome = {}
    job.signals.finished.connect(lambda r: outcome.update(result=r))
    job.signals.failed.connect(lambda m, d: outcome.update(failed=m, detail=d))
    runner = Runner()
    assert runner.start(job) is True
    assert runner.wait(timeout), "the job never finished"
    app.processEvents()
    assert "failed" not in outcome, outcome.get("detail") or outcome.get("failed")
    return outcome["result"]


def test_building_a_site_writes_one(window, app, monkeypatch, tmp_path):
    """Everything but the model call is real: copy review, render, disk, db."""
    import generate
    from gui import actions

    monkeypatch.setattr(generate, "SITES_DIR", str(tmp_path / "sites"))
    monkeypatch.setattr(generate, "_client", lambda key=None: SimpleNamespace())
    monkeypatch.setattr(generate, "_call",
                        lambda client, facts, correction="", model=None: (
                            COPY, SimpleNamespace(input_tokens=900, output_tokens=700)))
    window.cfg["copy"] = {"provider": "anthropic", "api_key": "sk-test"}
    window.cfg.setdefault("business", {})["home_city"] = "Newmarket, ON"
    monkeypatch.setattr(window, "confirm", lambda *a, **k: True)

    captured = {}
    monkeypatch.setattr(window, "run_job",
                        lambda job, **kw: captured.update(job=job, kw=kw) or True)
    actions.build_site(window, _seed_lead(window))

    path = _run(captured["job"], app)
    assert os.path.exists(path), "the button reported a page that is not there"
    assert "Halstead Roofing" in open(path, encoding="utf-8").read()
    # The worker's own connection has to be the one that committed this.
    assert db.get(window.con, "g1")["site_dir"], "the site was never recorded"
    assert generate.load_content("g1")["copy"] == COPY


def test_publishing_a_site_records_the_url(window, app, monkeypatch, tmp_path):
    import deploy
    import generate
    from gui import actions

    monkeypatch.setattr(generate, "SITES_DIR", str(tmp_path / "sites"))
    lead = _seed_lead(window)
    generate.write_site("g1", generate.render(
        lead, {**COPY, "verified_facts": [], "photos": []},
        template="trade", preview=True))

    monkeypatch.setattr(deploy, "wrangler_path", lambda: "/usr/bin/wrangler")
    monkeypatch.setattr(deploy, "deploy", lambda site_dir, project, **kw:
                        SimpleNamespace(url="https://abc.leadsmith-previews.pages.dev",
                                        project=project, files=2, bytes=2048))
    monkeypatch.setattr(window, "confirm", lambda *a, **k: True)

    captured = {}
    monkeypatch.setattr(window, "run_job",
                        lambda job, **kw: captured.update(job=job) or True)
    actions.preview_site(window, lead)

    result = _run(captured["job"], app)
    assert result.url.endswith("pages.dev")
    assert db.get(window.con, "g1")["preview_url"] == result.url


def test_checking_stripe_runs_on_a_worker_and_commits(window, app, monkeypatch):
    import billing
    from gui import actions

    window.cfg["stripe_secret_key"] = "sk_test_123"
    seen = {}

    def fake_sync(con, key):
        # The connection has to be usable from this thread, which is the whole
        # reason the action opens its own instead of borrowing the window's.
        con.execute("SELECT count(*) FROM subscriptions").fetchone()
        seen["key"] = key
        return SimpleNamespace(matched=3, problems=[])

    monkeypatch.setattr(billing, "sync", fake_sync)

    captured = {}
    monkeypatch.setattr(window, "run_job",
                        lambda job, **kw: captured.update(job=job) or True)
    actions.sync_billing(window)

    result = _run(captured["job"], app)
    assert seen["key"] == "sk_test_123"
    assert result.matched == 3


# ---------------------------------------------------------------------------
# The splash screen
#
# Startup is dominated by things no Qt code can cover: unpacking the one-file
# build, then importing PySide6. The splash therefore comes from PyInstaller's
# bootloader, and the only jobs left to us are taking it down at the right
# moment and never letting it break the app.
# ---------------------------------------------------------------------------
def test_the_splash_helpers_do_nothing_at_all_from_source():
    """No pyi_splash outside a frozen build — these must be quiet no-ops."""
    import startup
    assert startup._splash() is None
    startup.note("anything")       # must not raise
    startup.done()
    startup.done()                 # and must be safe twice


def test_a_broken_splash_cannot_take_the_app_down(monkeypatch):
    """Cosmetic failure stays cosmetic."""
    import startup

    class Exploding:
        def is_alive(self): return True
        def update_text(self, _): raise ConnectionError("splash died")
        def close(self): raise ConnectionError("splash died")

    monkeypatch.setattr(startup, "_splash", lambda: Exploding())
    startup.note("still fine")
    startup.done()


def test_the_window_takes_the_splash_down_once_it_is_up():
    """Ordering is the whole point: closing before show() leaves a blank gap,
    and never closing leaves a loading box over a working app."""
    import inspect
    from gui import app as gui_app

    src = inspect.getsource(gui_app.main)
    assert "startup.done()" in src, "the splash is never closed"
    assert src.index("window.show()") < src.index("startup.done()"), \
        "the splash must come down after the window is painted, not before"


def test_the_spec_ships_a_splash():
    spec = open("build/leadsmith.spec", encoding="utf-8").read()
    assert "Splash(" in spec
    # Both halves are required for a one-file build; the binaries carry Tcl/Tk.
    assert "splash," in spec and "splash.binaries," in spec

    import os
    import struct
    png = os.path.join("build", "splash.png")
    assert os.path.exists(png), "the spec names an image that is not committed"
    data = open(png, "rb").read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    width, height = struct.unpack(">II", data[16:24])
    # PyInstaller downscales anything larger, which would blur the wordmark.
    assert (width, height) <= (760, 480), f"{width}x{height} will be resized"
