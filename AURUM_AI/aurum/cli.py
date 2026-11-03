"""AURUM AI Module 01 command-line interface (read-only market data engine).

    python -m aurum.cli check
    python -m aurum.cli init-db
    python -m aurum.cli specs
    python -m aurum.cli collect [--symbol EURUSD] [--timeframe M15] [--full] [--loop]
    python -m aurum.cli export  [--symbol EURUSD] [--timeframe H1] [--format csv|parquet|both] [--on-exists skip|version|overwrite]
    python -m aurum.cli cot ... (Module 02: check, datasets, backfill, update, calculate, report, status, quality, export)
"""
from __future__ import annotations

import logging
import time
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
    secrets = [x.get_secret_value() for x in (s.mt5_password, s.db_password, s.database_url, s.aurum_admin_api_key)
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

from aurum.learning.cli import learn_app  # noqa: E402 - Trade Learning Agent: python -m aurum.cli learn ...

app.add_typer(learn_app, name="learn")

if __name__ == "__main__":
    app()
