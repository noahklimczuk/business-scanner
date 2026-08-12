"""Web search, used only to find a lead's Facebook or Instagram page.

Read this before turning it on.

There is no free, terms-clean search API. The options are: pay for Bing or
Serper (a key and a bill), scrape a search engine's HTML (free, and against
most engines' terms of use), or do the searches by hand. This module makes the
choice explicit rather than burying it:

    ddg     scrapes html.duckduckgo.com. Free, no key, and the thing most
            small tools do. It is still scraping — one query per lead, a
            second apart, and it gives up the moment it is rate-limited
            instead of hammering. Blocking is the realistic failure mode, not
            a legal one, but it is your call to make.
    manual  prints the search URLs and takes nothing automatically.
    none    skips social lookup entirely; enrich still normalises phones,
            detects chains, and finds duplicates, all offline.

A provider that fails must raise, never return an empty list — "we looked and
found nothing" and "the lookup broke" score very differently, and silently
conflating them would quietly downgrade every lead in the pipeline.
"""
from __future__ import annotations

import html as htmllib
import random
import re
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qs, quote_plus, urlparse

import requests

DDG_ENDPOINT = "https://html.duckduckgo.com/html/"
DDG_QUERY_URL = "https://duckduckgo.com/?q={}"

# A browser string, because the HTML endpoint returns nothing useful without
# one. Named here rather than hidden so the tradeoff stays visible.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

MIN_INTERVAL = 1.2      # seconds between queries, plus jitter
TIMEOUT = 20


class SearchError(RuntimeError):
    """Lookup broke. Never raised to mean 'found nothing'."""


class SearchBlocked(SearchError):
    """Rate-limited or refused. Stop asking; try again tomorrow."""


@dataclass
class SearchHit:
    url: str
    title: str = ""
    snippet: str = ""

    @property
    def text(self) -> str:
        return f"{self.title} {self.snippet}"


class NullProvider:
    name = "none"
    automatic = True

    def search(self, query: str) -> list[SearchHit]:
        return []


class ManualProvider:
    """Collects the queries so the operator can run them in a browser."""

    name = "manual"
    automatic = False

    def __init__(self) -> None:
        self.queries: list[str] = []

    def search(self, query: str) -> list[SearchHit]:
        self.queries.append(DDG_QUERY_URL.format(quote_plus(query)))
        return []


class DuckDuckGoProvider:
    name = "ddg"
    automatic = True

    def __init__(self, min_interval: float = MIN_INTERVAL,
                 session: Optional[requests.Session] = None) -> None:
        self.min_interval = min_interval
        self.session = session or requests.Session()
        self._last = 0.0

    def _wait(self) -> None:
        gap = time.monotonic() - self._last
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap + random.uniform(0, 0.4))
        self._last = time.monotonic()

    def search(self, query: str) -> list[SearchHit]:
        self._wait()
        try:
            r = self.session.get(DDG_ENDPOINT, params={"q": query}, timeout=TIMEOUT,
                                 headers={"User-Agent": USER_AGENT,
                                          "Accept-Language": "en-CA,en;q=0.9"})
        except requests.RequestException as exc:
            raise SearchError(f"search request failed: {exc}") from exc

        if r.status_code in (202, 403, 429):
            raise SearchBlocked(
                f"DuckDuckGo returned {r.status_code} — it has started rate-limiting "
                "this machine. Enrichment stops here; phone, chain and duplicate work "
                "already done is saved. Try again in a few hours, run with "
                "--provider manual, or slow it down with --pause 4."
            )
        if not r.ok:
            raise SearchError(f"DuckDuckGo returned HTTP {r.status_code}")
        return parse_ddg_html(r.text)


PROVIDERS = {"ddg": DuckDuckGoProvider, "manual": ManualProvider, "none": NullProvider}


def get_provider(name: str, pause: Optional[float] = None):
    if name not in PROVIDERS:
        raise ValueError(f"Unknown search provider '{name}'. "
                         f"Use one of: {', '.join(PROVIDERS)}")
    if name == "ddg":
        return DuckDuckGoProvider(min_interval=pause or MIN_INTERVAL)
    return PROVIDERS[name]()


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
_ANCHOR = re.compile(r'<a\b[^>]*?href="(?P<href>[^"]+)"[^>]*>(?P<text>.*?)</a>',
                     re.IGNORECASE | re.DOTALL)
_SNIPPET = re.compile(r'class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>',
                      re.IGNORECASE | re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")

# Engine plumbing, not results.
_SKIP_HOSTS = {"duckduckgo.com", "html.duckduckgo.com", "lite.duckduckgo.com",
               "spreadprivacy.com", "microsoft.com", "duck.com"}


def _strip(fragment: str) -> str:
    return htmllib.unescape(_TAGS.sub("", fragment)).strip()


def unwrap(href: str) -> str:
    """DuckDuckGo wraps results as //duckduckgo.com/l/?uddg=<encoded>."""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "uddg" in (parsed.query or ""):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            return target
    return href


def parse_ddg_html(page: str) -> list[SearchHit]:
    snippets = [_strip(s) for s in _SNIPPET.findall(page)]
    hits: list[SearchHit] = []
    seen: set[str] = set()
    for match in _ANCHOR.finditer(page):
        url = unwrap(match.group("href"))
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            continue
        host = (parsed.hostname or "").lower().removeprefix("www.")
        if not host or host in _SKIP_HOSTS:
            continue
        if url in seen:
            continue
        seen.add(url)
        title = _strip(match.group("text"))
        if not title:                      # favicons and bare image links
            continue
        hits.append(SearchHit(url=url, title=title,
                              snippet=snippets[len(hits)] if len(hits) < len(snippets) else ""))
    return hits[:20]
