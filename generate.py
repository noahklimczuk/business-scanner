"""Copy from Claude, layout from our templates, and a hard check between them.

The model writes headlines and service descriptions. It never writes structure,
and it never writes facts. Everything it returns goes through `review()` before
it can reach a page, because one invented "family owned since 1987" on a
stranger's website ends the pitch in the first thirty seconds — and the operator
will be standing in front of the owner when it does.

Copy is cached in `sites/<place_id>/content.json`. Re-rendering is free; only
`--regenerate` spends money again. That file is also the edit surface for
Phase 5: change the JSON, re-run build, redeploy. There is no CMS.
"""
from __future__ import annotations

import datetime
import json
import os
import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Optional
from urllib.parse import quote

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

import design
import hours as hours_mod
import visuals

ROOT = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(ROOT, "templates")
SITES_DIR = os.environ.get("LEADSMITH_SITES") or os.path.join(ROOT, "sites")

MODEL = "claude-opus-5"
MAX_TOKENS = 8000
# 2 added the optional blocks — process, faq, service areas, the connective
# copy. A version 1 file has none of them and renders exactly as it did, which
# is the whole reason the number went up rather than the fields being required.
CONTENT_VERSION = 2

# The guide's ceiling for a whole site. Generated pages land near 45KB; this
# only ever fires once real photographs are dropped in, which is exactly when
# nobody is thinking about page weight and a 4MB phone photo goes unnoticed.
PAGE_BUDGET_BYTES = 150 * 1024
# A demo is a different product with a different job. It is opened on an iPad
# on the shop's own wifi, not on rural LTE by someone who needs a plumber, and
# it carries eight to twelve photographs because showing what the site could
# look like *is* the point. Same page, same code, a budget that reflects it.
DEMO_BUDGET_BYTES = 3 * 1024 * 1024

# USD per million tokens for MODEL. Cache writes cost 1.25x input, reads 0.1x.
PRICE_IN = 5.00
PRICE_OUT = 25.00

# Who writes the copy. Anthropic is the default and the only one these rules
# were tuned against; the rest are here so the tool works before there is any
# money to spend on it, and so switching back later is a settings change rather
# than an edit to this file.
#
# Everything except Anthropic speaks the OpenAI /chat/completions shape, which
# is why one adapter covers all of them — including services not named here.
DEFAULT_PROVIDER = "anthropic"

PROVIDERS: dict[str, Any] = {
    "anthropic": {
        "label": "Anthropic (Claude)",
        "api": "anthropic",
        "base_url": "",
        "model": MODEL,
        "price_in": PRICE_IN,
        "price_out": PRICE_OUT,
        "key_hint": "sk-ant-…",
        "env": "ANTHROPIC_API_KEY",
        "console": "console.anthropic.com",
    },
    "gemini": {
        "label": "Google Gemini (free tier)",
        "api": "openai",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-2.5-flash",
        "price_in": 0.0,
        "price_out": 0.0,
        "key_hint": "AIza…",
        "env": "GEMINI_API_KEY",
        "console": "aistudio.google.com/apikey",
    },
    "groq": {
        "label": "Groq (free tier)",
        "api": "openai",
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
        "price_in": 0.0,
        "price_out": 0.0,
        "key_hint": "gsk_…",
        "env": "GROQ_API_KEY",
        "console": "console.groq.com/keys",
    },
    "custom": {
        "label": "Anything OpenAI-compatible",
        "api": "openai",
        "base_url": "",
        "model": "",
        "price_in": 0.0,
        "price_out": 0.0,
        "key_hint": "",
        "env": "LEADSMITH_COPY_KEY",
        "console": "",
    },
}


def resolve_provider(settings: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Turn the `copy` block of config.json into a ready-to-use provider.

    Defaults to Anthropic, so a config written before any of this existed keeps
    behaving exactly as it did. The operator's model and address override the
    built-in ones, which is what makes `custom` work at all.
    """
    settings = settings or {}
    name = (settings.get("provider") or DEFAULT_PROVIDER).strip().lower()
    if name not in PROVIDERS:
        known = ", ".join(sorted(PROVIDERS))
        raise ContentError(
            f'"{name}" is not a copywriting service this app knows about.\n'
            f"Pick one of: {known}. Settings → Copywriter changes it."
        )

    spec = dict(PROVIDERS[name])
    spec["provider"] = name
    for field in ("model", "base_url"):
        override = str(settings.get(field) or "").strip()
        if override:
            spec[field] = override

    key = str(settings.get("api_key") or "").strip()
    if key.startswith("PASTE_"):
        key = ""
    spec["api_key"] = key or os.environ.get(spec["env"], "").strip()
    return spec


def provider_from_config(cfg: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """The copywriter described by a whole config.json.

    Lives here rather than in either front end so the CLI and the app cannot
    disagree about which service is about to be billed.
    """
    cfg = cfg or {}
    settings = dict(cfg.get("copy") or {})
    if not settings.get("api_key"):
        legacy = str(cfg.get("anthropic_api_key") or "").strip()
        # Only ever onto Anthropic. An earlier version attached this key to
        # whichever service happened to be selected, so an operator who picked
        # a different one and left the key box empty got their Anthropic key
        # pointed at a third party's address — a leak, not just a wrong call.
        chosen = str(settings.get("provider") or DEFAULT_PROVIDER).strip().lower()
        if legacy and not legacy.startswith("PASTE_") and chosen == "anthropic":
            settings["provider"] = "anthropic"
            settings["api_key"] = legacy
    return resolve_provider(settings)


def provider_is_free(spec: dict[str, Any]) -> bool:
    """No per-word charge. Not the same as no limit — free tiers have caps."""
    return not (spec.get("price_in") or spec.get("price_out"))


class ContentError(RuntimeError):
    """The copy came back unusable. Written for the operator, not a log file."""


# ---------------------------------------------------------------------------
# Template selection
# ---------------------------------------------------------------------------
# Twelve page structures, not twelve colour schemes. See SITES.md for what each
# one is and why it is shaped the way it is.
#
# Order matters: the first template whose keywords match wins, so the specific
# categories are listed before the general ones. "Pet grooming salon" has to
# reach `pet` rather than `salon`, and "auto glass repair" has to reach `auto`
# rather than `trade`.
TEMPLATE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "pet": ("pet", "pets", "dog", "dogs", "cat", "cats", "kennel", "kennels",
            "groomer", "grooming salon", "doggy", "puppy", "aquarium",
            "pet store", "pet shop", "animal shelter", "boarding kennel"),
    "auto": ("auto", "automotive", "mechanic", "car repair", "garage",
             "body shop", "collision", "tire", "tires", "tyre", "muffler",
             "transmission", "oil change", "detailing", "car wash", "towing",
             "motorcycle", "windshield", "auto glass", "auto parts"),
    "clinic": ("dentist", "dental", "orthodontist", "denture", "doctor",
               "medical", "clinic", "physician", "chiropractor", "chiropractic",
               "physiotherapy", "physical therapy", "optometrist", "optician",
               "eye care", "veterinary", "veterinarian", "vet", "podiatrist",
               "audiologist", "hearing", "pharmacy", "walk-in clinic",
               "animal hospital"),
    "wellness": ("gym", "fitness", "yoga", "pilates", "crossfit", "martial arts",
                 "karate", "boxing", "personal trainer", "training studio",
                 "dance studio", "swim school", "wellness", "nutritionist",
                 "sports club"),
    "professional": ("lawyer", "law firm", "attorney", "solicitor", "notary",
                     "paralegal", "accountant", "accounting", "bookkeeping",
                     "tax", "insurance", "broker", "mortgage", "financial",
                     "consultant", "consulting", "real estate", "realtor",
                     "surveyor", "architect", "engineering firm", "translation",
                     "recruitment", "employment agency"),
    "education": ("school", "academy", "tutor", "tutoring", "daycare",
                  "day care", "preschool", "montessori", "childcare",
                  "learning centre", "learning center", "driving school",
                  "music school", "music lessons", "language school",
                  "college", "training centre", "training center"),
    "creative": ("photographer", "photography", "videographer", "film",
                 "studio", "design", "graphic", "print shop", "printing",
                 "sign shop", "signs", "event planner", "wedding planner",
                 "dj", "framing", "art gallery", "tattoo",
                 "recording studio", "marketing agency", "web design"),
    "home": ("landscaping", "landscaper", "lawn", "lawn care", "gardening",
             "tree service", "arborist", "snow removal", "cleaning",
             "cleaners", "janitorial", "maid", "pest control", "pool",
             "pool service", "window cleaning", "pressure washing",
             "junk removal", "moving", "movers", "septic", "chimney"),
    "food": ("restaurant", "cafe", "coffee", "bakery", "bar", "pizza", "deli",
             "diner", "grill", "caterer", "catering", "juice", "ice cream",
             "butcher", "food", "pub", "brewery", "bistro", "sushi",
             "steakhouse", "sandwich", "taco", "burger", "noodle", "kitchen"),
    "salon": ("salon", "barber", "hair", "nail", "spa", "beauty", "esthetic",
              "lash", "brow", "massage", "tanning", "grooming", "makeup",
              "waxing", "hairdresser", "stylist"),
    "retail": ("shop", "store", "boutique", "gift", "clothing", "jewel",
               "jewellery", "jewelry", "furniture", "hardware", "bookstore",
               "book shop", "bike shop", "toy", "grocer", "market",
               "convenience", "thrift", "consignment", "florist", "garden centre",
               "garden center", "nursery", "supply"),
    "trade": ("roofing", "roofer", "plumber", "plumbing", "electrician",
              "electrical", "hvac", "heating", "cooling", "furnace",
              "air conditioning", "contractor", "construction", "carpenter",
              "carpentry", "renovation", "remodeling", "remodelling",
              "flooring", "drywall", "painter", "painting", "masonry",
              "concrete", "paving", "fencing", "welding", "handyman",
              "excavation", "insulation", "waterproofing", "locksmith",
              "appliance repair", "garage door", "window", "door", "siding",
              "eavestrough", "gutter", "deck", "landscape construction"),
}
TEMPLATES = list(TEMPLATE_KEYWORDS)
DEFAULT_TEMPLATE = "trade"


def template_for(category: str | None) -> str:
    """The template a Google category belongs to.

    Whole words only. A substring match sends "Barber shop" to the food
    template, because "barber" contains "bar" — and that is not hypothetical,
    it is what the first version of this did.
    """
    c = (category or "").lower()
    for name, keywords in TEMPLATE_KEYWORDS.items():
        if any(re.search(rf"(?<![a-z]){re.escape(k)}(?![a-z])", c)
               for k in keywords):
            return name
    return DEFAULT_TEMPLATE


# ---------------------------------------------------------------------------
# The contract with the model
# ---------------------------------------------------------------------------
# What the model is asked for. Everything here is required of the *model* —
# strict JSON-schema modes insist that every declared property is required, and
# a model that can write three services can write three questions.
#
# `review()` is deliberately looser: only the version 1 core is mandatory there,
# so a content.json written before any of this existed still renders.
#
# Two fields are conspicuously absent, and their absence is the point.
# `service_areas` would have the model listing neighbouring towns it cannot
# know they serve, and `quote` would have it writing a testimonial nobody gave.
# Both exist in content.json; both are filled in by the operator from what the
# owner actually said, and the fabrication check treats them accordingly.
CONTENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "hero_eyebrow": {"type": "string"},
        "hero_headline": {"type": "string"},
        "hero_sub": {"type": "string"},
        "about": {"type": "array", "items": {"type": "string"},
                  "minItems": 2, "maxItems": 2},
        "services_intro": {"type": "string"},
        "services": {
            "type": "array", "minItems": 3, "maxItems": 6,
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"},
                               "description": {"type": "string"}},
                "required": ["name", "description"],
                "additionalProperties": False,
            },
        },
        "process_headline": {"type": "string"},
        "process": {
            "type": "array", "minItems": 3, "maxItems": 4,
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"},
                               "description": {"type": "string"}},
                "required": ["name", "description"],
                "additionalProperties": False,
            },
        },
        "faq": {
            "type": "array", "minItems": 3, "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {"q": {"type": "string"}, "a": {"type": "string"}},
                "required": ["q", "a"],
                "additionalProperties": False,
            },
        },
        "why_us": {"type": "array", "items": {"type": "string"},
                   "minItems": 3, "maxItems": 3},
        "cta_line": {"type": "string"},
        "closing_note": {"type": "string"},
        "meta_title": {"type": "string"},
        "meta_description": {"type": "string"},
    },
    "required": ["hero_eyebrow", "hero_headline", "hero_sub", "about",
                 "services_intro", "services", "process_headline", "process",
                 "faq", "why_us", "cta_line", "closing_note", "meta_title",
                 "meta_description"],
    "additionalProperties": False,
}

# What `review()` insists on, which is version 1 and nothing more. Everything
# added since is optional, so an older content.json is not retroactively broken
# by a field that did not exist when it was written.
REQUIRED_FIELDS = ("hero_headline", "hero_sub", "about", "services", "why_us",
                   "cta_line", "meta_title", "meta_description")

SYSTEM_PROMPT = """\
You write the copy for a small local business's website. The site is one page \
and its only job is to get the phone to ring.

Write for a person who needs this work done today and is deciding in about ten \
seconds — someone standing in their driveway on a bad phone connection. Plain \
sentences. Concrete nouns. The voice of a competent tradesperson describing \
their own work, not a marketing agency describing a client.

The one rule that matters: every fact on the page must come from the input. \
You are given a name, a category, a town and hours. That is all you know. You \
do not know how long they have been in business, who owns it, how many people \
work there, what equipment they own, whether they work on residential or \
commercial jobs or both, or whether they are licensed, insured, bonded, \
certified, family-run, award-winning, or the best at anything. Do not imply \
any of it. Do not write a founding year, a number of years of experience, a \
staff count, a service radius, a guarantee, a warranty, a price, a response \
time, a star rating, or a number of reviews. If a detail is not in the input, \
it does not go on the page.

What you may infer is the category of work itself: a plumber does drain \
clearing and fixture installation, a bakery sells bread. Describe the work, \
not the company's history, its equipment or its credentials.

When the input is thin — no hours, no address beyond the town — write less. A \
short page that is entirely true is the goal; padding it out with plausible \
detail about how they work is the failure this rule exists to prevent.

Banned because they are either unverifiable or make the page sound generated: \
best, premier, leading, top-rated, number one, trusted, reliable, dedicated, \
passionate, committed, quality you can count on, state-of-the-art, one-stop, \
nestled, proudly serving, we pride ourselves, unmatched, second to none.

The questions in `faq` are the ones a person actually asks before they ring: \
where are you, when are you open, what do you do, what happens when I call. \
Answer them from the hours, the address, the town and the services in the \
input, and from nothing else. If the input does not answer a question, do not \
ask it. "Do you offer free estimates?" is not a question you can answer.

The steps in `process` describe what happens after the call, in the ordinary \
shape of this kind of work — someone rings, someone comes and looks, the work \
is scheduled, the work is done. Describe the trade's own sequence. Do not put \
a timescale, a price or a guarantee in any of them.

Lengths: hero_eyebrow two or three words. hero_headline under 60 characters. \
hero_sub one sentence. Each about paragraph two or three sentences. \
services_intro one sentence. Each service description one sentence. \
process_headline under 60 characters. Each process step name two or three \
words and its description one sentence. Each faq question under 80 characters \
and its answer one or two sentences. Each why_us point under 12 words and \
specific to this trade. cta_line one short line that asks for the call. \
closing_note one short line — the practical thing worth knowing when someone \
is about to visit or ring. meta_title under 60 characters and containing the \
business name and town. meta_description under 155 characters.

Return only the JSON object."""


def facts_for(lead: dict[str, Any], city: str = "") -> str:
    """The only thing the model gets to know.

    The Google rating and review count are deliberately absent, and this is the
    one place that decision has to be enforced rather than merely intended.
    A real calibration run against a live model put "Rated 4.9 stars across 61
    customer reviews" on three pages out of four — following instructions
    exactly, because the rating was in the input and nothing said it was the
    one input that may not be repeated.

    It is not a question of truth; the rating is true. It is Places content,
    licensed for display inside our tool, and republishing it as the client's
    own website copy is a different thing from showing it to the operator. The
    schema builder and the stats strip already leave it out for the same
    reason. The copy path was the hole.
    """
    parsed = hours_mod.parse(lead.get("hours"))
    lines = [
        f"Business name: {lead.get('name')}",
        f"Category: {lead.get('category') or 'unknown'}",
        f"Address: {lead.get('address') or 'unknown'}",
    ]
    if city:
        lines.append(f"Town: {city}")
    if parsed:
        lines.append("Opening hours:")
        lines += [f"  {d['day']}: {d['label']}" for d in parsed]
    else:
        lines.append("Opening hours: not published")
    return "\n".join(lines)


def source_text(lead: dict[str, Any], city: str = "",
                verified: Optional[list[str]] = None) -> str:
    """Everything we actually know, lowercased, for the fabrication check.

    `verified` is the operator's own additions — things the owner told them
    directly, kept in `content.json` under "verified_facts". The rule was never
    "only Google may be believed", it is "nothing on the page is invented". Once
    the owner says they are licensed, that is input data too; it just arrived in
    a conversation instead of an API response.
    """
    text = facts_for(lead, city)
    if verified:
        text += "\nConfirmed by the owner:\n" + "\n".join(f"  {v}" for v in verified)
    return text.lower()


# ---------------------------------------------------------------------------
# Review: the check that stands between the model and the operator's pitch
# ---------------------------------------------------------------------------
_YEAR = re.compile(r"\b(?:18|19|20)\d{2}\b")
_HTML = re.compile(r"<[^>]+>")
_SENTENCE = re.compile(r"[^.!?]+[.!?]?")

# A span of time is only a fabrication when it is a claim about *the business*.
# "A new asphalt roof lasts 20 years" is a fact about roofing; "20 years of
# experience" is something we cannot possibly know. Match the duration, then
# look at the rest of the sentence to decide which one it is.
_DURATION = re.compile(
    r"\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|twenty|thirty|"
    r"forty|fifty)[\s-]*(?:\+\s*)?(?:years?|decades?|generations?)\b", re.I)
_ABOUT_THE_BUSINESS = re.compile(
    r"\b(?:experience|experienced|in business|serving|served|serve|family|"
    r"generations?|established|operating|trading|industry|our team|we have "
    r"been|we've been|been doing|doing this|in the trade|on the tools)\b", re.I)
# "two decades roofing", "20 years of ..." — a duration followed by "of" or by
# an activity is describing how long they have been at it, whatever else the
# sentence says. "lasts 20 years in this climate" is not.
_DURATION_TAIL = re.compile(r"^\s*(?:of\b|[a-z]+ing\b)", re.I)

# Google's rating, restated as the client's own website copy. Unlike everything
# else in this check the problem is not that it might be false — it is true —
# but that it is Places content and we are only licensed to display it inside
# our own tool. `_schema()` and `_facts()` already leave it off the page; the
# copy is the third way it can get there, and the only one a model can walk
# into on its own.
_RATING_CLAIM = re.compile(
    r"\b\d(?:\.\d)?\s*[-‑– ]?stars?\b"          # "4.7 stars", "5-star"
    r"|\brated\s+\d(?:\.\d)?"                             # "Rated 4.5"
    r"|\b\d(?:\.\d)?\s*/\s*5\b"                           # "4.7/5"
    r"|\b\d+\s+(?:\w+\s+)?reviews?\b",                    # "212 customer reviews"
    re.I)

# Claims about standing, credentials or promises. Allowed only if the words are
# already in the source data — a business literally called "Newmarket Licensed
# Plumbing" may say so.
# Claims about standing, credentials or promises. Matched on word boundaries so
# a term cannot fire from inside a longer word.
#
# Deliberately NOT here: bare "since". It is a plain conjunction — "since the
# roof is what keeps the rain out" is fine — and the founding-year case it was
# meant to catch is already covered by the year check.
#
# Deliberately still here: response-time promises like "same day". They read as
# false positives but are not: promising a response time on a stranger's
# website, when we have no idea whether they can meet it, is a problem for the
# client the day they cannot.
CREDENTIAL_TERMS = [
    "licensed", "licence", "license", "insured", "certified",
    "certification", "accredited", "qualified", "journeyman", "apprentice",
    "red seal", "master plumber", "master electrician", "gas fitter",
    "wsib", "tssa", "esa", "bbb", "award", "awarded", "award-winning",
    "voted", "rated #1", "family owned", "family-owned", "family run",
    "family-run", "locally owned", "veteran owned", "woman owned",
    "established", "founded", "generations", "guarantee",
    "guaranteed", "warranty", "warrantied", "free estimate", "free quote",
    "no obligation", "fully stocked", "24/7", "24 hours a day", "same-day",
    "same day", "next-day", "emergency service", "on call", "insurance approved",
    "background checked", "police checked", "bonded and insured",
]

SUPERLATIVES = [
    "best", "premier", "leading", "top-rated", "top rated", "number one",
    "#1", "unmatched", "second to none", "world-class", "state-of-the-art",
    "cutting-edge", "one-stop", "nestled", "proudly serving", "we pride",
    "trusted name", "unrivalled", "unrivaled", "finest", "superior",
]

# Keyed on the leaf field name, so `faq[2].a` is measured by the limit for "a".
# A limit that is not here is not enforced, which is the right default for copy
# whose length is a matter of taste rather than of layout.
_TEXT_LIMITS = {
    "hero_eyebrow": 48,
    "hero_headline": 70,
    "hero_sub": 160,
    "services_intro": 200,
    "process_headline": 70,
    "cta_line": 90,
    "closing_note": 180,
    "meta_title": 60,
    "meta_description": 155,
    "q": 100,
    "a": 320,
}

# Everything in a content.json that is not copy. `photos` is filenames and alt
# text, checked by `accessibility_issues()` instead; the rest is bookkeeping.
# Anything not listed here is copy and gets reviewed, which is the point: a
# field added next year is guarded the moment it exists rather than the moment
# somebody remembers to add it to a list.
_NOT_COPY = {"photos", "verified_facts", "version", "place_id", "template",
             "model", "generated_at", "generated_by", "cost_usd", "facts",
             "rejected_at", "issues", "stock", "demo", "showcase", "copy"}


def _strings(content: dict[str, Any]) -> list[tuple[str, str]]:
    """Every string in the content, with the path that names it.

    Version 1 enumerated the five known fields by hand, which meant every field
    added afterwards had to remember to add itself to the check — and one that
    forgot would ship unreviewed. Walking the object instead makes the default
    "reviewed", and the exceptions are the short list above.
    """
    out: list[tuple[str, str]] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, str):
            out.append((path, value))
        elif isinstance(value, dict):
            for key, item in value.items():
                if key in _NOT_COPY:
                    continue
                walk(item, f"{path}.{key}" if path else str(key))
        elif isinstance(value, (list, tuple)):
            for i, item in enumerate(value):
                walk(item, f"{path}[{i}]")

    walk(content, "")
    return out


def _leaf(path: str) -> str:
    """`faq[2].a` -> `a`. The name a limit is keyed on."""
    return re.sub(r"\[\d+\]$", "", path.rsplit(".", 1)[-1])


def _mentions(haystack: str, term: str) -> bool:
    """Whole-term match, so 'esa' cannot fire from inside 'these areas'."""
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", haystack) is not None


def review(content: dict[str, Any], source: str) -> list[str]:
    """Every reason this copy cannot go on someone's website. Empty is a pass."""
    issues: list[str] = []
    source = source.lower()

    # -- shape ---------------------------------------------------------------
    for key in REQUIRED_FIELDS:
        if key not in content:
            issues.append(f"missing field: {key}")
    if len(content.get("about") or []) != 2:
        issues.append("about must be exactly two paragraphs")
    if not 3 <= len(content.get("services") or []) <= 6:
        issues.append("services must have 3 to 6 items")
    if len(content.get("why_us") or []) != 3:
        issues.append("why_us must be exactly three points")
    # The optional blocks are only checked when they are there at all: an
    # absent one is a version 1 file, a malformed one is a mistake.
    if content.get("process") is not None and not 3 <= len(content["process"]) <= 4:
        issues.append("process must have 3 or 4 steps")
    if content.get("faq") is not None and not 3 <= len(content["faq"]) <= 6:
        issues.append("faq must have 3 to 6 questions")
    if content.get("service_areas") is not None and len(content["service_areas"]) > 10:
        issues.append("service_areas must have 10 towns at most")

    for field_name, text in _strings(content):
        if not text.strip():
            issues.append(f"{field_name} is empty")
        if _HTML.search(text):
            issues.append(f"{field_name} contains markup")
        limit = _TEXT_LIMITS.get(_leaf(field_name))
        if limit and len(text) > limit:
            issues.append(f"{field_name} is {len(text)} chars, limit {limit}")

    # A town we were not told about is a fabrication like any other, and a
    # worse one than most: "serving Aurora and Bradford" on a page for a
    # business that does not go there is a promise the owner has to break in
    # front of a customer. The route in is `verified_facts` — the owner said it,
    # the operator wrote it down.
    for i, area in enumerate(content.get("service_areas") or []):
        if str(area).strip().lower() not in source:
            issues.append(
                f"service_areas[{i}] claims they cover '{area}', which is not "
                f"in the data. Add it to verified_facts if the owner said so.")

    # -- fabrication ---------------------------------------------------------
    # A term is only a problem if we could not have known it. Anything already
    # in the Places data is a fact about them, not an invention.
    for field_name, text in _strings(content):
        low = text.lower()
        for year in _YEAR.findall(text):
            if year not in source:
                issues.append(f"{field_name} invents a year: {year}")

        # Only durations the sentence attaches to the business itself.
        for sentence in _SENTENCE.findall(text):
            span = _DURATION.search(sentence)
            if not span:
                continue
            if (_ABOUT_THE_BUSINESS.search(sentence)
                    or _DURATION_TAIL.match(sentence[span.end():])):
                issues.append(f"{field_name} claims the business has been going "
                              f"'{span.group().strip()}'")

        for term in CREDENTIAL_TERMS:
            if _mentions(low, term) and not _mentions(source, term):
                issues.append(f"{field_name} claims '{term}', which is not in the data")
        for term in SUPERLATIVES:
            if _mentions(low, term):
                issues.append(f"{field_name} uses '{term}'")

        for match in _RATING_CLAIM.finditer(text):
            # A business genuinely called "5 Star Roofing" is not restating
            # Google's rating when its own name appears in the copy. The name
            # is in the source; the rating no longer is.
            if match.group().lower() in source:
                continue
            issues.append(
                f"{field_name} republishes a Google rating or review count "
                f"({match.group().strip()!r}). It is true and it is still not "
                f"ours to put on their website.")

    return issues


# Alt text that technically exists and tells a blind visitor nothing. A
# screen reader already announces the element as an image, so "image" and
# "photo" are pure noise, and a filename is worse than silence.
_USELESS_ALT = {"image", "photo", "picture", "img", "graphic", "logo", "photo of",
                "image of", "picture of", "untitled", "alt", "alt text", "n/a"}


def accessibility_issues(content: dict[str, Any]) -> list[str]:
    """Reasons this content cannot ship under WCAG 2.0 AA. Empty is a pass.

    Separate from `review()` because it answers a different question — that one
    asks whether the copy is *true*, this one asks whether the page is *usable*
    — and because the fix is different: a fabrication means rewriting a
    sentence, a missing alt means asking the owner what the photo shows.

    Almost everything AA needs is settled in the templates and the palette,
    where the operator cannot get it wrong. Alt text is the one thing that
    cannot be: only the person who took the photograph knows what is in it. So
    it is the only thing checked here, and it is checked hard, because Ontario
    businesses carry the AODA obligation for what we hand them.
    """
    issues: list[str] = []
    photos = content.get("photos")
    if photos is None:
        return issues
    if not isinstance(photos, list):
        return ["photos must be a list"]

    for i, photo in enumerate(photos):
        if not isinstance(photo, dict):
            issues.append(f"photos[{i}] must be an object")
            continue
        src = str(photo.get("src") or "").strip()
        if not src:
            issues.append(f"photos[{i}] has no src")
            continue
        if photo.get("decorative"):
            continue

        alt = str(photo.get("alt") or "").strip()
        if not alt:
            issues.append(
                f'photos[{i}] ("{src}") has no alt text. Write what a visitor '
                f'who cannot see it would need — "New cedar shake roof on a '
                f'century home" — or set "decorative": true if the picture '
                f"carries nothing the words do not.")
        elif alt.lower().rstrip(".") in _USELESS_ALT:
            issues.append(f'photos[{i}] alt text is "{alt}", which tells a '
                          f"screen reader nothing it did not already say.")
        elif alt.strip().lower() == src.lower():
            issues.append(f'photos[{i}] alt text is just the filename ("{src}").')

    return issues


# ---------------------------------------------------------------------------
# The API call
# ---------------------------------------------------------------------------
@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    attempts: int = 0
    # Per-provider, because a free tier has to record a real zero rather than
    # Anthropic's price against tokens nobody was charged for.
    price_in: float = PRICE_IN
    price_out: float = PRICE_OUT

    @property
    def cost(self) -> float:
        return (
            self.input_tokens * self.price_in / 1e6
            + self.cache_write_tokens * self.price_in * 1.25 / 1e6
            + self.cache_read_tokens * self.price_in * 0.10 / 1e6
            + self.output_tokens * self.price_out / 1e6
        )

    def add(self, usage: Any) -> None:
        self.attempts += 1
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.cache_write_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0
        self.cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0


def _client(api_key: Optional[str] = None):
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise ContentError(
            "The anthropic package is not installed. Run: pip install -e ."
        ) from exc
    return anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()


def _message(facts: str, correction: str = "") -> str:
    if not correction:
        return facts
    return (f"{facts}\n\nYour previous draft was rejected. Fix exactly these and "
            f"return the whole object again:\n{correction}")


def _endpoint(spec: dict[str, Any]) -> tuple[str, dict[str, str]]:
    base = str(spec.get("base_url") or "").strip().rstrip("/")
    if not base:
        raise ContentError(
            f"No address for {spec['label']}. Settings → Copywriter → Address "
            "needs the base URL of an OpenAI-compatible API, ending in /v1."
        )
    headers = {"Content-Type": "application/json"}
    if spec.get("api_key"):
        headers["Authorization"] = f"Bearer {spec['api_key']}"
    return base, headers


def _raise_for_status(spec: dict[str, Any], response: Any) -> None:
    """Turn an HTTP failure into a sentence the operator can act on."""
    if response.status_code in (401, 403):
        raise ContentError(
            f"{spec['label']} rejected the key. Check Settings → Copywriter — "
            f"a key for one service will not work on another."
        )
    if response.status_code == 404:
        # Nearly always a model name, because the address is fixed for the
        # named services and model names change every few months.
        raise ContentError(
            f"{spec['label']} has no model called \"{spec.get('model')}\".\n"
            "Model names change. Settings → Copywriter → Check lists the ones "
            "your key can actually use right now."
        )
    if response.status_code == 429:
        # The expected failure on a free tier, so it gets its own sentence
        # rather than being lumped in with a generic HTTP error.
        raise ContentError(
            f"{spec['label']} says you have hit its rate limit.\n"
            "Free tiers cap requests per minute and per day. Wait and build "
            "again — nothing was lost, and the lead is still on the list."
        )
    if response.status_code >= 400:
        raise ContentError(
            f"{spec['label']} returned HTTP {response.status_code}:\n"
            f"  {response.text[:300].strip()}"
        )


def list_models(spec: dict[str, Any]) -> list[str]:
    """Ask the service which models this key can actually use.

    Model names change every few months, vary by provider tier, and differ
    between two keys for the same service — so the honest answer to "which one
    do I put here" comes from the service, not from a table baked into this
    file that is wrong by the time someone reads it.
    """
    import requests

    if spec.get("api") != "openai":
        return [spec["model"]] if spec.get("model") else []
    base, headers = _endpoint(spec)
    try:
        response = requests.get(f"{base}/models", headers=headers, timeout=30)
    except requests.RequestException as exc:
        raise ContentError(
            f"Could not reach {spec['label']} at {base} ({exc.__class__.__name__}).\n"
            "Check the address in Settings, and that this machine is online."
        ) from exc

    _raise_for_status(spec, response)
    try:
        data = (response.json() or {}).get("data") or []
    except ValueError as exc:
        raise ContentError(
            f"{spec['label']} did not answer with a model list. It may not be "
            "OpenAI-compatible enough for this."
        ) from exc
    # Some services namespace the id ("models/gemini-3.6-flash"); the call
    # accepts either, but the bare name is what a person recognises.
    return sorted({str(m.get("id", "")).split("/")[-1]
                   for m in data if m.get("id")})


def _call_openai(spec: dict[str, Any], facts: str,
                 correction: str = "") -> tuple[dict[str, Any], Any]:
    """One turn against anything that speaks OpenAI's /chat/completions.

    Written against `requests` rather than a vendor SDK on purpose: the shape is
    small and stable, every free service implements it, and the desktop build is
    already 56MB without adding a third client library to it.
    """
    import requests

    base, headers = _endpoint(spec)
    if not str(spec.get("model") or "").strip():
        raise ContentError(
            f"No model name for {spec['label']}. Settings → Copywriter → Model "
            "needs the exact name the service uses."
        )

    payload = {
        "model": spec["model"],
        "max_tokens": MAX_TOKENS,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _message(facts, correction)},
        ],
        # The same schema the Anthropic path enforces. Services that honour it
        # cannot return the wrong shape; the ones that only approximate it are
        # caught by review() a moment later, which is the real safety net.
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "site_copy", "schema": CONTENT_SCHEMA,
                            "strict": True},
        },
    }

    try:
        response = requests.post(f"{base}/chat/completions", json=payload,
                                 headers=headers, timeout=120)
    except requests.RequestException as exc:
        raise ContentError(
            f"Could not reach {spec['label']} at {base} ({exc.__class__.__name__}).\n"
            "Check the address in Settings, and that this machine is online."
        ) from exc

    _raise_for_status(spec, response)

    try:
        body = response.json()
        choice = body["choices"][0]
    except (ValueError, KeyError, IndexError) as exc:
        raise ContentError(
            f"{spec['label']} returned a reply this app could not read "
            f"({exc.__class__.__name__}). Re-run the build; if it keeps "
            "happening the service is not OpenAI-compatible enough for this."
        ) from exc

    if choice.get("finish_reason") == "length":
        raise ContentError(
            "The copy was cut off before it finished. Re-run the build; if it "
            f"happens again, raise MAX_TOKENS (currently {MAX_TOKENS:,})."
        )
    message = choice.get("message") or {}
    if message.get("refusal"):
        raise ContentError(
            f"{spec['label']} declined to write copy for this business:\n"
            f"  {message['refusal']}\nSkip this lead."
        )

    text = message.get("content") or ""
    raw = body.get("usage") or {}
    usage = SimpleNamespace(
        input_tokens=raw.get("prompt_tokens", 0) or 0,
        output_tokens=raw.get("completion_tokens", 0) or 0,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )
    try:
        return json.loads(text), usage
    except json.JSONDecodeError as exc:
        raise ContentError(
            f"{spec['label']} returned something that is not JSON ({exc.msg}). "
            "Re-run the build — this is usually transient. If it never "
            "succeeds, the model is too small to hold the format; try a larger "
            "one in Settings → Copywriter."
        ) from exc


def _call(client: Any, facts: str, correction: str = "",
          model: Optional[str] = None) -> tuple[dict[str, Any], Any]:
    message = _message(facts, correction)
    response = client.messages.create(
        model=model or MODEL,
        max_tokens=MAX_TOKENS,
        # The rules are identical for every business and easily clear the
        # 512-token cache minimum, so the second site of the evening is cheaper.
        system=[{"type": "text", "text": SYSTEM_PROMPT,
                 "cache_control": {"type": "ephemeral"}}],
        output_config={
            # Writing plain copy from a handful of facts is not a reasoning
            # problem; low effort keeps this well inside the cost target.
            "effort": "low",
            "format": {"type": "json_schema", "schema": CONTENT_SCHEMA},
        },
        messages=[{"role": "user", "content": message}],
    )

    if response.stop_reason == "refusal":
        raise ContentError(
            "Claude declined to write copy for this business. That is unusual for "
            "a plain listing — check the business name for anything that reads as "
            "a prohibited topic, then skip this lead."
        )
    if response.stop_reason == "max_tokens":
        raise ContentError(
            "The copy was cut off before it finished. Re-run the build; if it "
            f"happens again, raise MAX_TOKENS (currently {MAX_TOKENS:,})."
        )

    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return json.loads(text), response.usage
    except json.JSONDecodeError as exc:
        raise ContentError(
            f"Claude returned something that is not JSON ({exc.msg}). "
            "Re-run the build — this is almost always transient."
        ) from exc


def write_copy(lead: dict[str, Any], city: str = "",
               client: Any = None, api_key: Optional[str] = None,
               max_attempts: int = 2,
               verified: Optional[list[str]] = None,
               provider: Optional[dict[str, Any]] = None) -> tuple[dict[str, Any], Usage]:
    """One business in, validated copy out. Retries once on a rejected draft.

    `provider` is the `copy` block of config.json (see resolve_provider). An
    explicit `client` still wins, because the tests hand one in and because the
    Anthropic path has never needed anything else.
    """
    spec = resolve_provider(provider)
    if api_key:
        spec["api_key"] = api_key
    if client is None and spec["api"] == "anthropic":
        client = _client(spec.get("api_key") or None)

    facts = facts_for(lead, city)
    source = source_text(lead, city, verified)
    usage = Usage(price_in=spec["price_in"], price_out=spec["price_out"])
    correction = ""

    for attempt in range(1, max_attempts + 1):
        if client is not None:
            content, raw_usage = _call(client, facts, correction, spec["model"])
        else:
            content, raw_usage = _call_openai(spec, facts, correction)
        usage.add(raw_usage)
        issues = review(content, source)
        if not issues:
            return content, usage
        if attempt < max_attempts:
            correction = "\n".join(f"- {i}" for i in issues[:12])

    # Paid for and rejected. Keeping the draft costs nothing and turns a total
    # loss into a one-word edit — without it, a single over-eager term match
    # spends twice and leaves the operator with no site at all.
    path = save_rejected(lead["place_id"], content, issues, usage)
    raise ContentError(
        "The copy still breaks the rules after a retry, so no site was written:\n"
        + "\n".join(f"  - {i}" for i in issues[:12])
        + f"\n\nThe draft was not thrown away — it is in\n  {path}\n"
          "Fix the wording (or add the claim to \"verified_facts\" if the owner "
          "told you it is true), rename it to content.json, and run build again. "
          "That costs nothing."
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)


def squeeze_css(html: str) -> str:
    """Strip comments and indentation from the inlined stylesheet.

    The comments in styles.css.j2 explain why each rule is the way it is and are
    worth keeping in the template. They are worth nothing to a phone on rural
    LTE, and Lighthouse counts them. Only whitespace at line starts and blank
    lines go, so nothing inside a quoted value is touched.
    """
    def shrink(match: re.Match) -> str:
        css = _CSS_COMMENT.sub("", match.group(1))
        lines = [line.strip() for line in css.splitlines()]
        return "<style>" + "\n".join(l for l in lines if l) + "</style>"

    return re.sub(r"<style>(.*?)</style>", shrink, html, flags=re.S)


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["initials"] = lambda name: "".join(
        w[0] for w in re.findall(r"[A-Za-z]+", name or "")[:2]).upper() or "•"
    return env


def _favicon(palette: dict[str, str], name: str) -> str:
    """A data-URI mark in the business's own colour. No extra request, no file."""
    initials = "".join(w[0] for w in re.findall(r"[A-Za-z]+", name or "")[:2]).upper()
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        f'<rect width="64" height="64" rx="12" fill="{palette["accent"]}"/>'
        '<text x="32" y="44" text-anchor="middle" font-family="monospace" '
        f'font-size="30" font-weight="700" fill="#fff">{initials or "&#8226;"}</text></svg>'
    )
    return quote(svg)


def _facts(lead: dict[str, Any], city: str, hours_summary: str) -> list[dict[str, str]]:
    """The strip under the hero. Only things we are entitled to publish.

    Google's rating and review count are deliberately absent. They are the best
    line in the *pitch*, but republishing Places content as the client's own
    site content is a different thing from displaying it in our tool, and it is
    not worth the argument on someone else's commercial page.
    """
    # The category and town are already in the wordmark and the hero eyebrow, so
    # a strip repeating them is decoration. It only appears when it can say
    # something the reader has not read yet.
    facts = []
    if hours_summary:
        facts.append({"value": hours_summary, "label": "Open"})
    if city:
        facts.append({"value": f"{city} and nearby", "label": "Serving"})
    return facts if len(facts) >= 2 else []


# What the nav calls the services section. It is the one word on the page that
# has to sound like the trade rather than like a website.
SERVICES_WORD = {
    "trade": "Services", "food": "Menu", "salon": "Services", "auto": "Work",
    "wellness": "Classes", "clinic": "Care", "professional": "Practice",
    "retail": "Shop", "home": "Services", "pet": "Services",
    "creative": "Work", "education": "Programmes",
}

# How many image slots each template hands out, in document order. This is what
# a photo fetch asks for, and it is why `photos[3]` means "the fourth picture
# down the page" rather than "whichever slot happens to be fourth today".
#
# The number at the top of each template file is the same list; if they ever
# disagree, the template is right and this is stale. A slot with no photograph
# falls back to generated artwork on its own, so being one short is a
# cosmetic difference rather than a broken page.
PHOTO_SLOTS = {
    "trade": 6, "food": 10, "salon": 10, "auto": 7, "wellness": 4,
    "clinic": 8, "professional": 5, "retail": 8, "home": 10, "pet": 9,
    "creative": 8, "education": 5,
}


def map_link(lead: dict[str, Any]) -> str:
    query = lead.get("address") or lead.get("name") or ""
    return "https://www.google.com/maps/search/?api=1&query=" + \
        re.sub(r"\s+", "+", query.strip())


def locality(address: str | None) -> str:
    """'1 Main St, Newmarket, ON L3Y 0A0, Canada' -> 'Newmarket'."""
    parts = [p.strip() for p in (address or "").split(",") if p.strip()]
    return parts[1] if len(parts) >= 3 else (parts[0] if parts else "")


def _schema(lead: dict[str, Any], content: dict[str, Any],
            day_list: list[dict[str, Any]], site_url: str) -> dict[str, Any]:
    parts = [p.strip() for p in (lead.get("address") or "").split(",") if p.strip()]
    address: dict[str, Any] = {"@type": "PostalAddress"}
    if parts:
        address["streetAddress"] = parts[0]
    if len(parts) >= 3:
        address["addressLocality"] = parts[1]
        region = parts[2].split()
        if region:
            address["addressRegion"] = region[0]
            if len(region) > 1:
                address["postalCode"] = " ".join(region[1:])
    address.setdefault("addressCountry", "CA")

    data: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "LocalBusiness",
        "name": lead.get("name"),
        "description": content.get("meta_description"),
        "address": address,
    }
    if site_url:
        data["url"] = site_url
    if lead.get("phone_e164") or lead.get("phone"):
        data["telephone"] = lead.get("phone_e164") or lead.get("phone")
    if lead.get("lat") and lead.get("lng"):
        data["geo"] = {"@type": "GeoCoordinates",
                       "latitude": lead["lat"], "longitude": lead["lng"]}
    spec = hours_mod.schema_spec(day_list)
    if spec:
        data["openingHoursSpecification"] = spec
    # Deliberately no aggregateRating: Google's terms do not allow republishing
    # their review data as our own structured data.
    return data


@dataclass
class Site:
    html: str
    robots: str
    template: str
    palette: dict[str, str] = field(default_factory=dict)
    demo: bool = False

    @property
    def budget(self) -> int:
        """The weight this page is allowed. A demo is a different product."""
        return DEMO_BUDGET_BYTES if self.demo else PAGE_BUDGET_BYTES


def dial_href(lead: dict[str, Any], region: str = "CA") -> str:
    """The number a `tel:` link should carry.

    Enrichment normally supplies E.164, but a site can be built for a lead that
    was never enriched, and stripping punctuation off "(905) 555-0123" silently
    drops the country code. Normalise here rather than ship a weaker link.
    """
    raw = lead.get("phone") or ""
    # Enrichment stores E.164 even for numbers it judged invalid, so the stored
    # value is only trustworthy when it also passed. `phone_valid` is None on a
    # lead that was never enriched, which is not the same as failing.
    if lead.get("phone_e164") and lead.get("phone_valid") != 0:
        return lead["phone_e164"]
    if not raw:
        return ""

    from enrich import normalise_phone      # local: keeps the import graph flat

    info = normalise_phone(raw, region)
    if info.valid and info.e164:
        return info.e164
    # phonenumbers happily reads letters as vanity digits, so "(905) 555-0123
    # (shop)" comes back as a real-looking number that dials nowhere. When it
    # does not validate, ship the digits that were actually printed.
    return re.sub(r"[^\d+]", "", raw)


def render(lead: dict[str, Any], content: dict[str, Any], *,
           template: Optional[str] = None, preview: bool = True,
           site_url: str = "", operator: Optional[dict[str, Any]] = None,
           region: str = "CA", city_hint: str = "", demo: bool = False,
           demo_note: str = "", showcase: bool = False) -> Site:
    """One business plus its copy, rendered into a page.

    `demo` turns on the richer build: the ribbon that says it is a sample, the
    stock credits in the footer, and a weight budget that expects photographs.
    It changes nothing about what may be *claimed*.

    `showcase` is the one thing that does, and it exists for exactly one case:
    the twelve portfolio sites in `demo/fixtures/`, whose businesses are
    invented. The fabrication check protects a real business from claims nobody
    made about them; a business that does not exist has nothing to protect, and
    a page that cannot carry a testimonial cannot show what a page with one
    looks like. Nothing built from a Google listing ever sets it — `build
    --demo` on a real lead reviews its copy exactly as a production build does.
    The accessibility check runs either way, because a blind visitor to a
    showcase page is still a blind visitor.
    """
    template = template or template_for(lead.get("category"))
    if template not in TEMPLATES:
        raise ContentError(f"Unknown template '{template}'. "
                           f"Use one of: {', '.join(TEMPLATES)}")

    # The check runs here, not only at generation time. `content.json` is the
    # documented edit surface — if it only ran when Claude wrote the copy, then
    # hand-editing the file (the Phase 5 workflow) would walk straight past the
    # one guard protecting the pitch.
    issues = [] if showcase else review(
        content, source_text(lead, city_hint, content.get("verified_facts")))
    if issues:
        raise ContentError(
            "This copy cannot go on someone's website:\n"
            + "\n".join(f"  - {i}" for i in issues[:12])
            + f"\n\nEdit {os.path.join(site_dir(lead['place_id']), 'content.json')} "
              "and run build again. If the claim is true and the owner told you "
              "so, add it to \"verified_facts\" in that file and it will be "
              "allowed."
        )

    # Ontario's AODA makes WCAG 2.0 AA a legal obligation for these businesses'
    # websites, so this is a build failure rather than a warning. A warning is
    # something an operator working through forty sites scrolls past.
    barriers = accessibility_issues(content)
    if barriers:
        raise ContentError(
            "This page would not meet WCAG 2.0 AA, which Ontario's AODA "
            "requires of the client's website:\n"
            + "\n".join(f"  - {b}" for b in barriers[:12])
            + f"\n\nEdit {os.path.join(site_dir(lead['place_id']), 'content.json')} "
              "and run build again."
        )

    palette = design.palette_for(lead["place_id"], template)
    style = design.style_for(template)
    day_list = hours_mod.parse(lead.get("hours"))
    summary = hours_mod.summary(day_list)
    city = locality(lead.get("address"))
    phone_display = lead.get("phone") or ""
    phone_href = dial_href(lead, region)
    photo_list = visuals.photos_from(content)

    html = _env().get_template(f"{template}.html").render(
        lead=lead,
        c=content,
        palette=palette,
        style=style,
        days=day_list,
        hours_summary=summary,
        week_json=hours_mod.week_minutes(day_list),
        schema=_schema(lead, content, day_list, site_url),
        stats=_facts(lead, city, summary),
        services_word=SERVICES_WORD.get(template, "Services"),
        photos=photo_list,
        # Whoever took the picture gets named, on a demo and on a live site
        # alike. Most stock licences do not require it; putting it there anyway
        # costs one line of 12px type and settles the question permanently.
        credits=visuals.credits_for(photo_list),
        demo=demo,
        demo_note=demo_note,
        # A callable, not one rendered SVG: each slot on the page asks for its
        # own variant so the compositions differ.
        make_art=lambda variant=0: Markup(visuals.artwork(
            lead["place_id"], template, lead.get("name", ""), variant)),
        favicon=_favicon(palette, lead.get("name", "")),
        year=datetime.date.today().year,
        phone_display=phone_display,
        phone_href=phone_href,
        city=city,
        map_url=map_link(lead),
        preview=preview,
        site_url=site_url,
        operator=operator or {},
        template_name=template,
    )
    robots = ("User-agent: *\nDisallow: /\n" if preview
              else "User-agent: *\nAllow: /\n")
    return Site(html=squeeze_css(html), robots=robots, template=template,
                palette=palette, demo=demo)


# ---------------------------------------------------------------------------
# Disk
# ---------------------------------------------------------------------------
def site_dir(place_id: str) -> str:
    return os.path.join(SITES_DIR, place_id)


def load_content(place_id: str) -> Optional[dict[str, Any]]:
    path = os.path.join(site_dir(place_id), "content.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ContentError(
            f"{path} is not valid JSON ({exc.msg}, line {exc.lineno}).\n"
            "Fix the syntax — a trailing comma is the usual culprit — or delete "
            "the file and re-run build with --regenerate."
        ) from exc

    version = payload.get("version", 1)
    if not isinstance(version, int) or version > CONTENT_VERSION:
        raise ContentError(
            f"{path} was written by a newer version of this tool "
            f"(content version {version}, this build understands "
            f"{CONTENT_VERSION}). Update the tool rather than downgrading the file."
        )
    return payload


def save_rejected(place_id: str, content: dict[str, Any], issues: list[str],
                  usage: "Usage") -> str:
    """Keep a draft the review refused, so the spend is not simply lost."""
    directory = site_dir(place_id)
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "content.rejected.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({
            "version": CONTENT_VERSION,
            "place_id": place_id,
            "rejected_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "cost_usd": round(usage.cost, 4),
            "issues": issues,
            "verified_facts": [],
            "copy": content,
        }, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return path


def save_content(place_id: str, payload: dict[str, Any]) -> str:
    directory = site_dir(place_id)
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "content.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return path


def weigh(place_id: str) -> tuple[int, list[tuple[str, int]]]:
    """Total bytes a visitor would download, and the biggest offenders.

    Only files the page can actually reference — content.json and any rejected
    draft sit in the directory but are never deployed.
    """
    directory = site_dir(place_id)
    skip = {"content.json", "content.rejected.json"}
    files = []
    for name in sorted(os.listdir(directory)) if os.path.isdir(directory) else []:
        path = os.path.join(directory, name)
        if name in skip or not os.path.isfile(path):
            continue
        files.append((name, os.path.getsize(path)))
    return sum(size for _, size in files), sorted(files, key=lambda f: -f[1])


def write_site(place_id: str, site: Site) -> dict[str, str]:
    directory = site_dir(place_id)
    os.makedirs(directory, exist_ok=True)
    paths = {}
    for name, body in (("index.html", site.html), ("robots.txt", site.robots)):
        path = os.path.join(directory, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        paths[name] = path
    return paths
