"""WCAG 2.0 Level AA, which Ontario's AODA makes a legal obligation for the
private-sector businesses these pages are built for.

The pages are generated, so nobody reviews one before it goes to a client —
the same argument that makes the palette computed rather than chosen. These
tests are the review. Each one names the success criterion it covers so that a
failure says which obligation just broke, not merely which assertion did.

Criteria that do not apply are listed at the bottom with the reason, so the
gaps are deliberate rather than forgotten.
"""
import re
from html.parser import HTMLParser

import pytest

import design
import generate
from tests.test_generate import GOOD, LEAD
from tests.test_render import html_for, markup


# ---------------------------------------------------------------------------
# A small DOM, so these assert about elements rather than about substrings
# ---------------------------------------------------------------------------
class Element:
    def __init__(self, tag, attrs):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children: list["Element"] = []
        self.parent: "Element | None" = None
        self.text = ""

    def find_all(self, tag=None, **attrs):
        out = []
        for child in self.children:
            if (tag is None or child.tag == tag) and all(
                    child.attrs.get(k) == v for k, v in attrs.items()):
                out.append(child)
            out.extend(child.find_all(tag, **attrs))
        return out

    def all_text(self):
        return (self.text + "".join(c.all_text() for c in self.children))

    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent


VOID = {"meta", "link", "br", "img", "input", "hr", "source", "area", "base"}


class Soup(HTMLParser):
    """Enough of a tree for these assertions. Not a browser — but it does mean
    a test can ask "is this <h2> inside <main>" instead of hoping a regex
    happened to match the right one."""

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Element("#document", [])
        self.stack = [self.root]
        self.skip = 0
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
            return
        node = Element(tag, attrs)
        node.parent = self.stack[-1]
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if not self.skip:
            self.stack[-1].text += data


@pytest.fixture(params=generate.TEMPLATES)
def dom(request):
    return Soup(html_for(request.param)).root


@pytest.fixture(params=generate.TEMPLATES)
def css(request):
    page = html_for(request.param)
    return re.search(r"<style>(.*?)</style>", page, re.S).group(1)


# ---------------------------------------------------------------------------
# 1.1.1 Non-text Content
# ---------------------------------------------------------------------------
def test_generated_artwork_is_marked_as_decoration(dom):
    svgs = dom.find_all("svg")
    assert svgs, "the page should have artwork to check"
    for svg in svgs:
        assert svg.attrs.get("aria-hidden") == "true"
        assert svg.attrs.get("role") == "presentation"
        # IE and old Edge otherwise put every SVG in the tab order.
        assert svg.attrs.get("focusable") == "false"


def test_no_image_ships_without_an_alt_attribute():
    content = {**GOOD, "photos": [
        {"src": "hero.jpg", "alt": "New cedar shingles on a bungalow"},
        {"src": "pattern.png", "decorative": True},
    ]}
    dom = Soup(generate.render(LEAD, content).html).root
    images = dom.find_all("img")
    assert len(images) == 2
    assert "alt" in images[0].attrs and images[0].attrs["alt"]
    # Explicitly declared decoration is the only route to an empty alt.
    assert images[1].attrs.get("alt") == ""


def test_a_photo_with_no_alt_text_fails_the_build():
    content = {**GOOD, "photos": [{"src": "hero.jpg"}]}
    with pytest.raises(generate.ContentError) as excinfo:
        generate.render(LEAD, content)
    assert "alt text" in str(excinfo.value)
    assert "AODA" in str(excinfo.value)


@pytest.mark.parametrize("alt", ["image", "Photo", "picture.", "hero.jpg"])
def test_alt_text_that_says_nothing_fails_the_build(alt):
    content = {**GOOD, "photos": [{"src": "hero.jpg", "alt": alt}]}
    with pytest.raises(generate.ContentError):
        generate.render(LEAD, content)


def test_decorative_is_a_deliberate_claim_not_a_default():
    # The escape hatch exists, and it has to be asked for in writing.
    assert generate.accessibility_issues(
        {"photos": [{"src": "x.jpg", "decorative": True}]}) == []
    assert generate.accessibility_issues({"photos": [{"src": "x.jpg"}]})


# ---------------------------------------------------------------------------
# 1.3.1 Info and Relationships
# ---------------------------------------------------------------------------
def test_the_page_has_exactly_one_h1_and_no_skipped_levels(dom):
    levels = [int(e.tag[1]) for e in dom.find_all()
              if re.fullmatch(r"h[1-6]", e.tag)]
    assert levels.count(1) == 1
    for previous, current in zip(levels, levels[1:]):
        assert current <= previous + 1, f"jumped from h{previous} to h{current}"


def test_every_section_of_content_has_a_heading(dom):
    for section in dom.find_all("section"):
        # The hero is the exception and needs no name of its own: it holds the
        # h1, which names the whole page.
        if section.find_all("h1"):
            continue
        named = ("aria-labelledby" in section.attrs
                 or "aria-label" in section.attrs)
        assert named, f"unnamed section: {section.all_text()[:60]!r}"
        target = section.attrs.get("aria-labelledby")
        if target:
            assert dom.find_all(id=target), f"aria-labelledby={target} points nowhere"


def test_runs_of_services_and_reasons_are_lists(dom):
    lists = dom.find_all("ul")
    assert len(lists) >= 2                      # services, and why-call-us
    for group in lists:
        assert group.find_all("li")
        # Safari drops list semantics from a list with no bullets.
        assert group.attrs.get("role") == "list"


def test_each_stat_is_a_labelled_pair_not_two_loose_paragraphs():
    # The strip only appears when the week is regular enough to summarise in a
    # line, which the default fixture's Saturday hours rule out.
    regular = {**LEAD, "hours": [f"{day}: 8:00 AM – 5:00 PM" for day in
                                 ("Monday", "Tuesday", "Wednesday", "Thursday",
                                  "Friday")] + ["Saturday: Closed", "Sunday: Closed"]}
    dom = Soup(generate.render(regular, GOOD).html).root
    stats = dom.find_all("dl")
    assert len(stats) == 1, "the stats strip should be a description list"
    terms, values = stats[0].find_all("dt"), stats[0].find_all("dd")
    assert terms and len(terms) == len(values)
    assert [t.all_text().strip() for t in terms] == ["Open", "Serving"]


def test_the_hours_table_is_a_real_table(dom):
    table = dom.find_all("table")[0]
    assert table.find_all("caption")
    for row in table.find_all("tr"):
        heads = row.find_all("th")
        assert heads and heads[0].attrs.get("scope") == "row"


def test_the_address_is_marked_up_as_one(dom):
    assert dom.find_all("address")


# ---------------------------------------------------------------------------
# 1.4.1 Use of Color
# ---------------------------------------------------------------------------
def test_the_footer_phone_link_is_underlined(css):
    # It sits in a row of plain spans; without the underline its colour is the
    # only thing separating it from them.
    assert "text-decoration: none" not in _rule_for(css, "a")
    assert ".footer-in a { text-decoration: none" not in css


def test_the_big_phone_number_is_underlined(css):
    rule = _rule_for(css, ".phone-xl")
    assert "text-decoration: none" not in rule
    assert "text-decoration-thickness" in rule


def test_open_and_closed_are_words_not_just_a_coloured_dot(page_html):
    assert "Open now" in page_html and "Closed" in page_html
    assert 'class="pill-dot" aria-hidden="true"' in page_html


def test_today_is_announced_and_not_only_shown(page_html):
    assert "(today)" in page_html
    assert "sr-only" in page_html


# ---------------------------------------------------------------------------
# 1.4.3 Contrast (Minimum) — the palette carries this; see test_design.py for
# the sweep across every hue. These check the wiring.
# ---------------------------------------------------------------------------
def test_the_stylesheet_renders_the_same_alpha_the_contrast_check_assumes(css):
    # If these drift, the audit measures a bar that is not the bar that ships.
    assert f"{design.CHROME_ALPHA * 100:.0f}%, transparent)" in css


def test_a_dark_section_repoints_the_tokens_rather_than_restating_colours(css):
    night = _rule_for(css, ".on-night")
    for token in ("--ink:", "--muted:", "--surface:", "--accent-ink:", "--focus:"):
        assert token in night, f"{token} not re-pointed for dark sections"


def test_secondary_text_on_the_frosted_bars_survives_a_night_backdrop():
    palette = design.palette_for(LEAD["place_id"])
    chrome = design.chrome_over(palette["night"])
    assert design.contrast(palette["ink_muted_strong"], chrome) >= design.AA_TEXT
    # And the reason the plain muted grey is not used there.
    assert design.contrast(palette["ink_muted"], chrome) < design.AA_TEXT


# ---------------------------------------------------------------------------
# 1.4.4 Resize Text
# ---------------------------------------------------------------------------
def test_nothing_pins_the_viewport_against_zooming(page_html):
    viewport = re.search(r'name="viewport" content="([^"]+)"', page_html).group(1)
    assert "user-scalable=no" not in viewport
    assert "maximum-scale" not in viewport


def test_the_call_bar_can_wrap_when_the_text_is_doubled(css):
    # Two buttons and a full phone number do not fit a 390px bar at 200%.
    bar = _rule_for(css, ".callbar")
    assert "flex-wrap: wrap" in bar
    assert "white-space: nowrap" not in _rule_for(css, ".btn")


# ---------------------------------------------------------------------------
# 2.4.1 Bypass Blocks
# ---------------------------------------------------------------------------
def test_the_skip_link_is_first_and_points_at_main(dom):
    body = dom.find_all("body")[0]
    first_link = body.find_all("a")[0]
    assert "skip" in first_link.attrs.get("class", "")
    assert first_link.attrs["href"] == "#main"
    main = dom.find_all("main")[0]
    assert main.attrs.get("id") == "main"
    # Without this WebKit scrolls but leaves focus behind, and the next Tab
    # goes straight back into the nav the visitor was skipping.
    assert main.attrs.get("tabindex") == "-1"


def test_the_skip_link_becomes_visible_when_focused(css):
    assert ".skip:focus" in css


# ---------------------------------------------------------------------------
# 2.4.7 Focus Visible
# ---------------------------------------------------------------------------
def test_focus_is_never_suppressed(css):
    # `main` is the one exemption, and only because it is the skip link's
    # target: it is focused programmatically, never by tabbing to it.
    without_main = re.sub(r"main:focus[^{]*\{[^}]*\}", "", css)
    assert "outline: none" not in without_main
    assert "outline: 0" not in without_main


def test_focus_rings_exist_for_browsers_without_focus_visible(css):
    assert ":focus-visible {" in css
    assert "@supports not selector(:focus-visible)" in css


def test_an_in_page_jump_does_not_land_under_the_sticky_bars(css):
    root = _rule_for(css, "html")
    assert "scroll-padding-top" in root and "scroll-padding-bottom" in root


# ---------------------------------------------------------------------------
# 2.4.2 Page Titled / 3.1.1 Language of Page
# ---------------------------------------------------------------------------
def test_the_page_is_titled_and_declares_its_language(dom, page_html):
    assert '<html lang="en-CA">' in page_html
    title = dom.find_all("title")[0].all_text().strip()
    assert title and title != LEAD["name"]


# ---------------------------------------------------------------------------
# 2.3.1 Three Flashes / 2.2.2 Pause, Stop, Hide
# ---------------------------------------------------------------------------
def test_all_motion_is_scroll_driven_and_opt_out(css):
    # Nothing animates on a timer except the first screen, and every animation
    # lives inside a prefers-reduced-motion guard.
    assert "@media (prefers-reduced-motion: no-preference)" in css
    animation_blocks = css.count("animation-timeline: view()")
    assert animation_blocks >= 3
    guard = css.index("@media (prefers-reduced-motion: no-preference)")
    assert css.index("animation-timeline: view()") > guard


# ---------------------------------------------------------------------------
# 4.1.1 Parsing
# ---------------------------------------------------------------------------
def test_every_id_on_the_page_is_unique(dom):
    ids = [e.attrs["id"] for e in dom.find_all() if "id" in e.attrs]
    assert len(ids) == len(set(ids)), [i for i in ids if ids.count(i) > 1]


def test_aria_hidden_never_wraps_something_focusable(dom):
    for element in dom.find_all():
        if element.attrs.get("aria-hidden") != "true":
            continue
        assert not element.find_all("a")
        assert not element.find_all("button")


def test_landmarks_are_present_and_named_where_they_repeat(dom):
    assert dom.find_all("header") and dom.find_all("footer")
    navs = dom.find_all("nav")
    assert len(navs) >= 2                          # sections, and the call bar
    for nav in navs:                               # more than one, so each needs a name
        assert nav.attrs.get("aria-label")


# ---------------------------------------------------------------------------
# Not applicable, recorded so the gaps are deliberate
# ---------------------------------------------------------------------------
def test_there_is_nothing_on_the_page_that_needs_captions_or_a_transcript(page_html):
    # 1.2.1-1.2.5 are about audio and video. AODA exempts live captions and
    # audio description outright; the rest simply have no subject here.
    for element in ("<video", "<audio", "<iframe", "<object", "<embed"):
        assert element not in markup(page_html)


def test_there_are_no_forms_to_get_wrong(page_html):
    # 3.3.1-3.3.4 and 1.3.5 are about input. The page's only call to action is
    # a tel: link, which is also the CASL position: no contact form, no list.
    for element in ("<form", "<input", "<textarea", "<select"):
        assert element not in markup(page_html)


# ---------------------------------------------------------------------------
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")


def _rule_for(css, selector):
    """Every declaration that applies to exactly this selector.

    Matching on the whole comma-separated selector list rather than on a
    substring, so `.btn` does not accidentally pick up `.callbar .btn` — and so
    a declaration moved into a grouped rule or a media query is still found.
    """
    found = []
    for match in _RULE.finditer(css):
        selectors = [s.strip() for s in match.group(1).split(",")]
        if selector.strip() in selectors:
            found.append(match.group(2))
    assert found, f"no rule for {selector!r}"
    return " ".join(found)


@pytest.fixture(params=generate.TEMPLATES)
def page_html(request):
    return html_for(request.param)
