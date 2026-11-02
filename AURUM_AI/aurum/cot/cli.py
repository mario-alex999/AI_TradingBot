"""``python -m aurum.cli cot ...`` - Module 02 commands (read-only research; never trades).

Exit codes: 0 = success, 1 = failure, 2 = completed with problems (partial ingestion / data-quality errors).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

cot_app = typer.Typer(no_args_is_help=True, help="Module 02 - COT Institutional Intelligence Engine (CFTC data).")
console = Console()


def _ctx():
    from aurum.cli import _settings
    from aurum.cot.config import load_cot_config
    from aurum.database.connection import engine_from_settings
    s = _settings()
    try:
        cfg = load_cot_config()
    except Exception as exc:  # noqa: BLE001
        _fail(f"config/cot.yaml: {exc}")
    return s, cfg, engine_from_settings(s)


def _service(require_schema: bool = True):
    from aurum.cot.ingestion import CotService
    s, cfg, engine = _ctx()
    svc = CotService(s, cfg, engine)
    if require_schema:
        try:
            ready = svc.repo.schema_ready()
        except Exception as exc:  # noqa: BLE001
            _fail(f"Database unavailable at {s.safe_database_url}: {type(exc).__name__}: {exc}")
        if not ready:
            _fail("COT tables missing. Run: python -m aurum.cli init-db")
    return svc


def _fail(msg: str, code: int = 1):
    console.print(f"[bold red]ERROR[/] {msg}")
    raise typer.Exit(code)


def _price_loader(engine):
    from aurum.database.repository import Repository
    repo = Repository(engine)

    def load(symbol: str, timeframe: str):
        try:
            return repo.load_candles(symbol, timeframe)
        except Exception:  # noqa: BLE001 - Module 01 tables may be absent on a COT-only database
            import pandas as pd
            return pd.DataFrame(columns=["ts_utc", "close"])
    return load


def _parse_dt(v: str | None) -> datetime | None:
    if not v:
        return None
    dt = datetime.fromisoformat(v)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _print_job(job) -> int:
    t = Table(title=f"COT {job.job_type} {job.job_id} - {job.status.upper()}")
    for c in ("Report", "Period", "Source", "Retrieved", "New", "Revised", "Unchanged", "Rejected", "Issues", "Status"):
        t.add_column(c)
    for c in job.chunks:
        st = "skipped (resume)" if c.skipped else ("[green]success[/]" if c.status == "success" else f"[red]{c.status}[/]")
        t.add_row(c.report_type, f"{c.period_start}..{c.period_end}", c.source or "-", str(c.retrieved),
                  str(c.inserted), str(c.updated), str(c.unchanged), str(c.rejected), str(c.issues), st)
    console.print(t)
    for c in job.chunks:
        if c.error and not c.skipped:
            console.print(f"[red]{c.report_type} {c.period_start}..{c.period_end}:[/] {c.error}")
    if job.fallback_reason:
        console.print(f"[yellow]Note:[/] {job.fallback_reason}")
    tot = job.totals()
    console.print(f"Totals: {tot} | metric rows written: {job.metrics_written}")
    return 0 if job.status == "success" else 2


@cot_app.command()
def check() -> None:
    """Check CFTC connectivity (API and official history files), configuration and the COT database tables."""
    from aurum.cot.client import CftcClient
    s, cfg, engine = _ctx()
    from pathlib import Path
    rows = CftcClient(cfg, Path(s.resolve(s.data_dir)) / "raw" / "cot").check()
    t = Table(title="CFTC connectivity")
    for c in ("Dataset", "Source", "OK", "Detail"):
        t.add_column(c)
    for r in rows:
        detail = r.get("error") or (f"latest {r.get('latest')}" if r["source"] == "socrata" else f"{r.get('file')} "
                                    f"({r.get('bytes', 0):,} bytes)")
        t.add_row(r["dataset"], r["source"], "[green]yes[/]" if r["ok"] else "[red]no[/]", str(detail))
    console.print(t)
    usable = {r["dataset"] for r in rows if r["ok"]}
    console.print(f"Configured source: [bold]{cfg.source}[/]. Datasets with at least one working source: "
                  f"{len(usable)}/{len(cfg.datasets)}")
    unverified = [d.key for d in cfg.datasets.values() if d.dataset_id_status != "verified"]
    if unverified:
        console.print(f"[yellow]Dataset IDs not verified against the live API:[/] {', '.join(unverified)}")
    from aurum.cot.repository import CotRepository
    try:
        ready = CotRepository(engine).schema_ready()
        console.print(("[green]COT tables present[/]" if ready else "[yellow]COT tables missing - run init-db[/]")
                      + f" ({s.safe_database_url})")
    except Exception as exc:  # noqa: BLE001
        ready = False
        console.print(f"[red]Database:[/] {exc}")
    raise typer.Exit(0 if len(usable) == len(cfg.datasets) and ready else 1)


@cot_app.command()
def datasets() -> None:
    """Show configured CFTC report families, dataset IDs, coverage and the contract mappings."""
    _, cfg, _ = _ctx()
    t = Table(title="CFTC datasets (config/cot.yaml)")
    for c in ("Key", "Name", "Socrata ID", "ID status", "History from", "Yearly file"):
        t.add_column(c)
    for d in cfg.datasets.values():
        t.add_row(d.key, d.name, d.dataset_id, d.dataset_id_status, str(d.history_start), d.yearly_file)
    console.print(t)
    m = Table(title="Contract mappings")
    for c in ("Instrument", "Report", "CFTC contract", "Code", "Exchange", "Status", "Verified", "Primary group"):
        m.add_column(c)
    for x in cfg.mappings:
        m.add_row(x.aurum_symbol, x.report_type, x.cftc_contract_name, x.cftc_contract_code, x.exchange,
                  x.mapping_status, str(x.verified_at), x.primary_category)
    console.print(m)


@cot_app.command("verify-datasets")
def verify_datasets(write: bool = typer.Option(True, "--write/--no-write",
                                               help="Record verified IDs in config/cot.yaml and the database")) -> None:
    """Verify each configured Socrata dataset ID against publicreporting.cftc.gov metadata.

    Marks an ID verified only when the CFTC portal itself confirms it (title, futures-only scope, columns).
    Exit 0 = all verified, 1 = portal unreachable, 2 = at least one ID does not match."""
    from aurum.cot.client import SocrataSource
    from aurum.cot.config import set_dataset_id_status
    s, cfg, engine = _ctx()
    src = SocrataSource(cfg.socrata)
    results = [src.verify_dataset(ds) for ds in cfg.datasets.values()]
    t = Table(title=f"Dataset ID verification ({cfg.socrata.base_url})")
    for c in ("Dataset", "ID", "Result", "Portal title / reason"):
        t.add_column(c)
    colour = {"verified": "green", "mismatch": "red", "unreachable": "yellow"}
    for r in results:
        t.add_row(r["dataset"], r["dataset_id"], f"[{colour[r['status']]}]{r['status']}[/]",
                  r.get("title") if r["status"] == "verified" else str(r.get("reason")))
    console.print(t)
    today = date.today().isoformat()
    if write:
        for r in results:
            if r["status"] == "verified":
                set_dataset_id_status(r["dataset_id"], "verified", f"confirmed via {r['source']} on {today}")
            elif r["status"] == "mismatch":
                set_dataset_id_status(r["dataset_id"], "mismatch", f"{r['reason']} ({today})")
        if any(r["status"] != "unreachable" for r in results):
            from aurum.cot.config import load_cot_config
            from aurum.cot.repository import CotRepository
            try:
                CotRepository(engine).sync_config(load_cot_config())
                console.print("config/cot.yaml and the cot_datasets table updated.")
            except Exception as exc:  # noqa: BLE001
                console.print(f"[yellow]config/cot.yaml updated; database not synced:[/] {exc}")
    if any(r["status"] == "mismatch" for r in results):
        raise typer.Exit(2)
    if any(r["status"] == "unreachable" for r in results):
        console.print("[yellow]The CFTC API could not be reached from this network; IDs keep their current status. "
                      "The engine continues to use the official CFTC history files.[/]")
        raise typer.Exit(1)


@cot_app.command("sync-config")
def sync_config() -> None:
    """Copy datasets and mappings from config/cot.yaml into the database (also done by backfill/update)."""
    svc = _service()
    svc.sync()
    console.print(f"[green]Synced[/] {len(svc.cfg.datasets)} datasets and {len(svc.cfg.mappings)} mappings.")


@cot_app.command()
def backfill(years: float = typer.Option(4.0, "--years", help="Years of history to load (default 4)"),
             start: Optional[str] = typer.Option(None, "--start", help="Start date YYYY-MM-DD (overrides --years)"),
             end: Optional[str] = typer.Option(None, "--end", help="End date YYYY-MM-DD (default today)"),
             report_type: Optional[list[str]] = typer.Option(None, "--report-type", "-r",
                                                             help="legacy, tff, disaggregated (repeatable)"),
             no_resume: bool = typer.Option(False, "--no-resume", help="Re-download chunks already ingested")) -> None:
    """Historical backfill (resumable, idempotent) followed by metric calculation."""
    svc = _service()
    try:
        job = svc.backfill(years=years, start=date.fromisoformat(start) if start else None,
                           end=date.fromisoformat(end) if end else None, report_types=report_type,
                           resume=not no_resume, progress=lambda m: console.print(f"  {m}"))
    except ValueError as exc:
        _fail(str(exc))
    raise typer.Exit(_print_job(job))


@cot_app.command()
def update(report_type: Optional[list[str]] = typer.Option(None, "--report-type", "-r")) -> None:
    """Weekly incremental update: fetch newly published / revised reports, then recalculate metrics."""
    svc = _service()
    job = svc.update(report_types=report_type, progress=lambda m: console.print(f"  {m}"))
    raise typer.Exit(_print_job(job))


@cot_app.command()
def calculate(instrument: Optional[list[str]] = typer.Option(None, "--instrument", "-i")) -> None:
    """Recalculate all positioning metrics from stored reports (reproducible, idempotent)."""
    svc = _service()
    job = svc.calculate(symbols=[i.upper() for i in instrument] if instrument else None,
                        progress=lambda m: console.print(f"  {m}"))
    console.print(f"[green]Metric rows written:[/] {job.metrics_written}")


@cot_app.command()
def report(instrument: str = typer.Option(..., "--instrument", "-i", help="EURUSD, GBPUSD, XAUUSD, US30"),
           as_of: Optional[str] = typer.Option(None, "--as-of", help="UTC timestamp: show only what was public then"),
           as_json: bool = typer.Option(False, "--json", help="Print the structured report as JSON")) -> None:
    """Institutional intelligence report for one instrument."""
    from aurum.cot.intelligence import instrument_report
    svc = _service()
    svc.sync()
    try:
        rep = instrument_report(svc.repo, svc.cfg, instrument, svc._map_ids, _price_loader(svc.repo.engine),
                                as_of=_parse_dt(as_of))
    except KeyError as exc:
        _fail(str(exc).strip("'"))
    if as_json:
        console.print_json(json.dumps(rep, default=str))
        return
    console.rule(f"[bold]{rep['instrument']} - COT institutional positioning")
    for f in rep["families"]:
        if not f.get("available"):
            console.print(f"[yellow]{f['dataset_name']}:[/] {f.get('reason')}")
            continue
        fr = f["freshness"]
        console.print(f"[bold gold3]{f['dataset_name']}[/] - {f['contract']['name']} ({f['contract']['code']}, "
                      f"{f['contract']['mapping_status']})")
        console.print(f"Report date {f['report_date']} | released {f['release_date']} | freshness {fr['status']} "
                      f"(next expected release {fr['next_expected_release']:%Y-%m-%d %H:%M} UTC) | "
                      f"confidence {f['confidence']['level']}")
        t = Table()
        for c in ("Group", "Long", "Short", "Spread", "Net", "Wk chg", "%OI", "Pct 12m", "Pct 36m", "COT idx",
                  "z", "Classification", "Change"):
            t.add_column(c)
        fmt = lambda v, p="{:,.0f}": "-" if v is None else p.format(v)  # noqa: E731
        for c in f["categories"]:
            t.add_row(c["label"], fmt(c["long"]), fmt(c["short"]), fmt(c["spreading"]), fmt(c["net_position"], "{:+,.0f}"),
                      fmt(c["weekly_net_change"], "{:+,.0f}"), fmt(c["net_position_pct_oi"], "{:+.1f}"),
                      fmt(c["historical_percentile_12m"], "{:.0f}"), fmt(c["historical_percentile_36m"], "{:.0f}"),
                      fmt(c["cot_index"], "{:.0f}"), fmt(c["z_score"], "{:+.2f}"),
                      (c["positioning_strength"] or "-").replace("_", " "), (c["change_type"] or "-").replace("_", " "))
        console.print(t)
        for title, key in (("Facts (CFTC)", "facts"), ("Calculated metrics", "metrics"), ("Interpretation", "interpretation")):
            console.print(f"[bold]{title}[/]")
            for line in f["statements"][key]:
                console.print(f"  - {line}")
        d = f["divergence"]
        console.print(f"[bold]Divergence[/] ({d.get('category', '-')}, {d.get('price_timeframe', '-')}): "
                      + (f"latest window {d['latest']['window_start_report_date']} -> {d['latest']['report_date']}, "
                         f"signals: {', '.join(d['latest']['signals']) or 'none'}" if d.get("available") and d.get("latest")
                         else d.get("reason") or "n/a"))
        if f["confidence"]["reasons"]:
            console.print(f"[yellow]Limitations:[/] {'; '.join(f['confidence']['reasons'])}")
        for w in f["data_quality_warnings"][:5]:
            console.print(f"[yellow]Quality ({w['severity']}):[/] {w['description']}")
        console.print()
    console.print(f"[bold]Overall ({rep['overall']['confidence']} confidence):[/] {rep['overall']['interpretation']}")
    console.print(f"[dim]{rep['disclaimer']}[/]")


@cot_app.command()
def status() -> None:
    """Stored data, latest report/release dates, last ingestion and open data-quality issues."""
    svc = _service()
    st = svc.repo.status_summary()
    t = Table(title="COT data status")
    for c in ("Report", "Reports", "First report", "Latest report", "Latest release", "Last retrieval"):
        t.add_column(c)
    for d in st["datasets"]:
        t.add_row(d["report_type"], str(d["reports"]), str(d["first_report_date"]), str(d["latest_report_date"]),
                  str(d["latest_release_date"]), str(d["last_retrieval"]))
    console.print(t)
    console.print(f"Positions: {st['positions']:,} | metrics: {st['metrics']:,} | last successful ingestion: "
                  f"{st['last_successful_ingestion']} | failed ingestions: {st['failed_ingestions']} | "
                  f"open issues: {st['open_issues'] or 'none'}")
    if not st["datasets"]:
        console.print("[yellow]No COT data yet. Run: python -m aurum.cli cot backfill --years 4[/]")


@cot_app.command()
def quality(severity: Optional[str] = typer.Option(None, "--severity", help="info, warning, error, critical"),
            instrument: Optional[str] = typer.Option(None, "--instrument", "-i"),
            limit: int = typer.Option(50, "--limit")) -> None:
    """List open COT data-quality issues (exit 2 when errors/critical issues are open)."""
    svc = _service()
    rows, total = svc.repo.issues(severity=severity, symbol=instrument.upper() if instrument else None, limit=limit)
    t = Table(title=f"COT data-quality issues ({total} open)")
    for c in ("Severity", "Type", "Instrument", "Report", "Report date", "Seen", "Description"):
        t.add_column(c)
    for r in rows:
        t.add_row(r["severity"], r["issue_type"], r["aurum_symbol"] or "-", r["report_type"] or "-",
                  str(r["report_date"] or "-"), str(r["occurrences"]), r["description"])
    console.print(t)
    bad, _ = svc.repo.issues(severity="error", limit=1)
    crit, _ = svc.repo.issues(severity="critical", limit=1)
    raise typer.Exit(2 if bad or crit else 0)


@cot_app.command()
def export(kind: str = typer.Option("all", "--kind", help="all, raw, positions, metrics, analysis, quality"),
           instrument: Optional[str] = typer.Option(None, "--instrument", "-i"),
           overwrite: bool = typer.Option(False, "--overwrite", help="Replace existing files")) -> None:
    """Export COT data to CSV under data/exports/cot/ (each row marked real / incomplete / unavailable)."""
    from pathlib import Path
    from aurum.cot import exports
    from aurum.cot.intelligence import instrument_report
    svc = _service()
    svc.sync()
    out_dir = Path(svc.settings.resolve(svc.settings.data_dir)) / "exports" / "cot"
    syms = [instrument.upper()] if instrument else svc.cfg.symbols
    builder = lambda: [instrument_report(svc.repo, svc.cfg, s_, svc._map_ids, _price_loader(svc.repo.engine))  # noqa: E731
                       for s_ in syms]
    kinds = exports.KINDS if kind == "all" else (kind,)
    for k in kinds:
        try:
            path, n = exports.export(k, svc.repo, svc.cfg, out_dir, instrument, overwrite, builder)
        except ValueError as exc:
            _fail(str(exc))
        console.print(f"[green]{k}[/]: {n} rows -> {path}")
