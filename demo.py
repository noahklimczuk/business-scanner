"""The portfolio: twelve finished sites for businesses that do not exist.

The prospect's own preview is the thing that closes, but it is a hard artifact
to *open* a conversation with — it shows the shape of the offer rather than the
ceiling of it, and on the day you walk in it is half empty because nobody has
taken any photographs yet. So there is a second artifact, and this is it: one
complete site per category, built from hand-written copy that ships with the
tool, ready before you leave the house.

Three things follow from the businesses being invented, and all three are the
point:

- **It costs nothing and needs no key.** `leadsmith demo` writes twelve sites
  from JSON in `demo/fixtures/`. No model call, no spend, no network.
- **It can carry the things a real prospect's page must not.** A testimonial,
  for one. `render(showcase=True)` skips the fabrication check for exactly this
  case, because the check exists to protect a real business from claims nobody
  made about them, and Halstead Roofing is not a real business. Nothing built
  from a Google listing ever takes that path.
- **It is honest on its face.** Every page carries the demo ribbon and a
  footer note saying what it is.

`leadsmith build <place_id> --demo` is the other half: the *prospect's* site,
built rich, with stock photography standing in. That one reviews its copy
exactly as a production build does, because that one is about a real business.
"""
from __future__ import annotations

import datetime
import json
import os
from dataclasses import dataclass, field
from typing import Any, Optional

import design
import generate
import stock

ROOT = os.path.dirname(os.path.abspath(__file__))
FIXTURE_DIR = os.path.join(ROOT, "demo", "fixtures")
DEMOS_DIR = os.environ.get("LEADSMITH_DEMOS") or os.path.join(ROOT, "demos")


class DemoError(RuntimeError):
    """Written for the operator, not a log file."""


@dataclass
class Built:
    template: str
    name: str
    category: str
    path: str
    accent: str
    bytes: int
    photos: int = 0
    note: str = ""
    services: list[str] = field(default_factory=list)

    @property
    def href(self) -> str:
        """Where the gallery links, relative to `demos/index.html`."""
        return f"{self.template}/index.html"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def available() -> list[str]:
    """The templates that have a demo business written for them, in page order."""
    if not os.path.isdir(FIXTURE_DIR):
        return []
    found = {name[:-5] for name in os.listdir(FIXTURE_DIR)
             if name.endswith(".json")}
    # generate.TEMPLATES order, so `demo` builds them in the same sequence the
    # gallery lists them and neither is sorted alphabetically by accident.
    return [t for t in generate.TEMPLATES if t in found]


def load(template: str) -> dict[str, Any]:
    path = os.path.join(FIXTURE_DIR, f"{template}.json")
    if not os.path.exists(path):
        known = ", ".join(available()) or "none"
        raise DemoError(
            f"There is no demo business for '{template}'.\n"
            f"Written so far: {known}.")
    try:
        with open(path, encoding="utf-8") as fh:
            fixture = json.load(fh)
    except json.JSONDecodeError as exc:
        raise DemoError(f"{path} is not valid JSON ({exc.msg}, line {exc.lineno}).") from exc

    for key in ("lead", "copy"):
        if key not in fixture:
            raise DemoError(f"{path} has no \"{key}\" block.")
    fixture.setdefault("template", template)
    return fixture


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------
def site_dir(template: str) -> str:
    return os.path.join(DEMOS_DIR, template)


def build(template: str, *, photos: bool = True,
          stock_settings: Optional[dict[str, Any]] = None,
          operator: Optional[dict[str, Any]] = None) -> Built:
    """Write one showcase site into `demos/<template>/`.

    Photographs are best-effort by design. No key, no network or a rate limit
    all fall through to the generated artwork rather than failing the build —
    a demo you cannot build in a coffee shop is a demo you cannot build.
    """
    fixture = load(template)
    lead = dict(fixture["lead"])
    content = dict(fixture["copy"])
    directory = site_dir(template)
    os.makedirs(directory, exist_ok=True)

    note = ""
    if photos:
        result = stock.fetch(template, generate.PHOTO_SLOTS.get(template, 6),
                             directory, stock_settings, seed=lead["place_id"])
        note = result.note
        if result.photos:
            content["photos"] = result.as_content()
            with open(os.path.join(directory, "credits.json"), "w",
                      encoding="utf-8") as fh:
                fh.write(stock.manifest(result) + "\n")
    else:
        note = "Built with generated artwork; --no-photos was asked for."

    site = generate.render(
        lead, content, template=template,
        # A demo is never indexable. These pages carry no real business's name,
        # but they do carry a phone number in a 555 range and a made-up address,
        # and there is no version of "our sample bakery outranks a real bakery"
        # that ends well.
        preview=True, demo=True, showcase=True,
        demo_note=fixture.get("note", ""), operator=operator,
        city_hint=generate.locality(lead.get("address")))

    index = os.path.join(directory, "index.html")
    with open(index, "w", encoding="utf-8") as fh:
        fh.write(site.html)
    with open(os.path.join(directory, "robots.txt"), "w", encoding="utf-8") as fh:
        fh.write(site.robots)
    # The copy, in the same shape a real site's content.json has, so a line that
    # works can be lifted straight out of a demo and into a client's file.
    with open(os.path.join(directory, "content.json"), "w", encoding="utf-8") as fh:
        json.dump({"version": generate.CONTENT_VERSION,
                   "place_id": lead["place_id"], "template": template,
                   "showcase": True, "generated_at": datetime.datetime.now()
                   .isoformat(timespec="seconds"), "copy": content},
                  fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    total = sum(os.path.getsize(os.path.join(directory, f))
                for f in os.listdir(directory)
                if os.path.isfile(os.path.join(directory, f))
                and f not in ("content.json", "credits.json"))
    return Built(template=template, name=lead["name"],
                 category=lead.get("category", ""), path=index,
                 accent=site.palette["accent"], bytes=total,
                 photos=len(content.get("photos") or []), note=note,
                 services=[s["name"] for s in content.get("services") or []][:3])


def build_all(*, photos: bool = True,
              stock_settings: Optional[dict[str, Any]] = None,
              operator: Optional[dict[str, Any]] = None,
              on_progress=None) -> list[Built]:
    built = []
    names = available()
    for i, template in enumerate(names, 1):
        if on_progress:
            on_progress(i, len(names), template)
        built.append(build(template, photos=photos,
                           stock_settings=stock_settings, operator=operator))
    return built


# ---------------------------------------------------------------------------
# The gallery
# ---------------------------------------------------------------------------
def gallery(built: list[Built], operator: Optional[dict[str, Any]] = None) -> str:
    """The index the operator actually opens: twelve cards, one per demo.

    It is the same self-contained single file as everything else this tool
    writes, so the whole `demos/` folder copies to a memory stick and opens on
    a machine with nothing installed.
    """
    os.makedirs(DEMOS_DIR, exist_ok=True)
    html = generate._env().get_template("gallery.html").render(
        built=built,
        operator=operator or {},
        palette=design.palette_for("leadsmith-gallery", "professional"),
        style=design.style_for("professional"),
        year=datetime.date.today().year,
        total=len(built),
    )
    path = os.path.join(DEMOS_DIR, "index.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(generate.squeeze_css(html))
    return path
