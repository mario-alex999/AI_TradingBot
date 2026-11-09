"""CLI for Module 06: python -m aurum.cli risk ...  None of these commands places, modifies or closes an order."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

import typer

from aurum.ui import Table, console, error

risk_app = typer.Typer(help="Module 06 risk management: assessments, limits, locks, simulation. Never places trades.",
                       no_args_is_help=True)
config_app = typer.Typer(help="Show or change the versioned risk configuration.", no_args_is_help=True)
locks_app = typer.Typer(help="List, create and release risk locks.", no_args_is_help=True)
events_app = typer.Typer(help="Outbox events for n8n and other consumers.", no_args_is_help=True)
risk_app.add_typer(config_app, name="config")
risk_app.add_typer(locks_app, name="locks")
risk_app.add_typer(events_app, name="events")
STYLE = {"PASS": "green", "REVIEW": "yellow", "BLOCK": "red", "ERROR": "magenta", "WITHIN_LIMITS": "green", "WARNING": "yellow",
         "BLOCKED": "red", "DATA_UNAVAILABLE": "magenta"}


def _svc() -> Any:
    from aurum.config import get_settings
    from aurum.database.connection import engine_from_settings
    from aurum.risk.service import RiskService
    s = get_settings()
    return RiskService(engine_from_settings(s), s)


def _guard(fn: Any) -> Any:
    from aurum.risk.service import RiskError
    try:
        return fn()
    except RiskError as exc:
        error(f"{exc.code}: {exc.message}")
        if exc.details:
            console.print(exc.details)
        raise typer.Exit(1) from exc


def _actor(name: str | None) -> str:
    return name or os.environ.get("USERNAME") or os.environ.get("USER") or "cli"


def _print_assessment(r: dict[str, Any]) -> None:
    d = r["decision"]
    console.rule(f"[bold]{r['instrument']} {r['direction']}[/] - [{STYLE.get(d, 'white')}]{d}[/]", align="left", style="muted")
    console.print(f"assessment {r['assessment_id']} · config v{r['config_version']} · expires {r['expires_at']}"
                  + (" · [muted]replayed[/]" if r.get("replayed") else ""))
    c = r.get("calculation") or {}
    if c:
        console.print(f"volume {r['final_volume'] or r['recommended_volume']} lots · risk {r['monetary_risk']} {r['account_currency']} "
                      f"({r['risk_pct']}%) · reward {r['potential_reward']} · R:R after costs {r['risk_reward']}")
    for k in ("account_status", "portfolio_status", "correlation_status", "market_status"):
        st = r[k]["status"]
        console.print(f"  {k.replace('_status', ''):<12} [{STYLE.get(st, 'white')}]{st}[/]")
    for title, key, colour in (("Violations", "rule_violations", "red"), ("Errors", "errors", "magenta"), ("Warnings", "warnings", "yellow")):
        for v in r[key]:
            console.print(f"  [{colour}]{title[:-1]}[/] {v['rule_id']} {v['name']}: {v['message']}")
    console.print(f"[muted]{r['disclaimer']}[/]")


@risk_app.command()
def assess(symbol: Optional[str] = typer.Option(None, "--symbol", "-s"), direction: Optional[str] = typer.Option(None, "--direction", "-d"),
           entry: Optional[float] = typer.Option(None), sl: Optional[float] = typer.Option(None, "--sl"),
           tp: Optional[float] = typer.Option(None, "--tp"), risk_pct: Optional[float] = typer.Option(None, "--risk-pct"),
           volume: Optional[float] = typer.Option(None, "--volume"), idea: Optional[str] = typer.Option(None, "--idea", help="Module 05 candidate id"),
           strategy: Optional[str] = typer.Option(None), actor: Optional[str] = typer.Option(None),
           as_json: bool = typer.Option(False, "--json")) -> None:
    """Full risk decision for one proposed trade (or a Module 05 trade idea)."""
    body = {"symbol": symbol, "direction": direction, "entry": entry, "stop_loss": sl, "take_profit": tp, "risk_pct": risk_pct,
            "requested_volume": volume, "trade_idea_id": idea, "strategy": strategy, "source": "cli"}
    body = {k: v for k, v in body.items() if v is not None}
    r = _guard(lambda: _svc().assess(body, _actor(actor), None, None))
    console.print_json(json.dumps(r, default=str)) if as_json else _print_assessment(r)


@risk_app.command()
def size(symbol: str = typer.Option(..., "--symbol", "-s"), direction: str = typer.Option(..., "--direction", "-d"),
         entry: float = typer.Option(...), sl: float = typer.Option(..., "--sl"), tp: Optional[float] = typer.Option(None, "--tp"),
         risk_pct: Optional[float] = typer.Option(None, "--risk-pct"), equity: Optional[float] = typer.Option(None)) -> None:
    """What-if position size (stores nothing)."""
    r = _guard(lambda: _svc().position_size({"symbol": symbol, "direction": direction, "entry": entry, "stop_loss": sl,
                                             "take_profit": tp, "risk_pct": risk_pct, "equity": equity}))
    c = r["calculation"] or {}
    console.print(f"[bold]{symbol} {direction}[/] valid={r['valid']} equity source={r['equity_source']}")
    for k in ("recommended_volume", "estimated_loss", "estimated_loss_pct", "risk_reward", "adjusted_risk_reward",
              "total_loss_per_lot", "transaction_cost", "value_method"):
        console.print(f"  {k:<22} {c.get(k)}")
    for v in r["failed_checks"] + r["warnings"]:
        console.print(f"  [yellow]{v['rule_id']}[/] {v['message']}")


@risk_app.command()
def status() -> None:
    """Account risk overview."""
    a = _guard(lambda: _svc().account())
    st = a["overall_status"]
    console.print(f"Overall [{STYLE.get(st, 'white')}]{st}[/] · config v{a['config_version']} ({a['profile']}) · locks {a['active_locks']}")
    if a["reason"]:
        console.print(f"[yellow]{a['reason']}[/]")
    t = Table("Metric", "Value", "Limit")
    t.add_row("Equity / balance", f"{a['equity']} / {a['balance']} {a['currency'] or ''}", "")
    t.add_row("Drawdown", f"{a['drawdown_pct']}%", f"{a['max_drawdown_pct']}%")
    for k, p in a["periods"].items():
        t.add_row(f"{k} loss ({p['label']})", f"{p['loss_pct']}%" + (" [muted](approx.)[/]" if p["start_uncertain"] else ""), f"{p['limit_pct']}%")
    t.add_row("Consecutive losses", str(a["consecutive_losses"]), str(a["max_consecutive_losses"]))
    t.add_row("Trades today", str(a["trades_today"]), str(a["max_trades_per_day"]))
    t.add_row("Margin utilisation", f"{a['margin_utilization_pct']}%", "")
    console.print(t)


@risk_app.command()
def portfolio() -> None:
    """Open risk, exposure, correlation."""
    p = _guard(lambda: _svc().portfolio())
    console.print(f"Status [{STYLE.get(p['status'], 'white')}]{p['status']}[/] · open risk {p['total_open_risk']} ({p['total_open_risk_pct']}%)")
    for f in p["concentration_warnings"]:
        console.print(f"  [yellow]![/] {f}")
    t = Table("Kind", "Ref", "Symbol", "Side", "Risk", "Risk %")
    for i in p["positions"]:
        t.add_row(i["kind"], str(i["ref"])[:12], i["symbol"], i["side"], str(i["risk"]), str(i["risk_pct"]))
    console.print(t)


@risk_app.command()
def rules() -> None:
    """Rule catalog."""
    t = Table("Id", "Name", "Severity", "Enabled", "Category")
    for r in _guard(lambda: _svc().rules()):
        t.add_row(r["rule_id"], r["name"], r["severity"], "yes" if r["enabled"] else "no", r["category"])
    console.print(t)


@risk_app.command()
def history(limit: int = typer.Option(20), decision: Optional[str] = typer.Option(None), symbol: Optional[str] = typer.Option(None)) -> None:
    """Recent assessments."""
    h = _guard(lambda: _svc().history(limit=limit, offset=0, decision=decision, symbol=symbol, rule_id=None, strategy=None,
                                      date_from=None, date_to=None))
    t = Table("Created", "Symbol", "Dir", "Decision", "Volume", "Risk", "Violations")
    for r in h["items"]:
        t.add_row(str(r["created_at"])[:19], r["instrument"], r["direction"], f"[{STYLE.get(r['decision'])}]{r['decision']}[/]",
                  str(r["final_volume"] or r["recommended_volume"]), str(r["monetary_risk"]), ",".join(r["rule_violations"] + r["errors"]))
    console.print(t)
    console.print(f"[muted]{h['total']} total[/]")


@risk_app.command()
def health() -> None:
    """Dependency health and data freshness."""
    console.print_json(json.dumps(_guard(lambda: _svc().health()), default=str))


@risk_app.command("audit-verify")
def audit_verify() -> None:
    """Verify the audit log's hash chain."""
    ok, n, problem = _svc().repo.verify_audit()
    console.print(f"{n} audit rows: " + ("[green]chain intact[/]" if ok else f"[red]{problem}[/]"))
    if not ok:
        raise typer.Exit(1)


@risk_app.command()
def simulate(scenario: Optional[Path] = typer.Option(None, help="YAML file or folder; default: built-in scenarios"),
             profile: Optional[str] = typer.Option(None), report: Optional[Path] = typer.Option(None, help="Write a Markdown report")) -> None:
    """Run risk scenarios on simulated data in a throw-away database (no broker, no live data)."""
    from aurum.risk.simulation import SIM_NOTICE, load_scenarios, report_markdown, run_scenario
    results = []
    for sc in load_scenarios(scenario):
        if profile:
            sc["profile"] = profile
        results.append(run_scenario(sc))
    t = Table("Scenario", "Eval", "PASS", "REVIEW", "BLOCK", "ERROR", "Max DD %", "Locks")
    for r in results:
        t.add_row(r["name"], str(r["trades_evaluated"]), str(r["passed"]), str(r["review"]), str(r["blocked"]), str(r["errors"]),
                  f"{r['max_drawdown_pct']:.2f}", str(r["locks"]))
    console.print(t)
    console.print(f"[muted]{SIM_NOTICE}[/]")
    if report:
        report.write_text(report_markdown(results), encoding="utf-8")
        console.print(f"Report written to {report}")


@config_app.command("show")
def config_show() -> None:
    c = _guard(lambda: _svc().config())
    console.print(f"Active version {c['version']} ({c['meta']['profile']}) by {c['meta']['created_by']}: {c['meta']['reason']}")
    console.print_json(json.dumps(c["config"]))


@config_app.command("set")
def config_set(reason: str = typer.Option(..., help="Why this change is made"), profile: Optional[str] = typer.Option(None),
               changes: Optional[str] = typer.Option(None, help='JSON, e.g. {"risk_per_trade_pct": 0.5}'),
               acknowledge_weakening: bool = typer.Option(False, "--acknowledge-weakening"),
               actor: Optional[str] = typer.Option(None)) -> None:
    """Create a new configuration version."""
    svc = _svc()
    body = {"reason": reason, "base_version": svc.config()["version"], "profile": profile,
            "changes": json.loads(changes) if changes else {}, "acknowledge_weakening": acknowledge_weakening}
    out = _guard(lambda: svc.update_config(body, _actor(actor), None))
    console.print(f"[green]Configuration version {out['version']} is active.[/]")
    for w in out.get("weakened") or []:
        console.print(f"  [yellow]loosened[/] {w}")


@locks_app.command("list")
def locks_list(history: bool = typer.Option(False, "--history")) -> None:
    data = _guard(lambda: _svc().locks(history))
    t = Table("Lock", "Status", "Scope", "Reason", "Since", "Expires")
    for r in data["active"] + data["history"]:
        t.add_row(r["lock_id"][:8], r["status"], f"{r['scope']} {r['scope_value'] or ''}", r["reason"][:70], str(r["activated_at"])[:16],
                  str(r["expires_at"] or "manual")[:16])
    console.print(t)


@locks_app.command("create")
def locks_create(reason: str = typer.Option(...), scope: str = typer.Option("global"), value: Optional[str] = typer.Option(None),
                 actor: Optional[str] = typer.Option(None)) -> None:
    r = _guard(lambda: _svc().create_lock({"scope": scope, "scope_value": value, "reason": reason}, _actor(actor), None))
    console.print(f"[red]Lock {r['lock_id']} active[/] ({scope}).")


@locks_app.command("release")
def locks_release(lock_id: str, reason: str = typer.Option(...), actor: Optional[str] = typer.Option(None)) -> None:
    svc = _svc()
    full = next((x["lock_id"] for x in svc.locks(False)["active"] if x["lock_id"].startswith(lock_id)), lock_id)
    r = _guard(lambda: svc.release_lock(full, {"reason": reason}, _actor(actor), None))
    console.print(f"[green]Released {full}[/]. {r.get('note', '')}")


@events_app.command("list")
def events_list(after: int = typer.Option(0), limit: int = typer.Option(20)) -> None:
    t = Table("#", "Type", "Entity", "Status", "Attempts")
    for e in _svc().events(after, limit, None):
        t.add_row(str(e["sequence"]), e["event_type"], str(e["entity_id"])[:12], e["delivery"]["status"], str(e["delivery"]["attempts"]))
    console.print(t)


@events_app.command("dispatch")
def events_dispatch(url: Optional[str] = typer.Option(None, help="Webhook; default RISK_EVENTS_WEBHOOK_URL")) -> None:
    """Deliver pending events to a webhook (for example an n8n Webhook node), with retries and dead-lettering."""
    target = url or os.environ.get("RISK_EVENTS_WEBHOOK_URL")
    stats = _guard(lambda: _svc().dispatch_events(target))
    console.print(stats)
