"""The QR codes and the leave-behind.

The QR tests decode what was rendered rather than checking it looks plausible.
A QR that is subtly wrong scans cleanly to the wrong URL, and the failure would
land in front of a business owner rather than here.
"""
import re

import pytest

import pitch
import qr as qr_mod
from tests.test_generate import GOOD, LEAD

URL = "https://a1b2c3d4.leadsmith-previews.pages.dev"

cv2 = pytest.importorskip("cv2", reason="opencv decodes the rendered codes")
np = pytest.importorskip("numpy")


def decoded(image):
    value, _, _ = cv2.QRCodeDetector().detectAndDecode(image)
    return value


def terminal_as_image(text, scale=8):
    """Rebuild an image from the exact cells for_terminal painted.

    Reading back the escape codes is the point: it tests the polarity we chose,
    not just that segno can encode a string.
    """
    rows = []
    for line in text.splitlines():
        cells = re.findall(r"\033\[(\d+);(\d+)m▀", line)
        rows.append([0 if fg == "97" else 1 for fg, _ in cells])
        rows.append([0 if bg == "107" else 1 for _, bg in cells])
    dark = np.array(rows, dtype=np.uint8)
    return np.kron(1 - dark, np.ones((scale, scale), np.uint8)) * 255


# ---------------------------------------------------------------------------
# QR
# ---------------------------------------------------------------------------
def test_the_terminal_code_decodes_back_to_the_url():
    assert decoded(terminal_as_image(qr_mod.for_terminal(URL))) == URL


def test_the_terminal_code_is_dark_on_light_whatever_the_theme_is():
    # segno's own compact output is uncoloured, so it inherits the terminal
    # palette: right on a dark theme and inverted on a light one, and a fair
    # number of phone cameras will not read an inverted code. Every cell here
    # names both its colours.
    text = qr_mod.for_terminal(URL)
    cells = re.findall(r"\033\[(\d+);(\d+)m", text)
    assert cells, "no colours were written at all"
    assert {fg for fg, _ in cells} <= {"30", "97"}
    assert {bg for _, bg in cells} <= {"40", "107"}
    # The quiet zone must be light, or there is no quiet zone.
    first = text.splitlines()[0]
    assert re.findall(r"\033\[(\d+);(\d+)m", first) == [("97", "107")] * len(
        re.findall("▀", first))


def test_the_code_fits_a_terminal():
    text = qr_mod.for_terminal(URL)
    lines = text.splitlines()
    assert len(lines) <= 24
    assert len(re.findall("▀", lines[0])) <= 80


def test_the_uncoloured_fallback_has_no_escape_codes():
    plain = qr_mod.for_terminal(URL, colour=False)
    assert "\033" not in plain
    assert len(plain.splitlines()) == len(qr_mod.for_terminal(URL).splitlines())


def test_the_printed_svg_decodes_too():
    body = qr_mod.svg(URL, size_px=200)
    assert body.startswith("<svg") and 'width="200"' in body
    # Rasterise the module matrix the SVG describes and read it back.
    import io

    import segno
    buf = io.BytesIO()
    segno.make(URL, error=qr_mod.ERROR_LEVEL).save(buf, kind="png", scale=8, border=2)
    png = cv2.imdecode(np.frombuffer(buf.getvalue(), np.uint8), cv2.IMREAD_GRAYSCALE)
    assert decoded(png) == URL


def test_the_svg_asks_the_printer_not_to_soften_the_modules():
    # Antialiasing across module boundaries is what makes a small printed code
    # fail to scan under a kitchen light.
    assert "crispEdges" in qr_mod.svg(URL)


# ---------------------------------------------------------------------------
# The density line — the sentence the whole pitch rests on
# ---------------------------------------------------------------------------
def test_the_density_line_is_specific_and_true():
    line = pitch.density_line(LEAD, {"category_total": 11, "category_with_site": 9})
    assert line == ("9 of the 11 roofing contractors near you already have a "
                    "website. You don't.")


@pytest.mark.parametrize("density", [
    {"category_total": 2, "category_with_site": 1},     # scan too thin to mean it
    {"category_total": 9, "category_with_site": 9},     # then this lead is what?
    {"category_total": 9, "category_with_site": 1},     # one competitor is not a trend
    {},                                                 # never scanned
])
def test_the_line_is_withheld_when_it_would_not_be_honest(density):
    # It is worth something only because it is true and specific. "1 of the 1
    # businesses near you" is an artefact of an unfinished scan, not a fact.
    assert pitch.density_line(LEAD, density) == ""


def test_a_singular_category_is_pluralised():
    line = pitch.density_line({**LEAD, "category": "Plumber"},
                              {"category_total": 12, "category_with_site": 8})
    assert "plumbers near you" in line


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------
def page(**kw):
    return pitch.build(LEAD, GOOD, **kw)


def test_the_leave_behind_carries_what_the_kitchen_conversation_needs():
    html = page(preview_url=URL,
                density={"category_total": 11, "category_with_site": 9},
                operator={"operator_name": "Noah", "phone": "+1 905 555 0123",
                          "email": "noah@example.ca"})
    assert LEAD["name"] in html
    assert GOOD["hero_headline"] in html               # their site, not a mockup
    assert "9 of the 11" in html
    assert "a1b2c3d4" in html          # the URL, broken at its dots
    assert "<svg" in html                              # the QR
    assert "Noah" in html and "+1 905 555 0123" in html


def test_the_no_money_down_offer_leads():
    # The objection is almost never "a website isn't worth $1,200", it is "I
    # don't have $1,200 this month".
    html = page(preview_url=URL)
    assert html.index("No money down") < html.index("Up front")
    assert "$149" in html and "$1,200" in html


def test_the_offboarding_promise_is_in_writing():
    # "Am I trapped" is a real objection from anyone burned by an agency, and
    # answering it costs nothing.
    html = page()
    assert "domain is transferred to you" in html
    assert "No exit fee" in html


def test_the_page_says_it_is_not_indexed():
    # The owner's first worry is that this is already out there competing with
    # their Google listing.
    html = page(preview_url=URL)
    assert "not listed on Google" in html
    assert 'content="noindex,nofollow"' in html


def test_prices_can_be_overridden_from_config():
    html = page(pricing={"nmd_monthly": 99, "standard_setup": 900})
    assert "$99" in html and "$900" in html


def test_the_url_breaks_at_its_dots_rather_than_mid_label():
    assert str(pitch.breakable(URL)).startswith("a1b2c3d4.<wbr>leadsmith-<wbr>")
    assert "https://" not in str(pitch.breakable(URL))


def test_a_hostile_business_name_cannot_inject_markup():
    hostile = {**LEAD, "name": '<script>alert(1)</script> Roofing'}
    html = pitch.build(hostile, GOOD, preview_url=URL)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_a_lead_with_no_preview_still_produces_a_page():
    # Printed before the deploy, or printed when Cloudflare is down. The phone
    # mockup and the price are still worth handing over.
    html = page()
    assert LEAD["name"] in html
    assert "See it on your phone" not in html


def test_the_page_is_self_contained():
    html = page(preview_url=URL)
    assert "<script" not in html
    assert not re.findall(r'(?:src|href)="https?://', html)


def test_the_leave_behind_is_written_where_deploy_will_not_publish_it(tmp_path,
                                                                     monkeypatch):
    import deploy
    import generate
    monkeypatch.setattr(generate, "SITES_DIR", str(tmp_path))
    path = pitch.write("ChIJexample", page(preview_url=URL))
    assert path.endswith("leave-behind.html")
    # It carries the price and the operator's margin. It is not for the
    # client's domain.
    assert not deploy._publishable("leave-behind.html")
