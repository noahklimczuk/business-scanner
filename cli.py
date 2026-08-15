"""Leadsmith CLI. Every command lives here; the modules stay presentation-free."""
from __future__ import annotations

import json as jsonlib
import os
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

import billing as billing_mod
import board as board_mod
import config
import db
import deploy
import enrich as enrich_mod
import generate as generate_mod
import invoice as invoice_mod
import offboard
import pitch
import prospect
import qr as qr_mod
import search

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="Find businesses with no website, score them, and work the pipeline.")
console = Console()

# Cell count barely moves with latitude, so a dry run can plan without spending
# a geocode call. Southern Ontario; override with --lat/--lng for anywhere else.
DRY_RUN_LAT = 44.0

BAND_COLOUR = {"new": "cyan", "queued": "blue", "contacted": "yellow",
               "interested": "magenta", "sold": "green", "live": "bright_green",
               "dead": "bright_black"}
KIND_LABEL = {"none": "no site", "social": "social only", "defunct": "dead builder page"}


def _fail(message: str) -> None:
    console.print(Panel(message, title="stopped", border_style="red", expand=False))
    raise typer.Exit(1)


# ---------------------------------------------------------------------------
@app.command()
def scan(
    address: Optional[str] = typer.Option(None, "--address", "-a",
                                          help='Town or address, e.g. "Newmarket, ON".'),
    lat: Optional[float] = typer.Option(None, help="Skip geocoding with an explicit centre."),
    lng: Optional[float] = typer.Option(None, help="Skip geocoding with an explicit centre."),
    radius: Optional[float] = typer.Option(None, "--radius", "-r", help="Radius in km."),
    cell: Optional[int] = typer.Option(None, "--cell",
                                       help="Cell size in metres: 900 commercial strip, "
                                            "1500 suburban, 2500 rural."),
    types: Optional[str] = typer.Option(None, help="Comma-separated Places types "
                                                   "(default: the trades list)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Price the scan, send nothing."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the spend confirmation."),
) -> None:
    """Search a radius for businesses with no website and score them as leads."""
    try:
        cfg = config.load()
    except config.ConfigError as exc:
        _fail(str(exc))
        return

    defaults = cfg["defaults"]
    radius_km = radius if radius is not None else defaults["radius_km"]
    cell_m = cell if cell is not None else defaults["cell_m"]
    chosen_types = [t.strip() for t in types.split(",") if t.strip()] if types else None

    if radius_km <= 0 or cell_m <= 0:
        _fail("Radius and cell size must both be positive.")
    if radius_km > 50:
        _fail("Nearby Search caps out at a 50km radius. Run several scans instead.")

    con = db.connect()
    label = address or f"{lat},{lng}"
    geocoded = True

    if lat is None or lng is None:
        if not address:
            _fail("Give me somewhere to look: --address \"Newmarket, ON\" or --lat/--lng.")
        cached = db.geocode_cached(con, address)
        if cached:
            lat, lng, label = cached["lat"], cached["lng"], cached["formatted"] or address
        elif dry_run:
            # A geocode is a paid call, and --dry-run promises to spend nothing.
            lat, lng, geocoded = DRY_RUN_LAT, -79.5, False
        else:
            try:
                lat, lng, label = prospect.geocode(
                    config.api_key(cfg), address, defaults["region_code"], con)
            except (prospect.PlacesError, config.ConfigError) as exc:
                _fail(str(exc))
                return

    sp = prospect.plan(lat, lng, radius_km, cell_m, chosen_types, label)
    sp.geocoded = geocoded
    _print_plan(sp, dry_run)

    if dry_run:
        console.print("[dim]dry run — nothing sent, nothing spent[/dim]")
        return

    threshold = float(defaults["confirm_above_usd"])
    if sp.est_cost > threshold and not yes:
        if not typer.confirm(f"  That is over ${threshold:,.2f}. Continue?", default=False):
            console.print("[dim]cancelled[/dim]")
            return

    try:
        key = config.api_key(cfg)
    except config.ConfigError as exc:
        _fail(str(exc))
        return

    with Progress(TextColumn("[progress.description]{task.description}"),
                  BarColumn(), TextColumn("{task.completed}/{task.total} calls"),
                  TimeElapsedColumn(), console=console) as progress:
        task = progress.add_task("scanning", total=sp.calls)

        def on_progress(done: int, total: int, found: int) -> None:
            progress.update(task, completed=done,
                            description=f"scanning · {found} businesses")

        res = prospect.run(key, con, sp, on_progress)

    _print_result(con, sp, res)
    con.close()


def _print_plan(sp: prospect.ScanPlan, dry_run: bool) -> None:
    t = Table(box=None, show_header=False, pad_edge=False)
    t.add_column(style="dim")
    t.add_column()
    t.add_row("centre", sp.label if sp.geocoded else
              f"[yellow]assumed {sp.lat}, {sp.lng} (not geocoded — dry run)[/yellow]")
    t.add_row("radius", f"{sp.radius_m / 1000:g} km")
    t.add_row("cells", f"{len(sp.cells)} x {sp.cell_m:g}m")
    t.add_row("types", f"{len(sp.types)} in {len(sp.batches)} batches per cell")
    t.add_row("calls", f"{sp.calls:,}")
    t.add_row("estimate", f"[bold]${sp.est_cost:,.2f} USD[/bold] "
                          f"[dim]at ${prospect.COST_PER_CALL:.3f}/call[/dim]")
    console.print(Panel(t, title="scan plan", border_style="cyan", expand=False))


def _print_result(con, sp: prospect.ScanPlan, res: prospect.ScanResult) -> None:
    if res.aborted:
        console.print(Panel(res.aborted + "\n\nEverything found before that point is saved.",
                            title="scan stopped early", border_style="yellow", expand=False))

    t = Table(box=None, show_header=False, pad_edge=False)
    t.add_column(style="dim")
    t.add_column(justify="right")
    t.add_row("businesses seen", f"{res.unique:,} unique ({res.seen:,} rows)")
    t.add_row("new leads", f"[bold green]{res.new_leads:,}[/bold green]")
    t.add_row("existing refreshed", f"{res.refreshed:,}")
    t.add_row("near-miss (social/dead page)", f"{res.near_miss:,}")
    t.add_row("market rows kept", f"{res.market_rows:,}")
    t.add_row("chains dropped", f"{res.franchises_dropped:,}")
    t.add_row("closed dropped", f"{res.closed_dropped:,}")
    t.add_row("calls made / failed", f"{res.calls_made:,} / {res.calls_failed:,}")
    t.add_row("actual spend", f"[bold]${res.actual_cost:,.2f}[/bold] "
                              f"[dim]est ${sp.est_cost:,.2f}[/dim]")
    t.add_row("last 30 days", f"${db.spend_since(con, 30):,.2f}")
    console.print(Panel(t, title="scan complete", border_style="green", expand=False))

    if res.saturated_cells:
        cov = f"{res.min_coverage_m:,.0f}m" if res.min_coverage_m else "?"
        console.print(
            f"[yellow]![/yellow] {res.saturated_cells} cells returned the full "
            f"{prospect.MAX_RESULTS} results, so they were truncated — the tightest only "
            f"covered {cov} of its {sp.cell_m:g}m radius. Re-run those areas with "
            f"[bold]--cell {max(300, int(sp.cell_m // 2))}[/bold] to see what was cut off."
        )
    if res.errors:
        console.print(f"[yellow]![/yellow] {len(res.errors)} calls failed. "
                      f"First: {res.errors[0]}")
    if res.new_leads:
        console.print("\n  next: [bold]leadsmith list --limit 20[/bold]")


# ---------------------------------------------------------------------------
@app.command()
def enrich(
    limit: int = typer.Option(50, "--limit", "-n", help="Best-scoring leads first."),
    provider: str = typer.Option("ddg", "--provider",
                                 help=f"Social lookup: {', '.join(search.PROVIDERS)}."),
    city: Optional[str] = typer.Option(None, "--city",
                                       help="Disambiguates the search; defaults to "
                                            "business.home_city in config.json."),
    pause: Optional[float] = typer.Option(None, "--pause",
                                          help="Seconds between searches. Raise it if "
                                               "you get rate-limited."),
    force: bool = typer.Option(False, "--force", help="Re-enrich leads already done."),
) -> None:
    """Normalise phones, find social pages, drop chains and duplicates.

    Costs nothing at Google. Social lookup scrapes a search engine — see the
    note at the top of search.py, and use --provider none to skip it.
    """
    try:
        cfg = config.load()
        engine = search.get_provider(provider, pause)
    except (config.ConfigError, ValueError) as exc:
        _fail(str(exc))
        return

    where = city if city is not None else cfg["business"].get("home_city", "")
    con = db.connect()
    pending = db.leads_to_enrich(con, limit, force)
    if not pending:
        console.print("[dim]Nothing to enrich. Everything is done — "
                      "use --force to redo it.[/dim]")
        con.close()
        return

    with Progress(TextColumn("[progress.description]{task.description}"),
                  BarColumn(), TextColumn("{task.completed}/{task.total}"),
                  TimeElapsedColumn(), console=console) as progress:
        task = progress.add_task("enriching", total=len(pending))

        def on_progress(done: int, total: int, name: str) -> None:
            progress.update(task, completed=done, description=f"enriching · {name[:28]}")

        res = enrich_mod.run(con, engine, limit=limit, force=force, city=where,
                             region=cfg["defaults"]["region_code"],
                             progress=on_progress)

    t = Table(box=None, show_header=False, pad_edge=False)
    t.add_column(style="dim")
    t.add_column(justify="right")
    t.add_row("leads processed", f"{res.processed:,}")
    t.add_row("phones normalised", f"{res.phones_normalised:,}")
    t.add_row("phones that do not dial", f"{res.phones_invalid:,}")
    t.add_row("social pages found", f"[bold green]{res.socials_found:,}[/bold green]")
    t.add_row("searches run / failed", f"{res.searches:,} / {res.search_failed:,}")
    t.add_row("chains dropped", f"{res.chains_marked:,}")
    t.add_row("duplicates merged", f"{res.duplicates_marked:,}")
    t.add_row("scores improved", f"{res.improved:,} "
                                 f"[dim]net {res.score_delta:+,} points[/dim]")
    if res.consent_found:
        t.add_row("CASL bases recorded", f"{res.consent_found:,}")
    console.print(Panel(t, title="enrichment complete", border_style="green", expand=False))

    if res.stopped:
        console.print(Panel(res.stopped, title="social lookup stopped",
                            border_style="yellow", expand=False))
    if res.manual_queries:
        console.print("Run these yourself, then record what you find with "
                      "[bold]leadsmith consent[/bold]:")
        for url in res.manual_queries:
            console.print(f"  {url}")
    con.close()


# ---------------------------------------------------------------------------
@app.command()
def build(
    place_id: str,
    regenerate: bool = typer.Option(False, "--regenerate",
                                    help="Write new copy. Costs money; a plain "
                                         "build re-renders the saved copy free."),
    template: Optional[str] = typer.Option(None, "--template",
                                           help=f"Force one of: "
                                                f"{', '.join(generate_mod.TEMPLATES)}"),
    launch: bool = typer.Option(False, "--launch",
                                help="Drop the noindex. Only after they have said yes."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the spend confirmation."),
) -> None:
    """Build the website for one lead into sites/<place_id>/.

    Copy is written once and cached in content.json. Edit that file and re-run
    this command to change wording — there is no CMS and there is not going to
    be one.
    """
    try:
        cfg = config.load()
    except config.ConfigError as exc:
        _fail(str(exc))
        return

    con = db.connect()
    row = db.get(con, place_id)
    if not row:
        _fail(f"No lead with place_id {place_id}. Try: leadsmith list")
        return

    lead = dict(row)
    lead["hours"] = jsonlib.loads(row["hours_json"]) if row["hours_json"] else None
    chosen = template or generate_mod.template_for(lead.get("category"))

    try:
        saved = generate_mod.load_content(place_id)
    except generate_mod.ContentError as exc:
        _fail(str(exc))
        return
    content = (saved or {}).get("copy") if saved else None
    verified = (saved or {}).get("verified_facts") or []
    photos = (saved or {}).get("photos") or []
    usage = None

    if content is None or regenerate:
        spec = generate_mod.provider_from_config(cfg)
        free = generate_mod.provider_is_free(spec)
        estimate = 0.0 if free else 0.03
        console.print(Panel(
            f"{spec['label']} writes the copy for [bold]{lead['name']}[/bold]\n"
            + ("free tier — no charge, but it has daily limits\n" if free
               else f"estimated spend: [bold]${estimate:,.2f} USD[/bold]\n")
            + f"[dim]({spec['model']})[/dim]",
            title="about to build", border_style="cyan", expand=False))
        threshold = float(cfg["defaults"]["confirm_above_usd"])
        if estimate > threshold and not yes:
            if not typer.confirm("  Continue?", default=False):
                console.print("[dim]cancelled[/dim]")
                return
        try:
            with console.status("writing copy…"):
                content, usage = generate_mod.write_copy(
                    lead, city=cfg["business"].get("home_city", ""), provider=spec,
                    verified=(saved or {}).get("verified_facts"))
        except generate_mod.ContentError as exc:
            _fail(str(exc))
            return

        generate_mod.save_content(place_id, {
            "version": generate_mod.CONTENT_VERSION,
            "place_id": place_id,
            "template": chosen,
            "model": generate_mod.MODEL,
            "generated_at": db.now(),
            "cost_usd": round(usage.cost, 4),
            "facts": generate_mod.facts_for(
                lead, cfg["business"].get("home_city", "")),
            # Anything the owner tells you directly goes here; the fabrication
            # check treats it as source data on the next build.
            "verified_facts": (saved or {}).get("verified_facts", []),
            "copy": content,
        })
        db.record_generation(
            con, place_id, model=generate_mod.MODEL, template=chosen,
            attempts=usage.attempts, input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            cache_read_tokens=usage.cache_read_tokens, cost=usage.cost)
        con.commit()

    try:
        site = generate_mod.render(
            lead, {**content, "verified_facts": verified, "photos": photos},
            template=chosen, preview=not launch, operator=cfg["business"],
            city_hint=cfg["business"].get("home_city", ""))
    except generate_mod.ContentError as exc:
        _fail(str(exc))
        return

    paths = generate_mod.write_site(place_id, site)
    db.set_site_dir(con, place_id, generate_mod.site_dir(place_id))
    con.commit()

    size, heaviest = generate_mod.weigh(place_id)
    t = Table(box=None, show_header=False, pad_edge=False)
    t.add_column(style="dim")
    t.add_column()
    t.add_row("business", lead["name"])
    t.add_row("template", chosen)
    t.add_row("accent", f"[bold]{site.palette['accent']}[/bold] "
                        f"[dim]hue {site.palette['hue']}[/dim]")
    over = size > generate_mod.PAGE_BUDGET_BYTES
    weight = (f"[red]{size / 1024:.0f} KB — over the "
              f"{generate_mod.PAGE_BUDGET_BYTES // 1024} KB budget[/red]"
              if over else f"[dim]{size / 1024:.1f} KB[/dim]")
    t.add_row("page", f"{paths['index.html']}  {weight}")
    t.add_row("indexing", "[yellow]noindex + robots disallow[/yellow]" if not launch
                          else "[green]indexable[/green]")
    if usage:
        t.add_row("copy", f"new · ${usage.cost:.3f} "
                          f"[dim]{usage.attempts} attempt(s), "
                          f"{usage.output_tokens:,} output tokens[/dim]")
    else:
        t.add_row("copy", "[dim]reused from content.json — free[/dim]")
    t.add_row("last 30 days", f"${db.generation_spend(con, 30):,.2f} on copy")
    console.print(Panel(t, title="site built",
                        border_style="yellow" if over else "green", expand=False))

    if over:
        biggest = ", ".join(f"{n} {s / 1024:.0f}KB" for n, s in heaviest[:3])
        console.print(
            f"[yellow]![/yellow] This page is {size / 1024:.0f} KB. The target is "
            f"under {generate_mod.PAGE_BUDGET_BYTES // 1024} KB on rural LTE, and "
            f"the weight is in: {biggest}.\n  Resize the photos to about 1600px "
            f"wide and re-run build — nothing else in the page comes close.")
    console.print(f"\n  open it: [bold]{paths['index.html']}[/bold]")
    console.print("  edit the words: [bold]"
                  f"{os.path.join(generate_mod.site_dir(place_id), 'content.json')}"
                  "[/bold], then run build again")
    con.close()


# ---------------------------------------------------------------------------
@app.command()
def consent(
    place_id: str,
    email: Optional[str] = typer.Option(None, "--email", help="The published address."),
    source: Optional[str] = typer.Option(None, "--source",
                                         help="Where you saw it, e.g. 'public Facebook page'."),
    basis: str = typer.Option("conspicuous_publication", "--basis",
                              help=f"One of: {', '.join(db.CONSENT_BASES)}"),
    withdraw: bool = typer.Option(False, "--withdraw",
                                  help="They asked you to stop. Records it now."),
) -> None:
    """Record the CASL basis for writing to a lead — or that it was withdrawn.

    This tool never sends email. The record exists so that if you ever do, the
    reason and its date are written down, which is the part CASL asks for.
    """
    con = db.connect()
    if not db.get(con, place_id):
        _fail(f"No lead with place_id {place_id}.")
        return
    if withdraw:
        db.withdraw_consent(con, place_id)
        con.commit()
        console.print("[green]Withdrawal recorded.[/green] "
                      "CASL gives you 10 business days; you have it in writing now.")
        con.close()
        return
    if not email or not source:
        _fail("Recording a basis needs both --email (the address you saw) and "
              "--source (where you saw it). That pair is the whole record.")
        return
    try:
        db.record_consent(con, place_id, basis, email, source)
    except ValueError as exc:
        _fail(str(exc))
        return
    con.commit()
    console.print(f"[green]Recorded[/green] {basis} for {email} — {source}")
    console.print("[dim]Any message still needs your legal name, mailing address, "
                  "a working contact method, and an unsubscribe.[/dim]")
    con.close()


# ---------------------------------------------------------------------------
@app.command()
def dupes() -> None:
    """Listings that enrichment decided are the same business twice."""
    con = db.connect()
    rows = db.duplicates(con)
    if not rows:
        console.print("[dim]No duplicates flagged.[/dim]")
        con.close()
        return
    t = Table(header_style="dim")
    t.add_column("duplicate")
    t.add_column("folded into")
    t.add_column("place_id")
    for r in rows:
        t.add_row(r["name"] or "-", r["canonical_name"] or r["duplicate_of"], r["place_id"])
    console.print(t)
    con.close()


# ---------------------------------------------------------------------------
@app.command("list")
def list_leads(
    stage: Optional[str] = typer.Option(None, "--stage", "-s",
                                        help=f"One of: {', '.join(db.STAGES)}"),
    kind: Optional[str] = typer.Option(None, "--kind", "-k",
                                       help=f"One of: {', '.join(db.WEBSITE_KINDS)}"),
    near_miss: bool = typer.Option(False, "--near-miss",
                                   help="Only the social-page / dead-builder segment."),
    min_score: int = typer.Option(0, "--min-score"),
    limit: int = typer.Option(25, "--limit", "-n"),
    show_all: bool = typer.Option(False, "--all",
                                  help="Include dead leads and duplicates."),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Show leads, best first."""
    if stage and stage not in db.STAGES:
        _fail(f"Unknown stage '{stage}'. Use one of: {', '.join(db.STAGES)}")
    if kind and kind not in db.WEBSITE_KINDS:
        _fail(f"Unknown kind '{kind}'. Use one of: {', '.join(db.WEBSITE_KINDS)}")

    con = db.connect()
    rows = db.leads(con, stage=stage, kind=kind, min_score=min_score,
                    limit=None if near_miss else limit,
                    exclude_dead=not show_all, exclude_duplicates=not show_all)
    if near_miss:
        rows = [r for r in rows if r["website_kind"] in ("social", "defunct")][:limit]

    if as_json:
        console.print_json(jsonlib.dumps([dict(r) for r in rows], default=str))
        return

    if not rows:
        console.print("[dim]No leads match. Run a scan first: "
                      'leadsmith scan --address "Newmarket, ON"[/dim]')
        return

    t = Table(title=None, header_style="dim")
    t.add_column("score", justify="right")
    t.add_column("name", max_width=32, overflow="ellipsis")
    t.add_column("category", max_width=20, overflow="ellipsis")
    t.add_column("reviews", justify="right")
    t.add_column("rating", justify="right")
    t.add_column("phone")
    t.add_column("social")
    t.add_column("web")
    t.add_column("stage")
    for r in rows:
        social = "+".join(s for s, col in (("fb", "facebook_url"), ("ig", "instagram_url"))
                          if r[col])
        phone = r["phone_e164"] or r["phone"]
        if r["phone"] and r["phone_valid"] == 0:
            phone = f"[red]{r['phone']} ✗[/red]"
        t.add_row(
            str(r["score"]), r["name"] or "", r["category"] or "",
            str(r["review_count"] or 0),
            f"{r['rating']:.1f}" if r["rating"] else "-",
            phone or "[bright_black]none[/bright_black]",
            social,
            KIND_LABEL.get(r["website_kind"] or "none", r["website_kind"] or ""),
            f"[{BAND_COLOUR.get(r['stage'], 'white')}]{r['stage']}[/]",
        )
    console.print(t)

    counts = db.counts_by_stage(con)
    console.print("  " + " · ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    con.close()


# ---------------------------------------------------------------------------
@app.command()
def show(place_id: str) -> None:
    """Everything on one lead, including its touch history."""
    con = db.connect()
    row = db.get(con, place_id)
    if not row:
        _fail(f"No lead with place_id {place_id}. Try: leadsmith list")
        return

    t = Table(box=None, show_header=False)
    t.add_column(style="dim")
    t.add_column()
    for k in ("name", "category", "address", "phone", "phone_e164", "phone_type",
              "rating", "review_count", "website", "website_kind",
              "facebook_url", "instagram_url", "score", "stage", "found_at",
              "refreshed_at", "enriched_at", "duplicate_of", "consent_basis",
              "consent_email", "notes"):
        t.add_row(k, str(row[k]) if row[k] is not None else "-")
    if row["is_chain"]:
        t.add_row("chain", "[yellow]yes — head office owns this decision[/yellow]")
    if row["consent_basis"]:
        t.add_row("may email", "yes" if db.may_email(row) else
                               "[red]no — consent withdrawn[/red]")
    hours = jsonlib.loads(row["hours_json"]) if row["hours_json"] else None
    if hours:
        t.add_row("hours", "\n".join(hours))

    density = db.market_density(con, row["category"])
    if density["category_total"] >= 4:
        t.add_row("market", f"{density['category_with_site']} of "
                            f"{density['category_total']} nearby "
                            f"\"{row['category']}\" listings have a website")
    console.print(Panel(t, title=row["name"] or place_id, border_style="cyan", expand=False))

    for touch in db.touches(con, place_id):
        console.print(f"  [dim]{touch['at']}[/dim] {touch['channel']} → "
                      f"{touch['outcome']} {touch['note'] or ''}")
    con.close()


# ---------------------------------------------------------------------------
@app.command()
def stale(
    purge: bool = typer.Option(False, "--purge",
                               help="Scrub expired Google content. Pipeline state is kept."),
    days: int = typer.Option(db.CACHE_TTL_DAYS, "--days"),
) -> None:
    """Report — or clear — Places content held past the 30-day caching limit."""
    con = db.connect()
    rows = db.stale_leads(con, days)
    if not rows:
        console.print(f"[green]Nothing older than {days} days.[/green]")
        con.close()
        return

    console.print(f"[yellow]{len(rows)} leads carry Places content older than "
                  f"{days} days.[/yellow]")
    for r in rows[:10]:
        console.print(f"  [dim]{r['refreshed_at']}[/dim] {r['name']} ({r['stage']})")
    if len(rows) > 10:
        console.print(f"  [dim]…and {len(rows) - 10} more[/dim]")

    if not purge:
        console.print("\nGoogle's terms allow keeping place_id indefinitely but not the "
                      "rest.\nRe-run the scan to refresh them, or [bold]leadsmith stale "
                      "--purge[/bold] to scrub\nthe Google fields and keep your pipeline "
                      "state. Sold and live clients are never purged.")
        con.close()
        return

    n = db.purge_stale(con, days)
    con.commit()
    console.print(f"[green]Purged Google content from {n} leads.[/green] "
                  "Re-scan the area to refill them.")
    con.close()




# ---------------------------------------------------------------------------
# Phase 4 — preview, pitch, and the record of who has been visited
# ---------------------------------------------------------------------------
def _lead_for(con, place_id: str) -> dict:
    row = db.get(con, place_id)
    if not row:
        _fail(f"No lead with place_id {place_id}. Try: leadsmith list")
    lead = dict(row)
    lead["hours"] = jsonlib.loads(row["hours_json"]) if row["hours_json"] else None
    return lead


@app.command()
def preview(
    place_id: str,
    project: Optional[str] = typer.Option(None, "--project",
                                          help="Cloudflare Pages project. "
                                               f"Default: {deploy.DEFAULT_PROJECT}"),
    dry_run: bool = typer.Option(False, "--dry-run",
                                 help="Show exactly what would go public. Uploads nothing."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation."),
    plain: bool = typer.Option(False, "--plain", help="QR without ANSI colour."),
) -> None:
    """Put this lead's site on a private URL and print a QR code for it.

    The URL is real and public — anyone who has it can open it. It carries the
    business's name before they have agreed to anything, which is why the page
    is noindex, why robots.txt disallows everything, and why this asks first.
    """
    con = db.connect()
    lead = _lead_for(con, place_id)
    site = generate_mod.site_dir(place_id)

    problems = deploy.preflight(site)
    if problems:
        _fail("This site is not safe to publish:\n"
              + "\n".join(f"  - {p}" for p in problems))

    try:
        target = deploy.project_for(project)
    except deploy.DeployError as exc:
        _fail(str(exc))
        return

    if not (yes or dry_run):
        console.print(Panel(
            f"[bold]{lead['name']}[/bold]\n{lead.get('address') or ''}\n\n"
            f"A page carrying this business's name, address and phone number "
            f"goes onto the public internet.\nIt will not be indexed, but the "
            f"URL works for anyone who has it.",
            title="about to publish", border_style="yellow", expand=False))
        if not typer.confirm("Publish it?"):
            console.print("nothing published.")
            raise typer.Exit(0)

    try:
        result = deploy.deploy(site, target, dry_run=dry_run)
    except deploy.DeployError as exc:
        _fail(str(exc))
        return

    t = Table.grid(padding=(0, 2))
    t.add_column(style="dim")
    t.add_column()
    t.add_row("project", result.project)
    t.add_row("publishing", f"{result.files} files, {result.bytes / 1024:.1f} KB "
                            f"[dim]{', '.join(result.published)}[/dim]")
    if result.withheld:
        t.add_row("kept back", f"[dim]{', '.join(result.withheld)}[/dim]")

    if result.dry_run:
        t.add_row("url", "[dim]nothing uploaded — dry run[/dim]")
        console.print(Panel(t, title="preview plan", border_style="cyan", expand=False))
        con.close()
        return

    t.add_row("url", f"[bold]{result.url}[/bold]")
    console.print(Panel(t, title="preview published", border_style="green", expand=False))

    db.set_preview(con, place_id, result.url, result.project)
    con.commit()

    try:
        console.print()
        console.print(qr_mod.for_terminal(result.url, colour=not plain))
    except qr_mod.QRUnavailable as exc:
        # The URL is printed below regardless. A missing QR is an
        # inconvenience at the door, not a reason to fail a deploy that has
        # already happened.
        console.print(f"[dim]{exc}[/dim]")

    console.print(f"\n  [bold]{result.url}[/bold]")
    console.print(f"  leave-behind: [bold]leadsmith leavebehind {place_id}[/bold]")
    console.print(f"  take it down: [bold]leadsmith unpreview {place_id}[/bold]")
    con.close()


@app.command()
def unpreview(place_id: str,
              yes: bool = typer.Option(False, "--yes", "-y")) -> None:
    """Delete this lead's preview deployment and forget the URL."""
    con = db.connect()
    lead = _lead_for(con, place_id)
    url = lead.get("preview_url")
    if not url:
        _fail(f"{lead['name']} has no preview recorded.")
        return

    if not yes and not typer.confirm(f"Take down {url}?"):
        raise typer.Exit(0)
    try:
        deleted = deploy.take_down(lead.get("preview_project")
                                   or deploy.DEFAULT_PROJECT, url)
    except deploy.DeployError as exc:
        _fail(str(exc))
        return
    db.set_preview(con, place_id, "")
    con.commit()
    console.print(f"[green]deleted[/green] deployment {deleted} — {url}")
    con.close()


@app.command()
def leavebehind(
    place_id: str,
    open_after: bool = typer.Option(False, "--open", help="Open it in a browser."),
) -> None:
    """Write the one-page leave-behind to print and hand over.

    Print it to PDF from the browser — that is deliberate. A PDF library would
    be a heavy dependency and a font problem, and every machine can already
    print a page.
    """
    con = db.connect()
    lead = _lead_for(con, place_id)

    try:
        saved = generate_mod.load_content(place_id)
    except generate_mod.ContentError as exc:
        _fail(str(exc))
        return
    if not saved or not saved.get("copy"):
        _fail(f"No copy for {lead['name']} yet. Run: leadsmith build {place_id}")
        return

    cfg = config.load()
    html = pitch.build(
        lead, saved["copy"],
        preview_url=lead.get("preview_url") or "",
        density=db.market_density(con, lead.get("category")),
        operator=cfg.get("business", {}),
        pricing=cfg.get("pricing"),
    )
    path = pitch.write(place_id, html)

    t = Table.grid(padding=(0, 2))
    t.add_column(style="dim")
    t.add_column()
    t.add_row("page", path)
    density = pitch.density_line(lead, db.market_density(con, lead.get("category")))
    t.add_row("the line", density or "[dim]not enough of this trade scanned yet[/dim]")
    t.add_row("preview", lead.get("preview_url") or
                         "[yellow]none — run `leadsmith preview` first[/yellow]")
    console.print(Panel(t, title="leave-behind", border_style="green", expand=False))
    console.print("\n  open it, then print to PDF (one page, Letter).")

    if open_after:
        import webbrowser
        webbrowser.open("file://" + os.path.abspath(path))
    con.close()


@app.command()
def touch(
    place_id: str,
    outcome: str = typer.Argument(..., help=f"One of: {', '.join(db.OUTCOMES)}"),
    channel: str = typer.Option("walk-in", "--channel", "-c",
                                help=f"One of: {', '.join(db.CHANNELS)}"),
    note: str = typer.Option("", "--note", "-n"),
) -> None:
    """Record an attempt and let the outcome move the lead.

    Three attempts with nobody home closes it. That is the point: the scarce
    resource is your afternoon, not leads.
    """
    con = db.connect()
    lead = _lead_for(con, place_id)
    before = lead["stage"]
    try:
        after = db.log_touch(con, place_id, channel, outcome, note)
    except ValueError as exc:
        _fail(str(exc))
        return
    con.commit()

    moved = (f"[bold]{before}[/bold] → [bold]{after}[/bold]" if after != before
             else f"still [bold]{before}[/bold]")
    console.print(f"{lead['name']}: {outcome} · {moved}")

    if after == "dead" and outcome == "no-answer":
        console.print(f"[dim]  closed after {db.NO_ANSWER_LIMIT} attempts with "
                      f"no answer.[/dim]")
    elif outcome == "no-answer":
        left = db.NO_ANSWER_LIMIT - db.consecutive_no_answers(con, place_id)
        console.print(f"[dim]  {left} more no-answer(s) and this closes "
                      f"automatically.[/dim]")
    elif after == "interested":
        console.print(f"[dim]  leave-behind: leadsmith leavebehind {place_id}[/dim]")
    con.close()


# ---------------------------------------------------------------------------
# Phase 6 — operations
# ---------------------------------------------------------------------------
@app.command()
def board(
    out: Optional[str] = typer.Option(None, "--out", help="Where to write it."),
    open_after: bool = typer.Option(False, "--open", help="Open it in a browser."),
) -> None:
    """Write the local dashboard: call list, pipeline, live sites, money.

    A file, not a server. Nothing to leave running and nothing reachable from
    outside this laptop — which matters, because this page has every client's
    revenue on it.
    """
    con = db.connect()
    cfg = config.load()
    path = board_mod.write(board_mod.build(con, operator=cfg.get("business", {})),
                           out or board_mod.DEFAULT_PATH)

    calls = board_mod.call_list(con)
    money = billing_mod.summarise(db.subscriptions(con))
    down = [s for s in board_mod.live_sites(con) if s.get("last_check_ok") == 0]

    t = Table.grid(padding=(0, 2))
    t.add_column(style="dim")
    t.add_column()
    t.add_row("board", path)
    t.add_row("call today", f"{len(calls)} lead(s)")
    t.add_row("clients", f"{money.clients} · ${money.mrr:,.0f}/mo {money.currency}")
    if down:
        t.add_row("down", f"[red]{len(down)} live site(s) failed their last check[/red]")
    console.print(Panel(t, title="board", border_style="green", expand=False))

    if calls:
        console.print("\n  next up:")
        for lead in calls[:5]:
            why = lead["why"][0] if lead["why"] else ""
            console.print(f"    [bold]{lead['name']}[/bold]  "
                          f"{lead['phone'] or 'no phone'}  [dim]{why}[/dim]")
    if open_after:
        import webbrowser
        webbrowser.open("file://" + os.path.abspath(path))
    con.close()


@app.command()
def bill(
    place_id: str,
    plan: str = typer.Argument(..., help=f"One of: {', '.join(billing_mod.PLANS)}"),
    monthly: Optional[float] = typer.Option(None, help="Override the plan's monthly."),
    setup: Optional[float] = typer.Option(None, help="Override the plan's setup fee."),
    stripe_id: str = typer.Option("", "--stripe-id",
                                  help="The sub_... id, so `billing --sync` can "
                                       "check it is still being paid."),
    note: str = typer.Option("", "--note"),
) -> None:
    """Record what a client pays. Stripe collects it; this remembers it."""
    con = db.connect()
    lead = _lead_for(con, place_id)
    try:
        terms = billing_mod.plan_terms(plan)
    except billing_mod.BillingError as exc:
        _fail(str(exc))
        return

    db.set_subscription(
        con, place_id, plan=plan,
        monthly=monthly if monthly is not None else terms["monthly"],
        setup_fee=setup if setup is not None else terms["setup_fee"],
        term_months=terms["term_months"], stripe_id=stripe_id, note=note)
    # Someone who is paying is not a prospect. Without this they stay in the
    # call list and get rung as a lead, which is the single most embarrassing
    # thing this tool could do to its operator.
    if lead["stage"] not in ("sold", "live"):
        db.set_stage(con, place_id, "sold")
    con.commit()

    money = billing_mod.summarise(db.subscriptions(con))
    console.print(f"[green]{lead['name']}[/green] on {plan} — "
                  f"${monthly if monthly is not None else terms['monthly']:,.0f}/mo")
    console.print(f"[dim]  {money.clients} clients, ${money.mrr:,.0f}/mo total[/dim]")
    if not stripe_id:
        console.print("[dim]  no Stripe id — `billing --sync` cannot check this "
                      "one is actually being paid.[/dim]")
    con.close()


@app.command()
def billing(
    sync: bool = typer.Option(False, "--sync",
                              help="Ask Stripe whether we are still being paid."),
) -> None:
    """What the clients are worth, and whether Stripe agrees."""
    con = db.connect()
    cfg = config.load()
    rows = db.subscriptions(con)
    money = billing_mod.summarise(rows)
    costs = billing_mod.unit_costs(con)

    t = Table.grid(padding=(0, 2))
    t.add_column(style="dim")
    t.add_column()
    t.add_row("MRR", f"[bold]${money.mrr:,.2f}[/bold] {money.currency} "
                     f"[dim]· ${money.arr:,.0f}/yr[/dim]")
    t.add_row("clients", f"{money.clients} active, {money.paused} paused, "
                         f"{money.cancelled} cancelled")
    t.add_row("average", f"${money.average:,.2f} each")
    t.add_row("API cost", f"${costs['places'] + costs['generation']:,.2f} in "
                          f"30 days [dim](places ${costs['places']:,.2f}, "
                          f"copy ${costs['generation']:,.2f})[/dim]")
    console.print(Panel(t, title="billing", border_style="green", expand=False))

    if not sync:
        con.close()
        return

    try:
        result = billing_mod.sync(con, cfg.get("stripe_secret_key", ""))
    except billing_mod.BillingError as exc:
        _fail(str(exc))
        return
    con.commit()

    console.print(f"\n  checked {result.checked} against Stripe · "
                  f"{result.matched} agree")
    for problem in result.problems:
        console.print(f"  [red]![/red] [bold]{problem.name}[/bold]: "
                      f"we say {problem.ours}, Stripe says {problem.theirs}")
        console.print(f"      [dim]{problem.detail}[/dim]")
    if result.unlinked:
        console.print(f"  [dim]{len(result.unlinked)} not linked to Stripe: "
                      f"{', '.join(result.unlinked[:5])}[/dim]")
    if not result.problems:
        console.print("  [green]Stripe agrees with all of it.[/green]")
    con.close()


@app.command()
def export(
    place_id: str,
    out: str = typer.Option(".", "--out", help="Directory or filename for the zip."),
) -> None:
    """Everything a leaving client is owed, in one zip.

    The leave-behind promises this in print — "the domain is transferred to you
    and you get a copy of every file, no exit fee, no hostage" — to someone who
    has probably been burned by an agency before. It is one command because the
    day it gets called is the day nobody feels like doing it.
    """
    con = db.connect()
    lead = _lead_for(con, place_id)
    cfg = config.load()
    try:
        path = offboard.write(lead, out, operator=cfg.get("business", {}))
    except OSError as exc:
        _fail(f"Could not write the zip: {exc}")
        return

    size = os.path.getsize(path)
    console.print(f"[green]{lead['name']}[/green] → [bold]{path}[/bold] "
                  f"[dim]{size / 1024:.1f} KB[/dim]")
    console.print("[dim]  Contains the site, their words, and a plain-English "
                  "sheet for whoever they hire next.[/dim]")
    console.print("[dim]  Not included: our pricing, or the rejected-copy log.[/dim]")
    con.close()


# ---------------------------------------------------------------------------
# Phase 7 — invoicing
#
# A sub-command group rather than eight more top-level verbs, because `pay` and
# `void` on their own would read as things you do to a lead.
# ---------------------------------------------------------------------------
invoices = typer.Typer(no_args_is_help=True,
                       help="Raise, print and settle invoices. No API, no "
                            "account, no percentage.")
app.add_typer(invoices, name="invoice")


def _invoice_or_fail(con, number: str):
    row = db.invoice(con, number)
    if row is None:
        _fail(f"There is no invoice {number}. Try: leadsmith invoice list")
    return row


def _print_invoice(con, row, cfg) -> None:
    """The invoice as the operator needs to see it in a terminal."""
    lines = db.invoice_lines(con, int(row["id"]))
    payments = db.invoice_payments(con, int(row["id"]))
    sums = invoice_mod.totals(row, lines, payments)

    t = Table.grid(padding=(0, 2))
    t.add_column(style="dim")
    t.add_column()
    t.add_row("to", row["bill_to_name"])
    t.add_row("status", _status_markup(row, sums))
    if row["issued_at"]:
        t.add_row("issued", row["issued_at"])
    if row["due_at"]:
        t.add_row("due", row["due_at"])
    if row["period"]:
        t.add_row("period", invoice_mod.period_label(row["period"]))
    console.print(Panel(t, title=f"invoice {row['number']}",
                        border_style="green", expand=False))

    items = Table(box=None, pad_edge=False)
    items.add_column("what")
    items.add_column("amount", justify="right")
    for line in lines:
        label = line["description"]
        if float(line["quantity"]) != 1.0:
            label += f"  [dim]× {line['quantity']:g}[/dim]"
        items.add_row(label,
                      invoice_mod.money(invoice_mod.line_cents(
                          line["quantity"], line["unit_cents"])))
    items.add_row("[dim]subtotal[/dim]", invoice_mod.money(sums.subtotal_cents))
    if sums.tax_cents or row["tax_rate"]:
        items.add_row(f"[dim]{row['tax_label']} {row['tax_rate']:g}%[/dim]",
                      invoice_mod.money(sums.tax_cents))
    items.add_row("[bold]total[/bold]",
                  f"[bold]{invoice_mod.money(sums.total_cents, row['currency'])}[/bold]")
    if payments:
        items.add_row("[dim]paid[/dim]", invoice_mod.money(sums.paid_cents))
        items.add_row("[bold]balance[/bold]",
                      f"[bold]{invoice_mod.money(sums.balance_cents)}[/bold]")
    console.print(items)


def _status_markup(row, sums) -> str:
    if row["status"] == "void":
        return f"[dim]void — {row['void_reason'] or 'no reason recorded'}[/dim]"
    if row["status"] == "paid":
        return "[green]paid[/green]"
    if invoice_mod.is_overdue(row, sums.balance_cents):
        return f"[red]{invoice_mod.days_overdue(row)} days overdue[/red]"
    return row["status"]


def _write_and_report(con, number: str, cfg, open_after: bool) -> str:
    """Render the page, say where it is, and say what is missing from it."""
    html = invoice_mod.render(con, number, operator=cfg.get("business", {}), cfg=cfg)
    path = invoice_mod.write(number, html)
    console.print(f"[dim]  page[/dim]  {path}")
    console.print("[dim]  open it, then print to PDF or hand it over.[/dim]")

    settings = invoice_mod.settings_from(cfg)
    if not (settings["e_transfer_email"] or settings["cheque_payable_to"]):
        console.print("[yellow]  no way to pay is printed on it.[/yellow] "
                      "[dim]Set invoicing.e_transfer_email or "
                      "invoicing.cheque_payable_to in config.json.[/dim]")
    if open_after:
        import webbrowser
        webbrowser.open("file://" + os.path.abspath(path))
    return path


@invoices.command("new")
def invoice_new(
    place_id: str,
    line: list[str] = typer.Option([], "--line", "-l",
                                   help='"What it is for:amount", repeatable. '
                                        'e.g. --line "Extra page:250"'),
    from_plan: bool = typer.Option(False, "--from-plan",
                                   help="Use what they pay: setup fee (first "
                                        "invoice only) and one month."),
    period: Optional[str] = typer.Option(None, "--period",
                                         help="YYYY-MM, for a month of their "
                                              "plan. Refuses to bill it twice."),
    name: str = typer.Option("", "--name", help="Override the business name."),
    email: str = typer.Option("", "--email", help="Where to send it."),
    note: str = typer.Option("", "--note", help="A line the client will read."),
    due_days: Optional[int] = typer.Option(None, "--due-days",
                                           help="Payment terms. Default 14."),
    draft: bool = typer.Option(False, "--draft",
                               help="Raise it without issuing it yet."),
    open_after: bool = typer.Option(False, "--open", help="Open it in a browser."),
) -> None:
    """Raise an invoice against a client, and print it.

    Issued straight away unless you ask for a draft: an invoice sitting
    unissued is one nobody is going to pay.
    """
    con = db.connect()
    cfg = config.load()
    lead = _lead_for(con, place_id)

    items: list[dict] = []
    if from_plan or period:
        sub = db.subscription(con, place_id)
        if sub is None:
            _fail(f"{lead['name']} has no plan recorded, so there is nothing "
                  f"to bill from. Run: leadsmith bill {place_id} <plan>")
            return
        items += invoice_mod.subscription_lines(
            con, sub, period or invoice_mod.period_of())
    try:
        items += [invoice_mod.parse_line(text) for text in line]
    except invoice_mod.InvoiceError as exc:
        _fail(str(exc))
        return

    try:
        number = invoice_mod.create(con, place_id, items, cfg=cfg, period=period,
                                    note=note, bill_to_name=name,
                                    bill_to_email=email, terms_days=due_days)
        if not draft:
            invoice_mod.send(con, number)
    except invoice_mod.InvoiceError as exc:
        _fail(str(exc))
        return
    con.commit()

    row = db.invoice(con, number)
    _print_invoice(con, row, cfg)
    if draft:
        console.print(f"[dim]  draft — issue it with: leadsmith invoice send "
                      f"{number}[/dim]")
    else:
        _write_and_report(con, number, cfg, open_after)
    con.close()


@invoices.command("list")
def invoice_list(
    client: Optional[str] = typer.Option(None, "--client", help="One place_id."),
    status: Optional[str] = typer.Option(None, "--status",
                                         help=f"One of: {', '.join(db.INVOICE_STATUS)}"),
    year: Optional[int] = typer.Option(None, "--year"),
    outstanding: bool = typer.Option(False, "--outstanding",
                                     help="Only what is unpaid."),
) -> None:
    """Every invoice, newest first, and what is still owed on it."""
    con = db.connect()
    rows = db.invoices(con, place_id=client, status=status, year=year)
    grouped = db.lines_by_invoice(con, [int(r["id"]) for r in rows])

    table = Table(box=None, pad_edge=False)
    table.add_column("number")
    table.add_column("client")
    table.add_column("issued", style="dim")
    table.add_column("total", justify="right")
    table.add_column("balance", justify="right")
    table.add_column("status")

    shown = 0
    for row in rows:
        sums = invoice_mod.totals(row, grouped.get(int(row["id"]), []))
        sums.paid_cents = int(row["paid_cents"] or 0)
        if outstanding and (row["status"] != "sent" or sums.balance_cents <= 0):
            continue
        shown += 1
        # Nothing is owed on a void or on something already paid, and printing
        # a balance beside either reads as a debt that is not there.
        owing = (invoice_mod.money(sums.balance_cents)
                 if row["status"] == "sent" and sums.balance_cents > 0 else "—")
        table.add_row(
            row["number"], row["bill_to_name"], row["issued_at"] or "—",
            invoice_mod.money(sums.total_cents), owing,
            _status_markup(row, sums))

    if not shown:
        console.print("[dim]No invoices yet. Raise one: leadsmith invoice new "
                      "<place_id> --from-plan[/dim]")
        con.close()
        return
    console.print(table)

    owed = invoice_mod.outstanding(con)
    if owed.outstanding_cents:
        line = (f"\n  owed: [bold]{invoice_mod.money(owed.outstanding_cents)}[/bold] "
                f"across {owed.open_count} invoice(s)")
        if owed.overdue_cents:
            line += (f" · [red]{invoice_mod.money(owed.overdue_cents)} overdue"
                     f"[/red] (oldest {owed.oldest})")
        console.print(line)
    con.close()


@invoices.command("show")
def invoice_show(
    number: str,
    open_after: bool = typer.Option(False, "--open", help="Open the page."),
) -> None:
    """One invoice, and re-print its page."""
    con = db.connect()
    cfg = config.load()
    row = _invoice_or_fail(con, number)
    _print_invoice(con, row, cfg)
    if row["status"] != "draft":
        _write_and_report(con, number, cfg, open_after)
    else:
        console.print(f"[dim]  draft — issue it with: leadsmith invoice send "
                      f"{number}[/dim]")
    con.close()


@invoices.command("send")
def invoice_send(
    number: str,
    open_after: bool = typer.Option(True, "--open/--no-open",
                                    help="Open it in a browser."),
) -> None:
    """Issue a draft: set the date, start the clock, print the page.

    Nothing is emailed. The page is yours to hand over, print, or attach —
    which is the whole reason this exists without an account behind it.
    """
    con = db.connect()
    cfg = config.load()
    try:
        row = invoice_mod.send(con, number)
    except invoice_mod.InvoiceError as exc:
        _fail(str(exc))
        return
    con.commit()

    console.print(f"[green]{row['number']}[/green] issued {row['issued_at']} · "
                  f"due [bold]{row['due_at']}[/bold]")
    _write_and_report(con, number, cfg, open_after)
    con.close()


@invoices.command("pay")
def invoice_pay(
    number: str,
    amount: str = typer.Argument(..., help="What arrived, e.g. 149 or 74.50."),
    method: str = typer.Option("e-transfer", "--method", "-m",
                               help=f"One of: {', '.join(invoice_mod.METHODS)}"),
    reference: str = typer.Option("", "--ref", help="Cheque number, e-transfer id."),
    note: str = typer.Option("", "--note"),
) -> None:
    """Record money that arrived. Part payments are fine."""
    con = db.connect()
    try:
        result = invoice_mod.pay(con, number, amount, method=method,
                                 reference=reference, note=note)
    except invoice_mod.InvoiceError as exc:
        _fail(str(exc))
        return
    con.commit()

    row = db.invoice(con, number)
    if result.settled:
        console.print(f"[green]{number} paid in full.[/green] "
                      f"{invoice_mod.money(result.total_cents, row['currency'])} "
                      f"from {row['bill_to_name']}.")
        if result.credit_cents:
            console.print(f"[dim]  {invoice_mod.money(result.credit_cents)} more "
                          f"than the total — a credit against the next one.[/dim]")
    else:
        console.print(f"{number}: [bold]{invoice_mod.money(result.paid_cents)}"
                      f"[/bold] of {invoice_mod.money(result.total_cents)} · "
                      f"[yellow]{invoice_mod.money(result.balance_cents)} still "
                      f"owing[/yellow]")
    con.close()


@invoices.command("void")
def invoice_void(
    number: str,
    reason: str = typer.Option(..., "--reason", "-r",
                               help="Why. In a year this is the only "
                                    "explanation there will be."),
) -> None:
    """Cancel an invoice, keeping its number and saying why.

    Nothing is ever deleted: a gap in a numbered run is the first thing anybody
    reading a year of invoices asks about.
    """
    con = db.connect()
    try:
        row = invoice_mod.void(con, number, reason)
    except invoice_mod.InvoiceError as exc:
        _fail(str(exc))
        return
    con.commit()
    console.print(f"[dim]{row['number']} void — {row['void_reason']}[/dim]")
    if row["period"]:
        console.print(f"[dim]  {invoice_mod.period_label(row['period'])} is free "
                      f"to raise again.[/dim]")
    con.close()


@invoices.command("run")
def invoice_run(
    period: Optional[str] = typer.Option(None, "--period",
                                         help="YYYY-MM. Default: this month."),
    draft: bool = typer.Option(False, "--draft",
                               help="Raise them without issuing them."),
    dry_run: bool = typer.Option(False, "--dry-run",
                                 help="Say who would be billed. Writes nothing."),
) -> None:
    """Raise this month's invoice for every active client.

    Safe to run twice. A client who already has an invoice for the month is
    skipped rather than billed again — which is the only reason this is one
    command instead of a careful morning.
    """
    con = db.connect()
    cfg = config.load()
    month = period or invoice_mod.period_of()

    if dry_run:
        due = [s for s in db.subscriptions(con, status="active")
               if month not in db.invoiced_periods(con, s["place_id"])]
        console.print(Panel(
            f"{len(due)} client(s) would be invoiced for "
            f"{invoice_mod.period_label(month)}.\nNothing was written.",
            title="dry run", border_style="cyan", expand=False))
        for sub in due:
            console.print(f"  {sub['name'] or sub['place_id']}  "
                          f"[dim]${sub['monthly'] or 0:,.0f}/mo[/dim]")
        con.close()
        return

    result = invoice_mod.run(con, cfg, period=month, issue=not draft)
    con.commit()

    t = Table.grid(padding=(0, 2))
    t.add_column(style="dim")
    t.add_column()
    t.add_row("period", invoice_mod.period_label(result.period))
    t.add_row("raised", f"{len(result.created)} invoice(s) · "
                        f"{invoice_mod.money(result.total_cents)}")
    t.add_row("issued", "yes" if result.sent else "no — drafts")
    console.print(Panel(t, title="monthly run", border_style="green", expand=False))

    for number in result.created:
        row = db.invoice(con, number)
        console.print(f"  [green]{number}[/green]  {row['bill_to_name']}")
        if result.sent:
            invoice_mod.write(number, invoice_mod.render(
                con, number, operator=cfg.get("business", {}), cfg=cfg))
    for name, why in result.skipped:
        console.print(f"  [dim]{name} — {why}[/dim]")
    if result.created and result.sent:
        console.print(f"[dim]  pages written to {invoice_mod.INVOICES_DIR}[/dim]")
    con.close()


@invoices.command("export")
def invoice_export(
    year: Optional[int] = typer.Option(None, "--year", help="Default: all of them."),
    out: str = typer.Option("invoices.csv", "--out", help="Where to write it."),
) -> None:
    """Every invoice as a CSV, for whoever does the books."""
    con = db.connect()
    text = invoice_mod.export_csv(con, year=year)
    with open(out, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    rows = max(0, len(text.strip().splitlines()) - 1)
    console.print(f"[green]{rows}[/green] invoice(s) → [bold]{out}[/bold]")
    con.close()


if __name__ == "__main__":
    app()
