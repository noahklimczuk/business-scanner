"""Generated artwork for businesses that have no photographs yet.

Apple's whole visual language rests on a product shot, and we do not have one:
Places photos are licensed for display in our tool, not for republication on the
client's commercial site, and stock photography of a generic kitchen looks
exactly like stock photography of a generic kitchen.

So the hero has a real image slot. When `content.json` carries photos — which
is the first thing to collect after a sale — they are used. Until then it is
filled with a composition generated from the business's own accent hue and a
geometry chosen for its trade. It is a few kilobytes of inline SVG, it needs no
request, and it never looks like someone else's photo.
"""
from __future__ import annotations

import hashlib
from typing import Any

VIEWBOX = 640

# Each motif is grounded in the subject rather than decorative: rooflines and
# pitch angles for trades, plates and rising steam for food, ribbon curves for
# salons.
MOTIFS = ("trade", "food", "salon")


def _seed(place_id: str, salt: str = "") -> int:
    return int.from_bytes(
        hashlib.sha256((place_id + salt).encode("utf-8")).digest()[:4], "big")


def _jitter(place_id: str, salt: str, spread: float) -> float:
    """Deterministic offset in [-spread, +spread] so no two compositions align."""
    return ((_seed(place_id, salt) % 2000) / 1000.0 - 1.0) * spread


def _trade(place_id: str, v: str) -> str:
    """Stacked rooflines at real pitch angles, receding."""
    parts = []
    count = 4 + (_seed(place_id, v) % 2)
    for i in range(count):
        lift = 96 * i + _jitter(place_id, f"t{i}{v}", 40)
        pitch = 150 + _jitter(place_id, f"p{i}{v}", 60)
        parts.append(
            f'<path d="M-40 {520 - lift:.0f} L320 {520 - lift - pitch:.0f} '
            f'L680 {520 - lift:.0f}" fill="none" stroke="var(--art-line)" '
            f'stroke-width="{3 + i * 1.1:.1f}" stroke-linejoin="round" '
            f'opacity="{0.26 + 0.14 * i:.2f}"/>')
    parts.append('<rect x="-40" y="520" width="720" height="160" '
                 'fill="var(--art-fill)" opacity="0.14"/>')
    return "".join(parts)


def _food(place_id: str, v: str) -> str:
    """Concentric rings — a plate from above — with steam rising off centre."""
    parts = []
    cx = 320 + _jitter(place_id, f"cx{v}", 110)
    for i, radius in enumerate((250, 196, 142, 88)):
        parts.append(
            f'<circle cx="{cx:.0f}" cy="360" r="{radius + _jitter(place_id, f"r{i}{v}", 26):.0f}" '
            f'fill="none" stroke="var(--art-line)" stroke-width="{2.5 + i * 1.2:.1f}" '
            f'opacity="{0.24 + 0.11 * i:.2f}"/>')
    for i in range(3):
        x = cx - 70 + i * 70 + _jitter(place_id, f"s{i}{v}", 34)
        parts.append(
            f'<path d="M{x:.0f} 210 C{x - 32:.0f} 150 {x + 32:.0f} 110 '
            f'{x:.0f} 46" fill="none" stroke="var(--art-line)" '
            f'stroke-width="4" stroke-linecap="round" opacity="0.34"/>')
    return "".join(parts)


def _salon(place_id: str, v: str) -> str:
    """Ribbon curves, the way hair falls."""
    parts = []
    for i in range(5):
        offset = i * 54 + _jitter(place_id, f"r{i}{v}", 70)
        parts.append(
            f'<path d="M{-40 + offset:.0f} 660 C{140 + offset:.0f} 430 '
            f'{60 + offset:.0f} 250 {300 + offset:.0f} -40" fill="none" '
            f'stroke="var(--art-line)" stroke-width="{2.5 + i * 1.0:.1f}" '
            f'stroke-linecap="round" opacity="{0.22 + 0.09 * i:.2f}"/>')
    return "".join(parts)


_BUILDERS = {"trade": _trade, "food": _food, "salon": _salon}


def initials(name: str | None) -> str:
    import re
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
    import html as htmllib

    builder = _BUILDERS.get(template, _trade)
    salt = f"v{variant}"
    mark = htmllib.escape(initials(name), quote=True)
    monogram = (
        f'<text x="320" y="404" text-anchor="middle" font-size="248" '
        f'font-weight="700" letter-spacing="-12" fill="var(--art-fill)" '
        f'opacity="0.16" font-family="-apple-system, BlinkMacSystemFont, '
        f'Segoe UI, sans-serif">{mark}</text>') if mark and variant == 0 else ""
    return (
        f'<svg class="art-svg" viewBox="0 0 {VIEWBOX} {VIEWBOX}" '
        f'role="presentation" aria-hidden="true" focusable="false" '
        f'xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid slice">'
        f"{monogram}{builder(place_id, salt)}</svg>"
    )


def photos_from(content: dict[str, Any] | None, lead: dict[str, Any] | None = None
                ) -> list[dict[str, str]]:
    """Owner-supplied photos, if any have been collected yet.

    Shape in content.json:
        "photos": [{"src": "hero.jpg", "alt": "New roof on Davis Drive",
                    "width": 2000, "height": 1333}]
    Files sit next to content.json in sites/<place_id>/. Never Places photos.

    `alt` is required and `generate.accessibility_issues()` refuses to build
    without it. A photo that genuinely carries nothing — a texture, a pattern
    behind a heading — can say `"decorative": true` instead, which renders
    `alt=""` so a screen reader skips it. Those are the only two options, and
    both are deliberate; there is no path here that quietly ships an image with
    no alt text at all (1.1.1).
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
        })
    return out
