"""Per-business colour, derived from place_id and guaranteed to be readable.

Every site gets its own accent so no two look alike, but "random hue" is a trap:
pure yellow at the same lightness as pure blue is unreadable on white, and the
operator is not going to eyeball 200 of them. So the hue comes from the
place_id and everything else is *computed* — chroma pulled back until the colour
is inside sRGB, lightness pushed down until the contrast ratio clears WCAG AA.

The maths is OKLCH -> sRGB. It is worth the forty lines: HSL lightness is a lie
across hues, and a palette built on it produces sites that look fine in review
and fail on a phone in daylight.
"""
from __future__ import annotations

import hashlib
import math
from typing import Iterable

# Light, very slightly cool. Deliberately not the warm cream that every
# generated site is wearing this year — this reads as clean trade signage.
SURFACE = "#FBFBFC"
SURFACE_SUNK = "#F1F2F5"
INK = "#14171C"
INK_MUTED = "#5A616E"
HAIRLINE = "#DFE2E8"

AA_TEXT = 4.5          # WCAG AA, body text
AA_LARGE = 3.0         # WCAG AA, large text and UI boundaries

# Trades want a colour that reads as competence; salons want it softer. Same
# machinery, different chroma ceiling.
CHROMA_BY_TEMPLATE = {"trade": 0.155, "food": 0.145, "salon": 0.115}


# ---------------------------------------------------------------------------
# Colour space
# ---------------------------------------------------------------------------
def _srgb_gamma(c: float) -> float:
    return 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055


def _srgb_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def oklch_to_rgb(lightness: float, chroma: float, hue_deg: float) -> tuple[float, float, float]:
    """OKLCH -> linear-light sRGB triple, unclamped so callers can gamut-check."""
    h = math.radians(hue_deg)
    a, b = chroma * math.cos(h), chroma * math.sin(h)

    l_ = lightness + 0.3963377774 * a + 0.2158037573 * b
    m_ = lightness - 0.1055613458 * a - 0.0638541728 * b
    s_ = lightness - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3

    return (
        4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
        -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
        -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
    )


def _in_gamut(rgb: Iterable[float]) -> bool:
    return all(-0.0001 <= c <= 1.0001 for c in rgb)


def oklch_to_hex(lightness: float, chroma: float, hue_deg: float) -> str:
    """Nearest in-gamut hex, reducing chroma rather than clipping channels.

    Clipping a channel shifts the hue — a clipped red drifts orange. Pulling
    chroma in keeps the hue the operator sees across the whole catalogue.
    """
    lo, hi = 0.0, chroma
    if not _in_gamut(oklch_to_rgb(lightness, chroma, hue_deg)):
        for _ in range(24):
            mid = (lo + hi) / 2
            if _in_gamut(oklch_to_rgb(lightness, mid, hue_deg)):
                lo = mid
            else:
                hi = mid
        chroma = lo
    rgb = oklch_to_rgb(lightness, chroma, hue_deg)
    return "#" + "".join(
        f"{round(min(1.0, max(0.0, _srgb_gamma(c))) * 255):02x}" for c in rgb
    )


def relative_luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return (0.2126 * _srgb_linear(r) + 0.7152 * _srgb_linear(g)
            + 0.0722 * _srgb_linear(b))


def contrast(a: str, b: str) -> float:
    la, lb = relative_luminance(a), relative_luminance(b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------
def hue_for(place_id: str) -> float:
    """Stable hue in [0, 360). Same business, same colour, forever."""
    digest = hashlib.sha256(place_id.encode("utf-8")).digest()
    return (int.from_bytes(digest[:4], "big") % 3600) / 10.0


def _darken_until(target: float, against: str, chroma: float, hue: float,
                  start: float = 0.62) -> str:
    """Walk lightness down until the contrast ratio clears `target`."""
    lightness = start
    colour = oklch_to_hex(lightness, chroma, hue)
    while contrast(colour, against) < target and lightness > 0.12:
        lightness -= 0.02
        colour = oklch_to_hex(lightness, chroma, hue)
    return colour


def palette_for(place_id: str, template: str = "trade") -> dict[str, str]:
    """The whole colour system for one business. Every pair meets WCAG AA."""
    hue = hue_for(place_id)
    chroma = CHROMA_BY_TEMPLATE.get(template, CHROMA_BY_TEMPLATE["trade"])

    # The fill colour, dark enough that white text on it is legible.
    accent = _darken_until(AA_TEXT, "#FFFFFF", chroma, hue)
    # A tint for section backgrounds. Low chroma so body copy stays comfortable.
    accent_wash = oklch_to_hex(0.965, min(0.028, chroma * 0.2), hue)
    accent_edge = oklch_to_hex(0.88, min(0.06, chroma * 0.45), hue)

    # The same hue used as text, which needs to be darker still — this is why
    # one "brand colour" is never enough. Darken against the darkest surface it
    # can land on, not just the page background, or it fails inside the washed
    # sections where it is used most.
    darkest = min((SURFACE, accent_wash, SURFACE_SUNK), key=relative_luminance)
    accent_ink = _darken_until(AA_TEXT, darkest, chroma, hue)

    return {
        "accent": accent,
        "accent_ink": accent_ink,
        "accent_wash": accent_wash,
        "accent_edge": accent_edge,
        "accent_fg": "#FFFFFF",
        "surface": SURFACE,
        "surface_sunk": SURFACE_SUNK,
        "ink": INK,
        "ink_muted": INK_MUTED,
        "hairline": HAIRLINE,
        "hue": f"{hue:.1f}",
    }


def audit(palette: dict[str, str]) -> dict[str, float]:
    """Contrast ratios for the pairs the templates actually put together."""
    return {
        "ink_on_surface": contrast(palette["ink"], palette["surface"]),
        "muted_on_surface": contrast(palette["ink_muted"], palette["surface"]),
        "accent_fg_on_accent": contrast(palette["accent_fg"], palette["accent"]),
        "accent_ink_on_surface": contrast(palette["accent_ink"], palette["surface"]),
        "ink_on_wash": contrast(palette["ink"], palette["accent_wash"]),
        "accent_ink_on_wash": contrast(palette["accent_ink"], palette["accent_wash"]),
        "muted_on_sunk": contrast(palette["ink_muted"], palette["surface_sunk"]),
    }
