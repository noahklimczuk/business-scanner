"""Turn Google's opening-hours strings into something a page can use.

Places returns `weekdayDescriptions` as human sentences — "Monday: 8:00 AM –
5:00 PM" — with an en dash and, often, a narrow no-break space before AM/PM.
Parsing that in the browser would be silly, so it happens here at build time and
the page ships a small array of minute offsets plus twenty lines of JS.

Everything downstream needs a different shape: the visible table wants labels,
JSON-LD wants ISO times, and the "open now" pill wants minutes since midnight.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Optional

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# The page's JS uses Date.getDay(), which starts the week on Sunday.
JS_INDEX = {"Sunday": 0, "Monday": 1, "Tuesday": 2, "Wednesday": 3,
            "Thursday": 4, "Friday": 5, "Saturday": 6}

_DAY_LINE = re.compile(r"^\s*([A-Za-z]+)\s*:\s*(.*)$")
_RANGE = re.compile(
    r"(\d{1,2})(?::(\d{2}))?\s*([AaPp])?\.?[Mm]?\.?\s*[–—-]\s*"
    r"(\d{1,2})(?::(\d{2}))?\s*([AaPp])?\.?[Mm]?\.?"
)
_DASH = re.compile(r"[‐-―]")


def _normalise(text: str) -> str:
    # Google uses U+202F (narrow no-break space) before AM/PM and an en dash in
    # the range. Both survive JSON and both break naive parsing.
    text = unicodedata.normalize("NFKC", text or "")
    return _DASH.sub("-", text.replace(" ", " ").replace(" ", " ")).strip()


def _to_minutes(hour: int, minute: int, meridiem: Optional[str]) -> int:
    if meridiem:
        meridiem = meridiem.lower()
        if meridiem == "p" and hour != 12:
            hour += 12
        elif meridiem == "a" and hour == 12:
            hour = 0
    return hour * 60 + minute


def _parse_ranges(body: str) -> tuple[list[tuple[int, int]], bool, bool]:
    """(ranges in minutes, closed, open 24h)."""
    low = body.lower()
    if "closed" in low:
        return [], True, False
    if "24 hours" in low or "24 hrs" in low:
        return [(0, 1440)], False, True

    ranges: list[tuple[int, int]] = []
    for m in _RANGE.finditer(body):
        oh, om, oap, ch, cm, cap = m.groups()
        # "9:00 - 5:00 PM": the opening time inherits the closing meridiem, but
        # only when that keeps the range moving forwards.
        open_ap, close_ap = oap, cap
        if open_ap is None and close_ap is not None:
            open_ap = close_ap
            if _to_minutes(int(oh), int(om or 0), open_ap) >= _to_minutes(
                    int(ch), int(cm or 0), close_ap):
                open_ap = "a" if close_ap.lower() == "p" else "p"
        start = _to_minutes(int(oh), int(om or 0), open_ap)
        end = _to_minutes(int(ch), int(cm or 0), close_ap)
        if end <= start:
            end += 1440          # closes after midnight — the kitchen case
        ranges.append((start, end))
    return ranges, False, False


def _label(minutes: int) -> str:
    minutes %= 1440
    hour, minute = divmod(minutes, 60)
    suffix = "am" if hour < 12 else "pm"
    hour12 = hour % 12 or 12
    return f"{hour12}:{minute:02d}{suffix}" if minute else f"{hour12}{suffix}"


def parse(weekday_descriptions: Optional[list[str]]) -> list[dict[str, Any]]:
    """Google's strings -> one entry per day, Monday first."""
    found: dict[str, dict[str, Any]] = {}
    for line in weekday_descriptions or []:
        match = _DAY_LINE.match(_normalise(line))
        if not match:
            continue
        name = match.group(1).capitalize()
        if name not in JS_INDEX:
            continue
        ranges, closed, all_day = _parse_ranges(match.group(2))
        if not ranges and not closed:
            continue                                   # unparseable, skip the day
        found[name] = {
            "day": name,
            "short": name[:3],
            "js_index": JS_INDEX[name],
            "closed": closed or not ranges,
            "all_day": all_day,
            "ranges": ranges,
            "label": ("Closed" if closed or not ranges else
                      "Open 24 hours" if all_day else
                      ", ".join(f"{_label(s)} – {_label(e)}" for s, e in ranges)),
        }
    return [found[d] for d in DAYS if d in found]


def week_minutes(days: list[dict[str, Any]]) -> list[list[list[int]]]:
    """Minute ranges indexed by Date.getDay(), for the client-side open check."""
    week: list[list[list[int]]] = [[] for _ in range(7)]
    for day in days:
        if not day["closed"]:
            week[day["js_index"]] = [[s, e] for s, e in day["ranges"]]
    return week


def schema_spec(days: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`openingHoursSpecification` for the LocalBusiness JSON-LD."""
    spec = []
    for day in days:
        if day["closed"]:
            continue
        for start, end in day["ranges"]:
            spec.append({
                "@type": "OpeningHoursSpecification",
                "dayOfWeek": f"https://schema.org/{day['day']}",
                "opens": f"{start // 60 % 24:02d}:{start % 60:02d}",
                "closes": f"{end // 60 % 24:02d}:{end % 60:02d}",
            })
    return spec


def summary(days: list[dict[str, Any]]) -> str:
    """One line for the header, e.g. 'Mon–Fri 8am – 5pm'. Empty if irregular."""
    open_days = [d for d in days if not d["closed"]]
    if len(open_days) < 2:
        return ""
    labels = {d["label"] for d in open_days}
    if len(labels) != 1:
        return ""
    run = [d["short"] for d in open_days]
    span = f"{run[0]}–{run[-1]}" if len(run) > 2 else " & ".join(run)
    return f"{span} {open_days[0]['label']}"
