"""AURUM AI Module 01 command-line interface (read-only market data engine).

    python -m aurum.cli check
    python -m aurum.cli init-db
    python -m aurum.cli specs
    python -m aurum.cli collect [--symbol EURUSD] [--timeframe M15] [--full] [--loop]
    python -m aurum.cli export  [--symbol EURUSD] [--timeframe H1] [--format csv|parquet|both] [--on-exists skip|version|overwrite]
    python -m aurum.cli cot ... (Module 02: check, datasets, backfill, update, calculate, report, status, quality, export)
    python -m aurum.cli analyze ... (Module 03: run, confluence, config)
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Optional

import typer
from aurum.ui import Table, console, error, humanize, kv_table, status

from aurum.config import (TIMEFRAME_SECONDS, InstrumentConfig, Settings, SymbolMappingError, enabled_instruments,
                          get_settings, load_instruments)
from aurum.logging_config import setup_logging

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="AURUM AI - Module 01: READ-ONLY MetaTrader 5 market data collection engine.")
log = logging.getLogger("aurum.cli")


def _settings() -> Settings:
    s = get_settings()
    secrets = [x.get_secret_value() for x in (s.mt5_password, s.db_password, s.database_url, s.aurum_admin_api_key,
                                              s.economic_calendar_api_key, s.fred_api_key)
               if x is not None]
    setup_logging(s.log_level, s.resolve(s.log_dir), secrets)
    return s


def _instruments(s: Settings, symbol: str | None) -> list[InstrumentConfig]:
    all_inst = load_instruments(s.resolve(s.instruments_config))
    if symbol:
        sym = symbol.upper()
        if sym not in all_inst:
            raise SymbolMappingError(f"'{sym}' is not defined in {s.instruments_config}. "
                                     f"Known: {', '.join(all_inst)}")
        return [all_inst[sym]]
    return enabled_instruments(s, all_inst)


def _timeframes(s: Settings, timeframe: str | None) -> list[str]:
    if timeframe:
        tf = timeframe.upper()
        if tf not in TIMEFRAME_SECONDS:
            raise typer.BadParameter(f"Unknown timeframe '{tf}'. Supported: {', '.join(TIMEFRAME_SECONDS)}")
        return [tf]
    return s.enabled_timeframes


def _fail(msg: str, code: int = 1) -> None:
    error(msg)
    raise typer.Exit(code)


def _gateway(s: Settings):
    from aurum.gateways.mt5_gateway import MT5Gateway
    return MT5Gateway(s)


def _repo(s: Settings):
    from aurum.database.connection import engine_from_settings
    from aurum.database.repository import Repository
    return Repository(engine_from_settings(s))


@app.command()
def check() -> None:
    """Check MT5 connection, DEMO account status, symbols, and database connectivity."""
    from aurum.database.connection import check_database, engine_from_settings
    from aurum.gateways.mt5_gateway import MT5Error
    s = _settings()
    ok = True
    console.rule("[bold]AURUM AI · system check[/] [muted](read-only)[/]", style="muted", align="left")
    try:
        instruments = _instruments(s, None)
    except (SymbolMappingError, FileNotFoundError) as exc:
        _fail(str(exc))
    # MT5
    try:
        with _gateway(s) as gw:
            r = gw.report
            console.print(kv_table("MetaTrader 5", [
                ("Account", r.login), ("Server", r.server), ("Broker", r.company), ("Account type", r.trade_mode),
                ("Currency", r.currency), ("Leverage", f"1:{r.leverage}"), ("Terminal", r.terminal_name),
                ("Build", r.terminal_build), ("Connected", r.terminal_connected), ("Broker clock", r.broker_clock)]))
            st = Table(title="Symbols", numeric=("Bid", "Ask", "Spread (pts)"))
            for c in ("Symbol", "Broker symbol", "Status", "Bid", "Ask", "Spread (pts)", "Trade mode"):
                st.add_column(c)
            for inst in instruments:
                spec = gw.symbol_spec(inst.broker_symbol)
                if spec is None:
                    ok = False
                    st.add_row(inst.symbol, inst.broker_symbol, status("not found"), "", "", "", "")
                else:
                    st.add_row(inst.symbol, inst.broker_symbol, status("ok"), str(spec["bid"]), str(spec["ask"]),
                               str(spec["spread_points"]), str(spec["trade_mode"]))
            console.print(st)
            cc = gw.verify_clock(instruments)
            console.print(f"Broker clock {status(cc.status)}: {cc.message}")
            ok = ok and cc.ok
    except MT5Error as exc:
        ok = False
        console.print(f"[negative]MT5:[/] {exc}")
    # Database
    try:
        info = check_database(engine_from_settings(s))
        console.print(f"Database {status('ok')} [muted]{s.safe_database_url} · {info['dialect']} · "
                      f"TimescaleDB {'on' if info['timescaledb'] else 'off'} · schema "
                      f"{'ready' if info['schema_ready'] else 'not initialised'}[/]")
        if not info["schema_ready"]:
            ok = False
            console.print("[warning]Schema not initialised.[/] Run: python -m aurum.cli init-db")
    except Exception as exc:
        ok = False
        console.print(f"[negative]Database:[/] cannot connect to {s.safe_database_url} ({type(exc).__name__}: {exc})")
    console.rule("[positive]All checks passed[/]" if ok else "[negative]Some checks failed[/]", style="muted", align="left")
    raise typer.Exit(0 if ok else 1)


@app.command()
def diagnose(mt5: bool = typer.Option(True, "--mt5/--no-mt5", help="Diagnose the MetaTrader 5 connection"),
             db: bool = typer.Option(True, "--db/--no-db", help="Diagnose the database connection")) -> None:
    """Step-by-step, read-only diagnosis of the MT5 and database connections (explains errors such as -6)."""
    from aurum.diagnostics import FAIL, OK, WARN, diagnose_database, diagnose_mt5
    s = _settings()
    steps = []
    if mt5:
        try:
            instruments = _instruments(s, None)
        except (SymbolMappingError, FileNotFoundError) as exc:
            _fail(str(exc))
        steps += diagnose_mt5(s, instruments)
    if db:
        steps += diagnose_database(s)
    t = Table(title="AURUM AI - connection diagnosis (read-only)")
    for c in ("Area", "Step", "Result", "Detail"):
        t.add_column(c)
    for st in steps:
        t.add_row(st.area, st.name, status(st.status), st.detail)
    console.print(t)
    for st in steps:
        if st.hint and st.status != OK:
            console.print(f"[{'negative' if st.status == FAIL else 'warning'}]{st.area} - {st.name}:[/] {st.hint}")
    failed = [st for st in steps if st.status == FAIL]
    warned = [st for st in steps if st.status == WARN]
    console.rule("[negative]Problems found[/]" if failed else ("[warning]Passed with warnings[/]" if warned
                                                               else "[positive]All diagnostics passed[/]"),
                 style="muted", align="left")
    raise typer.Exit(1 if failed else 0)


@app.command("init-db")
def init_db() -> None:
    """Create or upgrade the database schema (idempotent)."""
    from aurum.database.connection import check_database, engine_from_settings, init_database
    s = _settings()
    try:
        engine = engine_from_settings(s)
        steps = init_database(engine)
        info = check_database(engine)
    except Exception as exc:
        _fail(f"Database initialisation failed for {s.safe_database_url}: {type(exc).__name__}: {exc}")
    console.print(f"[positive]Schema ready[/] [muted]({', '.join(steps)}) on {s.safe_database_url}[/]")
    if info["dialect"] == "postgresql":
        console.print("TimescaleDB hypertables: " + ("[positive]enabled[/]" if info["timescaledb"]
                      else "[warning]not available[/], using plain PostgreSQL tables"))


@app.command()
def specs(save: bool = typer.Option(True, help="Store a specification snapshot in the database")) -> None:
    """Display instrument specifications from the broker."""
    from datetime import datetime, timezone
    from aurum.collectors.instrument_collector import collect_specs
    from aurum.gateways.mt5_gateway import MT5Error
    s = _settings()
    try:
        instruments = _instruments(s, None)
        with _gateway(s) as gw:
            res = collect_specs(gw, instruments)
    except (MT5Error, SymbolMappingError, FileNotFoundError) as exc:
        _fail(str(exc))
    cols = ["symbol", "broker_symbol", "asset_class", "digits", "point", "contract_size", "tick_size", "tick_value",
            "volume_min", "volume_max", "volume_step", "spread_points", "bid", "ask", "trade_mode"]
    text_cols = {"symbol", "broker_symbol", "asset_class", "trade_mode"}
    t = Table(title="Instrument specifications", numeric=[humanize(c) for c in cols if c not in text_cols])
    for c in cols:
        t.add_column(humanize(c))
    for spec in res.available.values():
        t.add_row(*[str(spec.get(c)) for c in cols])
    console.print(t)
    for sym, reason in res.unavailable.items():
        console.print(f"[warning]{sym}[/]: {reason}")
    if save and res.available:
        try:
            repo = _repo(s)
            now = datetime.now(timezone.utc)
            for inst in instruments:
                if inst.symbol in res.available:
                    iid = repo.upsert_instrument(inst.symbol, inst.broker_symbol, inst.asset_class, inst.description)
                    repo.insert_spec(iid, res.available[inst.symbol], now, "specs-cli")
            console.print("[positive]Specification snapshot stored.[/]")
        except Exception as exc:
            console.print(f"[warning]Not stored in the database[/] ({type(exc).__name__}: {exc}).")


@app.command()
def collect(symbol: Optional[str] = typer.Option(None, "--symbol", "-s", help="Canonical symbol, e.g. EURUSD"),
            timeframe: Optional[str] = typer.Option(None, "--timeframe", "-t", help="D1, H4, H1, M15, M5 ..."),
            full: bool = typer.Option(False, "--full", help="Re-download HISTORICAL_CANDLE_COUNT candles"),
            loop: bool = typer.Option(False, "--loop", help="Repeat every COLLECTION_INTERVAL_SECONDS until Ctrl+C")) -> None:
    """Collect historical candles, current ticks and specs; validate; store in the database."""
    from aurum.gateways.mt5_gateway import MT5Error
    from aurum.services.market_data_service import MarketDataService
    s = _settings()
    try:
        instruments, tfs = _instruments(s, symbol), _timeframes(s, timeframe)
        repo = _repo(s)
        from aurum.database.connection import check_database
        if not check_database(repo.engine)["schema_ready"]:
            _fail("Database schema not initialised. Run: python -m aurum.cli init-db")
    except typer.Exit:
        raise
    except (SymbolMappingError, FileNotFoundError) as exc:
        _fail(str(exc))
    except Exception as exc:
        _fail(f"Database unavailable at {s.safe_database_url}: {type(exc).__name__}: {exc}")
    try:
        with _gateway(s) as gw:
            service = MarketDataService(s, gw, repo)
            while True:
                # re-verified every run so a broker DST switch / wrong zone never writes shifted candles
                cc = gw.verify_clock(instruments)
                if not cc.ok:
                    log.error("Broker clock mismatch - collection stopped", extra={"detail": cc.message})
                    _fail(cc.message)
                if cc.status == "unknown":
                    log.info("Broker clock not verified", extra={"detail": cc.message})
                summary = service.run(instruments, tfs, full=full)
                _print_summary(summary)
                if not loop:
                    break
                console.print(f"[muted]Next collection in {s.collection_interval_seconds}s · Ctrl+C to stop[/]")
                time.sleep(s.collection_interval_seconds)
    except KeyboardInterrupt:
        console.print("Stopped by user.")
    except MT5Error as exc:
        _fail(str(exc))


@app.command("stream-ticks")
def stream_ticks(interval: float = typer.Option(1.0, "--interval", min=0.25, help="Seconds between quote reads"),
                 symbol: Optional[str] = typer.Option(None, "--symbol", "-s", help="Canonical symbol, e.g. EURUSD"),
                 account_every: float = typer.Option(10.0, "--account-every", min=0,
                                                     help="Seconds between account snapshots (0 = off)"),
                 deals_every: float = typer.Option(60.0, "--deals-every", min=0,
                                                   help="Seconds between deal-history syncs (0 = off)"),
                 lookback_days: int = typer.Option(30, "--lookback-days", min=1, help="Deal history window per sync")) -> None:
    """Record live bid/ask from MT5 every --interval seconds so the dashboard updates in real time (read-only).
    Also mirrors the DEMO account (balance/equity snapshots and positions rebuilt from the deal history)."""
    import uuid

    from aurum.account.recorder import AccountRecorder
    from aurum.collectors.tick_collector import collect_tick
    from aurum.gateways.mt5_gateway import MT5Error
    s = _settings()
    try:
        instruments = _instruments(s, symbol)
        repo = _repo(s)
    except (SymbolMappingError, FileNotFoundError) as exc:
        _fail(str(exc))
    run_id = f"stream-{uuid.uuid4()}"
    stored = 0
    try:
        with _gateway(s) as gw:
            ids = {}
            for inst in instruments:
                ids[inst.symbol] = repo.instrument_id(inst.symbol) or repo.upsert_instrument(
                    inst.symbol, inst.broker_symbol, inst.asset_class, inst.description)
            console.print(f"Streaming quotes for {', '.join(ids)} every {interval:g}s [muted]· Ctrl+C to stop[/]")
            last: dict[str, int] = {}
            recorder = AccountRecorder(repo.engine)
            next_snap = next_deals = next_compact = 0.0
            while True:
                for inst in instruments:
                    tick = collect_tick(gw, inst)
                    if tick and last.get(inst.symbol) != tick["mt5_time_msc"]:
                        last[inst.symbol] = tick["mt5_time_msc"]
                        repo.upsert_tick(ids[inst.symbol], tick, run_id)
                        stored += 1
                mono = time.monotonic()
                try:
                    if account_every and mono >= next_snap:
                        next_snap = mono + account_every
                        recorder.record_snapshot(gw.account_snapshot())
                    if deals_every and mono >= next_deals:
                        next_deals = mono + deals_every
                        recorder.sync_deals(gw, lookback_days)
                    if mono >= next_compact:
                        next_compact = mono + 3600
                        recorder.compact()
                except Exception as exc:  # noqa: BLE001 - account mirroring (MT5 or database errors) never stops the quote stream
                    log.warning("Account sync skipped", exc_info=True)
                    console.print(f"[warning]Account sync skipped:[/] {type(exc).__name__}: {exc}")
                time.sleep(interval)
    except KeyboardInterrupt:
        console.print(f"Stopped. [muted]{stored:,} quotes recorded.[/]")
    except MT5Error as exc:
        _fail(str(exc))


@app.command("sync-account")
def sync_account(days: int = typer.Option(90, "--days", min=1, help="Deal history to read, in days"),
                 loop: bool = typer.Option(False, "--loop", help="Keep the mirror fresh: sync every --interval seconds "
                                                                 "until Ctrl+C. MT5 errors are logged and retried."),
                 interval: int = typer.Option(60, "--interval", min=10, help="Seconds between syncs with --loop")) -> None:
    """Mirror the DEMO account: a balance/equity snapshot plus positions rebuilt from the deal history.
    Read-only (account_info + history_deals_get). Feeds /account, /trades, /risk and /performance."""
    from aurum.account.recorder import AccountRecorder
    from aurum.gateways.mt5_gateway import MT5Error
    s = _settings()
    repo = _repo(s)
    recorder = AccountRecorder(repo.engine)

    def show(res: dict) -> None:
        console.print(kv_table(f"Account synced · {datetime.now():%H:%M:%S}",
                               [("Equity", f"{res['equity']:,.2f} {res['currency']}"), ("Positions synced", res["positions"]),
                                ("Still open", res["open"]), ("Cash flows", res["cash_flows"])]))

    if not loop:
        try:
            with _gateway(s) as gw:
                res = recorder.sync(gw, lookback_days=days)
        except MT5Error as exc:
            _fail(str(exc))
        show(res)
        return
    console.print(f"[muted]Syncing the DEMO account every {interval}s · Ctrl+C to stop[/]")
    try:
        while True:
            try:
                with _gateway(s) as gw:
                    while True:          # one MT5 connection for as long as it works
                        show(recorder.sync(gw, lookback_days=days))
                        time.sleep(interval)
            except (MT5Error, OSError, RuntimeError) as exc:
                # MT5 closed, logged out, or the terminal restarted: keep trying instead of leaving the mirror stale.
                log.warning("Account sync failed; retrying", extra={"error": f"{type(exc).__name__}: {exc}"[:300]})
                console.print(f"[warning]Sync failed ({type(exc).__name__}: {exc}). Retrying in {interval}s.[/]")
                time.sleep(interval)
    except KeyboardInterrupt:
        console.print("Stopped by user.")


def _print_summary(summary) -> None:
    t = Table(title=f"Collection run {summary.run_id} · {status(summary.status)}",
              numeric=("Received", "Stored", "Forming excluded"))
    for c in ("Symbol", "TF", "Status", "Received", "Stored", "Forming excluded", "Issues", "Message"):
        t.add_column(c)
    for r in summary.series:
        issues = ", ".join(f"{k}={v}" for k, v in r.issues.items()) or "—"
        t.add_row(r.symbol, r.timeframe, status(r.status), f"{r.received:,}", f"{r.stored:,}",
                  f"{r.excluded_forming:,}", issues, r.message)
    console.print(t)
    for sym, reason in summary.unavailable.items():
        console.print(f"[warning]Skipped {sym}[/]: {reason}")


@app.command()
def export(symbol: Optional[str] = typer.Option(None, "--symbol", "-s"),
           timeframe: Optional[str] = typer.Option(None, "--timeframe", "-t"),
           fmt: str = typer.Option("both", "--format", "-f", help="csv | parquet | both"),
           on_exists: str = typer.Option("skip", "--on-exists", help="skip | version | overwrite"),
           include_invalid: bool = typer.Option(False, help="Also export candles marked invalid"),
           include_incomplete: bool = typer.Option(False, help="Also export forming candles")) -> None:
    """Export stored candles to data/exports/<SYMBOL>/<TF>.csv|.parquet."""
    from aurum.exporters.market_data_exporter import MarketDataExporter
    s = _settings()
    formats = ("csv", "parquet") if fmt == "both" else (fmt,)
    try:
        exporter = MarketDataExporter(_repo(s), s.resolve(s.data_dir) / "exports")
        results = exporter.export([symbol.upper()] if symbol else None, [timeframe.upper()] if timeframe else None,
                                  formats, on_exists, include_invalid, include_incomplete)
    except ValueError as exc:
        _fail(str(exc))
    except Exception as exc:
        _fail(f"Export failed: {type(exc).__name__}: {exc}")
    if not results:
        console.print("[warning]No stored candles match.[/] Run: python -m aurum.cli collect")
        return
    t = Table(title="Export", numeric=("Rows",))
    for c in ("Symbol", "TF", "Format", "Rows", "Action", "Path"):
        t.add_column(c)
    for r in results:
        t.add_row(r.symbol, r.timeframe, r.fmt, f"{r.rows:,}", r.action, f"[muted]{r.path}[/]")
    console.print(t)


from aurum.cot.cli import cot_app  # noqa: E402 - Module 02 commands: python -m aurum.cli cot ...

app.add_typer(cot_app, name="cot")

from aurum.analysis.cli import analysis_app  # noqa: E402 - Module 03 commands: python -m aurum.cli analyze ...

app.add_typer(analysis_app, name="analyze")

from aurum.learning.cli import learn_app  # noqa: E402 - Trade Learning Agent: python -m aurum.cli learn ...

app.add_typer(learn_app, name="learn")

from aurum.market_intelligence.cli import intelligence_app  # noqa: E402 - Module 04: python -m aurum.cli intelligence ...

app.add_typer(intelligence_app, name="intelligence")

from aurum.ai_review.cli import ai_review_app  # noqa: E402 - optional AI review: python -m aurum.cli ai-review ...

app.add_typer(ai_review_app, name="ai-review")

from aurum.decision.cli import ai_app, decide_app  # noqa: E402 - Module 05 commands: decide ... and ai ...

app.add_typer(decide_app, name="decide")
app.add_typer(ai_app, name="ai")

from aurum.risk.cli import risk_app  # noqa: E402 - Module 06 commands: python -m aurum.cli risk ...

app.add_typer(risk_app, name="risk")

from aurum.approvals.cli import approvals_app, users_app  # noqa: E402 - Module 07: python -m aurum.cli users|approvals ...

app.add_typer(users_app, name="users")
app.add_typer(approvals_app, name="approvals")

from aurum.orchestration.cli import orchestration_app  # noqa: E402 - Module 08: python -m aurum.cli orchestration ...

app.add_typer(orchestration_app, name="orchestration")

from aurum.mt5link.cli import mt5_slots_app  # noqa: E402 - per-person MT5 links: python -m aurum.cli mt5-slots ...

app.add_typer(mt5_slots_app, name="mt5-slots")

if __name__ == "__main__":
    app()
