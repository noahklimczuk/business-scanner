"""The update check and the swap, with GitHub mocked out.

Nothing here touches the network, and nothing here runs a downloaded file —
the module does not either, which is the point of most of these.
"""
import hashlib
import os

import pytest
import requests

import update


@pytest.fixture(autouse=True)
def no_real_http(monkeypatch):
    def explode(*a, **kw):
        raise AssertionError("a test tried to reach the network")
    monkeypatch.setattr(requests, "get", explode)


class Response:
    """Enough of a requests.Response for this module."""

    def __init__(self, payload=None, *, ok=True, status_code=200, body=b"",
                 headers=None, bad_json=False):
        self.payload, self.ok, self.status_code = payload, ok, status_code
        self._body, self.headers = body, headers or {}
        self._bad_json = bad_json

    def json(self):
        if self._bad_json:
            raise ValueError("not json")
        return self.payload

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]


def release_json(tag="v0.9.0", *, body=b"binary", name=update.ASSET_NAME,
                 url=None, digest=None):
    sha = hashlib.sha256(body).hexdigest()
    return {
        "tag_name": tag,
        "body": "what changed",
        "html_url": f"https://github.com/{update.REPO}/releases/tag/{tag}",
        "assets": [{
            "name": name,
            "browser_download_url": url or (
                f"https://github.com/{update.REPO}/releases/download/{tag}/{name}"),
            "size": len(body),
            "digest": digest if digest is not None else f"sha256:{sha}",
        }],
    }


# ---------------------------------------------------------------------------
# Version comparison
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("candidate,current,expected", [
    ("0.2.6", "0.2.5", True),
    ("v0.2.6", "0.2.5", True),
    ("0.2.5", "0.2.5", False),
    ("0.2.4", "0.2.5", False),
    # The one a string comparison gets wrong, and the reason this is not one.
    ("0.2.10", "0.2.9", True),
    ("0.2.9", "0.2.10", False),
    ("0.3", "0.2.9", True),
    ("1.0", "0.9.9", True),
    # Shorter is not older: 0.3 and 0.3.0 are the same release.
    ("0.3", "0.3.0", False),
])
def test_only_a_strictly_newer_version_is_an_update(candidate, current, expected):
    assert update.is_newer(candidate, current) is expected


def test_an_unreadable_tag_is_never_newer():
    assert not update.is_newer("nightly", "0.2.5")


def test_the_shipped_version_is_the_one_pyproject_declares():
    """Two copies of a version number is one copy too many: the update check
    compares this string against the newest release tag."""
    import tomllib
    with open("pyproject.toml", "rb") as fh:
        project = tomllib.load(fh)["project"]
    assert "version" in project.get("dynamic", []), \
        "pyproject should read the version from version.py, not hold its own"


# ---------------------------------------------------------------------------
# Asking GitHub
# ---------------------------------------------------------------------------
def test_the_newest_release_is_read_off_the_api():
    found = update.latest(lambda *a, **kw: Response(release_json("v0.9.0")))
    assert found.version == "0.9.0"
    assert found.tag == "v0.9.0"
    assert found.asset_url.endswith(update.ASSET_NAME)
    assert found.digest.startswith("sha256:")


def test_an_update_is_only_offered_when_it_is_newer():
    getter = lambda *a, **kw: Response(release_json("v0.2.5"))       # noqa: E731
    assert update.check("0.2.5", getter) is None
    assert update.check("0.2.4", getter).version == "0.2.5"


@pytest.mark.parametrize("response", [
    Response(ok=False, status_code=403),        # rate limited
    Response(bad_json=True),                    # a captive portal's login page
    Response({}),                               # no tag_name
    Response({"tag_name": ""}),
])
def test_a_check_that_cannot_be_answered_is_silent(response):
    assert update.latest(lambda *a, **kw: response) is None


def test_no_network_is_not_an_error():
    """Someone on a phone hotspot at a client's door does not need telling."""
    def offline(*a, **kw):
        raise requests.ConnectionError("no route to host")
    assert update.latest(offline) is None
    assert update.check("0.0.1", offline) is None


def test_an_asset_hosted_somewhere_other_than_github_is_ignored():
    payload = release_json(url="https://cdn.example.com/evil/Leadsmith.exe")
    found = update.latest(lambda *a, **kw: Response(payload))
    assert found.asset_url == "", "a non-GitHub download must not be offered"
    assert not found.installable


def test_a_release_with_no_exe_attached_is_not_installable():
    payload = release_json(name="notes.txt")
    found = update.latest(lambda *a, **kw: Response(payload))
    assert found.asset_url == ""
    assert not found.installable


# ---------------------------------------------------------------------------
# Downloading
# ---------------------------------------------------------------------------
def fetch(payload, body):
    def getter(url, **kw):
        if url == update.LATEST_ENDPOINT:
            return Response(payload)
        return Response(body=body)
    return getter


def test_a_good_download_lands_beside_the_executable(tmp_path):
    body = b"a new build" * 100
    payload = release_json(body=body)
    found = update.latest(lambda *a, **kw: Response(payload))

    path = update.download(found, str(tmp_path), getter=fetch(payload, body))
    assert os.path.dirname(path) == str(tmp_path)
    assert open(path, "rb").read() == body


def test_a_download_that_fails_its_checksum_is_deleted(tmp_path):
    """What a tampered or corrupted download looks like, and the one case
    where doing nothing is the whole feature."""
    payload = release_json(body=b"the real build")
    found = update.latest(lambda *a, **kw: Response(payload))
    # Same length, different bytes — a swap that the size check cannot see and
    # only the hash catches.
    swapped = b"tampered wi7h!"
    assert len(swapped) == len(b"the real build")

    with pytest.raises(update.UpdateError, match="checksum"):
        update.download(found, str(tmp_path), getter=fetch(payload, swapped))
    assert os.listdir(tmp_path) == [], "the bad download was left on disk"


def test_a_release_with_no_published_checksum_is_not_installed(tmp_path):
    """No digest, no install. There would be nothing to check it against."""
    body = b"a new build"
    payload = release_json(body=body, digest="")
    found = update.latest(lambda *a, **kw: Response(payload))
    assert not found.installable

    with pytest.raises(update.UpdateError, match="checksum"):
        update.download(found, str(tmp_path), getter=fetch(payload, body))
    assert os.listdir(tmp_path) == []


def test_a_truncated_download_is_thrown_away(tmp_path):
    payload = release_json(body=b"x" * 5000)
    found = update.latest(lambda *a, **kw: Response(payload))

    with pytest.raises(update.UpdateError, match="stopped early"):
        update.download(found, str(tmp_path), getter=fetch(payload, b"x" * 40))
    assert os.listdir(tmp_path) == []


def test_progress_is_reported_and_can_stop_the_download(tmp_path):
    body = b"y" * (update.CHUNK * 3)
    payload = release_json(body=body)
    found = update.latest(lambda *a, **kw: Response(payload))

    seen = []

    def progress(done, total):
        seen.append((done, total))
        return False              # as if Stop were pressed

    with pytest.raises(update.UpdateError, match="cancelled"):
        update.download(found, str(tmp_path), progress, getter=fetch(payload, body))
    assert seen and seen[0][1] == len(body)
    assert os.listdir(tmp_path) == [], "a cancelled download left a part file"


def test_a_download_url_off_github_is_refused_outright(tmp_path):
    found = update.Release(version="9.9.9", tag="v9.9.9", notes="", page_url="",
                           asset_url="http://example.com/Leadsmith.exe",
                           asset_size=4, digest="sha256:00")
    with pytest.raises(update.UpdateError, match="other than GitHub"):
        update.download(found, str(tmp_path), getter=lambda *a, **kw: Response(body=b"evil"))


# ---------------------------------------------------------------------------
# Installing
# ---------------------------------------------------------------------------
@pytest.fixture()
def frozen(monkeypatch, tmp_path):
    """A pretend one-file build sitting in a folder, as on the operator's desk."""
    running = tmp_path / "Leadsmith.exe"
    running.write_bytes(b"the old build")
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr("sys.executable", str(running))
    return running


def test_the_new_build_takes_the_name_and_the_old_one_is_kept(frozen, tmp_path):
    """Windows will not overwrite a running exe but will let it be renamed —
    which is why this works with no helper process and no batch script."""
    new = tmp_path / "Leadsmith.exe.new"
    new.write_bytes(b"the new build")

    path = update.install(str(new), str(frozen))

    assert path == str(frozen)
    assert frozen.read_bytes() == b"the new build"
    assert (tmp_path / ("Leadsmith.exe" + update.OLD_SUFFIX)).read_bytes() == b"the old build"
    assert not new.exists()


def test_a_failed_swap_puts_the_working_build_back(frozen, tmp_path, monkeypatch):
    """The one outcome that must be impossible is no working executable."""
    new = tmp_path / "Leadsmith.exe.new"
    new.write_bytes(b"the new build")

    real_replace = os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:                     # the move that puts the new one in
            raise OSError("locked by antivirus")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    with pytest.raises(update.UpdateError, match="restored"):
        update.install(str(new), str(frozen))

    assert frozen.exists()
    assert frozen.read_bytes() == b"the old build"


def test_installing_a_file_that_vanished_changes_nothing(frozen, tmp_path):
    with pytest.raises(update.UpdateError, match="went missing"):
        update.install(str(tmp_path / "not-there.exe"), str(frozen))
    assert frozen.read_bytes() == b"the old build"


def test_the_replaced_build_is_cleared_on_the_next_launch(frozen, tmp_path):
    old = tmp_path / ("Leadsmith.exe" + update.OLD_SUFFIX)
    old.write_bytes(b"the build we replaced")

    update.clean_up(str(frozen))
    assert not old.exists()
    assert frozen.exists(), "clean-up removed the wrong file"


def test_clean_up_does_nothing_from_source(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.frozen", False, raising=False)
    leftover = tmp_path / ("Leadsmith.exe" + update.OLD_SUFFIX)
    leftover.write_bytes(b"unrelated")
    update.clean_up(str(tmp_path / "Leadsmith.exe"))
    assert leftover.exists()


def test_a_source_checkout_never_offers_to_replace_itself(monkeypatch):
    """There is no single file to swap; the answer here is `git pull`."""
    monkeypatch.setattr("sys.frozen", False, raising=False)
    found = update.latest(lambda *a, **kw: Response(release_json()))
    assert not found.installable
    assert not update.can_install()
