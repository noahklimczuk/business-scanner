"""Publishing. The one part of the tool that puts a real business's name on the
public internet before they have agreed to anything, so the tests are mostly
about what it refuses to do."""
import json
import os
import subprocess
from types import SimpleNamespace

import pytest

import deploy

PREVIEW_HTML = ('<!doctype html><html><head>'
                '<meta name="robots" content="noindex,nofollow">'
                '</head><body>Hi</body></html>')
LAUNCH_HTML = "<!doctype html><html><head></head><body>Hi</body></html>"


def site(tmp_path, html=PREVIEW_HTML, robots="User-agent: *\nDisallow: /\n",
         extras=None):
    d = tmp_path / "site"
    d.mkdir(exist_ok=True)
    (d / "index.html").write_text(html)
    (d / "robots.txt").write_text(robots)
    for name, body in (extras or {}).items():
        (d / name).write_text(body)
    return str(d)


def ran(stdout="", stderr="", code=0):
    """A stand-in for subprocess.run that records the argv it was given."""
    calls = []

    def runner(argv, **kw):
        calls.append(argv)
        return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)

    runner.calls = calls
    return runner


# ---------------------------------------------------------------------------
# Preflight: what it refuses
# ---------------------------------------------------------------------------
def test_a_launch_build_is_refused(tmp_path):
    problems = deploy.preflight(site(tmp_path, html=LAUNCH_HTML))
    assert any("noindex" in p for p in problems)


def test_the_check_reads_the_files_not_the_intent(tmp_path):
    # `build --launch` writes an indexable page into the same directory. The
    # only honest way to know which one is sitting there is to open it.
    path = site(tmp_path)
    assert deploy.preflight(path) == []
    (tmp_path / "site" / "index.html").write_text(LAUNCH_HTML)
    assert deploy.preflight(path)


def test_a_missing_or_permissive_robots_is_refused(tmp_path):
    path = site(tmp_path, robots="User-agent: *\nAllow: /\n")
    assert any("robots" in p for p in deploy.preflight(path))
    os.remove(os.path.join(path, "robots.txt"))
    assert any("robots" in p for p in deploy.preflight(path))


def test_an_unbuilt_lead_says_to_build_it(tmp_path):
    problems = deploy.preflight(str(tmp_path / "nothing-here"))
    assert len(problems) == 1 and "leadsmith build" in problems[0]


def test_an_image_the_page_needs_but_that_is_missing_is_caught(tmp_path):
    html = PREVIEW_HTML.replace("Hi", '<img src="hero.jpg" alt="A roof">')
    problems = deploy.preflight(site(tmp_path, html=html))
    assert any("hero.jpg" in p for p in problems)


def test_an_image_that_is_present_is_fine(tmp_path):
    html = PREVIEW_HTML.replace("Hi", '<img src="hero.jpg" alt="A roof">')
    path = site(tmp_path, html=html, extras={"hero.jpg": "not really a jpeg"})
    assert deploy.preflight(path) == []


# ---------------------------------------------------------------------------
# Staging: what actually goes public
# ---------------------------------------------------------------------------
def test_the_working_files_are_never_uploaded(tmp_path):
    # sites/<place_id>/ is our working directory, not a web root. content.json
    # holds the draft copy and `verified_facts` — things the owner said in a
    # conversation — and content.rejected.json holds the claims the fabrication
    # check caught. Uploading the directory serves all of it at /content.json.
    path = site(tmp_path, extras={
        "content.json": json.dumps({"verified_facts": ["owner said they are licensed"]}),
        "content.rejected.json": "{}",
        "leave-behind.html": "<html>price and margin</html>",
        "notes.txt": "he seemed keen",
    })
    published, withheld = deploy.stage(path, str(tmp_path / "out"))
    assert published == ["index.html", "robots.txt"]
    assert set(withheld) == {"content.json", "content.rejected.json",
                             "leave-behind.html", "notes.txt"}
    assert sorted(os.listdir(tmp_path / "out")) == ["index.html", "robots.txt"]


def test_owner_photographs_are_published(tmp_path):
    path = site(tmp_path, extras={"hero.jpg": "x", "shop.PNG": "y"})
    published, _ = deploy.stage(path, str(tmp_path / "out"))
    assert "hero.jpg" in published and "shop.PNG" in published


def test_an_unrecognised_file_type_is_withheld_rather_than_published(tmp_path):
    # An allowlist, so a file nobody has thought about does not go public by
    # default. That is the whole point of it being an allowlist.
    path = site(tmp_path, extras={"invoice.pdf": "x", "backup.zip": "y",
                                  "index.html.bak": "z"})
    published, withheld = deploy.stage(path, str(tmp_path / "out"))
    assert published == ["index.html", "robots.txt"]
    assert set(withheld) == {"invoice.pdf", "backup.zip", "index.html.bak"}


def test_a_dry_run_reports_the_real_staging_not_a_guess(tmp_path):
    path = site(tmp_path, extras={"content.json": "{}"})
    result = deploy.deploy(path, "proj", dry_run=True)
    assert result.dry_run and result.url == ""
    assert result.published == ["index.html", "robots.txt"]
    assert result.withheld == ["content.json"]
    assert result.files == 2


def test_deploy_refuses_before_it_uploads_anything(tmp_path):
    runner = ran()
    with pytest.raises(deploy.DeployError):
        deploy.deploy(site(tmp_path, html=LAUNCH_HTML), "proj", runner=runner)
    assert runner.calls == []


# ---------------------------------------------------------------------------
# wrangler
# ---------------------------------------------------------------------------
def test_the_url_is_taken_from_wranglers_output(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy, "_require_wrangler", lambda: "/usr/bin/wrangler")
    runner = ran(stdout="✨ Deployment complete! Take a peek over at "
                        "https://a1b2c3d4.leadsmith-previews.pages.dev\n")
    result = deploy.deploy(site(tmp_path), "leadsmith-previews", runner=runner)
    assert result.url == "https://a1b2c3d4.leadsmith-previews.pages.dev"
    argv = runner.calls[0]
    assert argv[1:3] == ["pages", "deploy"]
    assert "--project-name=leadsmith-previews" in argv
    # The staged copy, never the working directory.
    assert argv[3] != str(tmp_path / "site")


def test_success_with_no_url_is_treated_as_a_failure(tmp_path, monkeypatch):
    # "It worked but there is nothing to show anyone" is not a success — the
    # operator is standing outside the shop waiting for a URL.
    monkeypatch.setattr(deploy, "_require_wrangler", lambda: "/usr/bin/wrangler")
    with pytest.raises(deploy.DeployError, match="no \\*.pages.dev URL"):
        deploy.deploy(site(tmp_path), "proj", runner=ran(stdout="Uploaded 2 files"))


def test_a_wrangler_failure_keeps_its_own_error_message(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy, "_require_wrangler", lambda: "/usr/bin/wrangler")
    runner = ran(stderr="Authentication error [code: 10000]", code=1)
    with pytest.raises(deploy.DeployError) as excinfo:
        deploy.deploy(site(tmp_path), "proj", runner=runner)
    assert "code: 10000" in str(excinfo.value)


def test_a_timeout_does_not_claim_to_know_what_happened(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy, "_require_wrangler", lambda: "/usr/bin/wrangler")

    def runner(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 180)

    with pytest.raises(deploy.DeployError, match="may or may not"):
        deploy.deploy(site(tmp_path), "proj", runner=runner)


def test_a_missing_wrangler_explains_the_install(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy, "wrangler_path", lambda: None)
    with pytest.raises(deploy.DeployError, match="npm install -g wrangler"):
        deploy.deploy(site(tmp_path), "proj")


# ---------------------------------------------------------------------------
# Project names and takedown
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad", ["-leading", "trailing-", "under_scores",
                                 "has spaces", "x" * 60])
def test_an_invalid_project_name_fails_here_not_after_the_upload(bad):
    # Cloudflare would reject these too, but only after the files are uploaded.
    with pytest.raises(deploy.DeployError):
        deploy.project_for(bad)


@pytest.mark.parametrize("given", ["Capitals", "  spaced-out  ", ""])
def test_a_name_that_only_needs_tidying_is_tidied_rather_than_refused(given):
    # Cloudflare project names are lowercase. Shouting at the operator about a
    # capital letter we can simply fix is not a useful thing to do.
    assert deploy.project_for(given) == (given.strip().lower()
                                         or deploy.DEFAULT_PROJECT)


def test_the_default_project_is_valid():
    assert deploy.project_for(None) == deploy.DEFAULT_PROJECT


def test_takedown_uses_the_deployment_id_from_the_hostname(monkeypatch):
    monkeypatch.setattr(deploy, "_require_wrangler", lambda: "/usr/bin/wrangler")
    runner = ran(stdout="deleted")
    got = deploy.take_down("proj", "https://a1b2c3d4.proj.pages.dev", runner=runner)
    assert got == "a1b2c3d4"
    assert "a1b2c3d4" in runner.calls[0]


def test_takedown_rejects_something_that_is_not_a_preview_url(monkeypatch):
    monkeypatch.setattr(deploy, "_require_wrangler", lambda: "/usr/bin/wrangler")
    with pytest.raises(deploy.DeployError):
        deploy.take_down("proj", "https://example", runner=ran())
