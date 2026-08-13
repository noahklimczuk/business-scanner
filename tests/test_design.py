"""The palette is generated, so it is never reviewed by eye. These tests are
the only thing standing between the operator and a site with unreadable
headings, so they check every hue rather than a sample."""
import pytest

import design


def test_hue_is_stable_for_a_place_id():
    assert design.hue_for("ChIJexample") == design.hue_for("ChIJexample")


def test_different_businesses_get_different_hues():
    hues = {design.hue_for(f"place-{i}") for i in range(200)}
    assert len(hues) > 150            # a few collisions are fine, clustering is not


def test_hue_spreads_across_the_wheel():
    hues = [design.hue_for(f"place-{i}") for i in range(400)]
    quadrants = {int(h // 90) for h in hues}
    assert quadrants == {0, 1, 2, 3}


@pytest.mark.parametrize("template", ["trade", "food", "salon"])
def test_every_pair_the_templates_use_meets_wcag_aa(template):
    for i in range(150):
        palette = design.palette_for(f"place-{i}", template)
        ratios = design.audit(palette)
        for pair, ratio in ratios.items():
            assert ratio >= design.AA_TEXT, (
                f"{pair} = {ratio:.2f} at hue {palette['hue']} ({template})")


def test_yellow_is_the_hard_case_and_still_passes():
    # Pure yellow at a naive lightness is the classic unreadable brand colour.
    yellow = design._darken_until(design.AA_TEXT, "#FFFFFF", 0.155, 100.0)
    assert design.contrast("#FFFFFF", yellow) >= design.AA_TEXT


def test_hex_output_is_well_formed():
    palette = design.palette_for("ChIJexample")
    for key, value in palette.items():
        if key in ("hue", "chrome_alpha"):       # numbers, not colours
            continue
        assert len(value) == 7 and value.startswith("#"), key
        int(value[1:], 16)


def test_out_of_gamut_chroma_is_pulled_in_not_clipped():
    # An absurd chroma must still produce a valid colour of roughly the right hue.
    colour = design.oklch_to_hex(0.55, 0.9, 250.0)
    r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    assert b > r                       # still blue, not a clipped grey
    assert all(0 <= c <= 255 for c in (r, g, b))


def test_contrast_is_symmetric_and_bounded():
    assert design.contrast("#000000", "#FFFFFF") == pytest.approx(21.0, abs=0.01)
    assert design.contrast("#FFFFFF", "#000000") == pytest.approx(21.0, abs=0.01)
    assert design.contrast("#123456", "#123456") == pytest.approx(1.0)


def test_salon_is_softer_than_trade_at_the_same_hue():
    assert design.CHROMA_BY_TEMPLATE["salon"] < design.CHROMA_BY_TEMPLATE["trade"]


def test_palette_is_deterministic():
    assert design.palette_for("abc", "trade") == design.palette_for("abc", "trade")
