"""Module 03 CLI: python -m aurum.cli analyze ...

    analyze run --symbol EURUSD --timeframe H1 [--as-of 2026-11-02T10:00] [--provisional] [--persist] [--json]
    analyze confluence --symbol EURUSD [--timeframes D1,H4,H1,M15,M5] [--persist] [--json]
    analyze config
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

analysis_app = typer.Typer(no_args_is_help=True, help="Module 03: technical analysis and SMC (read-only).")
console = Console()


def _service():
    from aurum.cli import _settings
    from aurum.analysis.services.analysis_service import TechnicalAnalysisService
    return TechnicalAnalysisService(_settings())


def _as_of(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


def _fail(msg: str) -> None:
    console.print(f"[bold red]ERROR[/] {msg}")
    raise typer.Exit(1)


def _guard(fn, *a, **k):
    from aurum.analysis.services.analysis_service import UnknownInstrumentError
    try:
        return fn(*a, **k)
    except (UnknownInstrumentError, ValueError) as exc:
        _fail(str(exc.args[0] if exc.args else exc))
    except Exception as exc:  # noqa: BLE001
        _fail(f"{type(exc).__name__}: {exc}. Is the database initialised (python -m aurum.cli init-db) and "
              "has Module 01 collected candles (python -m aurum.cli collect)?")


@analysis_app.command("run")
def run(symbol: str = typer.Option(..., "--symbol", "-s"), timeframe: str = typer.Option("H1", "--timeframe", "-t"),
        as_of: Optional[str] = typer.Option(None, "--as-of", help="UTC ISO time; only candles closed by then"),
        provisional: bool = typer.Option(False, "--provisional", help="Include the forming candle (provisional)"),
        persist: bool = typer.Option(False, "--persist", help="Store the result in the database"),
        as_json: bool = typer.Option(False, "--json", help="Print the full result as JSON")) -> None:
    """Analyse one symbol on one timeframe."""
    svc = _service()
    a = _guard(svc.analyze, symbol, timeframe, _as_of(as_of), provisional, True, persist)
    if as_json:
        console.print_json(json.dumps(a.to_dict(), default=str))
        return
    console.rule(f"[bold]{a.symbol} {a.timeframe} - as of {a.as_of:%Y-%m-%d %H:%M} UTC"
                 + (" (PROVISIONAL)" if a.provisional else ""))
    if a.status != "ok":
        console.print(f"[yellow]{a.status}[/]: {'; '.join(a.messages)}")
        return
    t = Table(show_header=True)
    for c in ("Scope", "Bias", "State", "Confidence", "CHoCH", "Explanation"):
        t.add_column(c)
    for scope, st in a.structure.items():
        t.add_row(scope, st.bias, st.state, f"{st.confidence:.0%}", st.choch.status if st.choch else "-", st.explanation)
    console.print(t)
    pd_ = a.premium_discount
    if pd_:
        console.print(f"Dealing range {pd_.range_low:.6g} - {pd_.range_high:.6g}, equilibrium {pd_.equilibrium:.6g}; "
                      f"price {pd_.current_price:.6g} in [bold]{pd_.zone}[/] ({pd_.position:.0%}).")
    if a.ote:
        console.print(f"OTE: {a.ote.explanation} Price inside: {a.ote.price_inside}.")
    z = Table(title="Active zones")
    for c in ("Kind", "Direction", "Bottom", "Top", "State", "Detected (UTC)"):
        z.add_column(c)
    for ob in [o for o in a.order_blocks if o.active][-8:]:
        z.add_row("Order block", ob.direction, f"{ob.current_bottom:.6g}", f"{ob.current_top:.6g}", ob.state,
                  f"{ob.detected_at:%Y-%m-%d %H:%M}")
    for g in [g for g in a.fair_value_gaps if g.active][-8:]:
        z.add_row("FVG", g.direction, f"{g.current_lower:.6g}", f"{g.current_upper:.6g}", g.state,
                  f"{g.detected_at:%Y-%m-%d %H:%M}")
    console.print(z)
    s = a.score
    console.print(f"[bold]Score:[/] {s.explanation}")
    for w in s.warnings:
        console.print(f"[yellow]Warning:[/] {w}")
    console.print(f"[dim]{a.disclaimer}[/]")


@analysis_app.command("confluence")
def confluence(symbol: str = typer.Option(..., "--symbol", "-s"),
               timeframes: Optional[str] = typer.Option(None, "--timeframes", help="e.g. D1,H4,H1,M15,M5"),
               as_of: Optional[str] = typer.Option(None, "--as-of"),
               persist: bool = typer.Option(False, "--persist"),
               no_cot: bool = typer.Option(False, "--no-cot", help="Skip optional COT confluence (Module 02)"),
               as_json: bool = typer.Option(False, "--json")) -> None:
    """Multi-timeframe confluence for one symbol."""
    svc = _service()
    tfs = [t.strip().upper() for t in timeframes.split(",")] if timeframes else None
    r = _guard(svc.analyze_confluence, symbol, tfs, _as_of(as_of), False, not no_cot, persist)
    if as_json:
        console.print_json(json.dumps(r.to_dict(), default=str))
        return
    console.rule(f"[bold]{r.symbol} multi-timeframe confluence - as of {r.as_of:%Y-%m-%d %H:%M} UTC")
    t = Table()
    for c in ("TF", "Status", "External", "Internal", "Prem/Disc", "Score", "Grade"):
        t.add_column(c)
    for tf, v in r.per_timeframe.items():
        sc = v.get("score") or {}
        t.add_row(tf, v["status"], f"{v['external_bias']} ({v['external_state']})", v["internal_bias"],
                  str(v["premium_discount"]), f"{sc.get('bias', '-')} {sc.get('score', 0):.0f}", sc.get("grade", "-"))
    console.print(t)
    console.print(f"[bold]Consensus:[/] {r.alignment['consensus']} ({r.alignment['ratio']:.0%} of weight)")
    console.print(f"[bold]Score:[/] {r.score.explanation}")
    for f in r.findings:
        console.print(f" - {f}")
    console.print(f"COT: {r.cot.get('interpretation') or r.cot.get('reason')}")
    for w in r.warnings:
        console.print(f"[yellow]Warning:[/] {w}")
    console.print(f"[dim]{r.disclaimer}[/]")


@analysis_app.command("config")
def config() -> None:
    """Show the effective Module 03 configuration."""
    from aurum.analysis.config import load_analysis_config
    cfg = load_analysis_config()
    console.print(f"Fingerprint: {cfg.fingerprint}")
    console.print_json(json.dumps(cfg.to_dict(), default=str))
