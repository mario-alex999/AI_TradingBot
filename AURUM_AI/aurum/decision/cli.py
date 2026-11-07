"""CLI for Module 05: python -m aurum.cli decide ... It proposes candidates for review and never places a trade."""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Optional

import typer

from aurum.ui import Table, console, error

from . import DISCLAIMER

decide_app = typer.Typer(help="Decision engine: trade candidates for human review. Never places trades.")


def _service() -> Any:
    from .service import DecisionService
    return DecisionService()


def _fail(msg: str) -> None:
    error(msg)
    raise typer.Exit(1)


def _guarded(fn: Any) -> Any:
    from aurum.analysis.services.analysis_service import UnknownInstrumentError

    from .lifecycle import IllegalTransition
    from .service import DecisionError
    try:
        return fn()
    except (DecisionError, IllegalTransition, ValueError) as exc:
        _fail(str(exc))
    except UnknownInstrumentError as exc:
        _fail(str(exc.args[0]))


def _print_run(r: dict[str, Any]) -> None:
    console.rule(f"[bold]{r['symbol']}[/] [muted]{r['as_of']:%Y-%m-%d %H:%M} UTC[/]", style="muted", align="left")
    gaps = [k for k, v in r["availability"].items() if not v["available"]]
    if gaps:
        console.print(f"[muted]Missing inputs: {', '.join(gaps)}[/]")
    if r["signals"]:
        t = Table(title="Candidates", numeric=("RR", "Confidence"))
        for c in ("Id", "Template", "TF", "Direction", "RR", "Confidence", "Status"):
            t.add_column(c)
        for s in r["signals"]:
            t.add_row(s["id"][:8], s["strategy"], s["timeframe"], s["direction"], f"{s['risk_reward']:.2f}", str(s["confidence"]), s["status"])
        console.print(t)
    else:
        console.print("No setup right now. Why each template passed:")
        for skip in r["skipped"]:
            console.print(f"  - {skip['template']} {skip['timeframe']}: {skip['reason']}")


def _print_table(rows: list[dict[str, Any]]) -> None:
    t = Table(title="Candidates", numeric=("RR", "Confidence"))
    for c in ("Id", "Instrument", "Template", "TF", "Direction", "RR", "Confidence", "Status"):
        t.add_column(c)
    for s in rows:
        t.add_row(s["id"], s["instrument"], s["strategy"], s["timeframe"], s["direction"], f"{s['risk_reward']:.2f}", str(s["confidence"]), s["status"])
    console.print(t)


ai_app = typer.Typer(help="AI analysis engine: explainable analysis for human review. Never places trades.")
strategies_app = typer.Typer(help="Strategy detectors.")
config_app = typer.Typer(help="Engine configuration.")
ai_app.add_typer(strategies_app, name="strategies")
ai_app.add_typer(config_app, name="config")


def _print_analysis(r: dict[str, Any]) -> None:
    a = r["analysis"]
    d = a["decision"]
    console.print(f"[bold]{d['decision']}[/] · regime {a['regime']['regime']} · bias {a['bias']['overall']} ({a['bias']['agreement']:.0%})")
    for reason in d["reasons"]:
        console.print(f"  - {reason}")
    for c in a["conflicts"]:
        console.print(f"  [muted]conflict ({c['kind']}):[/] {c['detail']}")


@ai_app.command("status")
def ai_status() -> None:
    """Show counts, recent runs and the provider."""
    status()


@ai_app.command("analyze")
def ai_analyze(symbol: Optional[str] = typer.Option(None, "--symbol"), timeframe: Optional[str] = typer.Option(None, "--timeframe"),
               timeframes: Optional[str] = typer.Option(None, "--timeframes", help="Comma separated, e.g. D1,H4,H1,M15,M5"),
               ai: bool = typer.Option(True, "--ai/--no-ai")) -> None:
    """Analyse an instrument and record the result."""
    frames = [t.strip().upper() for t in (timeframes or timeframe or "").split(",") if t.strip()] or None
    svc = _service()
    for r in _guarded(lambda: svc.run_all([symbol.upper()] if symbol else None, None, ai, frames)):
        _print_run(r)
        _print_analysis(r)
    console.print(f"[muted]{DISCLAIMER}[/]")


@ai_app.command("bias")
def ai_bias(symbol: str = typer.Option(..., "--symbol")) -> None:
    """Directional bias on each timeframe."""
    b = _guarded(lambda: _service().bias(symbol))
    t = Table(title=f"{symbol.upper()} bias: {b['overall']} ({b['agreement']:.0%})", numeric=("Weight",))
    t.add_column("Timeframe"), t.add_column("Bias"), t.add_column("State"), t.add_column("Weight")
    for tf, v in b["timeframes"].items():
        t.add_row(tf, v["bias"], str(v["state"]), str(v["weight"]))
    console.print(t)


@ai_app.command("regime")
def ai_regime(symbol: str = typer.Option(..., "--symbol")) -> None:
    """Market regime with its evidence."""
    r = _guarded(lambda: _service().regime(symbol))
    console.print(f"[bold]{r['regime']}[/] ({r['quality']})")
    for x in r["supporting"]:
        console.print(f"  + {x}")
    for x in r["conflicting"]:
        console.print(f"  - {x}")


@ai_app.command("setups")
def ai_setups(symbol: str = typer.Option(..., "--symbol")) -> None:
    """Scan the strategies now, without saving anything."""
    r = _guarded(lambda: _service().setups(symbol))
    for s in r["setups"]:
        console.print(f"{s['strategy']} {s['timeframe']} {s['direction']} grade {s['evidence_grade']} valid {s['valid']} {s['blocked_by'] or ''}")
    for s in r["skipped"]:
        console.print(f"[muted]{s['template']} {s['timeframe']}: {s['reason']}[/]")


@strategies_app.command("list")
def ai_strategies() -> None:
    """List the strategy detectors."""
    t = Table(title="Strategies")
    t.add_column("Id"), t.add_column("Enabled"), t.add_column("Timeframes")
    for s in _service().strategies():
        t.add_row(s["id"], str(s["enabled"]), ",".join(s["timeframes"]))
    console.print(t)


@ai_app.command("providers")
def ai_providers() -> None:
    """List AI providers. Prints no secrets."""
    for p in _service().providers()["providers"]:
        console.print(f"{p['id']}: selected {p['selected']} · configured {p['configured']} · model {p['model']} · budget enforced {p['budget_enforced']}")


@ai_app.command("evaluate")
def ai_evaluate(from_: datetime = typer.Option(..., "--from"), to: datetime = typer.Option(..., "--to"), symbol: Optional[str] = typer.Option(None, "--symbol"),
                template: Optional[str] = typer.Option(None, "--template")) -> None:
    """Historical evaluation on stored candles."""
    backtest(from_, to, symbol, template)


@config_app.command("show")
def ai_config_show() -> None:
    """Show the active configuration."""
    v = _service().config_view()
    console.print(f"Fingerprint {v['fingerprint']}")
    console.print_json(data=v["config"])


@ai_app.command("check")
def ai_check() -> None:
    """Check the inputs, the tables and the AI settings. Prints no secrets."""
    check()


@decide_app.command("run")
def run(symbol: Optional[str] = typer.Option(None, "--symbol"), as_of: Optional[datetime] = typer.Option(None, "--as-of"),
        ai: bool = typer.Option(True, "--ai/--no-ai", help="--no-ai skips the AI reviewer; it stays off unless enabled in .env."),
        loop: bool = typer.Option(False, "--loop", help="Repeat every DECISION_LOOP_INTERVAL_SECONDS.")) -> None:
    """Find candidates, check the gates and publish them for review."""
    svc = _service()
    while True:
        for r in _guarded(lambda: svc.run_all([symbol.upper()] if symbol else None, as_of, ai)):
            _print_run(r)
        console.print(f"[muted]{DISCLAIMER}[/]")
        if not loop:
            return
        time.sleep(svc.settings.decision_loop_interval_seconds)


@decide_app.command("list")
def list_(status: Optional[str] = typer.Option(None, "--status"), symbol: Optional[str] = typer.Option(None, "--symbol"),
          limit: int = typer.Option(50, "--limit", min=1, max=500)) -> None:
    """List candidates, newest first."""
    _print_table(_guarded(lambda: _service().signals(status, symbol, limit)))


@decide_app.command("show")
def show(candidate: str) -> None:
    """Show one candidate's full dossier."""
    d = _guarded(lambda: _service().dossier(candidate))
    console.print(f"[bold]{d['instrument']} {d['direction']}[/] · {d['strategy']} {d['timeframe']} · {d['status']}")
    console.print(f"Entry {d['entry_zone'][0]} - {d['entry_zone'][1]} · stop {d['stop_loss']} · target {d['take_profit']} · RR {d['risk_reward']}")
    console.print(f"Confidence {d['confidence']} (score {d['deterministic_score']}, grade {d['grade']}) · suggested lots {d['suggested_volume']}"
                  + (f" ({d['volume_reason']})" if d["volume_reason"] else ""))
    console.print(f"[muted]Invalidation[/] {d['invalidation']}")
    t = Table(title="Evidence", numeric=("Points",))
    t.add_column("Component"), t.add_column("Points"), t.add_column("Why")
    for e in d["evidence_detail"]:
        t.add_row(e["component"], f"{e['points']:.1f}/{e['max_points']:.0f}", e["sentence"])
    console.print(t)
    t = Table(title="Gates")
    t.add_column("Gate"), t.add_column("Result")
    for g in d["gates"]:
        t.add_row(g["gate"], g["result"])
    console.print(t)
    ai = d["ai_review"]
    console.print(f"[muted]AI review[/] {ai['status']}" + (f" ({ai['reason']})" if ai.get("reason") else "") + (f": {ai['verdict']}" if ai.get("verdict") else ""))
    for h in d["status_history"]:
        console.print(f"[muted]{h['at']:%Y-%m-%d %H:%M}[/] {h['from'] or '—'} -> {h['to']} ({h['actor']}) {h['note'] or ''}")
    console.print(f"[muted]{d['disclaimer']}[/]")


@decide_app.command("approve")
def approve(candidate: str, note: str = typer.Option(..., "--note"), reviewer: str = typer.Option("operator", "--reviewer")) -> None:
    """Record your approval. This sends nothing to MT5."""
    d = _guarded(lambda: _service().approve(candidate, reviewer, note))
    console.print(f"Approved {d['id']}. Your decision is recorded; AURUM AI does not place trades.")


@decide_app.command("reject")
def reject(candidate: str, note: str = typer.Option(..., "--note"), reviewer: str = typer.Option("operator", "--reviewer")) -> None:
    """Record your rejection."""
    console.print(f"Rejected {_guarded(lambda: _service().reject(candidate, reviewer, note))['id']}.")


@decide_app.command("reopen")
def reopen(candidate: str, note: str = typer.Option(..., "--note"), reviewer: str = typer.Option("operator", "--reviewer")) -> None:
    """Override the AI reviewer's rejection, with a note."""
    console.print(f"Re-opened {_guarded(lambda: _service().reopen(candidate, reviewer, note))['id']}.")


@decide_app.command("expire-stale")
def expire_stale() -> None:
    """Expire candidates past their time or their stop."""
    console.print(f"{_guarded(lambda: _service().expire_stale())} candidate(s) expired.")


@decide_app.command("outcomes-update")
def outcomes_update() -> None:
    """Label what happened to each candidate from stored candles."""
    console.print(_guarded(lambda: _service().update_outcomes()) or "Nothing to update.")


@decide_app.command("backtest")
def backtest(from_: datetime = typer.Option(..., "--from"), to: datetime = typer.Option(..., "--to"), symbol: Optional[str] = typer.Option(None, "--symbol"),
             template: Optional[str] = typer.Option(None, "--template")) -> None:
    """Walk-forward replay of templates and gates over stored history. The AI reviewer is not used."""
    rep = _guarded(lambda: _service().backtest(from_, to, [symbol] if symbol else None, template, lambda m: console.print(f"[muted]{m}[/]")))
    m, d = rep["metrics"], rep["details"]
    console.print(f"{m['totalTrades']} trades · win rate {m['winRate']}% · profit factor {m['profitFactor']} · max drawdown {m['maxDrawdownPct']}%")
    console.print(f"{d['steps']} steps · {d['candidates_seen']} candidates seen · blocked by gate: {d['blocked_by_gate']}")
    for w in d["warnings"]:
        console.print(f"[muted]{w}[/]")
    console.print(f"[muted]{d['disclaimer']}[/]")


@decide_app.command("status")
def status() -> None:
    """Show counts, recent runs and the AI reviewer's state."""
    st = _service().status()
    console.print(f"Tables ready: {st['schema_ready']} · counts: {st['counts'] or 'none'}")
    ai = st["ai_reviewer"]
    console.print(f"AI reviewer: enabled {ai['enabled']} · blocked {ai['blocked_reason'] or 'no'} · budget ${ai['budget_usd']} · used ${ai['budget_used_usd']}")
    for r in st["recent_runs"][:5]:
        console.print(f"[muted]{r['created_at']:%Y-%m-%d %H:%M}[/] {r['symbol']} {r['status']}")


@decide_app.command("check")
def check() -> None:
    """Check the inputs, the tables and the AI settings. Prints no secrets."""
    svc = _service()
    ok = svc.repo.schema_ready()
    console.print(f"Tables ready: {ok}" + ("" if ok else " (run `python -m aurum.cli init-db`)"))
    for symbol in svc.symbols():
        ctx = _guarded(lambda: svc.builder.build(symbol))
        gaps = {k: v["reason"] for k, v in ctx.availability.items() if not v["available"]}
        console.print(f"{symbol}: {'all inputs available' if not gaps else gaps}")
    ai = svc.ai.state()
    key = bool(svc.settings.anthropic_api_key and svc.settings.anthropic_api_key.get_secret_value())
    console.print(f"AI reviewer: enabled {ai['enabled']} · key present {key} · budget ${ai['budget_usd']} · used ${ai['budget_used_usd']}")
