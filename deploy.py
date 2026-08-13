"""Putting a preview on the internet, and taking it down again.

This is the only part of the tool that publishes anything. A preview carries a
real business's name, address and phone number on a public URL before they have
agreed to anything at all — before they know we exist — so it gets more
ceremony than the rest of the codebase, not less:

  - the build must be a *preview* build. `preflight()` reads the files on disk
    and refuses if the noindex meta or the robots disallow is missing. Not
    "the caller passed preview=True": the actual bytes that would be served.
  - the operator is shown exactly what is about to become public and has to
    agree. `--yes` exists for the second and subsequent runs of an afternoon.
  - the URL is recorded on the lead, so `unpreview` can take it down and the
    operator can find it again in three weeks when the owner finally calls.

We shell out to `wrangler` rather than speaking Cloudflare's direct-upload
protocol ourselves. That protocol is a JWT fetch, a content-hash manifest, a
bulk asset upload and a deployment POST, and writing all of it against an API
this codebase has no account to test with would produce plausible code of
completely unknown correctness. `wrangler pages deploy` is Cloudflare's own
supported path, it is one `npm i -g wrangler` for the operator, and when it
breaks it breaks with Cloudflare's error message rather than ours.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Optional

# `sites/<place_id>/` is not a web root. It is our working directory for that
# business, and it also holds `content.json` — the draft copy, plus
# `verified_facts`, which is whatever the owner said in a conversation — and
# `content.rejected.json`, which is a list of the claims the fabrication check
# caught the model making about them. Uploading the directory would serve all
# of that at /content.json to anyone who guessed the URL.
#
# So nothing is uploaded from there directly. `stage()` copies out the files
# that are actually the website and leaves everything else behind, and the
# allowlist is the check: a file type nobody has thought about does not get
# published by default.
PUBLISHABLE_NAMES = {"index.html", "robots.txt", "favicon.ico"}
PUBLISHABLE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".avif", ".svg")

# Cloudflare's own limits on a Pages project name. Worth knowing here because
# the failure otherwise arrives after the upload.
PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,56}[a-z0-9]$")
DEFAULT_PROJECT = "leadsmith-previews"
TIMEOUT = 180

# wrangler prints the deployment URL on a line of its own, usually decorated.
# Match the URL, not the sentence around it, because the sentence changes
# between wrangler versions and the URL does not.
_URL = re.compile(r"https://[a-z0-9-]+\.[a-z0-9-]+\.pages\.dev\b")


class DeployError(RuntimeError):
    """Written for the operator standing in a car park, not for a log file."""


@dataclass
class Deployment:
    url: str
    project: str
    files: int
    bytes: int
    dry_run: bool = False
    published: list[str] = field(default_factory=list)
    withheld: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
def preflight(site_dir: str) -> list[str]:
    """Everything wrong with this directory, before anything is uploaded.

    Reads the files rather than trusting how they were built. The build flag
    and the bytes on disk agree right up until someone re-runs `build
    --launch`, forgets, and walks into a shop with a page that is asking Google
    to index a business that has not bought anything.
    """
    problems: list[str] = []
    index = os.path.join(site_dir, "index.html")
    robots = os.path.join(site_dir, "robots.txt")

    if not os.path.isdir(site_dir):
        return [f"No site at {site_dir}. Run `leadsmith build <place_id>` first."]
    if not os.path.exists(index):
        return [f"No index.html in {site_dir}. Run build again."]

    with open(index, encoding="utf-8") as fh:
        html = fh.read()
    if "noindex" not in html:
        problems.append(
            "index.html has no noindex — this looks like a launch build. A "
            "preview must not compete with the client's own Google listing.")
    if not os.path.exists(robots):
        problems.append("robots.txt is missing.")
    else:
        with open(robots, encoding="utf-8") as fh:
            if "Disallow: /" not in fh.read():
                problems.append("robots.txt does not disallow crawling.")

    # An image the page references but that is not going to be staged would
    # deploy as a broken picture on a stranger's shop window. Worth catching
    # here rather than in front of them.
    for src in re.findall(r'<img[^>]+src="([^":]+)"', html):
        if not os.path.exists(os.path.join(site_dir, src)):
            problems.append(f"index.html references {src}, which is not in "
                            f"{site_dir}.")
        elif not _publishable(os.path.basename(src)):
            problems.append(f"index.html references {src}, which is not a file "
                            f"type this will publish.")
    return problems


def _publishable(name: str) -> bool:
    if name.startswith("."):
        return False
    return (name in PUBLISHABLE_NAMES
            or name.lower().endswith(PUBLISHABLE_SUFFIXES))


def stage(site_dir: str, dest: str) -> tuple[list[str], list[str]]:
    """Copy the parts of `site_dir` that are the website into `dest`.

    Returns (published, withheld) by filename, both sorted, so the caller can
    show the operator exactly what is about to be public — and, more usefully,
    what is not.
    """
    os.makedirs(dest, exist_ok=True)
    published, withheld = [], []
    for name in sorted(os.listdir(site_dir)):
        source = os.path.join(site_dir, name)
        if not os.path.isfile(source):
            withheld.append(name + "/")
            continue
        if _publishable(name):
            shutil.copy2(source, os.path.join(dest, name))
            published.append(name)
        else:
            withheld.append(name)
    return published, withheld


def contents(directory: str) -> tuple[int, int]:
    """How many files and how many bytes are about to go public."""
    files = bytes_ = 0
    for root, _, names in os.walk(directory):
        for name in names:
            if name.startswith("."):
                continue
            files += 1
            bytes_ += os.path.getsize(os.path.join(root, name))
    return files, bytes_


def project_for(name: Optional[str]) -> str:
    project = (name or DEFAULT_PROJECT).strip().lower()
    if not PROJECT_RE.match(project):
        raise DeployError(
            f"'{project}' is not a valid Cloudflare Pages project name. "
            "Lowercase letters, digits and hyphens, 1-58 characters, and it "
            "cannot start or end with a hyphen.")
    return project


# ---------------------------------------------------------------------------
# wrangler
# ---------------------------------------------------------------------------
def wrangler_path() -> Optional[str]:
    return shutil.which("wrangler")


def _require_wrangler() -> str:
    path = wrangler_path()
    if not path:
        raise DeployError(
            "wrangler is not installed, and it is what talks to Cloudflare.\n"
            "  npm install -g wrangler\n"
            "  wrangler login\n"
            "Then run this again. Nothing has been uploaded.")
    return path


def _credentials_present(env: Optional[dict[str, str]] = None) -> bool:
    """A token in the environment, or a `wrangler login` on this machine."""
    env = os.environ if env is None else env
    if env.get("CLOUDFLARE_API_TOKEN"):
        return True
    home = env.get("HOME") or ""
    return bool(home) and os.path.exists(
        os.path.join(home, ".config", ".wrangler"))


def deploy(site_dir: str, project: str, *, dry_run: bool = False,
           branch: str = "preview", runner=subprocess.run) -> Deployment:
    """Upload `site_dir` to Cloudflare Pages and return the URL it landed on.

    `runner` is injected so the tests can exercise every branch of this — the
    argv we build, the URL parsing, the failure paths — without a Cloudflare
    account and without uploading anything anywhere.
    """
    problems = preflight(site_dir)
    if problems:
        raise DeployError("This site is not safe to publish:\n"
                          + "\n".join(f"  - {p}" for p in problems))

    # Stage even for a dry run, so what the operator is shown is the real
    # answer rather than a prediction of it.
    with tempfile.TemporaryDirectory(prefix="leadsmith-preview-") as staged:
        published, withheld = stage(site_dir, staged)
        if "index.html" not in published:
            raise DeployError(f"Nothing publishable in {site_dir}.")
        files, bytes_ = contents(staged)

        if dry_run:
            return Deployment(url="", project=project, files=files,
                              bytes=bytes_, dry_run=True,
                              published=published, withheld=withheld)

        binary = _require_wrangler()
        argv = [binary, "pages", "deploy", staged,
                f"--project-name={project}", f"--branch={branch}"]

        try:
            done = runner(argv, capture_output=True, text=True, timeout=TIMEOUT)
        except subprocess.TimeoutExpired as exc:
            raise DeployError(
                f"wrangler did not finish within {TIMEOUT}s. The deployment "
                f"may or may not have gone through — check "
                f"https://dash.cloudflare.com before running this again."
            ) from exc
        except OSError as exc:
            raise DeployError(f"Could not run wrangler: {exc}") from exc

        output = (done.stdout or "") + (done.stderr or "")
        if done.returncode != 0:
            hint = ""
            if not _credentials_present():
                hint = "\nTry `wrangler login`, or set CLOUDFLARE_API_TOKEN."
            raise DeployError(
                f"wrangler failed (exit {done.returncode}).{hint}\n\n"
                + output.strip()[-1200:])

        found = _URL.search(output)
        if not found:
            raise DeployError(
                "wrangler reported success but printed no *.pages.dev URL, so "
                "there is nothing to show anyone. Its output was:\n\n"
                + output.strip()[-1200:])

        return Deployment(url=found.group(), project=project, files=files,
                          bytes=bytes_, published=published, withheld=withheld)


def take_down(project: str, url: str, runner=subprocess.run) -> str:
    """Delete one deployment.

    Cloudflare identifies deployments by id, not by URL, and the id is the
    first label of the hostname — `a1b2c3d4.project.pages.dev`. That is a
    property of how Pages builds preview URLs, so it is worth stating rather
    than deriving silently.
    """
    binary = _require_wrangler()
    host = re.sub(r"^https?://", "", url).split("/")[0]
    deployment_id = host.split(".")[0]
    if not deployment_id or "." not in host:
        raise DeployError(f"'{url}' does not look like a Pages preview URL.")

    done = runner([binary, "pages", "deployment", "delete", deployment_id,
                   f"--project-name={project}", "--yes"],
                  capture_output=True, text=True, timeout=TIMEOUT)
    output = (done.stdout or "") + (done.stderr or "")
    if done.returncode != 0:
        raise DeployError(
            f"Could not delete deployment {deployment_id}:\n\n"
            + output.strip()[-800:])
    return deployment_id
