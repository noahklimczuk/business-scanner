"""Finding out that a newer build exists, and putting it in place.

The app ships as one Leadsmith.exe attached to a GitHub release. There is no
installer, no package manager and no service watching for it, so without this
the operator only learns a fix exists if somebody tells them — which for a tool
one person runs on one laptop means never.

Three rules shape everything here.

  - **Nothing is executed.** The new build is downloaded, checked, and moved
    into place; then the operator is asked to restart. The app never launches
    the file it just fetched. Restarting is a thing they understand and can
    refuse, and it keeps the whole update path free of "we ran what we
    downloaded" as a failure mode.
  - **The bytes are verified before anything is replaced.** The release API
    publishes a SHA-256 for each asset. A build whose hash does not match is
    thrown away with the working copy untouched. An asset with no published
    digest is not installed at all — see `install`.
  - **A failed check is silent and harmless.** No network, GitHub down, a rate
    limit, a machine with the clock wrong: none of it may interrupt somebody
    halfway through a scan. `check` returns None and the app carries on.

Presentation lives in the GUI; this module is importable without Qt and its
tests never touch the network.
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import requests

from version import VERSION

REPO = "noahklimczuk/business-scanner"
LATEST_ENDPOINT = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
ASSET_NAME = "Leadsmith.exe"

TIMEOUT = 20              # the check; a person is waiting on the manual one
DOWNLOAD_TIMEOUT = 120    # ~57MB on a bad connection at a doorway
CHUNK = 256 * 1024

# Where a downloaded build may come from. GitHub serves release assets by
# redirecting to its object store, so both are needed — and a redirect that
# leaves them is refused rather than followed, because the whole point of
# checking is that we are choosing what to run tomorrow.
ALLOWED_HOSTS = ("github.com", "api.github.com", "objects.githubusercontent.com",
                 "release-assets.githubusercontent.com")

# The suffix a replaced build is parked under. Windows will not let a running
# .exe be deleted or overwritten, but it will let it be renamed — that is the
# whole trick, and it is why this works without a helper process or a batch
# script babysitting the swap.
OLD_SUFFIX = ".old"


class UpdateError(Exception):
    """Something went wrong that the operator needs to read. Written for them."""


@dataclass
class Release:
    version: str          # "0.2.6"
    tag: str              # "v0.2.6"
    notes: str
    page_url: str
    asset_url: str = ""
    asset_size: int = 0
    digest: str = ""      # "sha256:…", straight from the release API

    @property
    def installable(self) -> bool:
        """Can this be applied in place, or only pointed at?"""
        return bool(self.asset_url and self.digest and can_install())


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------
def parse(text: str) -> tuple[int, ...]:
    """"v0.2.10" -> (0, 2, 10). Anything unparseable sorts lowest.

    Numeric parts, compared as numbers: 0.2.10 is newer than 0.2.9, which is
    the comparison a plain string would get wrong in the one case it matters.
    """
    numbers = re.findall(r"\d+", (text or "").strip().lstrip("vV"))
    return tuple(int(n) for n in numbers) or (0,)


def is_newer(candidate: str, current: str = VERSION) -> bool:
    """Strictly newer. Equal or older is not an update, and never offered."""
    a, b = parse(candidate), parse(current)
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


# ---------------------------------------------------------------------------
# Asking GitHub
# ---------------------------------------------------------------------------
def latest(getter: Optional[Callable[..., Any]] = None) -> Optional[Release]:
    """The newest published release, or None if that cannot be established.

    `getter` is injected so the tests can exercise every branch of this without
    a network — the same shape `deploy.deploy` uses for wrangler. Resolved here
    rather than as a default argument, because a default binds `requests.get`
    once at import and no amount of patching afterwards would reach it.
    """
    getter = getter or requests.get
    try:
        response = getter(
            LATEST_ENDPOINT,
            headers={"Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28"},
            timeout=TIMEOUT)
    except Exception:                                             # noqa: BLE001
        # Offline, DNS, a proxy, a captive portal at a client's shop. None of
        # it is worth a word to somebody who did not ask.
        return None
    if not getattr(response, "ok", False):
        return None
    try:
        data = response.json() or {}
    except ValueError:
        return None

    tag = str(data.get("tag_name") or "").strip()
    if not tag:
        return None

    release = Release(
        version=tag.lstrip("vV"),
        tag=tag,
        notes=str(data.get("body") or "").strip(),
        page_url=str(data.get("html_url") or RELEASES_PAGE),
    )
    for asset in data.get("assets") or []:
        if asset.get("name") != ASSET_NAME:
            continue
        url = str(asset.get("browser_download_url") or "")
        if not _allowed(url):
            continue
        release.asset_url = url
        release.asset_size = int(asset.get("size") or 0)
        release.digest = str(asset.get("digest") or "")
        break
    return release


def check(current: str = VERSION,
          getter: Optional[Callable[..., Any]] = None) -> Optional[Release]:
    """The newest release if it is newer than what is running, else None."""
    found = latest(getter)
    if found is None or not is_newer(found.version, current):
        return None
    return found


def _allowed(url: str) -> bool:
    parts = urlparse(url)
    if parts.scheme != "https":
        return False
    host = (parts.hostname or "").lower()
    return host in ALLOWED_HOSTS


# ---------------------------------------------------------------------------
# Fetching it
# ---------------------------------------------------------------------------
ProgressFn = Callable[[int, int], Optional[bool]]


def download(release: Release, directory: str,
             progress: Optional[ProgressFn] = None,
             getter: Optional[Callable[..., Any]] = None) -> str:
    """Fetch the build into `directory` and return the path to it.

    Written beside the executable rather than into the system temp directory,
    because the finished file has to be renamed over the running one and
    `os.replace` is only atomic within a volume. A temp folder on C: and an app
    run from a memory stick is exactly the case that would otherwise fail at
    the last step, after the whole download.

    `progress` is called with (bytes so far, bytes expected) and stops the
    download by returning False — the same contract as `prospect.run`, so the
    Stop button reaches this the same way.
    """
    if not release.asset_url:
        raise UpdateError(
            f"Release {release.tag} has no {ASSET_NAME} attached, so there is "
            f"nothing to install.\nDownload it by hand from {release.page_url}.")
    if not _allowed(release.asset_url):
        raise UpdateError(
            "That release points somewhere other than GitHub, so it was not "
            f"downloaded.\nCheck {release.page_url} yourself before trusting it.")

    getter = getter or requests.get
    os.makedirs(directory, exist_ok=True)
    target = os.path.join(directory, f"{ASSET_NAME}.new")
    digest = hashlib.sha256()
    written = 0

    try:
        response = getter(release.asset_url, stream=True, timeout=DOWNLOAD_TIMEOUT,
                          headers={"Accept": "application/octet-stream"})
    except requests.RequestException as exc:
        raise UpdateError(f"Could not reach GitHub to download the update: {exc}") from exc
    if not getattr(response, "ok", False):
        raise UpdateError(
            f"GitHub refused the download (HTTP {getattr(response, 'status_code', '?')}).\n"
            f"Try again, or get it from {release.page_url}.")

    expected = release.asset_size or int(
        (getattr(response, "headers", None) or {}).get("Content-Length") or 0)
    try:
        with open(target, "wb") as fh:
            for chunk in response.iter_content(chunk_size=CHUNK):
                if not chunk:
                    continue
                fh.write(chunk)
                digest.update(chunk)
                written += len(chunk)
                if progress and progress(written, expected) is False:
                    raise UpdateError("Update cancelled. Nothing was replaced.")
    except UpdateError:
        _discard(target)
        raise
    except OSError as exc:
        _discard(target)
        raise UpdateError(
            f"Could not write the update to {directory}: {exc}\n"
            "That is usually a full disk, or a folder this account cannot "
            "write to — Program Files, most often.") from exc

    if release.asset_size and written != release.asset_size:
        _discard(target)
        raise UpdateError(
            f"The download stopped early — {written:,} bytes of "
            f"{release.asset_size:,}. Nothing was replaced; try again.")

    got = digest.hexdigest()
    want = release.digest.split(":", 1)[-1].strip().lower()
    if not want or got != want:
        _discard(target)
        raise UpdateError(
            "The downloaded file does not match the checksum GitHub published "
            "for it, so it was deleted and nothing was replaced.\n\n"
            f"expected  {want or '(none published)'}\ngot       {got}\n\n"
            "Try once more. If it happens again, do not install it by hand — "
            "say so, because this is what a tampered download looks like.")
    return target


def _discard(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Putting it in place
# ---------------------------------------------------------------------------
def can_install() -> bool:
    """Only a frozen build can replace itself.

    From source there is no single file to swap — the answer there is `git
    pull`, and offering an Install button that cannot work would be worse than
    offering nothing.
    """
    return bool(getattr(sys, "frozen", False))


def install_dir() -> str:
    return os.path.dirname(os.path.abspath(sys.executable))


def install(downloaded: str, running: Optional[str] = None) -> str:
    """Swap the downloaded build in for the running one. Returns its path.

    Windows refuses to delete or overwrite a running executable but allows it
    to be *renamed*, so the running build is moved aside to Leadsmith.exe.old
    and the new one takes its name. The running process keeps working from the
    renamed file until it exits; the next launch removes the leftover.

    If the second move fails the first is undone, because the one outcome that
    must not be possible is an operator left with no working executable.
    """
    running = os.path.abspath(running or sys.executable)
    old = running + OLD_SUFFIX

    if not os.path.exists(downloaded):
        raise UpdateError("The downloaded update went missing before it could "
                          "be installed. Nothing was replaced.")

    # A leftover from a previous update. Deleting it can fail if another copy
    # of the app is still running from it, which is not a reason to stop.
    _discard(old)

    os.replace(running, old)
    try:
        os.replace(downloaded, running)
    except OSError as exc:
        os.replace(old, running)          # put it back exactly as it was
        raise UpdateError(
            f"Could not put the new build in place: {exc}\n"
            "The version you were running has been restored and is untouched."
        ) from exc
    return running


def clean_up(running: Optional[str] = None) -> None:
    """Delete the build we replaced. Called once on launch, never fatal.

    It cannot be removed during the update that created it — it is the file
    that process is executing — so it is dealt with the next time the app
    starts, when it is nothing but a stale 57MB sitting beside the exe.
    """
    if not can_install():
        return
    try:
        _discard(os.path.abspath(running or sys.executable) + OLD_SUFFIX)
    except Exception:                                             # noqa: BLE001
        pass
