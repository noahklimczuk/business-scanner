#!/usr/bin/env python3
"""Run axe-core against a built site, in a real browser.

NOT part of the tool. Nothing in leadsmith imports this, `build` does not know
it exists, and deleting it breaks nothing — same footing as tools/calibrate.py.
It needs two things that are deliberately not dependencies of leadsmith, which
is why it lives here rather than behind a CLI command:

    pip install playwright && playwright install chromium
    npm install axe-core

tests/test_a11y.py is the guarantee; it runs on every commit and needs neither
of those. This is for the other job: producing evidence. Ontario's AODA makes
WCAG 2.0 AA a legal obligation for the client's site, and an operator who is
asked — by a client, by a client's lawyer, by a complaint — wants a report from
a tool the asker recognises, not an assurance that the tests pass.

It only checks the `wcag2a` and `wcag2aa` rule tags. That is exactly what AODA
cites: not WCAG 2.1, not axe's best-practice rules. A pass here is the legal
bar and nothing softer, and it is not the whole bar either — roughly a third of
the criteria cannot be checked by a machine at all. The ones that matter here
are alt text that is accurate rather than merely present, and copy that reads
plainly. The first is why `build` refuses to ship a photo without alt text; the
second is what the copy prompt is for.

Each page is audited four ways, because the interesting failures hide in three
of them:

    reduced motion / full motion   — the static page and the animated one
    390px / 1440px                 — a phone and a laptop
    top of page / fully scrolled   — scroll-driven reveals only settle once
                                     they have been scrolled through

and always *after* the entry animations have finished. axe reads composited
pixels, so auditing at load measures a fade in progress and reports every
fading heading as a contrast failure. That is the scanner sampling a
transition, not a finding — but run it at t=0 and you will see it, so it is
worth knowing why before it lands in someone's inbox.

Usage
-----
    python tools/audit_a11y.py sites/ChIJ.../index.html
    python tools/audit_a11y.py 'sites/*/index.html' --json report.json
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import sys

TAGS = ["wcag2a", "wcag2aa"]
SETTLE_MS = 1200
VIEWPORTS = ((390, 844, "phone"), (1440, 900, "laptop"))


def _axe_source() -> str:
    """axe.min.js from wherever npm put it."""
    candidates = [
        os.path.join(os.getcwd(), "node_modules/axe-core/axe.min.js"),
        os.path.join(os.path.dirname(__file__), "node_modules/axe-core/axe.min.js"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return open(path, encoding="utf-8").read()
    raise SystemExit("axe-core not found. Run: npm install axe-core")


async def _launch(pw, explicit: str | None):
    """Playwright's own Chromium if it is there, otherwise one we can find.

    A pinned playwright often wants a browser build that is not installed —
    including in CI images that ship Chromium at a fixed path. Failing with
    "run playwright install" when a perfectly good browser is on disk wastes
    the operator's afternoon, so look before giving up.
    """
    candidates = [explicit] if explicit else []
    candidates += [os.environ.get("CHROMIUM_PATH")]
    candidates += sorted(glob.glob("/opt/pw-browsers/chromium*/chrome-linux/chrome"))
    candidates += ["/usr/bin/chromium", "/usr/bin/chromium-browser",
                   "/usr/bin/google-chrome"]

    if not explicit:
        try:
            return await pw.chromium.launch()
        except Exception:                                   # noqa: BLE001
            pass
    for path in candidates:
        if path and os.path.exists(path):
            # --no-sandbox because these audits run in containers as root more
            # often than not, and there is no untrusted content here.
            return await pw.chromium.launch(executable_path=path,
                                            args=["--no-sandbox"])
    raise SystemExit(
        "No Chromium found. Either `playwright install chromium`, or point "
        "--browser at an existing binary.")


async def _audit_page(page, axe: str) -> dict:
    await page.add_script_tag(content=axe)
    return await page.evaluate(
        "async () => await axe.run(document, %s)"
        % json.dumps({"runOnly": {"type": "tag", "values": TAGS}}))


async def _scroll_through(page) -> None:
    await page.evaluate(
        "async () => { const h = document.body.scrollHeight;"
        " for (let y = 0; y < h; y += 400) { window.scrollTo(0, y);"
        " await new Promise(r => requestAnimationFrame(r)); }"
        " window.scrollTo(0, h); }")


async def run(paths: list[str], report_path: str | None,
              browser_path: str | None = None) -> int:
    from playwright.async_api import async_playwright

    axe = _axe_source()
    findings, checked = [], 0

    async with async_playwright() as pw:
        browser = await _launch(pw, browser_path)
        for path in paths:
            url = "file://" + os.path.abspath(path)
            for width, height, device in VIEWPORTS:
                for motion in ("reduce", "no-preference"):
                    context = await browser.new_context(
                        viewport={"width": width, "height": height},
                        reduced_motion=motion)
                    page = await context.new_page()
                    await page.goto(url)
                    await page.wait_for_timeout(SETTLE_MS)

                    for position in ("top", "scrolled"):
                        if position == "scrolled":
                            await _scroll_through(page)
                            await page.wait_for_timeout(600)
                        result = await _audit_page(page, axe)
                        checked += 1
                        where = f"{os.path.basename(path)} {device} " \
                                f"motion={motion} {position}"
                        for violation in result["violations"]:
                            findings.append({"page": path, "device": device,
                                             "motion": motion, "at": position,
                                             "id": violation["id"],
                                             "impact": violation["impact"],
                                             "help": violation["help"],
                                             "nodes": [n["html"][:200]
                                                       for n in violation["nodes"]]})
                            print(f"{where}\n  [{violation['impact']}] "
                                  f"{violation['id']}: {violation['help']}")
                            for node in violation["nodes"][:4]:
                                print(f"      {node['html'][:120]}")
                    await context.close()
            print(f"{os.path.basename(path):28} audited")
        await browser.close()

    print(f"\n{checked} audits over {len(paths)} page(s): {len(findings)} violations.")
    if not findings:
        print("Clean against WCAG 2.0 A and AA. Note that this covers the "
              "criteria a machine can check —\nalt text that is accurate rather "
              "than merely present, and copy a stranger can follow, are not\n"
              "among them and stay the operator's job.")
    if report_path:
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump({"audits": checked, "tags": TAGS, "violations": findings},
                      fh, indent=2)
        print(f"Report: {report_path}")
    return 1 if findings else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pages", nargs="+",
                    help="built HTML files, or globs like 'sites/*/index.html'")
    ap.add_argument("--json", dest="report", default=None,
                    help="write the full findings to this file")
    ap.add_argument("--browser", default=None,
                    help="path to a Chromium binary, if Playwright cannot find one")
    args = ap.parse_args()

    paths: list[str] = []
    for pattern in args.pages:
        paths.extend(sorted(glob.glob(pattern)) or
                     ([pattern] if os.path.exists(pattern) else []))
    if not paths:
        raise SystemExit(f"No pages matched: {' '.join(args.pages)}")

    return asyncio.run(run(paths, args.report, args.browser))


if __name__ == "__main__":
    sys.exit(main())
