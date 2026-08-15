"""Twelve templates, and the things that have to stay true across all of them.

The point of the rebuild was that a dental clinic should not get the roofing
page in a different colour. These are the tests that say so — that the pages
are structurally different, that the copy still reaches every one of them, and
that nothing a template adds can quietly break the guarantees in the core.
"""
import re

import pytest

import demo
import design
import generate
import visuals
from tests.test_generate import GOOD, LEAD
from tests.test_render import css_of, has_class, html_for, markup


def services_section(page):
    """Just the services section, so a class used elsewhere cannot answer for it."""
    start = page.index('id="services"')
    return page[start:page.index("</section>", start)]


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("category,expected", [
    ("Roofing contractor", "trade"), ("Plumber", "trade"),
    ("HVAC contractor", "trade"), ("Handyman", "trade"),
    ("Restaurant", "food"), ("Coffee shop", "food"), ("Sports bar", "food"),
    ("Hair salon", "salon"), ("Barber shop", "salon"), ("Day spa", "salon"),
    ("Auto repair shop", "auto"), ("Tire shop", "auto"), ("Car wash", "auto"),
    ("Gym", "wellness"), ("Yoga studio", "wellness"), ("Pilates studio", "wellness"),
    ("Dentist", "clinic"), ("Optometrist", "clinic"), ("Veterinary care", "clinic"),
    ("Law firm", "professional"), ("Accountant", "professional"),
    ("Real estate agency", "professional"),
    ("Gift shop", "retail"), ("Hardware store", "retail"), ("Florist", "retail"),
    ("Landscaping service", "home"), ("Snow removal service", "home"),
    ("Cleaning service", "home"),
    ("Pet groomer", "pet"), ("Dog daycare", "pet"), ("Pet store", "pet"),
    ("Photographer", "creative"), ("Tattoo shop", "creative"),
    ("Sign shop", "creative"),
    ("Preschool", "education"), ("Driving school", "education"),
    ("Tutoring service", "education"),
])
def test_a_category_reaches_the_template_built_for_it(category, expected):
    assert generate.template_for(category) == expected


def test_the_specific_beats_the_general():
    # Each of these matches two templates' keywords. The first one listed in
    # TEMPLATE_KEYWORDS wins, and the order is the whole mechanism.
    assert generate.template_for("Pet grooming salon") == "pet"
    assert generate.template_for("Auto glass shop") == "auto"
    assert generate.template_for("Window cleaning service") == "home"
    assert generate.template_for("Dance studio") == "wellness"


def test_a_category_nobody_thought_of_still_gets_a_page():
    assert generate.template_for("Llama trekking") == generate.DEFAULT_TEMPLATE
    assert generate.template_for(None) == generate.DEFAULT_TEMPLATE


# ---------------------------------------------------------------------------
# They are actually different
# ---------------------------------------------------------------------------
def test_no_two_templates_produce_the_same_page():
    pages = {t: markup(html_for(t)) for t in generate.TEMPLATES}
    assert len(set(pages.values())) == len(pages)


def test_the_services_list_is_laid_out_differently_across_the_catalogue():
    # If every template rendered `.cards`, the rebuild would have been twelve
    # colour schemes after all.
    layouts = {}
    for template in generate.TEMPLATES:
        section = services_section(html_for(template))
        found = [name for name in ("menu", "stack", "index-list", "rail",
                                   "tiles", "cards")
                 if has_class(section, name)]
        assert found, f"{template} has no recognisable services layout"
        layouts[template] = found[0]
    assert len(set(layouts.values())) >= 5, layouts


def test_each_template_has_its_own_type_personality():
    displays = {design.style_for(t)["font_display"] for t in generate.TEMPLATES}
    assert len(displays) >= 3          # sans, serif, rounded
    weights = {design.style_for(t)["display_weight"] for t in generate.TEMPLATES}
    assert len(weights) >= 4


def test_each_template_has_its_own_motion_character():
    motions = {design.style_for(t)["motion"] for t in generate.TEMPLATES}
    assert len(motions) >= 8
    # And every one of them is a keyframe that actually exists in the CSS.
    for template in generate.TEMPLATES:
        css = css_of(html_for(template))
        assert f"@keyframes {design.style_for(template)['motion']}" in css


def test_each_template_draws_its_own_artwork():
    art = {visuals.artwork("p", t, "AB") for t in generate.TEMPLATES}
    assert len(art) == len(generate.TEMPLATES)


def test_the_accent_lands_inside_the_category_arc():
    # A clinic is never mustard and a garage is never mint, however the hash
    # falls. Same business, same colour, forever — inside a sensible band.
    for template in generate.TEMPLATES:
        low, high = design.style_for(template)["hue_arc"]
        for i in range(40):
            hue = design.hue_for(f"place-{i}", template)
            # The arc may run past 360, so compare in the arc's own frame.
            shifted = hue + 360 if hue < low % 360 and high > 360 else hue
            assert low - 0.01 <= shifted <= high + 0.01, (template, hue)


def test_without_a_template_the_hue_still_spans_the_whole_wheel():
    # Anything that called this before the arcs existed keeps its behaviour.
    hues = [design.hue_for(f"place-{i}") for i in range(400)]
    assert {int(h // 90) for h in hues} == {0, 1, 2, 3}


# ---------------------------------------------------------------------------
# What a template may not break
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("template", generate.TEMPLATES)
def test_every_template_asks_for_the_call(template):
    page = html_for(template)
    assert page.count('href="tel:+19055550123"') >= 3
    assert has_class(page, "callbar")


@pytest.mark.parametrize("template", generate.TEMPLATES)
def test_every_template_carries_the_whole_of_the_copy(template):
    # A section quietly dropped from a recipe is copy the operator paid for and
    # the visitor never sees.
    page = markup(html_for(template))
    for service in GOOD["services"]:
        assert service["name"] in page, service["name"]
    for point in GOOD["why_us"]:
        assert point in page
    for para in GOOD["about"]:
        assert para in page
    assert GOOD["hero_headline"] in page and GOOD["cta_line"] in page


@pytest.mark.parametrize("template", generate.TEMPLATES)
def test_the_optional_blocks_reach_every_template(template):
    rich = {**GOOD,
            "process_headline": "What happens after you call",
            "process": [{"name": "You ring", "description": "Tell us what you see."},
                        {"name": "We look", "description": "Someone comes out."},
                        {"name": "We fix it", "description": "And sweep up after."}],
            "faq": [{"q": "Which areas do you work in?", "a": "Newmarket and around it."},
                    {"q": "When are you open?", "a": "Weekdays from eight."},
                    {"q": "Do you look first?", "a": "Always, before quoting."}],
            "service_areas": ["Newmarket"],
            "closing_note": "Ring first thing if it is actively leaking."}
    page = markup(html_for(template, content=rich))
    assert "What happens after you call" in page
    assert "Which areas do you work in?" in page
    assert "<details" in page and "<summary" in page
    assert "Newmarket" in page


@pytest.mark.parametrize("template", generate.TEMPLATES)
def test_a_version_one_content_file_still_renders(template):
    # Nothing added since v1 is required, so a content.json written before any
    # of it existed produces a complete page.
    page = markup(html_for(template, content=GOOD))
    assert "<details" not in page          # no faq block, no faq section
    assert GOOD["hero_headline"] in page


@pytest.mark.parametrize("template", generate.TEMPLATES)
def test_no_template_smuggles_an_animation_past_the_motion_guard(template):
    # Templates may add CSS. They may not add a scroll timeline outside the one
    # block that print and prefers-reduced-motion switch off.
    css = css_of(html_for(template))
    guard = "@media (prefers-reduced-motion: no-preference) {"
    from tests.test_render import block_after
    motion = block_after(css, guard)
    assert "animation-timeline" not in css.replace(motion, "")


@pytest.mark.parametrize("template", generate.TEMPLATES)
def test_no_template_hides_content_behind_an_opacity(template):
    css = css_of(html_for(template))
    outside_keyframes = re.sub(r"@keyframes[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}",
                               "", css, flags=re.S)
    assert not re.search(r"opacity:\s*0\s*[;}]", outside_keyframes)


@pytest.mark.parametrize("template", generate.TEMPLATES)
def test_no_copy_is_ever_set_on_top_of_a_photograph(template):
    # What is in the picture is not known at build time, so the contrast ratio
    # of anything over it is unknowable and 1.4.3 is not left to luck.
    page = html_for(template, content={**GOOD, "photos": [
        {"src": f"{i}.jpg", "alt": f"A photograph number {i}"} for i in range(12)]})
    for panel in re.findall(r'<div class="art-panel[^"]*"[^>]*>(.*?)</div>',
                            page, re.S):
        text = re.sub(r"<[^>]+>", "", panel).strip()
        assert not text, f"{template}: text inside an image slot: {text[:40]!r}"


@pytest.mark.parametrize("template", generate.TEMPLATES)
def test_every_template_fits_the_budget_with_room_to_spare(template):
    assert len(html_for(template).encode("utf-8")) < generate.PAGE_BUDGET_BYTES / 2


# ---------------------------------------------------------------------------
# The slot map
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("template", generate.TEMPLATES)
def test_the_photo_slot_count_matches_what_the_template_actually_asks_for(template):
    # PHOTO_SLOTS is what a photo fetch buys. If it undercounts, the last slots
    # fall back to artwork on a page that paid for photographs; if it
    # overcounts, files are downloaded and never shown.
    fixture = demo.load(template)
    photos = [{"src": f"{i:02d}.jpg", "alt": f"A photograph number {i}"}
              for i in range(40)]
    page = generate.render(fixture["lead"], {**fixture["copy"], "photos": photos},
                           template=template, showcase=True).html
    used = {int(m) for m in re.findall(r'src="(\d\d)\.jpg"', page)}
    assert used, template
    assert max(used) + 1 == generate.PHOTO_SLOTS[template], (
        f"{template} uses slots up to {max(used)}, "
        f"PHOTO_SLOTS says {generate.PHOTO_SLOTS[template]}")
    # Contiguous from zero, so `photos[3]` means the fourth picture down.
    assert used == set(range(max(used) + 1))
