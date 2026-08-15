"""Licensed stock photography for demo sites, fetched once and kept on disk.

Constraint 4 in the guide says client sites use owner-supplied images, licensed
stock, or generated imagery — and never Google Places photos, whose licence
covers display inside our own tool rather than republication on a third party's
commercial site. This module is the "licensed stock" third of that sentence.

Three decisions worth knowing about.

**Downloaded, not hotlinked.** The demo is opened standing in a shop, and shops
have bad wifi. A page that fetches eight photographs from a CDN at the moment
of the pitch is a page that is grey for the first ten seconds of it. Pexels is
the default provider for exactly this reason: its licence permits downloading
and self-hosting, so the file lands in the site directory and the page is
self-contained the way every other page this tool builds is. Unsplash is
supported for operators who already have a key, with the same treatment.

**Alt text comes from the provider, never from us.** Both APIs return a
description written by a human who saw the picture. Inventing alt text from the
search term would produce "roofer working on a roof" for a photograph of a
ladder, which is worse than no alt text because it is confidently wrong.
Anything without a usable description is skipped rather than guessed at.

**Failure is not an error.** No key, no network, a rate limit, a search that
returns nothing — all of them fall back to the generated artwork in
`visuals.py` and say so. A demo that cannot be built because a photo service is
down is a demo that cannot be built in a coffee shop, which is where they get
built.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

CACHE_DIR = os.environ.get("LEADSMITH_STOCK_CACHE") or os.path.join(
    os.path.expanduser("~"), ".leadsmith", "stock")

TIMEOUT = 30
# Enough to fill the widest slot on a 2x display without being a phone photo.
# Neither provider re-encodes on request, so this is a choice between sizes they
# already made, not a resize we are asking for.
WIDE = 1880


class StockUnavailable(RuntimeError):
    """No photographs this time. Written for the operator, not a log file."""


PROVIDERS = {
    "pexels": {
        "label": "Pexels",
        "env": "PEXELS_API_KEY",
        "console": "pexels.com/api",
        "key_hint": "563492ad…",
    },
    "unsplash": {
        "label": "Unsplash",
        "env": "UNSPLASH_ACCESS_KEY",
        "console": "unsplash.com/oauth/applications",
        "key_hint": "Client-ID …",
    },
}
DEFAULT_PROVIDER = "pexels"


# ---------------------------------------------------------------------------
# What to search for
# ---------------------------------------------------------------------------
# One list per template, in the order the page uses its image slots: the first
# query fills the hero, and the rest fill whatever comes after it. They are
# deliberately concrete — "cedar shingle roof" rather than "roofing" — because a
# vague query on any stock service returns a person in a hard hat pointing at a
# clipboard, and that photograph has sold nothing to anyone.
QUERIES: dict[str, tuple[str, ...]] = {
    "trade": ("new shingle roof house", "roofer working on roof",
              "carpenter tools workbench", "construction site framing",
              "ladder against house", "worker measuring timber"),
    "food": ("restaurant plated dish", "cafe interior warm light",
             "fresh bread bakery", "barista pouring coffee",
             "chef plating food", "restaurant table setting",
             "pastry counter display", "kitchen pass service"),
    "salon": ("hair salon interior", "hairdresser styling hair",
              "salon washing station", "manicure hands close up",
              "salon mirror styling chair", "beauty treatment room",
              "hair colouring foils", "barber cutting hair"),
    "auto": ("car on lift in garage", "mechanic working on engine",
             "auto repair workshop tools", "tyre change garage",
             "car diagnostics laptop", "clean car detailing"),
    "wellness": ("gym interior equipment", "yoga class studio",
                 "person lifting weights", "fitness trainer coaching",
                 "stretching mat studio", "spin class bikes"),
    "clinic": ("modern dental surgery room", "clinic waiting room bright",
               "doctor with patient consultation", "medical instruments tray",
               "physiotherapy treatment", "reception desk clinic",
               "hands examining chart", "clinic corridor daylight"),
    "professional": ("modern office meeting room", "desk with documents pen",
                     "handshake business meeting", "office reception bright",
                     "person reviewing paperwork"),
    "retail": ("boutique shop interior", "shop shelves display",
               "shopkeeper wrapping purchase", "storefront window display",
               "products on wooden shelf", "shop counter till",
               "gift wrapped packages"),
    "home": ("landscaped garden lawn", "gardener mowing lawn",
             "hedge trimming garden", "patio stone path garden",
             "autumn leaves garden clearing", "snow cleared driveway",
             "flower bed planting", "clean tidy backyard",
             "pressure washing driveway", "garden tools shed"),
    "pet": ("dog being groomed", "happy dog outdoors",
            "cat at grooming table", "dog running in field",
            "puppy playing", "pet shop interior", "dog bath wash"),
    "creative": ("photography studio lighting", "camera on tripod",
                 "portrait session backdrop", "wedding photography couple",
                 "editing desk screens", "printed photographs on table",
                 "event lighting stage", "studio backdrop paper"),
    "education": ("children classroom learning", "teacher with students",
                  "art supplies table children", "reading books children",
                  "music lesson instrument", "playground outside school"),
}


@dataclass
class Photo:
    """One licensed photograph, on disk and ready for content.json."""
    src: str
    alt: str
    width: int
    height: int
    credit: str
    url: str = ""

    def as_content(self) -> dict[str, Any]:
        return {"src": self.src, "alt": self.alt, "width": self.width,
                "height": self.height, "credit": self.credit}


@dataclass
class Result:
    photos: list[Photo] = field(default_factory=list)
    note: str = ""

    def as_content(self) -> list[dict[str, Any]]:
        return [p.as_content() for p in self.photos]


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def resolve(settings: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """The `stock` block of config.json, filled in from the environment.

    An operator with no key at all is the normal case and not an error: they
    get generated artwork, which is what every production site gets anyway.
    """
    settings = settings or {}
    name = (settings.get("provider") or DEFAULT_PROVIDER).strip().lower()
    if name not in PROVIDERS:
        known = ", ".join(sorted(PROVIDERS))
        raise StockUnavailable(
            f'"{name}" is not a photo service this app knows about. '
            f"Pick one of: {known}.")
    spec = dict(PROVIDERS[name])
    spec["provider"] = name
    key = str(settings.get("api_key") or "").strip()
    if key.startswith("PASTE_"):
        key = ""
    spec["api_key"] = key or os.environ.get(spec["env"], "").strip()
    return spec


def configured(cfg: Optional[dict[str, Any]] = None) -> bool:
    """Whether a demo build can expect photographs at all."""
    try:
        return bool(resolve((cfg or {}).get("stock"))["api_key"])
    except StockUnavailable:
        return False


# ---------------------------------------------------------------------------
# The providers
# ---------------------------------------------------------------------------
def _search_pexels(spec: dict[str, Any], query: str, page: int) -> list[dict[str, Any]]:
    import requests

    response = requests.get(
        "https://api.pexels.com/v1/search",
        params={"query": query, "per_page": 5, "page": page,
                "orientation": "landscape"},
        headers={"Authorization": spec["api_key"]}, timeout=TIMEOUT)
    _raise_for_status(spec, response)
    found = []
    for photo in (response.json() or {}).get("photos") or []:
        alt = str(photo.get("alt") or "").strip()
        if not alt:
            # No description means no honest alt text, and inventing one from
            # the search term is how a photograph of a ladder ends up captioned
            # "roofer working on a roof".
            continue
        found.append({
            "download": (photo.get("src") or {}).get("large2x")
                        or (photo.get("src") or {}).get("large"),
            "alt": alt,
            "width": int(photo.get("width") or 0),
            "height": int(photo.get("height") or 0),
            "credit": f"{photo.get('photographer', 'Unknown')} on Pexels",
            "url": photo.get("url") or "",
        })
    return found


def _search_unsplash(spec: dict[str, Any], query: str, page: int) -> list[dict[str, Any]]:
    import requests

    response = requests.get(
        "https://api.unsplash.com/search/photos",
        params={"query": query, "per_page": 5, "page": page,
                "orientation": "landscape", "content_filter": "high"},
        headers={"Authorization": f"Client-ID {spec['api_key']}",
                 "Accept-Version": "v1"}, timeout=TIMEOUT)
    _raise_for_status(spec, response)
    found = []
    for photo in (response.json() or {}).get("results") or []:
        alt = str(photo.get("alt_description") or "").strip()
        if not alt:
            continue
        raw = (photo.get("urls") or {}).get("raw") or ""
        found.append({
            "download": f"{raw}&w={WIDE}&q=75&fm=jpg&fit=max" if raw else "",
            "alt": alt[:1].upper() + alt[1:],
            "width": int(photo.get("width") or 0),
            "height": int(photo.get("height") or 0),
            "credit": f"{(photo.get('user') or {}).get('name', 'Unknown')} on Unsplash",
            "url": (photo.get("links") or {}).get("html") or "",
        })
    return found


_SEARCH = {"pexels": _search_pexels, "unsplash": _search_unsplash}


def _raise_for_status(spec: dict[str, Any], response: Any) -> None:
    if response.status_code in (401, 403):
        raise StockUnavailable(
            f"{spec['label']} rejected the key. Settings → Photos, or get one "
            f"free at {spec['console']}.")
    if response.status_code == 429:
        raise StockUnavailable(
            f"{spec['label']} says you have hit its rate limit. The demo will "
            "build with generated artwork; try again in an hour for photos.")
    if response.status_code >= 400:
        raise StockUnavailable(
            f"{spec['label']} returned HTTP {response.status_code}.")


# ---------------------------------------------------------------------------
# Cache and download
# ---------------------------------------------------------------------------
def _cache_path(url: str) -> str:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
    return os.path.join(CACHE_DIR, f"{digest}.jpg")


def _download(url: str) -> bytes:
    """Bytes for a photo URL, from the cache when it has been seen before.

    Forty demos of the same trade share their photographs, so this turns forty
    downloads into one — which matters most on the day it matters most, sitting
    in a car park building a demo before walking in.
    """
    import requests

    path = _cache_path(url)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, "rb") as fh:
            return fh.read()

    response = requests.get(url, timeout=TIMEOUT)
    if response.status_code >= 400:
        raise StockUnavailable(f"Could not download a photo (HTTP "
                               f"{response.status_code}).")
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(response.content)
    return response.content


def _slug(text: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]
    return slug or fallback


def fetch(template: str, count: int, dest: str,
          settings: Optional[dict[str, Any]] = None,
          seed: str = "") -> Result:
    """Up to `count` licensed photographs for `template`, written into `dest`.

    Never raises on a missing key or a dead network: it returns what it managed
    to get, with a note saying why there is not more. An empty result is a
    perfectly good outcome — the page falls back to generated artwork slot by
    slot, so a demo with three photographs and five drawings still renders.

    `seed` shifts which page of results is taken, so two businesses on the same
    template do not get the same six pictures.
    """
    import requests

    queries = QUERIES.get(template) or QUERIES["trade"]
    try:
        spec = resolve(settings)
    except StockUnavailable as exc:
        return Result(note=str(exc))
    if not spec["api_key"]:
        return Result(note=(
            f"No {spec['label']} key, so this demo uses generated artwork. A "
            f"free key at {spec['console']} turns on photographs."))

    offset = int(hashlib.sha256((seed or template).encode()).hexdigest()[:4], 16)
    search = _SEARCH[spec["provider"]]
    photos: list[Photo] = []
    note = ""
    os.makedirs(dest, exist_ok=True)

    for i in range(count):
        query = queries[i % len(queries)]
        try:
            candidates = search(spec, query, page=1 + (offset + i // len(queries)) % 4)
        except StockUnavailable as exc:
            note = str(exc)
            break
        except requests.RequestException as exc:
            note = (f"Could not reach {spec['label']} ({exc.__class__.__name__}), "
                    "so the rest of this demo uses generated artwork.")
            break

        taken = {p.url for p in photos}
        candidate = next((c for c in candidates
                          if c["download"] and c["url"] not in taken), None)
        if candidate is None:
            continue

        name = f"{i:02d}-{_slug(query, f'photo-{i}')}.jpg"
        try:
            body = _download(candidate["download"])
        except (StockUnavailable, requests.RequestException) as exc:
            note = f"A photo could not be downloaded ({exc.__class__.__name__})."
            break
        with open(os.path.join(dest, name), "wb") as fh:
            fh.write(body)
        photos.append(Photo(src=name, alt=candidate["alt"],
                            width=candidate["width"], height=candidate["height"],
                            credit=candidate["credit"], url=candidate["url"]))

    if not photos and not note:
        note = (f"{spec['label']} returned nothing usable for {template}, so "
                "this demo uses generated artwork.")
    return Result(photos=photos, note=note)


def manifest(result: Result) -> str:
    """The credits, as JSON, for the record kept beside the demo."""
    return json.dumps([{"file": p.src, "credit": p.credit, "source": p.url,
                        "alt": p.alt} for p in result.photos],
                      indent=2, ensure_ascii=False)
