"""Leadsmith CLI. Every command lives here; the modules stay presentation-free."""
from __future__ import annotations

import json as jsonlib
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

import config
import db
import prospect

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
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Show leads, best first."""
    if stage and stage not in db.STAGES:
        _fail(f"Unknown stage '{stage}'. Use one of: {', '.join(db.STAGES)}")
    if kind and kind not in db.WEBSITE_KINDS:
        _fail(f"Unknown kind '{kind}'. Use one of: {', '.join(db.WEBSITE_KINDS)}")

    con = db.connect()
    rows = db.leads(con, stage=stage, kind=kind, min_score=min_score,
                    limit=None if near_miss else limit)
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
    t.add_column("web")
    t.add_column("stage")
    for r in rows:
        t.add_row(
            str(r["score"]), r["name"] or "", r["category"] or "",
            str(r["review_count"] or 0),
            f"{r['rating']:.1f}" if r["rating"] else "-",
            r["phone"] or "[bright_black]none[/bright_black]",
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
    for k in ("name", "category", "address", "phone", "rating", "review_count",
              "website", "website_kind", "score", "stage", "found_at",
              "refreshed_at", "consent_basis", "notes"):
        t.add_row(k, str(row[k]) if row[k] is not None else "-")
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
