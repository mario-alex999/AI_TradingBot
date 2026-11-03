"""Module 04 CLI: python -m aurum.cli intelligence ...

    intelligence check                               configuration, schema, providers, data availability
    intelligence sessions [--symbol S] [--as-of T]   sessions, overlaps, kill zones
    intelligence session-levels --symbol EURUSD      previous / current session, day, week, month levels
    intelligence calendar [--days 7] [--currency USD] [--importance high]
    intelligence calendar-update [--days-back 7] [--days-ahead 21]
    intelligence calendar-upcoming [--hours 48]
    intelligence macro                               central-bank rates and indicators
    intelligence macro-update                        refresh official central-bank / macro data
    intelligence news-update                         refresh official central-bank news feeds
    intelligence volatility --symbol EURUSD
    intelligence conditions --symbol EURUSD
    intelligence analyze --symbol EURUSD [--persist] [--json]
    intelligence meetings-check                      configured vs official central-bank meeting dates
    intelligence meetings-update                     write official dates to config/central_banks.meetings.yaml
    intelligence status
    intelligence export --symbol EURUSD [--out data/exports]

Every table says where its data came from; unavailable providers are shown as unavailable, never filled in.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

intelligence_app = typer.Typer(no_args_is_help=True,
                               help="Module 04: trading sessions and market intelligence (read-only).")
console = Console()
UTC = timezone.utc


def _service():
    from aurum.cli import _settings
    from aurum.market_intelligence.service import MarketIntelligenceService
    return MarketIntelligenceService(_settings())


def _as_of(v: Optional[str]) -> Optional[datetime]:
    if not v:
        return None
    d = datetime.fromisoformat(v)
    return d.replace(tzinfo=UTC) if d.tzinfo is None else d


def _fail(msg: str) -> None:
    console.print(f"[bold red]ERROR[/] {msg}")
    raise typer.Exit(1)


def _guard(fn, *a, **k):
    from aurum.market_intelligence.service import SchemaNotReady, UnknownInstrumentError
    try:
        return fn(*a, **k)
    except (UnknownInstrumentError, ValueError, SchemaNotReady) as exc:
        _fail(str(exc.args[0] if exc.args else exc))
    except Exception as exc:  # noqa: BLE001
        _fail(f"{type(exc).__name__}: {exc}. Is the database running and initialised (python -m aurum.cli init-db)?")


def _dump(obj: Any) -> None:
    console.print_json(json.dumps(obj, default=str))


def _t(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, str):
        v = datetime.fromisoformat(v)
    return v.astimezone(UTC).strftime("%Y-%m-%d %H:%M")


def _n(v: Any, d: int = 5) -> str:
    return "-" if v is None else f"{v:.{d}f}" if isinstance(v, float) else str(v)


@intelligence_app.command("check")
def check() -> None:
    """Configuration, schema, data and provider readiness."""
    svc = _service()
    st = _guard(svc.status)
    t = Table(title="Module 04 readiness", show_lines=False)
    t.add_column("Check")
    t.add_column("Result")
    t.add_row("Configuration", f"OK (fingerprint {st['config_fingerprint']}, methodology {st['methodology_version']})")
    t.add_row("Database tables", "OK" if st["schema_ready"] else f"MISSING {st['missing_tables']} - run init-db")
    for sym in st["instruments"]:
        df = svc.candles.frame(sym, "H1", datetime.now(UTC))
        t.add_row(f"Module 01 candles {sym}", f"{len(df)} closed H1 candles, last {_t(df['ts'].iloc[-1]) if len(df) else '-'}"
                  if len(df) else "[red]none stored[/]")
    for p in st["calendar_providers"]:
        t.add_row(f"Calendar: {p['provider']}", f"{p['status']}" + (f" - {p['access']}" if not p["configured"] else "")
                  + (f" (last success {_t(p['last_success_at'])})" if p.get("last_success_at") else ""))
    cov = st.get("calendar_coverage") or {}
    t.add_row("Calendar coverage", f"{cov.get('level', 'none').upper()} - {cov.get('reason', '')}")
    t.add_row("Official macro sources", "enabled" if st["official_data_enabled"] else "disabled")
    t.add_row("FRED (optional)", "configured" if st["fred_configured"] else "not configured (FRED_API_KEY)")
    for bank, m in (st.get("meeting_schedules") or {}).items():
        if m["status"] != "ok":
            t.add_row(f"Meetings: {bank}", f"[yellow]{m['status']}[/] - {escape(m.get('message') or '')}")
    t.add_row("Trade execution", "none (read-only module)")
    console.print(t)


def _meetings(write: bool, as_json: bool) -> None:
    res = _guard(_service().meetings_check, write)
    if as_json:
        return _dump(res)
    t = Table(title="Central-bank meeting schedules (official calendars)")
    for c in ("Bank", "Status", "Official dates", "Missing from config", "Configured, not on page", "Schedule"):
        t.add_column(c)
    for bank, r in res["banks"].items():
        st = r.get("staleness") or {}
        t.add_row(bank, r["status"] if r["status"] == "ok" else f"[yellow]{r['status']}[/] {escape(r.get('error', ''))}",
                  str(len(r.get("official") or [])), ", ".join(r.get("missing_from_config") or []) or "-",
                  ", ".join(r.get("not_on_official_page") or []) or "-",
                  f"{st.get('status')} (last {st.get('last_configured', '-')})")
    console.print(t)
    if res.get("written"):
        console.print(f"Wrote official dates to {res['written']} (merged with central_banks.yaml at load).")


@intelligence_app.command("meetings-check")
def meetings_check(as_json: bool = typer.Option(False, "--json")) -> None:
    """Compare configured meeting dates with the banks' official calendars (read-only)."""
    _meetings(False, as_json)


@intelligence_app.command("meetings-update")
def meetings_update(as_json: bool = typer.Option(False, "--json")) -> None:
    """Write the official meeting dates to config/central_banks.meetings.yaml (additive overlay)."""
    _meetings(True, as_json)


@intelligence_app.command("sessions")
def sessions(symbol: Optional[str] = typer.Option(None, "--symbol", "-s"), as_of: Optional[str] = typer.Option(None, "--as-of"),
             as_json: bool = typer.Option(False, "--json")) -> None:
    snap = _guard(_service().sessions, _as_of(as_of), symbol)
    if as_json:
        return _dump(snap)
    t = Table(title=f"Sessions at {_t(snap['as_of'])} UTC (calculated)")
    for c in ("Window", "Kind", "Zone", "Local now", "Local hours", "Status", "Opens (UTC)", "Closes (UTC)", "Min to open/close"):
        t.add_column(c)
    for s in snap["sessions"] + snap["kill_zones"]:
        cur, nxt = s["current"], s["next"]
        t.add_row(s["name"], s["kind"], f"{s['timezone']} {'DST' if s['dst'] else ''}", s["local_time"][11:],
                  f"{s['local_open']}-{s['local_close']}", s["status"], _t((cur or nxt or {}).get("start_utc")),
                  _t((cur or nxt or {}).get("end_utc")),
                  _n(s["minutes_until_close"] if cur else s["minutes_until_open"], 0))
    console.print(t)
    for o in snap["overlaps"]:
        iv = o["current"] or o["next"] or {}
        console.print(f"  {o['name']:<16} {o['kind']:<10} {'ACTIVE' if o['active'] else 'inactive':<9} "
                      f"{_t(iv.get('start_utc'))} -> {_t(iv.get('end_utc'))}")
    console.print(f"Active: {', '.join(snap['active_sessions']) or 'none'}; kill zones: "
                  f"{', '.join(snap['active_kill_zones']) or 'none'}")


@intelligence_app.command("session-levels")
def session_levels(symbol: str = typer.Option(..., "--symbol", "-s"), as_of: Optional[str] = typer.Option(None, "--as-of"),
                   as_json: bool = typer.Option(False, "--json")) -> None:
    r = _guard(_service().levels, symbol, _as_of(as_of))
    if as_json:
        return _dump(r)
    t = Table(title=f"{r['symbol']} levels at {_t(r['as_of'])} UTC (calculated from Module 01 candles)")
    for c in ("Level", "Price", "Source candle", "TF", "Period", "State", "Swept", "Broken", "Missing"):
        t.add_column(c)
    for lv in r["levels"]:
        t.add_row(lv["level_type"], _n(lv["price"]), _t(lv["source_candle_time"]), lv["timeframe"],
                  f"{_t(lv['period_start'])} -> {_t(lv['period_end'])}", lv["state"], _t(lv.get("swept_at")),
                  _t(lv.get("broken_at")), _n(lv.get("missing_candles"), 0))
    console.print(t)
    for i in r["issues"]:
        console.print(f"[yellow]note[/] {i}")


def _events_table(title: str, res: dict[str, Any]) -> None:
    t = Table(title=title)
    for c in ("Time (UTC)", "Precision", "Ccy", "Event", "Imp.", "Category", "Forecast", "Previous", "Actual", "Status",
              "Instruments", "Provider"):
        t.add_column(c)
    for e in res["items"]:
        t.add_row(_t(e["scheduled_at"]), e["time_precision"], e.get("currency") or "-", e["name"][:48],
                  e.get("importance") or "-", e["category"], _n(e.get("consensus"), 2), _n(e.get("previous"), 2),
                  _n(e.get("actual"), 2), e["status"], ",".join(a["instrument"] for a in e["affected_instruments"]) or "-",
                  e["provider"])
    console.print(t)
    cov = res["coverage"]
    console.print(f"Coverage: [bold]{cov['level'].upper()}[/] - {cov['reason']}")


@intelligence_app.command("calendar")
def calendar(days: int = typer.Option(7, "--days"), currency: Optional[str] = typer.Option(None, "--currency"),
             importance: Optional[str] = typer.Option(None, "--importance"), as_json: bool = typer.Option(False, "--json")) -> None:
    now = datetime.now(UTC)
    res = _guard(_service().calendar, now - timedelta(days=1), now + timedelta(days=days), None,
                 [currency.upper()] if currency else None, [importance.lower()] if importance else None,
                 None, None, 1, 500)
    if as_json:
        return _dump(res)
    _events_table(f"Stored economic events ({res['total']}) - provider data", res)


@intelligence_app.command("calendar-upcoming")
def calendar_upcoming(hours: int = typer.Option(48, "--hours")) -> None:
    now = datetime.now(UTC)
    res = _guard(_service().calendar, now, now + timedelta(hours=hours), None, None, None, None, None, 1, 500)
    _events_table(f"Upcoming events, next {hours} h - provider data", res)


@intelligence_app.command("calendar-update")
def calendar_update(days_back: int = typer.Option(7, "--days-back"), days_ahead: int = typer.Option(21, "--days-ahead")) -> None:
    res = _guard(_service().refresh_calendar, days_back, days_ahead, lambda m: console.print(f"  {m}"))
    failed = [p for p in res["providers"] if p["status"] != "success"]
    console.print(f"Coverage: [bold]{res['coverage']['level'].upper()}[/] - {res['coverage']['reason']}")
    if failed:
        raise typer.Exit(1)


@intelligence_app.command("macro")
def macro(as_of: Optional[str] = typer.Option(None, "--as-of"), as_json: bool = typer.Option(False, "--json")) -> None:
    o = _guard(_service().macro_overview, _as_of(as_of))
    if as_json:
        return _dump(o)
    t = Table(title=f"Central banks at {_t(o['as_of'])} UTC (official sources)")
    for c in ("Bank", "Rate", "In effect since", "Latest obs.", "Previous", "Latest decision", "Next meeting (UTC)", "Source", "Retrieved", "Status"):
        t.add_column(c)
    for b in o["central_banks"]:
        rate = "-" if b.get("current_rate") is None else (f"{b['current_rate_lower']:.2f}-{b['current_rate']:.2f}"
                                                         if b.get("current_rate_lower") is not None else f"{b['current_rate']:.2f}")
        d = b.get("latest_decision") or {}
        nm = b.get("next_meeting") or {}
        t.add_row(b["bank"].upper(), rate, str(b.get("last_change_effective") or "-"), str(b.get("effective_date") or "-"), _n(b.get("previous_rate"), 2),
                  f"{d.get('decision_date', '-')} {d.get('status', '')} {d.get('change_bps', '') if d else ''}",
                  _t(nm.get("announcement_utc")) + (" (typical time)" if nm else ""), b.get("source") or "-",
                  _t(b.get("retrieved_at")), b["status"] + (" STALE" if b.get("stale") else ""))
    console.print(t)
    t2 = Table(title="Indicators (point-in-time)")
    for c in ("Series", "Name", "Period", "Value", "Previous", "Source", "Status"):
        t2.add_column(c)
    for i in o["indicators"]:
        la, pr = i.get("latest") or {}, i.get("previous") or {}
        t2.add_row(i["id"], i["name"], str(la.get("period", "-")), _n(la.get("value"), 2), _n(pr.get("value"), 2),
                   i["source"], i["status"])
    console.print(t2)


@intelligence_app.command("macro-update")
def macro_update() -> None:
    res = _guard(_service().refresh_macro, lambda m: console.print(f"  {m}"))
    for k, v in (res.get("series") or {}).items():
        console.print(f"  {k}: {v.get('status')} {v.get('inserted', '')} {v.get('reason') or v.get('error') or ''}")


@intelligence_app.command("news-update")
def news_update() -> None:
    res = _guard(_service().refresh_news)
    console.print(f"{res.get('received', 0)} headlines received, {res.get('inserted', 0)} new; errors: {res.get('errors')}")


@intelligence_app.command("volatility")
def volatility(symbol: str = typer.Option(..., "--symbol", "-s"), as_of: Optional[str] = typer.Option(None, "--as-of"),
               as_json: bool = typer.Option(False, "--json")) -> None:
    v = _guard(_service().volatility, symbol, _as_of(as_of))
    if as_json:
        return _dump(v)
    t = Table(title=f"{v['symbol']} volatility at {_t(v['as_of'])} UTC (calculated)")
    for c in ("TF", "Status", "ATR", "ATR pct", "Realised vol %", "Annualised %", "Expansion", "Abnormal", "Regime"):
        t.add_column(c)
    for tf, x in v["timeframes"].items():
        t.add_row(tf, x["status"], _n(x.get("atr")), _n(x.get("atr_percentile"), 0), _n(x.get("realised_vol_pct"), 3),
                  _n(x.get("annualised_vol_pct"), 1), f"{x.get('expansion_state', '-')} {_n(x.get('expansion_ratio'), 2)}",
                  str(x.get("abnormal_movement", "-")), x["regime"])
    console.print(t)
    console.print(f"Regime: [bold]{v['regime']}[/] - {v['regime_reason']}")


@intelligence_app.command("conditions")
def conditions(symbol: str = typer.Option(..., "--symbol", "-s"), as_of: Optional[str] = typer.Option(None, "--as-of")) -> None:
    r = _guard(_service().analyze, symbol, _as_of(as_of))
    c = r["conditions"]
    console.print(f"[bold]{r['symbol']}[/] at {_t(r['as_of'])} UTC: " + " | ".join(c["summary"]))
    for x in c["conditions"]:
        console.print(f"  {'*' if x['active'] else ' '} {x['tag']:<20} {x['confidence']:.2f}")
        for e in x["evidence"]:
            console.print(f"        - {escape('[' + str(e['source']) + ']')} {escape(str(e['fact']))}")
    console.print(f"[dim]{c['note']}[/]")


@intelligence_app.command("analyze")
def analyze(symbol: str = typer.Option(..., "--symbol", "-s"), as_of: Optional[str] = typer.Option(None, "--as-of"),
            persist: bool = typer.Option(False, "--persist", help="Store results in the Module 04 tables"),
            as_json: bool = typer.Option(False, "--json")) -> None:
    r = _guard(_service().analyze, symbol, _as_of(as_of), persist)
    if as_json:
        return _dump(r)
    sess = r["sessions"]
    console.print(f"[bold]{r['symbol']}[/] market intelligence at {_t(r['as_of'])} UTC "
                  f"(module {r['module_version']}, methodology {r['methodology_version']})")
    console.print(f"  Sessions: {', '.join(sess['active_sessions']) or 'none'}; overlap: {sess['in_overlap']}; "
                  f"kill zones: {', '.join(sess['active_kill_zones']) or 'none'}")
    console.print(f"  Volatility: {r['volatility']['regime']} - {r['volatility']['regime_reason']}")
    er = r["event_risk"]
    console.print(f"  Event risk: {er['state']} - {er['reason']}")
    console.print(f"  Conditions: {', '.join(r['conditions']['tags'])}")
    s = r["scores"]
    console.print(f"  Scores: event risk {_n((s['event_risk'] or {}).get('score'), 1)}, session activity "
                  f"{_n((s['session_activity'] or {}).get('score'), 1)}, volatility {_n((s['volatility'] or {}).get('score'), 1)}")
    for name, i in r["integrations"].items():
        console.print(f"  {name}: {'available' if i and i.get('available') else 'UNAVAILABLE'}"
                      f"{' - ' + str(i.get('reason')) if i and i.get('reason') else ''}")
    console.print(f"  Liquidity events: {len(r['liquidity']['events'])}; levels: {len(r['levels']['levels'])}")
    if r["run_uid"]:
        console.print(f"  Persisted as run {r['run_uid']}")
    console.print(f"[dim]{r['disclaimer']}[/]")


@intelligence_app.command("status")
def status(as_json: bool = typer.Option(False, "--json")) -> None:
    st = _guard(_service().status)
    if as_json:
        return _dump(st)
    console.print(f"Schema ready: {st['schema_ready']}  coverage: {(st.get('calendar_coverage') or {}).get('level')}")
    for k, v in (st.get("row_counts") or {}).items():
        console.print(f"  {k:<40} {v}")
    for l in st.get("last_ingestions") or []:
        console.print(f"  ingestion {_t(l['started_at'])} {l['provider']:<20} {l['status']:<8} "
                      f"recv={l['received']} new={l['inserted']} upd={l['updated']} {l['error'] or ''}")


@intelligence_app.command("export")
def export(symbol: str = typer.Option(..., "--symbol", "-s"), as_of: Optional[str] = typer.Option(None, "--as-of"),
           out: Path = typer.Option(Path("data/exports/intelligence"), "--out")) -> None:
    """Write the full analysis (JSON) - provider data and calculated values are labelled in the file."""
    svc = _service()
    r = _guard(svc.analyze, symbol, _as_of(as_of))
    folder = svc.settings.resolve(out)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{r['symbol']}_{r['as_of']:%Y%m%dT%H%M}Z_intelligence.json"
    path.write_text(json.dumps(r, default=str, indent=1), encoding="utf-8")
    console.print(f"Written {path}")
