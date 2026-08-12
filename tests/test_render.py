"""What actually ships to the client's domain. These assert the promises in the
guide: the phone is a tel: link, the preview cannot be indexed, the schema is
real, nothing is fetched from another host, and no Google photo appears."""
import json
import re

import pytest

import generate
from tests.test_generate import GOOD, LEAD


def html_for(template="trade", lead=None, content=None, **kw):
    return generate.render(lead or LEAD, content or GOOD, template=template, **kw).html


def markup(page):
    """The document without its stylesheet — for asserting a thing is *absent*.
    The CSS mentions `.hours-table` and clamp values like 4.75rem, which make
    naive absence checks pass or fail for the wrong reason."""
    return re.sub(r"<style>.*?</style>", "", page, flags=re.S)


@pytest.fixture(params=generate.TEMPLATES)
def page(request):
    return html_for(request.param)


# ---------------------------------------------------------------------------
def test_the_phone_is_a_tel_link_everywhere(page):
    assert 'href="tel:+19055550123"' in page
    assert page.count('href="tel:+19055550123"') >= 3   # header, plate, call bar


def test_the_mobile_call_bar_is_present(page):
    assert 'class="callbar"' in page
    assert "Call (905) 555-0123" in page


def test_a_preview_cannot_be_indexed(page):
    assert '<meta name="robots" content="noindex,nofollow">' in page


def test_launching_strips_the_noindex_and_adds_a_canonical():
    live = html_for(preview=False, site_url="https://klimczukroofing.ca")
    assert "noindex" not in live
    assert '<link rel="canonical" href="https://klimczukroofing.ca">' in live


def test_robots_txt_matches_the_indexing_state():
    assert "Disallow: /" in generate.render(LEAD, GOOD).robots
    assert "Allow: /" in generate.render(LEAD, GOOD, preview=False).robots


def test_open_graph_tags_are_complete(page):
    for prop in ("og:type", "og:title", "og:description", "og:site_name", "og:locale"):
        assert f'property="{prop}"' in page


def test_local_business_schema_is_valid_json_and_complete(page):
    raw = re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.S)
    data = json.loads(raw.group(1))
    assert data["@type"] == "LocalBusiness"
    assert data["name"] == "Klimczuk Roofing"
    assert data["telephone"] == "+19055550123"
    assert data["geo"]["latitude"] == 44.0592
    assert data["address"]["addressLocality"] == "Newmarket"
    assert data["address"]["addressRegion"] == "ON"
    assert data["address"]["postalCode"] == "L3Y 2N7"
    assert len(data["openingHoursSpecification"]) == 6      # Sunday is closed


def test_schema_does_not_republish_googles_rating():
    # Their rating is the best line in the pitch and the wrong thing to restate
    # as the client's own structured data.
    page = html_for()
    data = json.loads(re.search(r'ld\+json">(.*?)</script>', page, re.S).group(1))
    assert "aggregateRating" not in data
    assert "4.7" not in markup(page)


def test_the_map_link_points_at_the_address(page):
    assert "google.com/maps/search/" in page
    assert "421+Davis+Dr" in page


def test_hours_are_rendered_and_shipped_to_the_client_side_check(page):
    assert "8am – 5pm" in page
    assert "[[480,1020]]" in page.replace(" ", "")      # Monday, in minutes


def test_nothing_is_loaded_from_another_host(page):
    # A webfont or a CDN script is a second round trip on rural LTE, and the
    # whole point of this page is that it arrives before the visitor gives up.
    assert "<script src" not in page
    assert "<link rel=\"stylesheet\"" not in page
    for url in re.findall(r'(?:src|href)="(https?://[^"]+)"', page):
        assert url.startswith("https://www.google.com/maps/"), url


def test_no_google_places_photo_reaches_a_client_page(page):
    for banned in ("googleusercontent", "maps.googleapis.com/maps/api/place/photo",
                   "places.googleapis.com"):
        assert banned not in page


def test_the_page_fits_the_weight_budget(page):
    assert len(page.encode("utf-8")) < 150 * 1024


def test_the_page_is_actually_small():
    # The budget is 150KB; a self-contained page has no excuse to be near it.
    assert len(html_for().encode("utf-8")) < 40 * 1024


def test_accessibility_basics(page):
    assert '<html lang="en-CA">' in page
    assert 'class="skip"' in page
    assert "<h1" in page and page.count("<h1") == 1
    assert 'name="viewport"' in page


def test_a_business_name_with_markup_in_it_is_escaped():
    hostile = {**LEAD, "name": 'Bob\'s "Best" <script>alert(1)</script> Roofing'}
    page = html_for(lead=hostile)
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_a_hostile_name_cannot_break_out_of_the_generated_artwork():
    # The SVG is marked safe before it reaches Jinja, so it has to escape its
    # own interpolations. Google listings are not trusted input.
    import visuals
    art = visuals.artwork("p", "trade", '"><script>alert(1)</script>')
    assert "<script>" not in art
    assert "&lt;script&gt;" in art
    assert art.count('aria-label="') == 1


def test_copy_with_an_ampersand_survives_the_schema():
    content = {**GOOD, "meta_description": "Roofing & flat roofs in Newmarket"}
    page = html_for(content=content)
    data = json.loads(re.search(r'ld\+json">(.*?)</script>', page, re.S).group(1))
    assert data["description"] == "Roofing & flat roofs in Newmarket"


def test_a_lead_with_no_phone_does_not_render_a_dead_call_button():
    page = html_for(lead={**LEAD, "phone": None, "phone_e164": None})
    assert "tel:" not in page
    assert 'class="callbar"' not in page


def test_a_lead_with_no_hours_skips_the_open_now_machinery():
    page = html_for(lead={**LEAD, "hours": None})
    assert "data-openstate" not in markup(page)
    assert "hours-table" not in markup(page)


def test_the_accent_is_the_businesss_own_and_appears_in_the_css(page):
    palette = generate.design.palette_for(LEAD["place_id"], "trade")
    assert palette["accent"] in html_for("trade")


def test_each_template_lays_the_services_out_differently():
    trade, food, salon = (html_for(t) for t in ("trade", "food", "salon"))
    assert has_class(trade, "cards") and has_class(food, "menu")
    assert has_class(salon, "stack")
    assert "Roof replacement" in trade and "Roof replacement" in food


def test_an_unknown_template_is_refused():
    with pytest.raises(generate.ContentError, match="Unknown template"):
        generate.render(LEAD, GOOD, template="brochure")


# ---------------------------------------------------------------------------
def test_content_round_trips_through_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(generate, "SITES_DIR", str(tmp_path))
    assert generate.load_content("ChIJexample") is None
    generate.save_content("ChIJexample", {"version": 1, "copy": GOOD})
    assert generate.load_content("ChIJexample")["copy"] == GOOD


def test_writing_a_site_produces_exactly_the_two_files(tmp_path, monkeypatch):
    monkeypatch.setattr(generate, "SITES_DIR", str(tmp_path))
    site = generate.render(LEAD, GOOD)
    paths = generate.write_site("ChIJexample", site)
    assert sorted(p.name for p in (tmp_path / "ChIJexample").iterdir()) == [
        "index.html", "robots.txt"]
    assert open(paths["index.html"], encoding="utf-8").read().startswith("<!doctype html>")


def test_an_unenriched_lead_still_gets_a_full_international_tel_link():
    # Phase 2 normally supplies E.164. Stripping punctuation off the display
    # number instead would silently drop the +1.
    raw = {**LEAD, "phone_e164": None, "phone": "(905) 555-0123"}
    assert generate.dial_href(raw) == "+19055550123"
    assert 'href="tel:+19055550123"' in html_for(lead=raw)


def test_a_number_that_cannot_be_parsed_falls_back_to_its_digits():
    # Better a partial number the owner can correct than no call button at all.
    odd = {**LEAD, "phone_e164": None, "phone": "905-555-012 (shop)"}
    assert generate.dial_href(odd) == "905555012"


def test_no_phone_means_no_href():
    assert generate.dial_href({**LEAD, "phone_e164": None, "phone": None}) == ""


def test_a_stored_e164_that_enrichment_marked_invalid_is_not_trusted():
    # phonenumbers reads letters as vanity digits, so enrich can store a
    # real-looking E.164 for a number that dials nowhere. phone_valid is the
    # authority; None (never enriched) is not the same as 0 (failed).
    bad = {**LEAD, "phone_e164": "+19055550127467", "phone_valid": 0,
           "phone": "905-555-012 (shop)"}
    assert generate.dial_href(bad) == "905555012"
    unenriched = {**LEAD, "phone_valid": None}
    assert generate.dial_href(unenriched) == "+19055550123"


# --------------------------------------------------------------------------
# Visuals and motion
# --------------------------------------------------------------------------
def css_of(page):
    css = re.search(r"<style>(.*?)</style>", page, re.S).group(1)
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)      # comments mention the
                                                            # very things we grep for


def has_class(page, name):
    return re.search(rf'class="[^"]*\b{name}\b', page) is not None


def block_after(css, opener):
    """The text of the {...} block that follows `opener`, brace-matched."""
    start = css.index(opener) + len(opener)
    depth, i = 0, start
    while i < len(css):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return css[start:i]
        i += 1
    raise AssertionError(f"unbalanced braces after {opener!r}")


def test_generated_artwork_fills_the_image_slot_when_there_are_no_photos(page):
    assert 'class="art-svg"' in page
    assert 'class="art-panel"' in page


def test_owner_photos_replace_the_generated_artwork():
    content = {**GOOD, "photos": [
        {"src": "roof-davis-dr.jpg", "alt": "New roof on Davis Drive",
         "width": 2000, "height": 1333}]}
    page = html_for(content=content)
    assert 'src="roof-davis-dr.jpg"' in page
    assert 'alt="New roof on Davis Drive"' in page
    assert 'width="2000"' in page and 'height="1333"' in page   # no layout shift


def test_the_first_photo_is_eager_and_later_ones_are_lazy():
    content = {**GOOD, "photos": [
        {"src": "a.jpg", "alt": "a"}, {"src": "b.jpg", "alt": "b"}]}
    page = html_for(content=content)
    assert 'fetchpriority="high"' in page
    assert 'loading="lazy"' in page


def test_a_remote_photo_url_is_refused():
    # The only way a Places photo could reach a client page is through this
    # field, so it does not accept anything off-origin.
    for bad in ("https://lh3.googleusercontent.com/p/x", "//evil.example/x.jpg",
                "http://example.com/x.jpg"):
        content = {**GOOD, "photos": [{"src": bad, "alt": "x"}]}
        page = html_for(content=content)
        assert bad not in page
        assert 'class="art-svg"' in page          # falls back to generated art


def test_og_image_only_appears_once_there_is_a_real_photo_and_a_url():
    plain = html_for(preview=False, site_url="https://x.ca")
    assert "og:image" not in plain
    withphoto = html_for(content={**GOOD, "photos": [{"src": "a.jpg", "alt": "a"}]},
                         preview=False, site_url="https://x.ca")
    assert '<meta property="og:image" content="https://x.ca/a.jpg">' in withphoto


def test_every_scroll_animation_is_behind_a_support_and_motion_query(page):
    css = css_of(page)
    assert "@supports (animation-timeline: view())" in css
    assert "prefers-reduced-motion: no-preference" in css
    # Everything scroll-driven must live inside the motion block, so removing
    # it leaves no animation-timeline behind.
    motion = block_after(css, "@media (prefers-reduced-motion: no-preference) {")
    assert "animation-timeline" in motion
    assert "animation-timeline" not in css.replace(motion, "")


def test_no_element_starts_invisible():
    # The classic failure of scroll-reveal: content defaults to opacity 0 and a
    # browser that never fires the animation shows a blank page. Only keyframes
    # may mention a zero opacity.
    css = css_of(html_for())
    outside_keyframes = re.sub(r"@keyframes[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}",
                               "", css, flags=re.S)
    assert not re.search(r"opacity:\s*0\s*[;}]", outside_keyframes)
    assert not re.search(r"visibility:\s*hidden", outside_keyframes)


def test_motion_is_css_only_and_adds_no_javascript(page):
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", page, re.S)
    joined = " ".join(scripts)
    for js_motion in ("IntersectionObserver", "requestAnimationFrame",
                      "addEventListener('scroll'", "onscroll"):
        assert js_motion not in joined


def test_the_dark_sections_use_the_lightened_accent():
    # An accent darkened for white text on it would be invisible as text on
    # black. design.py derives a separate one; the templates must use it.
    css = css_of(html_for())
    palette = generate.design.palette_for(LEAD["place_id"], "trade")
    assert palette["accent_bright"] in css
    assert ".on-night" in css


def test_artwork_is_deterministic_but_differs_between_businesses():
    import visuals
    a = visuals.artwork("place-a", "trade", "Alpha Roofing")
    assert a == visuals.artwork("place-a", "trade", "Alpha Roofing")
    assert a != visuals.artwork("place-b", "trade", "Alpha Roofing")


def test_each_trade_gets_geometry_from_its_own_world():
    import visuals
    trade = visuals.artwork("p", "trade", "X")
    food = visuals.artwork("p", "food", "X")
    salon = visuals.artwork("p", "salon", "X")
    assert "<circle" in food and "<circle" not in trade      # plates vs rooflines
    assert trade != salon != food
