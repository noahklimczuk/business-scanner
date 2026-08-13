#!/usr/bin/env python3
"""Calibrate the fabrication check against copy a real model actually writes.

NOT part of the tool. Nothing in leadsmith imports this, `build` does not know
it exists, and deleting it breaks nothing. It is a bench instrument for one
question:

    when a language model writes website copy for a small trade, does
    `generate.review()` catch what it should, and does it leave alone what it
    should?

That question does not need Claude specifically to answer it, which matters
when the Anthropic account has no credit. Any model that writes plausible
small-business marketing copy will reach for "licensed and insured", "family
owned since 1998" and "the best in town" — and a model that follows the
system prompt *less* literally than Claude is a harsher test, not a weaker one.

What this cannot tell you, and do not pretend otherwise:
  - whether Claude's copy at `effort: low` reads well. Different model.
  - what a site costs. Different pricing.

Usage
-----
    export GEMINI_API_KEY=...            # free, no card: aistudio.google.com
    python tools/calibrate.py --provider gemini

    export ANTHROPIC_API_KEY=...         # once the account has credit
    python tools/calibrate.py --provider anthropic

    python tools/calibrate.py --list-models --provider gemini
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests                                          # noqa: E402

import generate                                          # noqa: E402

GEMINI_ROOT = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_DEFAULT = "gemini-2.5-flash"
TIMEOUT = 90

# Synthetic, so no real business has speculative copy written about it during a
# bench run. The shape is what matters: a real name, a category, a Newmarket
# address, and hours in exactly the form Places returns them.
BENCH = [
    {
        "place_id": "bench-trade", "name": "Halstead Roofing",
        "category": "Roofing contractor",
        "address": "412 Davis Dr, Newmarket, ON L3Y 2N7, Canada",
        "phone": "(905) 555-0142", "rating": 4.7, "review_count": 84,
        "hours": ["Monday: 7:30 AM – 5:00 PM", "Tuesday: 7:30 AM – 5:00 PM",
                  "Wednesday: 7:30 AM – 5:00 PM", "Thursday: 7:30 AM – 5:00 PM",
                  "Friday: 7:30 AM – 4:00 PM", "Saturday: 8:00 AM – 1:00 PM",
                  "Sunday: Closed"],
    },
    {
        "place_id": "bench-food", "name": "Bakehouse on Main",
        "category": "Bakery",
        "address": "188 Main St S, Newmarket, ON L3Y 3Y9, Canada",
        "phone": "(905) 555-0176", "rating": 4.5, "review_count": 212,
        "hours": ["Monday: Closed", "Tuesday: 7:00 AM – 4:00 PM",
                  "Wednesday: 7:00 AM – 4:00 PM", "Thursday: 7:00 AM – 4:00 PM",
                  "Friday: 7:00 AM – 5:00 PM", "Saturday: 7:00 AM – 3:00 PM",
                  "Sunday: 8:00 AM – 2:00 PM"],
    },
    {
        "place_id": "bench-salon", "name": "Copperleaf Studio",
        "category": "Hair salon",
        "address": "17250 Yonge St, Newmarket, ON L3Y 5V1, Canada",
        "phone": "(905) 555-0198", "rating": 4.9, "review_count": 61,
        "hours": ["Monday: Closed", "Tuesday: 10:00 AM – 7:00 PM",
                  "Wednesday: 10:00 AM – 7:00 PM", "Thursday: 10:00 AM – 8:00 PM",
                  "Friday: 9:00 AM – 6:00 PM", "Saturday: 9:00 AM – 4:00 PM",
                  "Sunday: Closed"],
    },
    {
        "place_id": "bench-thin", "name": "T & K Drain Services",
        "category": "Plumber",
        "address": "Newmarket, ON, Canada",
        "phone": None, "rating": None, "review_count": 3,
        "hours": None,          # the bare listing: nothing to lean on
    },
]

# Copy that is honest and should sail through. If a run "improves" the term
# list by adding something that rejects one of these, the improvement is a
# regression — the check would start refusing copy that is fine.
HONEST = [
    "A new asphalt roof lasts about 20 years in this climate.",
    "Since the roof is what keeps the rain out, we start there.",
    "We answer the phone and come and look at it.",
    "Flat roofs need a different approach than shingles.",
    # Not "sold the same day it is baked" — that reads as a same-day promise,
    # and the check is right to stop it. The bar here is honest *and* claimless.
    "Bread is mixed before dawn and baked in small batches through the morning.",
    "Colour appointments take about two hours in the chair.",
]


# ---------------------------------------------------------------------------
# Providers. Both speak the same two calls: list models, and write one draft.
# ---------------------------------------------------------------------------
def _gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Gemini's responseSchema is OpenAPI-flavoured and rejects some JSON Schema
    keywords that the Anthropic call is happy with."""
    if not isinstance(schema, dict):
        return schema
    out = {k: v for k, v in schema.items() if k != "additionalProperties"}
    if "properties" in out:
        out["properties"] = {k: _gemini_schema(v) for k, v in out["properties"].items()}
    if "items" in out:
        out["items"] = _gemini_schema(out["items"])
    return out


class Gemini:
    name = "gemini"
    env = "GEMINI_API_KEY"

    def __init__(self, key: str, model: str = GEMINI_DEFAULT):
        self.key, self.model = key, model

    def models(self) -> list[str]:
        r = requests.get(f"{GEMINI_ROOT}/models", params={"key": self.key},
                         timeout=TIMEOUT)
        r.raise_for_status()
        return [m["name"].removeprefix("models/") for m in r.json().get("models", [])
                if "generateContent" in m.get("supportedGenerationMethods", [])]

    def draft(self, system: str, facts: str) -> tuple[dict[str, Any], dict[str, int]]:
        r = requests.post(
            f"{GEMINI_ROOT}/models/{self.model}:generateContent",
            params={"key": self.key},
            json={
                "system_instruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": facts}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseSchema": _gemini_schema(generate.CONTENT_SCHEMA),
                    "maxOutputTokens": generate.MAX_TOKENS,
                },
            },
            timeout=TIMEOUT,
        )
        if r.status_code == 404:
            raise SystemExit(
                f"Gemini has no model called '{self.model}'. Run with "
                f"--list-models to see what this key can reach.")
        if not r.ok:
            raise SystemExit(f"Gemini returned {r.status_code}: {r.text[:300]}")
        body = r.json()
        try:
            text = body["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError):
            raise SystemExit(f"Unexpected Gemini response shape: "
                             f"{json.dumps(body)[:300]}")
        usage = body.get("usageMetadata", {})
        return json.loads(text), {
            "in": usage.get("promptTokenCount", 0),
            "out": usage.get("candidatesTokenCount", 0),
        }


class Claude:
    name = "anthropic"
    env = "ANTHROPIC_API_KEY"

    def __init__(self, key: str, model: str = generate.MODEL):
        import anthropic
        self.client = anthropic.Anthropic(api_key=key)
        self.model = model

    def models(self) -> list[str]:
        return [m.id for m in self.client.models.list(limit=20)]

    def draft(self, system: str, facts: str) -> tuple[dict[str, Any], dict[str, int]]:
        # Deliberately the same call generate.py makes, so a run here is a real
        # rehearsal of the production path rather than an approximation.
        response = self.client.messages.create(
            model=self.model,
            max_tokens=generate.MAX_TOKENS,
            system=[{"type": "text", "text": system,
                     "cache_control": {"type": "ephemeral"}}],
            output_config={"effort": "low",
                           "format": {"type": "json_schema",
                                      "schema": generate.CONTENT_SCHEMA}},
            messages=[{"role": "user", "content": facts}],
        )
        if response.stop_reason == "refusal":
            raise SystemExit("Claude refused this business.")
        text = next(b.text for b in response.content if b.type == "text")
        return json.loads(text), {"in": response.usage.input_tokens,
                                  "out": response.usage.output_tokens}


PROVIDERS = {"gemini": Gemini, "anthropic": Claude}


# ---------------------------------------------------------------------------
# The bench
# ---------------------------------------------------------------------------
def honest_copy_still_passes() -> list[str]:
    """Guard against tightening the check until it rejects good writing."""
    template = {
        "hero_headline": "Roofing in Newmarket",
        "hero_sub": "placeholder",
        "about": ["placeholder one.", "placeholder two."],
        "services": [{"name": f"Service {i}", "description": "One line."}
                     for i in range(3)],
        "why_us": ["We answer the phone", "Written scope first", "We tidy up"],
        "cta_line": "Call and we will take a look",
        "meta_title": "Halstead Roofing — Newmarket",
        "meta_description": "Roofing in Newmarket, Ontario.",
    }
    source = generate.source_text(BENCH[0], "Newmarket, ON")
    broken = []
    for sentence in HONEST:
        issues = generate.review({**template, "hero_sub": sentence}, source)
        if issues:
            broken.append(f"{sentence!r} -> {issues[0]}")
    return broken


def run(provider: Any, out_dir: str) -> int:
    os.makedirs(out_dir, exist_ok=True)
    print(f"provider: {provider.name} / {provider.model}\n")

    regressions = honest_copy_still_passes()
    if regressions:
        print("!! the check already rejects honest copy — fix this first:")
        for r in regressions:
            print(f"   {r}")
        print()

    flagged_total, drafts = 0, 0
    for lead in BENCH:
        facts = generate.facts_for(lead, "Newmarket, ON")
        source = generate.source_text(lead, "Newmarket, ON")
        try:
            copy, usage = provider.draft(generate.SYSTEM_PROMPT, facts)
        except SystemExit:
            raise
        except Exception as exc:                       # noqa: BLE001
            print(f"— {lead['name']}: call failed: {type(exc).__name__}: {exc}")
            continue

        drafts += 1
        issues = generate.review(copy, source)
        flagged_total += len(issues)
        path = os.path.join(out_dir, f"{lead['place_id']}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"lead": lead["name"], "usage": usage,
                       "issues": issues, "copy": copy}, fh, indent=2,
                      ensure_ascii=False)

        status = "clean" if not issues else f"{len(issues)} flagged"
        print(f"— {lead['name']} ({lead['category']}) — {status}"
              f"   [{usage['in']} in / {usage['out']} out]")
        print(f"    headline: {copy.get('hero_headline', '')!r}")
        print(f"    sub:      {copy.get('hero_sub', '')!r}")
        for para in (copy.get("about") or [])[:1]:
            print("    about:    " + textwrap.shorten(para, 110))
        for issue in issues:
            print(f"    FLAG  {issue}")
        print(f"    saved: {path}\n")

    if not drafts:
        print("no drafts came back — nothing was measured.")
        return 1

    print(f"{drafts} drafts, {flagged_total} findings.")
    print(textwrap.dedent("""
        Reading the result
          A finding that is a real invented claim  -> the check is working.
          A finding on a sentence that reads fine  -> the term is too broad.
          An invented claim with NO finding        -> add a term, then re-run;
                                                      the honest-copy guard above
                                                      must stay empty.
    """).strip())
    return 0 if not regressions else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--provider", choices=sorted(PROVIDERS), default="gemini")
    ap.add_argument("--model", default=None)
    ap.add_argument("--list-models", action="store_true")
    ap.add_argument("--out", default="calibration")
    args = ap.parse_args()

    cls = PROVIDERS[args.provider]
    key = os.environ.get(cls.env, "").strip()
    if not key:
        free = ("  A free key, no card required: https://aistudio.google.com/apikey\n"
                if args.provider == "gemini" else "")
        raise SystemExit(f"Set {cls.env} first.\n{free}")

    provider = cls(key, args.model) if args.model else cls(key)
    if args.list_models:
        for name in provider.models():
            print(name)
        return 0
    return run(provider, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
