"""What the buttons do.

The bridge between the window and the tool. Every function here takes the
window, does the talking-to-the-operator part on the UI thread, and hands the
slow part to a worker.

Nothing in this file contains business logic. Scanning is `prospect.run`,
copy is `generate.write_copy`, publishing is `deploy.deploy` — exactly what the
CLI calls, with the same guards. Two front ends, one core; if a rule lived here
instead, the CLI would quietly not have it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import webbrowser
from contextlib import contextmanager
from typing import Any, Optional

import billing
import config
import db
import deploy
import generate
import offboard
import pitch
import prospect
from gui.work import Job

# What one page of copy costs, near enough, for the confirmation dialog. The
# real figure is recorded per generation and shown on the Money page; this is
# only ever used to decide whether to stop and ask.
COPY_ESTIMATE_USD = 0.03


@contextmanager
def worker_db():
    """A database connection belonging to the thread that will use it.

    SQLite refuses a connection across threads, and the window's `con` is made
    on the UI thread. Every `work()` below runs on a worker, so it opens its
    own and closes it on the way out — including when the work raises, or a
    failed scan would leak a handle per attempt.

    A shared connection with `check_same_thread=False` would be fewer lines and
    the wrong trade: the UI thread reads from `window.con` to refresh pages
    while a job is mid-transaction, and the two would interleave on one
    connection. Separate connections to one file is what SQLite is good at.
    """
    con = db.connect()
    try:
        yield con
    finally:
        con.close()


def copywriter(window) -> dict[str, Any]:
    """Which service writes the copy, resolved from settings.

    Reads the `copy` block, falling back to the old top-level
    `anthropic_api_key` so a config written before there was a choice keeps
    working untouched.
    """
    return generate.provider_from_config(window.cfg)


def _keys(window) -> dict[str, Optional[str]]:
    return {"google": config.api_key(window.cfg),
            "copy": copywriter(window).get("api_key") or None}


def open_path(path: str) -> None:
    """Open a file with whatever the desktop uses.

    `os.startfile` on Windows, which is the real target; the others are so the
    app is developable on a Mac or a Linux box.
    """
    if sys.platform == "win32":
        os.startfile(path)                                # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------
def scan(window, town: str, radius_km: float, cell_m: int, *,
         dry_run: bool, page=None) -> None:
    try:
        key = config.api_key(window.cfg)
    except Exception as exc:                                      # noqa: BLE001
        window.error(str(exc))
        return

    con = window.con
    cached = db.geocode_cached(con, town) if town else None

    if dry_run:
        cells = prospect.grid(44.0, -79.5, radius_km * 1000, cell_m)
        batches = -(-len(prospect.DEFAULT_TYPES) // prospect.TYPE_BATCH_SIZE)
        calls = len(cells) * batches
        if page:
            page.show_result([
                ("Map cells", f"{len(cells):,}"),
                ("Searches", f"{calls:,}"),
                ("Estimate", f"${calls * prospect.COST_PER_CALL:,.2f}"),
                ("Geocoding", "already cached — free"
                              if cached else
                              f"one extra call, ${prospect.COST_PER_GEOCODE:.3f}"),
            ], "Priced — nothing sent")
        window.toast("Priced. Nothing was sent and nothing was spent.")
        return

    cells = prospect.grid(44.0, -79.5, radius_km * 1000, cell_m)
    batches = -(-len(prospect.DEFAULT_TYPES) // prospect.TYPE_BATCH_SIZE)
    estimate = len(cells) * batches * prospect.COST_PER_CALL
    if not cached:
        estimate += prospect.COST_PER_GEOCODE

    if not window.confirm(
            "About to spend money",
            f"Searching {radius_km:g} km around {town} will make about "
            f"{len(cells) * batches:,} Google searches.\n\n"
            f"Estimated cost: ${estimate:,.2f} USD\n"
            f"Spent in the last 30 days: ${db.spend_since(con, 30):,.2f}\n\n"
            f"Go ahead?",
            f"Spend ${estimate:,.2f}"):
        return

    def work(job: Job):
        with worker_db() as wcon:
            # `geocode` does its own 30-day caching when handed the connection,
            # so re-scanning the same town this week costs nothing extra.
            job.report(0, 0, f"Finding {town}…")
            region = window.cfg.get("defaults", {}).get("region_code", "CA")
            lat, lng, label = prospect.geocode(key, town, region, wcon)
            wcon.commit()

            plan = prospect.plan(lat, lng, radius_km, cell_m, label=label)
            job.report(0, plan.calls, f"Searching {label}…")

            # Three parameters because three is what `prospect.ProgressFn`
            # sends: calls done, calls planned, businesses found so far. Taking
            # two raised TypeError on the first search of every real scan.
            # Returning the report tells the scan whether Stop was pressed.
            def progress(done: int, total: int, found: int) -> bool:
                return job.report(
                    done, total,
                    f"Searching {label} — {done} of {total}, "
                    f"{found} businesses so far")

            result = prospect.run(key, wcon, plan, progress)
            wcon.commit()
            return plan, result

    def done(payload):
        plan, result = payload
        if page:
            lines = [
                ("Businesses seen", f"{result.seen:,}"),
                ("New leads", f"{result.new_leads:,}"),
                ("Already known", f"{max(0, result.seen - result.new_leads):,}"),
                ("Searches made", f"{result.calls_made:,}"
                                  + (f" ({result.calls_failed} failed)"
                                     if result.calls_failed else "")),
                ("Actually spent", f"${result.actual_cost:,.2f}"
                                   + (f"   (estimated ${plan.est_cost:,.2f})"
                                      if abs(result.actual_cost - plan.est_cost) > 0.005
                                      else "")),
            ]
            # A scan that stopped early still cost money, so saying only how
            # many leads it found would be the wrong half of the truth.
            if result.aborted:
                lines.append(("Stopped early", result.aborted))
            elif result.errors:
                lines.append(("First failure", result.errors[0]))
            # The CLI warns about this and the app was silent about it: cells
            # that came back full were truncated by Google, so there are
            # businesses in them nobody has seen.
            if result.saturated_cells:
                lines.append((
                    "Areas too busy to see",
                    f"{result.saturated_cells:,} of {len(plan.cells):,} cells "
                    f"filled up — re-run at {max(300, int(plan.cell_m // 2))}m "
                    f"to see what was cut off"))
            page.show_result(
                lines,
                f"Scan stopped early — {plan.label}" if result.aborted
                else f"Scanned {plan.label}")

        if result.aborted:
            # Stay on this page: the reason is in the card above, and moving
            # the operator to Today would hide the only account of what
            # happened to the money they just spent.
            window.toast(f"Scan stopped early. Kept {result.new_leads} new "
                         f"leads. Spent ${result.actual_cost:,.2f}.")
            return
        window.toast(f"Found {result.new_leads} new leads. "
                     f"Spent ${result.actual_cost:,.2f}.")
        window.go(0)

    window.run_job(Job(work), status="Scanning…", on_done=done)


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------
def build_site(window, lead: dict[str, Any], *, launch: bool = False) -> None:
    con, cfg = window.con, window.cfg
    place_id = lead["place_id"]

    try:
        saved = generate.load_content(place_id)
    except generate.ContentError as exc:
        window.error(str(exc))
        return

    needs_copy = not (saved and saved.get("copy"))
    spec = copywriter(window)
    if needs_copy:
        if not spec.get("api_key"):
            window.error(
                f"No key for {spec['label']}, so the copy cannot be written.",
                "Add it in Settings → Copywriter. Everything else in the app "
                "works without it — this is the one step that needs it.")
            return
        # A free tier has nothing to warn about, and a dialog that says "about
        # to spend money" over a $0.00 charge teaches the operator to click
        # through the one that matters. The paid path keeps its confirmation.
        if not generate.provider_is_free(spec) and not window.confirm(
                "About to spend money",
                f"{spec['label']} will write the copy for {lead['name']}.\n\n"
                f"Estimated cost: ${COPY_ESTIMATE_USD:,.2f} USD\n"
                f"Copy written in the last 30 days: "
                f"${db.generation_spend(con, 30):,.2f}\n\n"
                f"It is written once and cached — rebuilding later is free.",
                "Write the copy"):
            return

    def work(job: Job):
        content = (saved or {}).get("copy")
        verified = (saved or {}).get("verified_facts") or []
        photos = (saved or {}).get("photos") or []
        template = generate.template_for(lead.get("category"))
        city = cfg.get("business", {}).get("home_city", "")

        if content is None:
            job.report(0, 0, f"Writing copy for {lead['name']}…")
            content, usage = generate.write_copy(
                lead, city=city, provider=spec, verified=verified)
            generate.save_content(place_id, {
                "version": generate.CONTENT_VERSION,
                "place_id": place_id,
                "template": template,
                "model": generate.MODEL,
                "generated_at": db.now(),
                "cost_usd": round(usage.cost, 4),
                "facts": generate.facts_for(lead, city),
                "verified_facts": verified,
                "copy": content,
            })
            with worker_db() as wcon:
                db.record_generation(
                    wcon, place_id, model=generate.MODEL, template=template,
                    attempts=usage.attempts, input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cache_write_tokens=usage.cache_write_tokens,
                    cache_read_tokens=usage.cache_read_tokens, cost=usage.cost)
                wcon.commit()

        job.report(0, 0, "Rendering the page…")
        site = generate.render(
            lead, {**content, "verified_facts": verified, "photos": photos},
            template=template, preview=not launch, operator=cfg.get("business", {}),
            city_hint=city)
        paths = generate.write_site(place_id, site)
        with worker_db() as wcon:
            db.set_site_dir(wcon, place_id, generate.site_dir(place_id))
            wcon.commit()
        return paths["index.html"]

    def done(path):
        window.toast(f"Built {lead['name']}.")
        if window.confirm("Site built",
                          f"{lead['name']} is built.\n\nOpen it now?", "Open"):
            open_path(path)

    window.run_job(Job(work), status="Building…", on_done=done)


def open_site(window, lead: dict[str, Any]) -> None:
    path = os.path.join(generate.site_dir(lead["place_id"]), "index.html")
    if not os.path.exists(path):
        window.error("That site has not been built yet.")
        return
    open_path(path)


# ---------------------------------------------------------------------------
# Preview and pitch
# ---------------------------------------------------------------------------
def preview_site(window, lead: dict[str, Any]) -> None:
    site_dir = generate.site_dir(lead["place_id"])
    problems = deploy.preflight(site_dir)
    if problems:
        window.error("This site is not ready to publish.",
                     "\n".join(f"- {p}" for p in problems))
        return
    if not deploy.wrangler_path():
        window.error(
            "wrangler is not installed, and it is what talks to Cloudflare.",
            "Install Node.js, then run:\n\n    npm install -g wrangler\n"
            "    wrangler login\n\nThen try again. Nothing has been uploaded.")
        return

    if not window.confirm(
            "About to publish",
            f"A page carrying {lead['name']}'s name, address and phone number "
            f"goes onto the public internet.\n\n"
            f"It will not be indexed by Google, but anyone with the link can "
            f"open it.\n\nPublish it?", "Publish"):
        return

    def work(job: Job):
        job.report(0, 0, "Uploading to Cloudflare…")
        project = deploy.project_for(None)
        result = deploy.deploy(site_dir, project)
        with worker_db() as wcon:
            db.set_preview(wcon, lead["place_id"], result.url, result.project)
            wcon.commit()
        return result

    def done(result):
        from gui.dialogs import PreviewDialog
        PreviewDialog(window, lead["name"], result.url).exec()

    window.run_job(Job(work), status="Publishing…", on_done=done)


def leave_behind(window, lead: dict[str, Any]) -> None:
    try:
        saved = generate.load_content(lead["place_id"])
    except generate.ContentError as exc:
        window.error(str(exc))
        return
    if not saved or not saved.get("copy"):
        window.error("Build the site first — the leave-behind shows their page.")
        return

    row = db.get(window.con, lead["place_id"])
    html = pitch.build(
        dict(row), saved["copy"],
        preview_url=(row["preview_url"] if "preview_url" in row.keys() else "") or "",
        density=db.market_density(window.con, lead.get("category")),
        operator=window.cfg.get("business", {}),
        pricing=window.cfg.get("pricing"))
    path = pitch.write(lead["place_id"], html)
    open_path(path)
    window.toast("Leave-behind opened — print it to PDF from the browser.")


def log_outcome(window, lead: dict[str, Any], outcome: str) -> None:
    from gui.dialogs import OutcomeDialog
    dialog = OutcomeDialog(window, lead["name"], outcome)
    if not dialog.exec():
        return
    stage = db.log_touch(window.con, lead["place_id"], dialog.channel(),
                         outcome, dialog.note())
    window.con.commit()

    message = f"{lead['name']}: {outcome.replace('-', ' ')}"
    if stage == "dead" and outcome == "no-answer":
        message += f" — closed after {db.NO_ANSWER_LIMIT} attempts with no answer"
    elif outcome == "no-answer":
        left = db.NO_ANSWER_LIMIT - db.consecutive_no_answers(
            window.con, lead["place_id"])
        message += f" — {left} more and it closes automatically"
    else:
        message += f" — now {stage}"
    window.toast(message)
    window.refresh()


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------
def export_client(window, lead: dict[str, Any], directory: str) -> Optional[str]:
    row = db.get(window.con, lead["place_id"])
    return offboard.write(dict(row), directory,
                          operator=window.cfg.get("business", {}))


def sync_billing(window, on_done=None) -> None:
    key = (window.cfg.get("stripe_secret_key") or "").strip()
    if not key:
        window.error("No Stripe key.",
                     "Add `stripe_secret_key` in Settings. Read-only is "
                     "enough — nothing here writes to Stripe.")
        return

    def work(job: Job):
        job.report(0, 0, "Asking Stripe…")
        with worker_db() as wcon:
            result = billing.sync(wcon, key)
            wcon.commit()
        return result

    window.run_job(Job(work), status="Checking Stripe…", on_done=on_done)
