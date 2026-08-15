"""Generated artwork for the image slots, and the rules for real photographs.

Apple's whole visual language rests on a product shot, and we do not have one:
Places photos are licensed for display in our tool, not for republication on the
client's commercial site, and a stock photograph of a generic kitchen looks
exactly like a stock photograph of a generic kitchen.

So every image slot has three tiers, best first:

1. the owner's photographs, out of `content.json`;
2. licensed stock, fetched by `stock.py` — the demo default;
3. a composition generated here from the business's own accent hue and a
   geometry that comes from its trade.

Tier 3 is not a placeholder for the other two. It is the default, it ships, and
it is built to be worth shipping: a few kilobytes of inline SVG, no request, no
network, and it never looks like someone else's photo.

Two constraints shape everything below.

**Ids must be unique per slot.** Four of these render on one page and gradients
are referenced by id, so every id carries a hash of the business, the template
and the slot. Two panels sharing an id is not a cosmetic bug — the second one
silently paints with the first one's fill.

**Colour comes from CSS custom properties**, never from literals, so the same
markup works on the light page and inside a dark section without a second copy.
"""
from __future__ import annotations

import hashlib
import html as htmllib
import math
import re
from typing import Any, Callable

VIEWBOX = 640

# One motif per template. Each is grounded in the subject rather than
# decorative: rooflines and pitch angles for trades, plates and rising steam for
# food, gauge arcs for the garage, contour lines for landscaping.
MOTIFS = ("trade", "food", "salon", "auto", "wellness", "clinic",
          "professional", "retail", "home", "pet", "creative", "education")


def _seed(place_id: str, salt: str = "") -> int:
    return int.from_bytes(
        hashlib.sha256((place_id + salt).encode("utf-8")).digest()[:4], "big")


def _jitter(place_id: str, salt: str, spread: float) -> float:
    """Deterministic offset in [-spread, +spread] so no two compositions align."""
    return ((_seed(place_id, salt) % 2000) / 1000.0 - 1.0) * spread


def _unit(place_id: str, salt: str) -> float:
    """Deterministic value in [0, 1)."""
    return (_seed(place_id, salt) % 10_000) / 10_000.0


# ---------------------------------------------------------------------------
# Shared pieces
# ---------------------------------------------------------------------------
# Stroke, cap, join and the "no fill" are set once on a wrapping <g> in
# `artwork()` and inherited by everything inside it. Repeating them per element
# cost about 45 characters a shape, which on a page with ten image slots was a
# kilobyte and a half of the same four attributes.
GROUP_OPEN = ('<g fill="none" stroke="var(--art-line)" stroke-linecap="round" '
              'stroke-linejoin="round">')
_FILL = 'fill="var(--art-fill)" stroke="none"'


def _line(d: str, width: float, opacity: float, cap: str = "") -> str:
    tip = f' stroke-linecap="{cap}"' if cap else ""
    return (f'<path d="{d}" stroke-width="{width:.1f}"{tip} '
            f'opacity="{opacity:.2f}"/>')


def _circle(cx: float, cy: float, r: float, width: float, opacity: float) -> str:
    return (f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="{r:.0f}" '
            f'stroke-width="{width:.1f}" opacity="{opacity:.2f}"/>')


def _disc(cx: float, cy: float, r: float, opacity: float) -> str:
    return (f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="{r:.0f}" {_FILL} '
            f'opacity="{opacity:.2f}"/>')


def _box(x: float, y: float, w: float, h: float, opacity: float,
         radius: float = 0, filled: bool = False, width: float = 3) -> str:
    paint = _FILL if filled else f'stroke-width="{width:.1f}"'
    return (f'<rect x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="{h:.0f}" '
            f'rx="{radius:.0f}" {paint} opacity="{opacity:.2f}"/>')


def _arc(cx: float, cy: float, r: float, start_deg: float, end_deg: float,
         width: float, opacity: float) -> str:
    """A stroked arc, written as a path because SVG has no arc primitive."""
    a0, a1 = math.radians(start_deg), math.radians(end_deg)
    x0, y0 = cx + r * math.cos(a0), cy + r * math.sin(a0)
    x1, y1 = cx + r * math.cos(a1), cy + r * math.sin(a1)
    large = 1 if abs(end_deg - start_deg) > 180 else 0
    return _line(f"M{x0:.1f} {y0:.1f} A{r:.0f} {r:.0f} 0 {large} 1 "
                 f"{x1:.1f} {y1:.1f}", width, opacity)


# ---------------------------------------------------------------------------
# The motifs
# ---------------------------------------------------------------------------
def _trade(place_id: str, v: str) -> str:
    """Stacked rooflines at real pitch angles, over a scaffold grid."""
    parts = [
        # The grid reads as drawing paper. It is what makes the geometry look
        # measured rather than sketched.
        _line(f"M{x} -40 L{x} 680", 1, 0.10)
        for x in range(80, 640, 80)
    ]
    count = 4 + (_seed(place_id, v) % 2)
    for i in range(count):
        lift = 96 * i + _jitter(place_id, f"t{i}{v}", 40)
        pitch = 150 + _jitter(place_id, f"p{i}{v}", 60)
        parts.append(_line(
            f"M-40 {520 - lift:.0f} L320 {520 - lift - pitch:.0f} "
            f"L680 {520 - lift:.0f}", 3 + i * 1.1, 0.26 + 0.14 * i, cap="butt"))
    # Measurement ticks along the base: the language of a quote, not a poster.
    for i in range(9):
        x = 40 + i * 70
        parts.append(_line(f"M{x} 560 L{x} {560 + (26 if i % 3 == 0 else 14)}",
                           2, 0.3, cap="butt"))
    parts.append(_line("M40 560 L600 560", 2, 0.3, cap="butt"))
    parts.append(f'<rect x="-40" y="596" width="720" height="120" {_FILL} '
                 f'opacity="0.12"/>')
    return "".join(parts)


def _food(place_id: str, v: str) -> str:
    """A plate from above, steam rising off centre, and scattered seed dots."""
    parts = []
    cx = 320 + _jitter(place_id, f"cx{v}", 110)
    for i, radius in enumerate((250, 196, 142, 88)):
        parts.append(_circle(cx, 360, radius + _jitter(place_id, f"r{i}{v}", 26),
                             2.5 + i * 1.2, 0.24 + 0.11 * i))
    for i in range(3):
        x = cx - 70 + i * 70 + _jitter(place_id, f"s{i}{v}", 34)
        parts.append(_line(f"M{x:.0f} 210 C{x - 32:.0f} 150 {x + 32:.0f} 110 "
                           f"{x:.0f} 46", 4, 0.34))
    for i in range(14):
        parts.append(_disc(60 + _unit(place_id, f"dx{i}{v}") * 520,
                           420 + _unit(place_id, f"dy{i}{v}") * 220,
                           3 + _unit(place_id, f"dr{i}{v}") * 4, 0.28))
    return "".join(parts)


def _salon(place_id: str, v: str) -> str:
    """Ribbon curves, the way hair falls, crossing one soft disc."""
    parts = [_circle(320 + _jitter(place_id, f"c{v}", 90), 300,
                     190 + _jitter(place_id, f"cr{v}", 40), 2, 0.22)]
    for i in range(5):
        offset = i * 54 + _jitter(place_id, f"r{i}{v}", 70)
        parts.append(_line(
            f"M{-40 + offset:.0f} 660 C{140 + offset:.0f} 430 "
            f"{60 + offset:.0f} 250 {300 + offset:.0f} -40",
            2.5 + i * 1.0, 0.22 + 0.09 * i))
    return "".join(parts)


def _auto(place_id: str, v: str) -> str:
    """A gauge sweep with ticks, over speed lines and a hex nut."""
    cx, cy = 320 + _jitter(place_id, f"gx{v}", 70), 330
    parts = [_arc(cx, cy, r, 145, 395, w, o) for r, w, o in
             ((250, 2, 0.22), (214, 6, 0.34), (170, 2, 0.20))]
    # The needle and its ticks. The needle angle is the only thing that moves
    # between businesses, which is exactly how a dial should differ.
    for i in range(13):
        a = math.radians(145 + i * (250 / 12))
        inner = 226 if i % 3 else 208
        parts.append(_line(
            f"M{cx + inner * math.cos(a):.1f} {cy + inner * math.sin(a):.1f} "
            f"L{cx + 246 * math.cos(a):.1f} {cy + 246 * math.sin(a):.1f}",
            3 if i % 3 else 5, 0.3 if i % 3 else 0.46, cap="butt"))
    needle = math.radians(160 + _unit(place_id, f"n{v}") * 210)
    parts.append(_line(f"M{cx:.0f} {cy:.0f} "
                       f"L{cx + 196 * math.cos(needle):.1f} "
                       f"{cy + 196 * math.sin(needle):.1f}", 7, 0.55, cap="butt"))
    parts.append(_disc(cx, cy, 16, 0.5))
    for i in range(5):
        y = 500 + i * 34
        parts.append(_line(f"M{-40 + _jitter(place_id, f'sl{i}{v}', 60):.0f} {y} "
                           f"L{420 - i * 46:.0f} {y}", 5, 0.16 + 0.05 * i, cap="butt"))
    return "".join(parts)


def _wellness(place_id: str, v: str) -> str:
    """A pulse expanding out of a rising run of bars."""
    parts = []
    cx = 320 + _jitter(place_id, f"px{v}", 80)
    for i in range(5):
        parts.append(_circle(cx, 290, 70 + i * 62 + _jitter(place_id, f"pr{i}{v}", 18),
                             2 + i * 1.4, 0.30 - 0.04 * i))
    for i in range(7):
        height = 60 + i * 46 + _jitter(place_id, f"b{i}{v}", 40)
        parts.append(_box(48 + i * 82, 620 - height, 46, height,
                          0.16 + 0.05 * i, radius=23, filled=True))
    parts.append(_line("M-40 400 C 140 340 220 470 340 400 C 460 330 540 440 680 380",
                       5, 0.4))
    return "".join(parts)


def _clinic(place_id: str, v: str) -> str:
    """A quiet grid of rounded squares, one plus, one long wave."""
    parts = []
    for row in range(4):
        for col in range(4):
            if (row + col + _seed(place_id, v)) % 5 == 0:
                continue
            size = 96
            parts.append(_box(64 + col * 132 + _jitter(place_id, f"g{row}{col}{v}", 8),
                              70 + row * 132, size, size,
                              0.10 + 0.03 * ((row + col) % 4), radius=28,
                              filled=(row + col) % 3 == 0, width=2.5))
    cx = 320 + _jitter(place_id, f"px{v}", 90)
    parts.append(_box(cx - 22, 236, 44, 168, 0.34, radius=22, filled=True))
    parts.append(_box(cx - 106, 320, 212, 44, 0.34, radius=22, filled=True))
    parts.append(_line("M-40 560 C 120 500 200 620 360 560 C 500 508 560 600 680 552",
                       4, 0.3))
    return "".join(parts)


def _professional(place_id: str, v: str) -> str:
    """Ruled columns, one rising line, one large circle. A ledger, essentially."""
    parts = [_line(f"M{x} -40 L{x} 680", 1.5, 0.12) for x in range(107, 640, 107)]
    parts += [_line(f"M-40 {y} L680 {y}", 1, 0.09) for y in range(90, 640, 90)]
    parts.append(_circle(320 + _jitter(place_id, f"c{v}", 60), 300,
                         210 + _jitter(place_id, f"cr{v}", 30), 2, 0.24))
    points = []
    for i in range(7):
        points.append((40 + i * 93, 500 - i * 44 + _jitter(place_id, f"p{i}{v}", 56)))
    path = "M" + " L".join(f"{x:.0f} {y:.0f}" for x, y in points)
    parts.append(_line(path, 5, 0.48, cap="butt"))
    for x, y in points:
        parts.append(_disc(x, y, 7, 0.5))
    return "".join(parts)


def _retail(place_id: str, v: str) -> str:
    """Stacked boxes, bunting, and a scatter of price tags."""
    parts = []
    for i in range(6):
        w = 120 + _unit(place_id, f"w{i}{v}") * 110
        x = 30 + (i % 3) * 200 + _jitter(place_id, f"x{i}{v}", 24)
        y = 300 + (i // 3) * 170
        parts.append(_box(x, y, w, 130, 0.14 + 0.06 * (i % 3), radius=16,
                          filled=i % 2 == 0, width=3))
    parts.append(_line("M-40 120 Q 160 200 320 120 Q 480 40 680 130", 3, 0.3))
    for i in range(7):
        x = 20 + i * 100
        y = 120 + math.sin(i * 0.9) * 40
        parts.append(_line(f"M{x} {y:.0f} L{x - 22} {y + 62:.0f} "
                           f"L{x + 22} {y + 62:.0f} Z", 3, 0.26, cap="butt"))
    return "".join(parts)


def _home(place_id: str, v: str) -> str:
    """Contour lines, a horizon, and a stand of trees. A property from above."""
    parts = []
    for i in range(7):
        lift = i * 58 + _jitter(place_id, f"c{i}{v}", 26)
        parts.append(_line(
            f"M-40 {600 - lift:.0f} C 130 {540 - lift:.0f} 200 {660 - lift:.0f} "
            f"340 {592 - lift:.0f} C 470 {530 - lift:.0f} 560 {630 - lift:.0f} "
            f"680 {566 - lift:.0f}", 2 + i * 0.6, 0.30 - 0.03 * i))
    for i in range(4):
        x = 80 + i * 150 + _jitter(place_id, f"t{i}{v}", 40)
        h = 120 + _unit(place_id, f"th{i}{v}") * 90
        parts.append(_line(f"M{x:.0f} {240 - h:.0f} L{x - 52:.0f} 240 "
                           f"L{x + 52:.0f} 240 Z", 3, 0.22 + 0.05 * i, cap="butt"))
    return "".join(parts)


def _pet(place_id: str, v: str) -> str:
    """Bouncing discs, a wagging curve, and paw-shaped dot clusters."""
    parts = [_line("M-40 470 C 120 350 210 560 350 440 C 480 330 570 500 680 400",
                   6, 0.34)]
    for i in range(5):
        parts.append(_disc(70 + i * 128 + _jitter(place_id, f"d{i}{v}", 26),
                           250 + _jitter(place_id, f"dy{i}{v}", 120),
                           26 + _unit(place_id, f"dr{i}{v}") * 30,
                           0.16 + 0.05 * i))
    for i in range(2):
        cx = 150 + i * 300 + _jitter(place_id, f"p{i}{v}", 60)
        cy = 540 + _jitter(place_id, f"py{i}{v}", 30)
        parts.append(_disc(cx, cy + 26, 34, 0.3))
        for j, angle in enumerate((200, 245, 295, 340)):
            a = math.radians(angle)
            parts.append(_disc(cx + 56 * math.cos(a), cy + 56 * math.sin(a),
                               15, 0.3))
    return "".join(parts)


def _creative(place_id: str, v: str) -> str:
    """Nested frames, an aperture, and a fall of light."""
    parts = []
    for i in range(4):
        inset = 40 + i * 56 + _jitter(place_id, f"f{i}{v}", 16)
        parts.append(_box(inset, inset * 0.8, 640 - inset * 2, 640 - inset * 1.6,
                          0.12 + 0.07 * i, radius=0, width=2 + i))
    cx, cy, r = 320 + _jitter(place_id, f"ax{v}", 50), 320, 118
    blades = []
    for i in range(6):
        a0 = math.radians(i * 60 + _unit(place_id, f"ab{v}") * 30)
        a1 = a0 + math.radians(60)
        blades.append(_line(
            f"M{cx + r * math.cos(a0):.1f} {cy + r * math.sin(a0):.1f} "
            f"L{cx + r * 0.42 * math.cos(a1):.1f} {cy + r * 0.42 * math.sin(a1):.1f}",
            3, 0.42, cap="butt"))
    parts += blades
    parts.append(_circle(cx, cy, r, 3, 0.5))
    for i in range(5):
        x = 60 + i * 130
        parts.append(_line(f"M{x} -40 L{x + 120} 680", 26, 0.05, cap="butt"))
    return "".join(parts)


def _education(place_id: str, v: str) -> str:
    """Bauhaus blocks: a square, a triangle, an arc, a circle, arranged by hash."""
    parts = []
    slots = [(70, 90), (330, 60), (90, 340), (350, 330)]
    for i, (x, y) in enumerate(slots):
        shape = (_seed(place_id, f"s{i}{v}") + i) % 4
        size = 150 + _unit(place_id, f"z{i}{v}") * 90
        opacity = 0.16 + 0.07 * i
        if shape == 0:
            parts.append(_box(x, y, size, size, opacity, radius=12, filled=True))
        elif shape == 1:
            parts.append(f'<path d="M{x:.0f} {y + size:.0f} L{x + size / 2:.0f} '
                         f'{y:.0f} L{x + size:.0f} {y + size:.0f} Z" {_FILL} '
                         f'opacity="{opacity:.2f}"/>')
        elif shape == 2:
            parts.append(_disc(x + size / 2, y + size / 2, size / 2, opacity))
        else:
            parts.append(_arc(x + size / 2, y + size / 2, size / 2, 180, 360,
                              14, opacity + 0.14))
    parts.append(_line("M-40 600 L680 600", 6, 0.3, cap="butt"))
    return "".join(parts)


_BUILDERS: dict[str, Callable[[str, str], str]] = {
    "trade": _trade, "food": _food, "salon": _salon, "auto": _auto,
    "wellness": _wellness, "clinic": _clinic, "professional": _professional,
    "retail": _retail, "home": _home, "pet": _pet, "creative": _creative,
    "education": _education,
}


def initials(name: str | None) -> str:
    return "".join(w[0] for w in re.findall(r"[A-Za-z]+", name or "")[:2]).upper()


def artwork(place_id: str, template: str, name: str = "", variant: int = 0) -> str:
    """Inline SVG for one image slot.

    `variant` distinguishes the several slots on a page. Rendering the same
    composition four times reads as a copy-paste mistake rather than a design,
    so each slot reseeds the geometry — and only the first carries the
    monogram, which would otherwise repeat down the whole page.

    Every slot is decorative and every slot says so. This used to give the
    first one `role="img" aria-label="Halstead Roofing artwork"`, which is a
    1.1.1 failure dressed up as compliance: the drawing carries no information
    a sighted visitor gets, so announcing it just makes a screen reader read
    the business name a fourth time before the copy starts. `aria-hidden` is
    the correct treatment for decoration, and it is what makes the page quieter
    rather than merely conformant. Real photography, when the owner supplies
    it, is a different matter and carries real alt text.

    `focusable="false"` because IE and old Edge put SVGs in the tab order,
    which would otherwise add four dead stops to keyboard navigation (2.4.3).

    Colours come from CSS custom properties so the same markup works on the
    light page and inside a dark section. The result is marked safe by the
    caller, so anything interpolated here is escaped here: business names come
    from Google listings, which anyone can create, and are not trusted input.
    """
    builder = _BUILDERS.get(template, _trade)
    salt = f"v{variant}"
    mark = htmllib.escape(initials(name), quote=True)
    monogram = (
        f'<text x="320" y="404" text-anchor="middle" font-size="248" '
        f'font-weight="700" letter-spacing="-12" fill="var(--art-fill)" '
        f'opacity="0.14" font-family="-apple-system, BlinkMacSystemFont, '
        f'Segoe UI, sans-serif">{mark}</text>') if mark and variant == 0 else ""
    return (
        f'<svg class="art-svg" viewBox="0 0 {VIEWBOX} {VIEWBOX}" '
        f'role="presentation" aria-hidden="true" focusable="false" '
        f'xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid slice">'
        f"{monogram}{GROUP_OPEN}{builder(place_id, salt)}</g></svg>"
    )


# ---------------------------------------------------------------------------
# Real photographs
# ---------------------------------------------------------------------------
def photos_from(content: dict[str, Any] | None, lead: dict[str, Any] | None = None
                ) -> list[dict[str, str]]:
    """Owner-supplied or licensed-stock photos, if there are any.

    Shape in content.json:
        "photos": [{"src": "hero.jpg", "alt": "New roof on Davis Drive",
                    "width": 2000, "height": 1333,
                    "credit": "Photo by A. Person on Pexels"}]
    Files sit next to content.json in sites/<place_id>/. Never Places photos.

    `alt` is required and `generate.accessibility_issues()` refuses to build
    without it. A photo that genuinely carries nothing — a texture, a pattern
    behind a heading — can say `"decorative": true` instead, which renders
    `alt=""` so a screen reader skips it. Those are the only two options, and
    both are deliberate; there is no path here that quietly ships an image with
    no alt text at all (1.1.1).

    `credit` is optional and only ever appears in the demo's credits line. It is
    what makes "those are stock, day one is me photographing yours" a sentence
    the operator can say while pointing at the page.
    """
    photos = ((content or {}).get("photos") or [])
    out = []
    for photo in photos:
        src = str(photo.get("src") or "").strip()
        if not src or src.startswith(("http://", "https://", "//")):
            # Anything remote is either a Places URL or a hotlink. Both are out.
            continue
        decorative = bool(photo.get("decorative"))
        out.append({
            "src": src,
            "alt": "" if decorative else str(photo.get("alt") or "").strip(),
            "decorative": decorative,
            "width": str(photo.get("width") or ""),
            "height": str(photo.get("height") or ""),
            "credit": str(photo.get("credit") or "").strip(),
        })
    return out


def credits_for(photos: list[dict[str, str]]) -> list[str]:
    """The distinct credit lines of a set of photos, in order, deduplicated."""
    seen: list[str] = []
    for photo in photos:
        credit = (photo.get("credit") or "").strip()
        if credit and credit not in seen:
            seen.append(credit)
    return seen
