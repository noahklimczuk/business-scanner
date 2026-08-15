"""Per-business colour and per-category design language, both computed.

Every site gets its own accent so no two look alike, but "random hue" is a trap:
pure yellow at the same lightness as pure blue is unreadable on white, and the
operator is not going to eyeball 200 of them. So the hue comes from the
place_id and everything else is *computed* — chroma pulled back until the colour
is inside sRGB, lightness pushed down until the contrast ratio clears WCAG AA.

The maths is OKLCH -> sRGB. It is worth the forty lines: HSL lightness is a lie
across hues, and a palette built on it produces sites that look fine in review
and fail on a phone in daylight.

AA is not a nice-to-have here. Ontario's AODA makes WCAG 2.0 Level AA a legal
obligation for these businesses' public websites, and 1.4.3 Contrast (Minimum)
is the criterion a generated palette is most likely to break. So every pair the
templates actually put together is enumerated in `audit()` and asserted across
every hue in the tests — including the pairs that only exist inside a dark
section, and the one behind a translucent bar.

Two things are new since the rebuild.

**Hue arcs.** A full-wheel hash gives a dental clinic a mustard accent as
happily as a teal one — legible, and wrong. Each template now declares an arc
and the place_id picks a position inside it. Same determinism, with a floor
under how wrong it can look. `hue_for(place_id)` with no template still spans
the whole wheel, so nothing that relied on the old behaviour changed.

**A design language per template.** Radii, type personality, section rhythm and
motion character are data here rather than conditionals in the stylesheet, so
"what makes the clinic template feel different from the garage template" is one
table you can read in ten seconds instead of forty scattered `{% if %}`s.
"""
from __future__ import annotations

import hashlib
import math
from typing import Iterable

# A near-white page and a near-black section, alternating. The neutrals are
# deliberately almost colourless so the one derived accent is the only colour
# on the page.
SURFACE = "#FBFBFD"
SURFACE_SUNK = "#F5F5F7"
INK = "#1D1D1F"
INK_MUTED = "#6E6E73"
HAIRLINE = "#D2D2D7"

# Secondary text that sits on the frosted bars rather than on the page. Those
# bars are translucent, so what is behind them is whatever happens to be
# scrolling past — including a true-black section. `INK_MUTED` is 4.91 on the
# page and 4.11 over that worst case, which is a fail. This grey is the same
# role, dark enough to survive it.
INK_MUTED_STRONG = "#55555A"

# Dark sections need their own pairs. An accent darkened until white text sits
# on it comfortably is far too dark to *be* text on black — this is the pair
# that silently fails when a light-only palette grows a dark section.
NIGHT = "#000000"
NIGHT_SUNK = "#141416"
NIGHT_INK = "#F5F5F7"
NIGHT_MUTED = "#A1A1A6"
# Solid, not `color-mix(… transparent)`. Hairlines are the one place the old
# CSS needed a Safari fallback for every single declaration; a flat hex needs
# none, and a custom property cannot carry a fallback the way a property can.
NIGHT_HAIRLINE = "#36363A"
NIGHT_EDGE = "#4A4A4E"

AA_TEXT = 4.5          # WCAG AA 1.4.3, body text
AA_LARGE = 3.0         # WCAG AA 1.4.3, large text (>=24px, or >=18.66px bold)
# Aim a little above the line rather than at it. A colour that computes to
# exactly 4.50 has no room for a browser that rounds a channel differently, and
# the difference between 4.50 and 4.65 is invisible.
AA_MARGIN = 0.15

# How opaque the sticky nav and the mobile call bar are behind their blur.
# Apple sits nearer 0.8, but Apple's pages are light the whole way down and
# ours alternate to near-black — at 0.8 the bar goes dark enough under a night
# section to fail its own label. Defined here rather than in the stylesheet so
# `audit()` measures the same number the CSS renders.
CHROME_ALPHA = 0.92


# ---------------------------------------------------------------------------
# The design language of each template
# ---------------------------------------------------------------------------
# Font stacks. No webfonts anywhere: a font file is a second round trip on rural
# LTE and the entire promise of these pages is that they arrive before the
# visitor gives up. Twelve templates that do not look related, out of families
# every device already has.
FONTS = {
    "sans": ('-apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", '
             'system-ui, Roboto, "Helvetica Neue", Arial, sans-serif'),
    "serif": ('ui-serif, "Iowan Old Style", "Palatino Linotype", Palatino, '
              'Georgia, "Times New Roman", serif'),
    "rounded": ('ui-rounded, "SF Pro Rounded", "Hiragino Maru Gothic ProN", '
                '"Segoe UI Variable Display", system-ui, -apple-system, '
                '"Segoe UI", Roboto, sans-serif'),
    "mono": ('ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, '
             '"Liberation Mono", monospace'),
}


class Style(dict):
    """The design language of one template, as plain data the stylesheet reads.

    A dict rather than a dataclass because it is handed straight to Jinja and
    every consumer wants attribute-ish access to strings; the fields are
    documented in `STYLES` below and nowhere else, on purpose.
    """


def _style(
    *, hue_arc: tuple[float, float], chroma: float,
    display: str = "sans", body: str = "sans", detail: str = "sans",
    display_weight: int = 700, display_tracking: str = "-.035em",
    display_case: str = "none", display_scale: float = 1.0,
    eyebrow_case: str = "none", eyebrow_tracking: str = "-.005em",
    eyebrow_weight: int = 600,
    r_card: str = "20px", r_panel: str = "26px", r_pill: str = "980px",
    r_button: str = "980px",
    rhythm: float = 1.0, motion: str = "rise", grain: float = 0.0,
) -> Style:
    return Style(
        hue_arc=hue_arc, chroma=chroma,
        font_display=FONTS[display], font_body=FONTS[body],
        font_detail=FONTS[detail],
        display_weight=display_weight, display_tracking=display_tracking,
        display_case=display_case, display_scale=display_scale,
        eyebrow_case=eyebrow_case, eyebrow_tracking=eyebrow_tracking,
        eyebrow_weight=eyebrow_weight,
        r_card=r_card, r_panel=r_panel, r_pill=r_pill, r_button=r_button,
        rhythm=rhythm, motion=motion, grain=grain,
    )


# Hue arcs are OKLCH degrees, not HSL: ~30 red, ~60 orange, ~95 yellow,
# ~145 green, ~195 teal, ~250 blue, ~300 violet, ~350 rose. An arc may run past
# 360; `hue_for` takes it modulo.
STYLES: dict[str, Style] = {
    # The van door. Heavy, square, unfussy; the phone number is the design.
    "trade": _style(
        hue_arc=(45, 80), chroma=0.155, display_weight=800,
        display_tracking="-.042em", display_case="none", display_scale=1.05,
        eyebrow_case="uppercase", eyebrow_tracking=".08em", eyebrow_weight=700,
        r_card="14px", r_panel="18px", r_button="10px",
        rhythm=0.95, motion="drop"),
    # The menu. Warm paper, a serif that knows what a wine list looks like.
    "food": _style(
        hue_arc=(15, 50), chroma=0.145, display="serif", detail="serif",
        display_weight=600, display_tracking="-.02em", display_scale=1.02,
        eyebrow_case="uppercase", eyebrow_tracking=".16em", eyebrow_weight=600,
        r_card="8px", r_panel="12px", r_button="4px",
        rhythm=1.05, motion="fade", grain=0.10),
    # The studio. Air, light weights, very large radii, nothing shouts.
    "salon": _style(
        hue_arc=(305, 350), chroma=0.115, display_weight=350,
        display_tracking="-.02em", display_scale=0.98,
        eyebrow_case="uppercase", eyebrow_tracking=".22em", eyebrow_weight=500,
        r_card="28px", r_panel="40px",
        rhythm=1.15, motion="soften"),
    # The garage. Dark, precise, monospace data lines, chrome edges.
    "auto": _style(
        hue_arc=(355, 395), chroma=0.160, detail="mono", display_weight=800,
        display_tracking="-.04em", display_case="uppercase", display_scale=0.94,
        eyebrow_case="uppercase", eyebrow_tracking=".18em", eyebrow_weight=600,
        r_card="4px", r_panel="6px", r_button="4px",
        rhythm=0.92, motion="slide"),
    # The floor. Kinetic, oversized, rounded; the timetable is the hero.
    "wellness": _style(
        hue_arc=(135, 180), chroma=0.160, display="rounded", body="rounded",
        display_weight=800, display_tracking="-.038em", display_scale=1.08,
        eyebrow_case="uppercase", eyebrow_tracking=".12em", eyebrow_weight=700,
        r_card="24px", r_panel="32px",
        rhythm=1.0, motion="surge"),
    # The waiting room. Cool, quiet, enormous whitespace, nothing sudden.
    "clinic": _style(
        hue_arc=(195, 250), chroma=0.105, display_weight=600,
        display_tracking="-.03em", display_scale=0.96,
        eyebrow_tracking=".02em", eyebrow_weight=600,
        r_card="20px", r_panel="28px",
        rhythm=1.2, motion="soften"),
    # The practice. Serif, hairlines, a numbered index of what they do.
    "professional": _style(
        hue_arc=(250, 290), chroma=0.090, display="serif", detail="serif",
        display_weight=600, display_tracking="-.022em", display_scale=0.95,
        eyebrow_case="uppercase", eyebrow_tracking=".2em", eyebrow_weight=600,
        r_card="4px", r_panel="6px", r_button="6px",
        rhythm=1.12, motion="rule"),
    # The shopfront. Colour blocks, sticker badges, a rail that snaps.
    "retail": _style(
        hue_arc=(320, 360), chroma=0.170, display="rounded", display_weight=800,
        display_tracking="-.04em", display_scale=1.04,
        eyebrow_case="uppercase", eyebrow_tracking=".1em", eyebrow_weight=700,
        r_card="18px", r_panel="26px",
        rhythm=0.98, motion="pop"),
    # The property. Organic masks, seasons, big landscape imagery.
    "home": _style(
        hue_arc=(110, 155), chroma=0.130, display_weight=700,
        display_tracking="-.034em",
        eyebrow_case="uppercase", eyebrow_tracking=".1em",
        r_card="22px", r_panel="34px",
        rhythm=1.05, motion="drift"),
    # The yard. Very large radii, a degree of tilt, entirely unserious.
    "pet": _style(
        hue_arc=(65, 105), chroma=0.155, display="rounded", body="rounded",
        display_weight=800, display_tracking="-.036em", display_scale=1.05,
        eyebrow_case="uppercase", eyebrow_tracking=".1em", eyebrow_weight=700,
        r_card="30px", r_panel="44px",
        rhythm=1.0, motion="spring"),
    # The gallery. Cinematic, thin, full-bleed; the frame is the furniture.
    "creative": _style(
        hue_arc=(275, 320), chroma=0.140, display="serif", display_weight=400,
        display_tracking="-.02em", display_scale=1.06,
        eyebrow_case="uppercase", eyebrow_tracking=".28em", eyebrow_weight=500,
        r_card="2px", r_panel="2px", r_button="2px",
        rhythm=1.18, motion="wipe", grain=0.14),
    # The classroom. Blocky, bright, chunky rounded type, nothing intimidating.
    "education": _style(
        hue_arc=(175, 215), chroma=0.150, display="rounded", body="rounded",
        display_weight=800, display_tracking="-.036em", display_scale=1.0,
        eyebrow_case="uppercase", eyebrow_tracking=".1em", eyebrow_weight=700,
        r_card="22px", r_panel="30px",
        rhythm=1.0, motion="pop"),
}

DEFAULT_TEMPLATE = "trade"

# Kept for compatibility: the three original templates were tuned by their
# chroma ceiling alone, and enough of the tool reads this to be worth not
# breaking. `STYLES` is the authority now.
CHROMA_BY_TEMPLATE = {name: style["chroma"] for name, style in STYLES.items()}


def style_for(template: str = DEFAULT_TEMPLATE) -> Style:
    return STYLES.get(template, STYLES[DEFAULT_TEMPLATE])


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


def blend(colour: str, alpha: float, behind: str) -> str:
    """What a translucent layer actually resolves to over a known backdrop.

    Contrast is measured against rendered pixels, so a semi-transparent bar has
    to be composited before it can be checked. sRGB compositing, because that
    is what the browser does for `color-mix(in srgb, … transparent)`.
    """
    top = [int(colour.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    bottom = [int(behind.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    return "#" + "".join(f"{round(alpha * t + (1 - alpha) * b):02x}"
                         for t, b in zip(top, bottom))


def chrome_over(backdrop: str, surface: str = SURFACE) -> str:
    """The rendered colour of a frosted bar with `backdrop` scrolling under it."""
    return blend(surface, CHROME_ALPHA, backdrop)


# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------
def hue_for(place_id: str, template: str | None = None) -> float:
    """Stable hue in [0, 360). Same business, same colour, forever.

    With a template, the hue lands inside that template's arc — a clinic is
    never mustard — while still differing business to business. Without one it
    spans the whole wheel, which is what the original did and what anything
    calling this without a category still expects.
    """
    digest = hashlib.sha256(place_id.encode("utf-8")).digest()
    position = int.from_bytes(digest[:4], "big") % 3600 / 3600.0
    if template is None:
        return position * 360.0
    low, high = style_for(template)["hue_arc"]
    return (low + position * (high - low)) % 360.0


def _darken_until(target: float, against: str, chroma: float, hue: float,
                  start: float = 0.62) -> str:
    """Walk lightness down until the contrast ratio clears `target`."""
    lightness = start
    colour = oklch_to_hex(lightness, chroma, hue)
    while contrast(colour, against) < target and lightness > 0.12:
        lightness -= 0.02
        colour = oklch_to_hex(lightness, chroma, hue)
    return colour


def _lighten_until(target: float, against: str, chroma: float, hue: float,
                   start: float = 0.62) -> str:
    """The mirror of `_darken_until`, for the accent used on dark sections."""
    lightness = start
    colour = oklch_to_hex(lightness, chroma, hue)
    while contrast(colour, against) < target and lightness < 0.99:
        lightness += 0.02
        colour = oklch_to_hex(lightness, chroma, hue)
    return colour


def palette_for(place_id: str, template: str = DEFAULT_TEMPLATE) -> dict[str, str]:
    """The whole colour system for one business. Every pair meets WCAG AA."""
    style = style_for(template)
    hue = hue_for(place_id, template if template in STYLES else None)
    chroma = style["chroma"]

    # The page itself carries a breath of the hue. Two per cent chroma is below
    # the threshold at which anyone would call it a colour, and it is the
    # difference between "a template" and "their site" when the two are open
    # side by side. Ink on it stays above 15:1, but it is audited anyway.
    surface = oklch_to_hex(0.986, min(0.006, chroma * 0.04), hue)
    surface_sunk = oklch_to_hex(0.962, min(0.012, chroma * 0.08), hue)
    hairline = oklch_to_hex(0.855, min(0.016, chroma * 0.11), hue)

    # Dark sections are tinted the same way. A true black next to a hue-tinted
    # white reads as two different designs stacked; a near-black carrying the
    # same hue reads as one.
    night = oklch_to_hex(0.155, min(0.022, chroma * 0.14), hue)
    night_sunk = oklch_to_hex(0.205, min(0.026, chroma * 0.16), hue)
    night_hairline = oklch_to_hex(0.32, min(0.03, chroma * 0.2), hue)
    night_edge = oklch_to_hex(0.42, min(0.036, chroma * 0.24), hue)

    # The fill colour, dark enough that white text on it is legible.
    accent = _darken_until(AA_TEXT + AA_MARGIN, "#FFFFFF", chroma, hue)
    # A tint for section backgrounds. Low chroma so body copy stays comfortable.
    accent_wash = oklch_to_hex(0.965, min(0.028, chroma * 0.2), hue)
    accent_edge = oklch_to_hex(0.88, min(0.06, chroma * 0.45), hue)

    # The same hue used as text, which needs to be darker still — this is why
    # one "brand colour" is never enough. Darken against the darkest surface it
    # can land on, not just the page background, or it fails inside the washed
    # sections where it is used most.
    #
    # The frosted bars are in that list for a reason that is easy to miss: they
    # are translucent, so with a night section scrolling underneath they render
    # around #e7e7e9 — darker than any opaque surface on the page. Solving it
    # here rather than component by component means accent-coloured text is
    # safe wherever it is put, instead of safe until someone moves it. It costs
    # about one step of lightness on the worst hue, which is not visible.
    darkest = min((surface, accent_wash, surface_sunk, chrome_over(night, surface)),
                  key=relative_luminance)
    accent_ink = _darken_until(AA_TEXT + AA_MARGIN, darkest, chroma, hue)

    # On a dark section the accent has to go the other way. Reusing `accent_ink`
    # here would put a near-black colour on a near-black background. It is
    # lightened against the *lighter* of the two night surfaces, because that is
    # the harder one.
    lightest_night = max((night, night_sunk), key=relative_luminance)
    accent_bright = _lighten_until(AA_TEXT + AA_MARGIN, lightest_night, chroma, hue)
    # Night text, lightened until it clears the lighter night surface too.
    night_ink = NIGHT_INK
    night_muted = _lighten_until(AA_TEXT + AA_MARGIN, lightest_night,
                                 min(0.012, chroma * 0.08), hue, start=0.66)

    # Aurora layers: high lightness, low chroma, blended at low opacity.
    accent_glow = oklch_to_hex(0.82, min(0.13, chroma * 0.9), hue)
    accent_glow_alt = oklch_to_hex(0.78, min(0.12, chroma * 0.8), (hue + 42) % 360)
    # A second hue, 150 degrees round, for the templates whose artwork wants two
    # colours rather than one. Still derived, still audited where it is text.
    accent_far = oklch_to_hex(0.72, min(0.14, chroma * 0.85), (hue + 152) % 360)

    return {
        "accent": accent,
        "accent_ink": accent_ink,
        "accent_bright": accent_bright,
        "accent_wash": accent_wash,
        "accent_edge": accent_edge,
        "accent_glow": accent_glow,
        "accent_glow_alt": accent_glow_alt,
        "accent_far": accent_far,
        "accent_fg": "#FFFFFF",
        "surface": surface,
        "surface_sunk": surface_sunk,
        "ink": INK,
        "ink_muted": INK_MUTED,
        "ink_muted_strong": INK_MUTED_STRONG,
        "hairline": hairline,
        "night": night,
        "night_sunk": night_sunk,
        "night_ink": night_ink,
        "night_muted": night_muted,
        "night_hairline": night_hairline,
        "night_edge": night_edge,
        "chrome_alpha": f"{CHROME_ALPHA * 100:.0f}",
        "hue": f"{hue:.1f}",
    }


def audit(palette: dict[str, str]) -> dict[str, float]:
    """Contrast ratios for every foreground/background pair the templates put
    together, including the ones that only exist in a dark section.

    This is the list the AA tests iterate. A pair that renders on a page but is
    missing here is untested, so adding a colour to the stylesheet means adding
    its pair here — that is the whole contract.
    """
    # A frosted bar is transparent, so the honest question is not "what colour
    # is the bar" but "what does the bar look like with the darkest thing on
    # the page behind it". That is a night section, and it is the case a
    # light-page-only check never sees.
    chrome = chrome_over(palette["night"], palette["surface"])

    return {
        # the light page
        "ink_on_surface": contrast(palette["ink"], palette["surface"]),
        "muted_on_surface": contrast(palette["ink_muted"], palette["surface"]),
        "muted_on_sunk": contrast(palette["ink_muted"], palette["surface_sunk"]),
        "ink_on_sunk": contrast(palette["ink"], palette["surface_sunk"]),
        "accent_ink_on_surface": contrast(palette["accent_ink"], palette["surface"]),
        "accent_ink_on_sunk": contrast(palette["accent_ink"], palette["surface_sunk"]),
        "ink_on_wash": contrast(palette["ink"], palette["accent_wash"]),
        "accent_ink_on_wash": contrast(palette["accent_ink"], palette["accent_wash"]),
        "muted_on_wash": contrast(palette["ink_muted"], palette["accent_wash"]),
        # buttons: the label against its own fill, both ways round
        "accent_fg_on_accent": contrast(palette["accent_fg"], palette["accent"]),
        "night_on_accent_bright": contrast(palette["night"], palette["accent_bright"]),
        # dark sections
        "night_ink_on_night": contrast(palette["night_ink"], palette["night"]),
        "night_muted_on_night": contrast(palette["night_muted"], palette["night"]),
        "night_muted_on_night_sunk": contrast(palette["night_muted"],
                                              palette["night_sunk"]),
        "night_ink_on_night_sunk": contrast(palette["night_ink"], palette["night_sunk"]),
        "accent_bright_on_night": contrast(palette["accent_bright"], palette["night"]),
        "accent_bright_on_night_sunk": contrast(palette["accent_bright"],
                                                palette["night_sunk"]),
        # the frosted nav and call bar, worst case: a night section behind them
        "chrome_ink": contrast(palette["ink"], chrome),
        "chrome_muted_strong": contrast(palette["ink_muted_strong"], chrome),
        "chrome_accent_ink": contrast(palette["accent_ink"], chrome),
        # focus rings — 2.4.7 needs the ring to be *visible*, which is the same
        # measurement as text against the surface it lands on
        "focus_on_surface": contrast(palette["ink"], palette["surface"]),
        "focus_on_night": contrast(palette["night_ink"], palette["night"]),
    }


def failures(palette: dict[str, str], target: float = AA_TEXT) -> dict[str, float]:
    """The pairs below `target`. Empty means the palette is AA-clean."""
    return {pair: ratio for pair, ratio in audit(palette).items() if ratio < target}
