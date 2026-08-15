"""The portfolio, and the demo build of a real lead.

The distinction these tests exist to protect: a showcase site is about a
business that does not exist and may carry a testimonial; a demo of a real
lead is about somebody's actual shop and may not carry anything the
fabrication check would refuse. Everything else about the two is the same.
"""
import json
import re

import pytest

import demo
import generate
import stock
from tests.test_generate import GOOD, LEAD


# ---------------------------------------------------------------------------
# Fixtures on disk
# ---------------------------------------------------------------------------
def test_every_template_has_a_demo_business_written_for_it():
    # A template with no fixture is a template the operator cannot show anyone,
    # which makes it a template that does not really exist.
    assert set(demo.available()) == set(generate.TEMPLATES)


def test_demos_are_listed_in_template_order_not_alphabetically():
    assert demo.available() == [t for t in generate.TEMPLATES
                                if t in demo.available()]


@pytest.mark.parametrize("template", generate.TEMPLATES)
def test_each_fixture_is_complete_enough_to_fill_a_page(template):
    fixture = demo.load(template)
    copy = fixture["copy"]
    for key in generate.REQUIRED_FIELDS:
        assert key in copy, f"{template} fixture has no {key}"
    # The optional blocks are what make a demo comprehensive rather than a
    # production page with sample photos on it.
    for key in ("process", "faq", "service_areas", "hero_eyebrow",
                "services_intro", "closing_note"):
        assert copy.get(key), f"{template} fixture has no {key}"
    assert 4 <= len(copy["services"]) <= 6
    assert fixture["lead"]["phone"] and fixture["lead"]["hours"]


@pytest.mark.parametrize("template", generate.TEMPLATES)
def test_no_demo_business_could_be_mistaken_for_a_real_one(template):
    # 555-01xx is the range reserved for fiction. A demo that ships a number
    # somebody actually answers is a demo that rings a stranger every time the
    # portfolio is opened on a phone.
    phone = demo.load(template)["lead"]["phone"]
    assert re.search(r"555-01\d\d", phone), phone


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------
@pytest.fixture
def demos(tmp_path, monkeypatch):
    monkeypatch.setattr(demo, "DEMOS_DIR", str(tmp_path))
    return tmp_path


@pytest.mark.parametrize("template", generate.TEMPLATES)
def test_every_showcase_site_carries_its_quote(demos, template):
    # Every fixture has one and every template renders one, which is half the
    # reason the showcase exists: it is the section a real prospect's page
    # cannot have until somebody actually says something.
    page = open(demo.build(template, photos=False).path, encoding="utf-8").read()
    quote = demo.load(template)["copy"]["quote"]["text"]
    assert quote.split(",")[0][:28] in page


@pytest.mark.parametrize("template", generate.TEMPLATES)
def test_a_showcase_site_builds_with_no_key_and_no_network(demos, template):
    built = demo.build(template, photos=False)
    page = open(built.path, encoding="utf-8").read()
    assert page.startswith("<!doctype html>")
    assert built.photos == 0
    assert built.bytes < generate.PAGE_BUDGET_BYTES
    # Generated artwork filled every slot rather than the page having holes.
    assert page.count('class="art-svg"') >= 4


def test_a_showcase_site_says_it_is_one(demos):
    page = open(demo.build("trade", photos=False).path, encoding="utf-8").read()
    assert 'class="ribbon"' in page
    assert "sample site" in page
    assert "invented" in page


def test_a_showcase_site_is_never_indexable(demos):
    # It carries a made-up address and a 555 number. There is no version of
    # "our sample bakery outranks a real bakery" that ends well.
    built = demo.build("food", photos=False)
    assert '<meta name="robots" content="noindex,nofollow">' in \
        open(built.path, encoding="utf-8").read()
    assert "Disallow: /" in open(built.path.replace("index.html", "robots.txt"),
                                 encoding="utf-8").read()


def test_a_showcase_site_writes_its_copy_out_in_the_content_json_shape(demos):
    demo.build("salon", photos=False)
    saved = json.load(open(demos / "salon" / "content.json", encoding="utf-8"))
    assert saved["version"] == generate.CONTENT_VERSION
    assert saved["showcase"] is True
    # Same shape as a client's file, so a line that works can be lifted out.
    assert set(generate.REQUIRED_FIELDS) <= set(saved["copy"])


def test_a_showcase_may_carry_a_testimonial_because_nobody_is_being_quoted(demos):
    # The fabrication check protects a real business from claims nobody made
    # about them. Fern & Poppy is not a real business.
    page = open(demo.build("salon", photos=False).path, encoding="utf-8").read()
    fixture = demo.load("salon")
    assert fixture["copy"]["quote"]["text"][:30] in page


def test_the_same_copy_on_a_real_lead_is_refused():
    # The other half of the rule above, and the one that matters: nothing built
    # from a Google listing takes the showcase path.
    quoted = {**GOOD, "quote": {"text": "The best roofers in town.", "by": "A"}}
    with pytest.raises(generate.ContentError):
        generate.render(LEAD, quoted)


def test_a_demo_of_a_real_lead_still_reviews_its_copy():
    forged = {**GOOD, "hero_sub": "Family owned since 1987."}
    with pytest.raises(generate.ContentError, match="1987"):
        generate.render(LEAD, forged, demo=True)


def test_a_demo_of_a_real_lead_is_marked_as_a_demo():
    page = generate.render(LEAD, GOOD, demo=True).html
    assert 'class="ribbon"' in page
    assert "sample site" in page
    assert generate.render(LEAD, GOOD).html.count('class="ribbon"') == 0


def test_a_demo_gets_a_bigger_budget_because_it_carries_photographs():
    assert generate.render(LEAD, GOOD, demo=True).budget > \
        generate.render(LEAD, GOOD).budget


# ---------------------------------------------------------------------------
# Photographs
# ---------------------------------------------------------------------------
def test_photos_land_in_the_site_and_are_credited(demos, monkeypatch):
    def fake_fetch(template, count, dest, settings=None, seed=""):
        return stock.Result(photos=[stock.Photo(
            src="00-a.jpg", alt="A cedar shingle roof on a bungalow",
            width=1880, height=1253, credit="A. Person on Pexels",
            url="https://example.test/1")])

    monkeypatch.setattr(demo.stock, "fetch", fake_fetch)
    built = demo.build("trade")
    page = open(built.path, encoding="utf-8").read()
    assert built.photos == 1
    assert 'src="00-a.jpg"' in page
    assert 'alt="A cedar shingle roof on a bungalow"' in page
    # Whoever took it gets named, and the record is kept beside the page.
    assert "A. Person on Pexels" in page
    assert json.load(open(demos / "trade" / "credits.json", encoding="utf-8"))


def test_a_photo_service_that_is_down_does_not_stop_the_demo(demos, monkeypatch):
    monkeypatch.setattr(demo.stock, "fetch",
                        lambda *a, **k: stock.Result(note="Pexels is unreachable."))
    built = demo.build("clinic")
    assert built.photos == 0
    assert "unreachable" in built.note
    assert 'class="art-svg"' in open(built.path, encoding="utf-8").read()


# ---------------------------------------------------------------------------
# The gallery
# ---------------------------------------------------------------------------
def test_the_gallery_links_to_every_demo_it_lists(demos):
    built = [demo.build(t, photos=False) for t in ("trade", "food", "salon")]
    page = open(demo.gallery(built), encoding="utf-8").read()
    for site in built:
        assert f'href="{site.href}"' in page
        assert site.name.replace("&", "&amp;") in page
    assert 'name="robots" content="noindex,nofollow"' in page


def test_the_gallery_is_self_contained(demos):
    built = [demo.build("pet", photos=False)]
    page = open(demo.gallery(built), encoding="utf-8").read()
    assert "<script" not in page
    assert '<link rel="stylesheet"' not in page
    for url in re.findall(r'(?:src|href)="(https?://[^"]+)"', page):
        raise AssertionError(f"gallery fetches {url}")


def test_an_unknown_template_says_which_ones_exist():
    with pytest.raises(demo.DemoError, match="trade"):
        demo.load("brochure")
