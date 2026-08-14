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

from PySide6.QtWidgets import QApplication                      # noqa: E402

import db                                                       # noqa: E402
import design                                                   # noqa: E402
from gui import theme                                           # noqa: E402
from gui.work import Job, Runner, _human                        # noqa: E402


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


def test_a_custom_service_needs_an_address_before_it_will_save(window, app):
    settings = window.pages[5]
    settings.google.set_text("AIzaTest")
    settings.provider.setCurrentIndex(settings.provider.findData("custom"))
    settings.copy_key.set_text("some-key")
    settings.copy_url.set_text("")
    settings.save()
    assert settings.copy_url.error.isVisibleTo(settings.copy_url)
