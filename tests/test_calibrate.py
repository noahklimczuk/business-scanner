"""The bench harness is dev-only, but it is the thing that decides whether the
fabrication check gets tightened or loosened — so it gets the same treatment as
the code it measures."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "tools"))

import calibrate                                          # noqa: E402
import generate                                           # noqa: E402


# ---------------------------------------------------------------------------
# Schema translation
# ---------------------------------------------------------------------------
def test_gemini_schema_drops_additional_properties_everywhere():
    # Gemini's responseSchema is OpenAPI-flavoured and 400s on the keyword.
    converted = calibrate._gemini_schema(generate.CONTENT_SCHEMA)
    assert "additionalProperties" not in json.dumps(converted)


def test_gemini_schema_keeps_the_parts_that_carry_meaning():
    converted = calibrate._gemini_schema(generate.CONTENT_SCHEMA)
    assert set(converted["properties"]) == set(
        generate.CONTENT_SCHEMA["properties"])
    assert converted["required"] == generate.CONTENT_SCHEMA["required"]
    # `services` is the nested case: an array of objects, both levels rewritten.
    services = converted["properties"]["services"]
    assert services["type"] == "array"
    assert set(services["items"]["properties"]) == {"name", "description"}


def test_gemini_schema_leaves_non_dicts_alone():
    assert calibrate._gemini_schema("string") == "string"


# ---------------------------------------------------------------------------
# The honest-copy guard
# ---------------------------------------------------------------------------
def test_honest_copy_is_not_rejected():
    """The guard that stops a calibration run from tightening the check into
    something that refuses ordinary, truthful writing."""
    assert calibrate.honest_copy_still_passes() == []


def test_the_guard_actually_detects_an_over_broad_term(monkeypatch):
    # If this passed with a term list that rejects everything, the guard would
    # be decorative.
    monkeypatch.setattr(calibrate, "HONEST", ["We fix roofs."])
    monkeypatch.setattr(generate, "review", lambda content, source: ["too broad"])
    assert calibrate.honest_copy_still_passes() == ["'We fix roofs.' -> too broad"]


# ---------------------------------------------------------------------------
# A whole run, against a provider that never touches the network
# ---------------------------------------------------------------------------
class FakeProvider:
    name = "fake"
    model = "fake-1"

    def __init__(self, copy):
        self.copy, self.calls = copy, 0

    def draft(self, system, facts):
        self.calls += 1
        return dict(self.copy), {"in": 900, "out": 400}


CLEAN = {
    "hero_headline": "Roofing in Newmarket",
    "hero_sub": "We answer the phone and come and look at it.",
    "about": ["We work on roofs.", "Newmarket and the towns around it."],
    "services": [{"name": f"Service {i}", "description": "One line."}
                 for i in range(3)],
    "why_us": ["We answer the phone", "Written scope first", "We tidy up"],
    "cta_line": "Call and we will take a look",
    "meta_title": "Halstead Roofing — Newmarket",
    "meta_description": "Roofing in Newmarket, Ontario.",
}


def test_run_writes_one_json_per_bench_lead(tmp_path):
    provider = FakeProvider(CLEAN)
    assert calibrate.run(provider, str(tmp_path)) == 0
    assert provider.calls == len(calibrate.BENCH)
    for lead in calibrate.BENCH:
        saved = json.loads((tmp_path / f"{lead['place_id']}.json").read_text())
        assert saved["lead"] == lead["name"]
        assert saved["issues"] == []
        assert saved["usage"] == {"in": 900, "out": 400}


def test_run_records_the_findings_rather_than_hiding_them(tmp_path):
    invented = {**CLEAN, "hero_sub": "Family owned and operated since 1998."}
    calibrate.run(FakeProvider(invented), str(tmp_path))
    saved = json.loads((tmp_path / "bench-trade.json").read_text())
    assert saved["issues"], "an invented founding year should be flagged"


def test_a_provider_that_fails_every_call_is_not_reported_as_a_clean_run(tmp_path):
    class Broken:
        name, model = "broken", "none"

        def draft(self, system, facts):
            raise RuntimeError("connection reset")

    # Zero drafts and zero findings must not read as "nothing wrong".
    assert calibrate.run(Broken(), str(tmp_path)) == 1


def test_bench_leads_are_synthetic():
    """No real business gets speculative copy written about it on a bench run."""
    for lead in calibrate.BENCH:
        assert lead["place_id"].startswith("bench-")
        assert "555-01" in (lead["phone"] or "555-01")


# ---------------------------------------------------------------------------
# The CLI surface
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("provider,env", [("gemini", "GEMINI_API_KEY"),
                                          ("anthropic", "ANTHROPIC_API_KEY")])
def test_missing_key_explains_itself_rather_than_traceback(provider, env,
                                                           monkeypatch):
    monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(sys, "argv", ["calibrate.py", "--provider", provider])
    with pytest.raises(SystemExit) as excinfo:
        calibrate.main()
    assert env in str(excinfo.value)
