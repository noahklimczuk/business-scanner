"""QR codes: one for the terminal, one for the printed leave-behind.

The terminal one is the point of `leadsmith preview`. The operator is standing
outside a shop with the URL on a laptop and needs it on the phone in their hand
before they walk in — typing `a1b2c3d4.leadsmith-previews.pages.dev` at a
doorway is how the pitch starts badly.

`segno` does the encoding. That is a deliberate dependency rather than 250
lines of Reed-Solomon here: a hand-rolled encoder that is subtly wrong produces
a code that scans cleanly to the wrong URL, and there is no worse failure mode
for this particular feature. It is pure Python with no transitive dependencies.
It is also imported lazily, so every other command still runs if it is missing.
"""
from __future__ import annotations

import io

# Error correction M: 15% recovery. Enough for a phone camera pointed at a
# laptop screen at an angle, without inflating the code to where it stops
# fitting an 80-column terminal.
ERROR_LEVEL = "m"


class QRUnavailable(RuntimeError):
    """segno is not installed. Not fatal — the URL still prints."""


def _segno():
    try:
        import segno
    except ImportError as exc:                              # pragma: no cover
        raise QRUnavailable(
            "The QR code needs `segno`, which is not installed:\n"
            "    pip install segno\n"
            "Everything else works without it — the URL is above.") from exc
    return segno


# Explicit black-on-white, as ANSI. See for_terminal() for why this is not
# left to the terminal's own palette.
_LIGHT_BG, _DARK_BG = 107, 40
_LIGHT_FG, _DARK_FG = 97, 30
_UPPER_HALF = "▀"          # ▀ — fg paints the top half, bg the bottom


def for_terminal(data: str, quiet_zone: int = 2, colour: bool = True) -> str:
    """A scannable QR code as text, using half-block characters.

    Half blocks put two QR rows in one character cell, which is what keeps a
    version-3 code inside a normal terminal — a module per row would be forty
    lines tall and scroll off the screen mid-scan.

    The colours are written out as ANSI rather than left to the terminal, and
    that is the whole reason this is not three lines calling segno's own
    `terminal(compact=True)`. That output is uncoloured block characters, so it
    inherits the theme: correct on a dark terminal, and *inverted* on a light
    one, where the quiet zone comes out black. A fair number of phone cameras
    will not read an inverted code, and the failure happens at the door with
    the owner watching rather than here.

    `colour=False` gives the uncoloured version, which is right when the output
    is being piped somewhere that would show the escape codes as garbage.
    """
    segno = _segno()
    code = segno.make(data, error=ERROR_LEVEL)
    rows = [list(row) for row in code.matrix_iter(border=quiet_zone)]
    if len(rows) % 2:
        rows.append([0] * len(rows[0]))         # pad to whole character cells

    lines = []
    for top, bottom in zip(rows[::2], rows[1::2]):
        cells = []
        for upper, lower in zip(top, bottom):
            if colour:
                cells.append(
                    f"\033[{_DARK_FG if upper else _LIGHT_FG};"
                    f"{_DARK_BG if lower else _LIGHT_BG}m{_UPPER_HALF}")
            else:
                # Unpainted: a filled block is a *light* module, matching the
                # convention segno uses, so this stays readable on a dark
                # terminal at least.
                cells.append({(0, 0): "█", (1, 1): " ",
                              (0, 1): "▀", (1, 0): "▄"}[(upper, lower)])
        lines.append("".join(cells) + ("\033[0m" if colour else ""))
    return "\n".join(lines)


def svg(data: str, size_px: int = 132, quiet_zone: int = 2,
        dark: str = "#1D1D1F") -> str:
    """An inline SVG QR for the leave-behind.

    Vector, so it prints at whatever resolution the printer has rather than at
    whatever resolution we guessed. A leave-behind is the thing that survives
    the conversation with the spouse, and it gets scanned off paper under a
    kitchen light — this is not the place for a blurry raster.
    """
    segno = _segno()
    buf = io.BytesIO()                    # segno's SVG writer emits bytes
    segno.make(data, error=ERROR_LEVEL).save(
        buf, kind="svg", scale=1, border=quiet_zone, dark=dark, light=None,
        xmldecl=False, svgns=True, omitsize=True, svgclass=None, lineclass=None)
    body = buf.getvalue().decode("utf-8")
    # `omitsize` drops width/height and leaves the viewBox, so one size can be
    # asked for here without re-rendering the modules. `crispEdges` stops a
    # printer's antialiasing from softening module boundaries into each other,
    # which is what makes a small printed code fail to scan.
    return body.replace(
        "<svg ", f'<svg width="{size_px}" height="{size_px}" '
                 f'shape-rendering="crispEdges" ', 1)
