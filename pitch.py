"""The leave-behind: one page, printed, that survives the conversation.

Roughly half of small business owners cannot say yes on the spot, because the
money is a household decision. The operator walks out, and what happens next
depends entirely on how well the person they left behind can explain it to
somebody who was not there. A URL scribbled on a business card loses that
conversation. This page is what wins it.

So it is not a brochure. Everything on it is there because it answers a
question that gets asked in a kitchen that evening:

  "what does it actually look like?"   -> their own site, their own colours,
                                          their own headline, at phone size
  "how do I see it?"                   -> a QR code, because nobody types a
                                          pages.dev subdomain correctly
  "do we even need one?"               -> "9 of the 11 roofing contractors
                                          near you have a website". That line
                                          is the whole pitch, it comes free
                                          out of the scan we already paid for,
                                          and it is a fact rather than a claim
  "what's the catch / are we stuck?"   -> the price both ways round, and the
                                          offboarding promise in writing

It prints to exactly one page and it is HTML, not a PDF. There is no PDF
library here and there does not need to be one: every machine the operator will
ever own can print a page to PDF, and an HTML file survives being emailed,
opened on a phone, and printed at a library. A PDF renderer would be a heavy
dependency, a font problem and a new way for the accent colour to come out
wrong.
"""
from __future__ import annotations

import datetime
import os
from typing import Any, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

import design
import generate
import qr as qr_mod
import visuals

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "templates")

# The guide's two offers. Lead with the second: the objection is almost never
# "a website isn't worth $1,200", it is "I don't have $1,200 this month".
DEFAULT_PRICING = {
    "standard_setup": 1200,
    "standard_monthly": 60,
    "nmd_setup": 0,
    "nmd_monthly": 149,
    "term_months": 12,
    "currency": "CAD",
}

_env: Optional[Environment] = None


def env() -> Environment:
    global _env
    if _env is None:
        _env = Environment(loader=FileSystemLoader(TEMPLATE_DIR),
                           autoescape=select_autoescape(["html"]),
                           trim_blocks=True, lstrip_blocks=True)
    return _env


def pricing_from(cfg: dict[str, Any] | None) -> dict[str, Any]:
    return {**DEFAULT_PRICING, **((cfg or {}).get("pricing") or {})}


def density_line(lead: dict[str, Any], density: dict[str, int]) -> str:
    """"9 of the 11 roofing contractors near you have a website."

    Empty when we cannot say it honestly. The sentence is only worth anything
    because it is true and specific, and "1 of the 1 businesses near you" is
    neither — it is an artefact of a scan that has not covered the town yet.
    """
    total = density.get("category_total") or 0
    with_site = density.get("category_with_site") or 0
    if total < 4 or with_site < 2 or with_site >= total:
        return ""
    trade = (lead.get("category") or "businesses").lower()
    if not trade.endswith("s"):
        trade += "s"
    return (f"{with_site} of the {total} {trade} near you already have a "
            f"website. You don't.")


def breakable(url: str) -> Markup:
    """The URL with break opportunities at its dots and hyphens.

    A pages.dev preview host is 37 characters and does not fit the column, and
    the browser's own last resort — break-all — snaps it mid-label into
    "...previews.p / ages.dev", which reads as a typo to someone deciding
    whether to trust it. `<wbr>` puts the breaks where a person would put them.
    """
    import html as htmllib

    safe = htmllib.escape(url.replace("https://", "").rstrip("/"), quote=True)
    for separator in (".", "-"):
        safe = safe.replace(separator, f"{separator}<wbr>")
    return Markup(safe)


def build(lead: dict[str, Any], content: dict[str, Any], *,
          preview_url: str = "", density: Optional[dict[str, int]] = None,
          operator: Optional[dict[str, Any]] = None,
          pricing: Optional[dict[str, Any]] = None,
          template: Optional[str] = None) -> str:
    """The leave-behind for one lead, as a self-contained HTML page."""
    template = template or generate.template_for(lead.get("category"))
    palette = design.palette_for(lead["place_id"], template)

    code = ""
    if preview_url:
        try:
            code = qr_mod.svg(preview_url, size_px=118, dark=palette["ink"])
        except qr_mod.QRUnavailable:
            # The page is still worth printing without it; the URL is on it in
            # type large enough to read across a counter.
            code = ""

    return env().get_template("leavebehind.html").render(
        lead=lead,
        c=content,
        palette=palette,
        preview_url=preview_url,
        display_url=breakable(preview_url) if preview_url else "",
        qr=Markup(code) if code else "",
        art=Markup(visuals.artwork(lead["place_id"], template,
                                   lead.get("name", ""), 0)),
        density=density_line(lead, density or {}),
        phone_display=lead.get("phone") or "",
        operator=operator or {},
        price=pricing_from({"pricing": pricing} if pricing else None),
        today=datetime.date.today().strftime("%-d %B %Y"),
    )


def write(place_id: str, html: str) -> str:
    directory = generate.site_dir(place_id)
    os.makedirs(directory, exist_ok=True)
    # Named so it cannot be confused with the site itself, and so deploy.py's
    # allowlist leaves it on the operator's laptop where it belongs. It carries
    # the price and the operator's margin; it is not for the client's domain.
    path = os.path.join(directory, "leave-behind.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return path
