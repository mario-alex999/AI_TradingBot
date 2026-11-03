"""CLI for the Trade Learning Agent: python -m aurum.cli learn run|status|reset."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import typer

from aurum.ui import Table, console, error
from aurum.ui import status as status_word

from .engine import TradeLearningAgent
from .models import LearningConfig, describe
from .store import LineageStore, to_dict

learn_app = typer.Typer(help="Trade Learning Agent: watch closed trades, learn what works, replicate or terminate. Read-only.")

LINEAGE_ID = "L1"


def _store() -> LineageStore:
    from aurum.config import get_settings
    s = get_settings()
    return LineageStore(Path(s.resolve(s.data_dir)) / "learning")


def _fail(msg: str) -> None:
    error(msg)
    raise typer.Exit(1)


def _print_lineage(agent: TradeLearningAgent) -> None:
    lin = agent.lineage
    console.rule(f"[bold]Trade Learning Agent · {lin.id}[/] [muted](read-only)[/]", style="muted", align="left")
    console.print(f"[muted]Status[/] {status_word(lin.status)} · [muted]trades observed[/] {lin.trades_observed:,} · "
                  f"[muted]last trade closed[/] {lin.last_close_time:%Y-%m-%d %H:%M} UTC" if lin.last_close_time else
                  f"[muted]Status[/] {status_word(lin.status)} · no trades observed yet")
    t = Table(title="Generations", numeric=("Trials", "Failed in a row"))
    for c in ("Agent", "Parent", "Status", "Pattern", "Trials", "Failed in a row"):
        t.add_column(c)
    for a in lin.agents:
        current = a.pattern or (tuple(tuple(x) for x in a.trials[-1]["pattern"]) if a.trials else None)
        t.add_row(a.id, a.parent or "—", status_word(a.status), describe(current) if current else "—",
                  f"{len(a.trials)}", f"{a.failed_trials}")
    console.print(t)
    active = lin.active_agent
    if active and active.status == "trialling":
        console.print(f"[accent]Now trialling[/] {describe(active.pattern or ())}: "
                      f"{len(active.trial_matched)}/{lin.config.trial_trades} matching trades so far "
                      f"(out of {active.trial_seen} closed since discovery).")
    events = [(e, a.id) for a in lin.agents for e in a.events][-8:]
    if events:
        console.print("[bold]Latest events[/]")
        for e, aid in events:
            console.print(f"  [muted]{e.at:%Y-%m-%d %H:%M}[/] {aid} · {e.message}")


@learn_app.command()
def run(csv: Optional[Path] = typer.Option(None, "--csv", help="Read closed trades from a CSV export instead of MT5"),
        days: int = typer.Option(90, "--days", help="MT5 history to read on the first run (later runs continue from the last trade)"),
        loop: bool = typer.Option(False, "--loop", help="Keep monitoring: check for newly closed trades every --interval seconds"),
        interval: int = typer.Option(300, "--interval", min=30),
        window: int = typer.Option(None, "--window", help="Discovery window (trades); only applied when a new lineage starts"),
        trial_trades: int = typer.Option(None, "--trial-trades", help="Matching trades per trial; only applied when a new lineage starts")) -> None:
    """Feed closed trades to the agent and advance its cycle. Never places, modifies or closes a trade."""
    store = _store()
    lineage = store.load(LINEAGE_ID)
    if lineage is None:
        cfg = LearningConfig()
        if window:
            cfg.discovery_window = window
        if trial_trades:
            cfg.trial_trades = trial_trades
        agent = TradeLearningAgent.start(cfg, lineage_id=LINEAGE_ID)
    else:
        agent = TradeLearningAgent(lineage)

    while True:
        if csv:
            from .sources import load_csv
            try:
                trades = load_csv(csv)
            except (OSError, ValueError) as exc:
                _fail(str(exc))
        else:
            from aurum.config import get_settings
            from aurum.gateways.mt5_gateway import MT5Error, MT5Gateway
            now = datetime.now(timezone.utc)
            since = agent.lineage.last_close_time - timedelta(days=1) if agent.lineage.last_close_time else now - timedelta(days=days)
            try:
                with MT5Gateway(get_settings()) as gw:
                    trades = gw.closed_trades(since, now)
            except MT5Error as exc:
                _fail(str(exc))
        events = agent.observe(trades)
        store.save(agent.lineage)
        for e in events:
            console.print(f"[muted]{e.at:%Y-%m-%d %H:%M}[/] {e.message}")
        _print_lineage(agent)
        if not loop or agent.lineage.active_agent is None:
            break
        console.print(f"[muted]Checking again in {interval}s · Ctrl+C to stop[/]")
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            console.print("Stopped by user.")
            break


@learn_app.command()
def status(as_json: bool = typer.Option(False, "--json", help="Print the full lineage as JSON")) -> None:
    """Show the current lineage: generations, patterns, trials and recent events."""
    lineage = _store().load(LINEAGE_ID)
    if lineage is None:
        console.print("[warning]No lineage yet.[/] Run: python -m aurum.cli learn run --csv trades.csv (or without --csv to read MT5 history)")
        return
    if as_json:
        console.print_json(json.dumps(to_dict(lineage), default=str))
        return
    _print_lineage(TradeLearningAgent(lineage))


@learn_app.command()
def reset(yes: bool = typer.Option(False, "--yes", help="Confirm deleting the lineage")) -> None:
    """Delete the stored lineage so a new generation 1 starts on the next run."""
    if not yes:
        _fail("This deletes the agent's history. Re-run with --yes to confirm.")
    console.print("[positive]Lineage deleted.[/]" if _store().delete(LINEAGE_ID) else "Nothing to delete.")
