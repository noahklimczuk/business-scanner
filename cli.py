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

import config
import db
import enrich as enrich_mod
import generate as generate_mod
import prospect
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

    saved = generate_mod.load_content(place_id)
    content = (saved or {}).get("copy") if saved else None
    usage = None

    if content is None or regenerate:
        estimate = 0.03
        console.print(Panel(
            f"Claude writes the copy for [bold]{lead['name']}[/bold]\n"
            f"estimated spend: [bold]${estimate:,.2f} USD[/bold] "
            f"[dim]({generate_mod.MODEL})[/dim]",
            title="about to spend", border_style="cyan", expand=False))
        threshold = float(cfg["defaults"]["confirm_above_usd"])
        if estimate > threshold and not yes:
            if not typer.confirm("  Continue?", default=False):
                console.print("[dim]cancelled[/dim]")
                return
        try:
            key = (cfg.get("anthropic_api_key") or "").strip() or None
            if key and key.startswith("PASTE_"):
                key = None
            with console.status("writing copy…"):
                content, usage = generate_mod.write_copy(
                    lead, city=cfg["business"].get("home_city", ""), api_key=key)
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
        site = generate_mod.render(lead, content, template=chosen, preview=not launch,
                                   operator=cfg["business"])
    except generate_mod.ContentError as exc:
        _fail(str(exc))
        return

    paths = generate_mod.write_site(place_id, site)
    db.set_site_dir(con, place_id, generate_mod.site_dir(place_id))
    con.commit()

    size = sum(os.path.getsize(p) for p in paths.values())
    t = Table(box=None, show_header=False, pad_edge=False)
    t.add_column(style="dim")
    t.add_column()
    t.add_row("business", lead["name"])
    t.add_row("template", chosen)
    t.add_row("accent", f"[bold]{site.palette['accent']}[/bold] "
                        f"[dim]hue {site.palette['hue']}[/dim]")
    t.add_row("page", f"{paths['index.html']}  [dim]{size / 1024:.1f} KB[/dim]")
    t.add_row("indexing", "[yellow]noindex + robots disallow[/yellow]" if not launch
                          else "[green]indexable[/green]")
    if usage:
        t.add_row("copy", f"new · ${usage.cost:.3f} "
                          f"[dim]{usage.attempts} attempt(s), "
                          f"{usage.output_tokens:,} output tokens[/dim]")
    else:
        t.add_row("copy", "[dim]reused from content.json — free[/dim]")
    t.add_row("last 30 days", f"${db.generation_spend(con, 30):,.2f} on copy")
    console.print(Panel(t, title="site built", border_style="green", expand=False))
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


if __name__ == "__main__":
    app()
