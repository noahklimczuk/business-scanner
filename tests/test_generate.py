"""Copy generation and the check that stands between the model and a real
business's website. No test here touches the Anthropic API."""
import json
from types import SimpleNamespace

import pytest

import generate

LEAD = {
    "place_id": "ChIJexample",
    "name": "Klimczuk Roofing",
    "category": "Roofing contractor",
    "address": "421 Davis Dr, Newmarket, ON L3Y 2N7, Canada",
    "phone": "(905) 555-0123",
    "phone_e164": "+19055550123",
    "lat": 44.0592, "lng": -79.4613,
    "rating": 4.7, "review_count": 84,
    "hours": ["Monday: 8:00 AM – 5:00 PM", "Tuesday: 8:00 AM – 5:00 PM",
              "Wednesday: 8:00 AM – 5:00 PM", "Thursday: 8:00 AM – 5:00 PM",
              "Friday: 8:00 AM – 5:00 PM", "Saturday: 9:00 AM – 1:00 PM",
              "Sunday: Closed"],
}

GOOD = {
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
    "meta_title": "Klimczuk Roofing — Roofing in Newmarket",
    "meta_description": "Roof replacement, leak repair and flat roofing in Newmarket, Ontario.",
}

SOURCE = generate.source_text(LEAD, "Newmarket, ON")


def copy_with(**overrides):
    content = json.loads(json.dumps(GOOD))
    content.update(overrides)
    return content


# ---------------------------------------------------------------------------
@pytest.mark.parametrize("category,expected", [
    ("Roofing contractor", "trade"),
    ("Plumber", "trade"),
    ("Restaurant", "food"),
    ("Coffee shop", "food"),
    ("Bakery", "food"),
    ("Hair salon", "salon"),
    ("Barber shop", "salon"),
    ("Nail salon", "salon"),
    ("Day spa", "salon"),
    (None, "trade"),
    ("Something unheard of", "trade"),
])
def test_template_selection(category, expected):
    assert generate.template_for(category) == expected


# ---------------------------------------------------------------------------
def test_clean_copy_passes():
    assert generate.review(GOOD, SOURCE) == []


def test_an_invented_founding_year_is_caught():
    issues = generate.review(
        copy_with(about=["Family roofing in Newmarket since 1987.", "We do flat roofs."]),
        SOURCE)
    assert any("1987" in i for i in issues)


def test_a_year_that_is_actually_in_the_data_is_allowed():
    lead = {**LEAD, "name": "Roofing 1987 Ltd"}
    source = generate.source_text(lead)
    content = copy_with(hero_headline="Roofing 1987 Ltd, Newmarket")
    assert not [i for i in generate.review(content, source) if "1987" in i]


@pytest.mark.parametrize("claim", [
    "We are fully licensed and insured.",
    "Our certified crew handles every job.",
    "A family owned shop serving the area.",
    "Every job comes with a written guarantee.",
    "Free estimate on any roof.",
    "We offer 24/7 emergency service.",
])
def test_credential_claims_are_caught(claim):
    issues = generate.review(copy_with(about=[claim, "We do flat roofs."]), SOURCE)
    assert issues, claim


def test_a_credential_in_the_business_name_is_not_an_invention():
    lead = {**LEAD, "name": "Newmarket Licensed Roofing"}
    source = generate.source_text(lead)
    content = copy_with(hero_headline="Newmarket Licensed Roofing")
    assert not [i for i in generate.review(content, source) if "licensed" in i]


@pytest.mark.parametrize("phrase", [
    "The best roofers in town.",
    "Newmarket's premier roofing company.",
    "A leading name in flat roofing.",
    "Nestled in the heart of York Region.",
    "We pride ourselves on our work.",
])
def test_superlatives_are_caught(phrase):
    assert generate.review(copy_with(hero_sub=phrase), SOURCE)


@pytest.mark.parametrize("phrase", [
    "Over 20 years of experience on local roofs.",
    "Three generations of roofers.",
    "Two decades roofing in York Region.",
])
def test_claims_about_length_of_time_are_caught(phrase):
    assert generate.review(copy_with(about=[phrase, "We do flat roofs."]), SOURCE)


def test_markup_is_rejected():
    assert generate.review(copy_with(hero_sub="Roofing <b>done right</b>"), SOURCE)


def test_shape_violations_are_caught():
    assert generate.review(copy_with(about=["only one paragraph"]), SOURCE)
    assert generate.review(copy_with(why_us=["one", "two"]), SOURCE)
    assert generate.review(copy_with(services=GOOD["services"][:2]), SOURCE)
    assert generate.review(copy_with(services=GOOD["services"] * 3), SOURCE)


def test_lengths_that_would_be_truncated_by_google_are_caught():
    assert generate.review(copy_with(meta_title="x" * 80), SOURCE)
    assert generate.review(copy_with(meta_description="x" * 200), SOURCE)
    assert generate.review(copy_with(hero_headline="x" * 120), SOURCE)


def test_empty_strings_are_caught():
    assert generate.review(copy_with(cta_line="   "), SOURCE)


def test_the_facts_the_model_sees_never_include_a_website_or_place_id():
    facts = generate.facts_for(LEAD, "Newmarket, ON")
    assert "ChIJexample" not in facts
    assert "Klimczuk Roofing" in facts
    assert "Monday: 8am – 5pm" in facts


def test_no_listing_hands_the_model_a_rating_to_lean_on():
    # A calibration run against a live model is what turned this up: given the
    # rating in the input, it put "Rated 4.9 stars across 61 customer reviews"
    # on three pages out of four — following the prompt exactly, because
    # nothing said the rating was the one input that may not be repeated.
    # The fix is not to instruct harder; it is to stop sending it.
    for lead in (LEAD, {**LEAD, "review_count": 2},
                 {**LEAD, "rating": 4.9, "review_count": 400}):
        facts = generate.facts_for(lead, "Newmarket, ON").lower()
        assert "rating" not in facts
        assert "review" not in facts
        assert "4.7" not in facts


@pytest.mark.parametrize("claim", [
    "Rated 4.9 stars across 61 customer reviews.",
    "A 4.7-star rating from local homeowners.",
    "We are rated 4.5 by our customers.",
    "Scoring 4.7/5 on Google.",
    "Trusted by 212 reviews.",
    "84 customer reviews and counting.",
])
def test_googles_rating_cannot_be_republished_as_the_clients_own_copy(claim):
    # Not a question of truth — the rating is true. It is Places content,
    # licensed for display inside our tool, and the schema builder and the
    # stats strip already leave it off the page for exactly this reason. The
    # copy was the third door and the only one a model can walk through alone.
    issues = generate.review(copy_with(hero_sub=claim), SOURCE)
    assert issues, claim
    assert any("rating or review count" in i for i in issues), issues


def test_a_business_actually_called_five_star_is_not_caught_by_that():
    lead = {**LEAD, "name": "5 Star Roofing"}
    source = generate.source_text(lead)
    content = copy_with(meta_title="5 Star Roofing — Newmarket")
    assert not [i for i in generate.review(content, source)
                if "rating or review count" in i]


def test_ordinary_copy_about_reviews_is_left_alone():
    # The rule is about republishing the number, not about the word.
    for fine in ("Read what your neighbours say about our work.",
                 "We will review the roof with you before we start."):
        assert not [i for i in generate.review(copy_with(hero_sub=fine), SOURCE)
                    if "rating or review count" in i]


# ---------------------------------------------------------------------------
class FakeUsage(SimpleNamespace):
    pass


def fake_response(payload, stop_reason="end_turn"):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text=text)],
        usage=FakeUsage(input_tokens=120, output_tokens=600,
                        cache_creation_input_tokens=800, cache_read_input_tokens=0),
    )


class FakeClient:
    def __init__(self, *responses):
        self._responses = list(responses)
        self.requests = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return self._responses.pop(0)


def test_write_copy_returns_validated_content():
    client = FakeClient(fake_response(GOOD))
    content, usage = generate.write_copy(LEAD, "Newmarket, ON", client=client)
    assert content == GOOD
    assert usage.attempts == 1
    assert usage.cost > 0


def test_the_request_is_shaped_the_way_the_api_expects():
    client = FakeClient(fake_response(GOOD))
    generate.write_copy(LEAD, "Newmarket, ON", client=client)
    request = client.requests[0]
    assert request["model"] == generate.MODEL
    # Structured outputs, not "please return JSON" and a prayer.
    assert request["output_config"]["format"]["type"] == "json_schema"
    assert request["output_config"]["effort"] == "low"
    # The rules are identical per business, so they are cached.
    assert request["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "temperature" not in request      # removed on this model, would 400


def test_a_rejected_draft_is_sent_back_with_the_specific_problems():
    bad = copy_with(about=["Serving Newmarket since 1994.", "We do flat roofs."])
    client = FakeClient(fake_response(bad), fake_response(GOOD))
    content, usage = generate.write_copy(LEAD, "Newmarket, ON", client=client)
    assert content == GOOD
    assert usage.attempts == 2
    retry = client.requests[1]["messages"][0]["content"]
    assert "1994" in retry and "rejected" in retry


def test_copy_that_stays_wrong_is_never_written_to_a_site():
    bad = copy_with(hero_sub="The best roofers in Newmarket since 1994.")
    client = FakeClient(fake_response(bad), fake_response(bad))
    with pytest.raises(generate.ContentError) as exc:
        generate.write_copy(LEAD, "Newmarket, ON", client=client)
    assert "1994" in str(exc.value)


def test_a_refusal_is_explained_not_raised_as_a_stack_trace():
    client = FakeClient(fake_response(GOOD, stop_reason="refusal"))
    with pytest.raises(generate.ContentError, match="declined"):
        generate.write_copy(LEAD, "Newmarket, ON", client=client)


def test_a_truncated_response_says_so():
    client = FakeClient(fake_response(GOOD, stop_reason="max_tokens"))
    with pytest.raises(generate.ContentError, match="cut off"):
        generate.write_copy(LEAD, "Newmarket, ON", client=client)


def test_non_json_is_a_clear_error():
    client = FakeClient(fake_response("Sure! Here is the copy:"))
    with pytest.raises(generate.ContentError, match="not JSON"):
        generate.write_copy(LEAD, "Newmarket, ON", client=client)


def test_cost_uses_the_cache_rates():
    usage = generate.Usage(input_tokens=1_000_000, output_tokens=0)
    assert usage.cost == pytest.approx(generate.PRICE_IN)
    cached = generate.Usage(cache_read_tokens=1_000_000)
    assert cached.cost == pytest.approx(generate.PRICE_IN * 0.10)
    written = generate.Usage(cache_write_tokens=1_000_000)
    assert written.cost == pytest.approx(generate.PRICE_IN * 1.25)


def test_a_site_stays_inside_the_cost_target():
    # ~800 cached system tokens, ~150 of facts, ~700 of copy. The guide's
    # ceiling is 15 cents; this test fails loudly if the shape of the call
    # changes enough to approach it.
    usage = generate.Usage(input_tokens=150, output_tokens=800,
                           cache_write_tokens=800)
    assert usage.cost < 0.15
