"""Orchestrates one collection run: specs -> ticks -> candles -> raw snapshot -> validation ->
processed snapshot -> database upsert -> logs. Failures for one symbol/timeframe never stop the others."""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from aurum.collectors.candle_collector import collect_candles, incremental_count
from aurum.collectors.instrument_collector import collect_specs
from aurum.collectors.tick_collector import collect_tick
from aurum.config import InstrumentConfig, Settings
from aurum.database.repository import Repository
from aurum.gateways.mt5_gateway import MT5Gateway
from aurum.services.validation_service import QualityIssue, ValidationService

log = logging.getLogger(__name__)


@dataclass
class SeriesResult:
    symbol: str
    timeframe: str
    status: str
    received: int = 0
    stored: int = 0
    excluded_forming: int = 0
    issues: dict[str, int] = field(default_factory=dict)
    message: str = ""


@dataclass
class RunSummary:
    run_id: str
    started_at: datetime
    finished_at: datetime | None = None
    unavailable: dict[str, str] = field(default_factory=dict)
    series: list[SeriesResult] = field(default_factory=list)
    ticks: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def status(self) -> str:
        if not self.series:
            return "failed"
        ok = [s for s in self.series if s.status == "success"]
        return "success" if len(ok) == len(self.series) and not self.unavailable else ("partial" if ok else "failed")


class MarketDataService:
    def __init__(self, settings: Settings, gateway: MT5Gateway, repo: Repository,
                 validator: ValidationService | None = None):
        self.settings = settings
        self.gateway = gateway
        self.repo = repo
        self.validator = validator or ValidationService(settings.gap_range_multiple)
        self.data_dir = settings.resolve(settings.data_dir)

    def run(self, instruments: list[InstrumentConfig], timeframes: list[str], full: bool = False,
            now_utc: datetime | None = None) -> RunSummary:
        now_utc = now_utc or datetime.now(timezone.utc)
        summary = RunSummary(run_id=str(uuid.uuid4()), started_at=now_utc)
        log.info("Collection run started", extra={"run_id": summary.run_id, "symbols": [i.symbol for i in instruments],
                                                  "timeframes": timeframes})
        specs = collect_specs(self.gateway, instruments)
        summary.unavailable = dict(specs.unavailable)
        for inst in instruments:
            inst_id = self.repo.upsert_instrument(inst.symbol, inst.broker_symbol, inst.asset_class, inst.description,
                                                  is_available=inst.symbol in specs.available)
            if inst.symbol not in specs.available:
                reason = specs.unavailable.get(inst.symbol, "unavailable")
                log.error("Instrument unavailable - skipped", extra={"symbol": inst.symbol, "reason": reason})
                issue = QualityIssue("instrument_unavailable", "error", None, {"message": reason, "symbol": inst.symbol})
                self.repo.record_issues([issue], inst_id, inst.symbol, None, summary.run_id)
                self.repo.log_collection(run_id=summary.run_id, event="specs", status="skipped", instrument_id=inst_id,
                                         symbol=inst.symbol, started_at=now_utc, finished_at=_now(), message=reason)
                continue
            self.repo.insert_spec(inst_id, specs.available[inst.symbol], now_utc, summary.run_id)
            self._collect_tick(inst, inst_id, summary)
            for tf in timeframes:
                summary.series.append(self._collect_series(inst, inst_id, tf, full, now_utc, summary.run_id))
        summary.finished_at = _now()
        self.repo.log_collection(run_id=summary.run_id, event="run", status=summary.status, started_at=summary.started_at,
                                 finished_at=summary.finished_at,
                                 rows_received=sum(s.received for s in summary.series),
                                 rows_stored=sum(s.stored for s in summary.series),
                                 issues_found=sum(sum(s.issues.values()) for s in summary.series),
                                 details={"unavailable": summary.unavailable})
        log.info("Collection run finished", extra={"run_id": summary.run_id, "status": summary.status})
        return summary

    def _collect_tick(self, inst: InstrumentConfig, inst_id: int, summary: RunSummary) -> None:
        try:
            tick = collect_tick(self.gateway, inst)
            if tick:
                self.repo.upsert_tick(inst_id, tick, summary.run_id)
                summary.ticks[inst.symbol] = tick
        except Exception as exc:
            log.exception("Tick collection failed", extra={"symbol": inst.symbol})
            self.repo.log_collection(run_id=summary.run_id, event="ticks", status="failed", instrument_id=inst_id,
                                     symbol=inst.symbol, started_at=_now(), finished_at=_now(), message=str(exc))

    def _collect_series(self, inst: InstrumentConfig, inst_id: int, tf: str, full: bool, now_utc: datetime,
                        run_id: str) -> SeriesResult:
        started = _now()
        res = SeriesResult(inst.symbol, tf, "failed")
        try:
            last = None if full else self.repo.latest_candle_ts(inst_id, tf)
            count = incremental_count(last, tf, self.settings.historical_candle_count, now_utc)
            batch = collect_candles(self.gateway, inst, tf, count, self.settings.exclude_forming_candle, now_utc)
            res.received, res.excluded_forming = len(batch.raw), batch.excluded_forming
            self._snapshot(batch.raw, "raw", run_id, inst.symbol, tf)
            result = self.validator.validate(batch.candles, tf, inst.trading_hours, inst.symbol)
            self._snapshot(result.candles, "processed", run_id, inst.symbol, tf)
            stored, revisions = self.repo.upsert_candles(inst_id, result.candles, run_id)
            issues = list(result.issues) + [QualityIssue("candle_revision", "warning", r["ts_utc"], r) for r in revisions]
            if batch.excluded_forming:
                issues.append(QualityIssue("forming_candle_excluded", "info", None,
                                           {"message": "currently forming candle excluded", "count": batch.excluded_forming}))
            self.repo.record_issues(issues, inst_id, inst.symbol, tf, run_id)
            res.stored = stored
            res.issues = {i.issue_type: sum(1 for j in issues if j.issue_type == i.issue_type) for i in issues}
            no_data = any(i.issue_type == "no_data" for i in issues)
            res.status = "failed" if no_data else "success"
            res.message = "no data returned by MT5" if no_data else f"requested {count}"
        except Exception as exc:
            log.exception("Candle collection failed", extra={"symbol": inst.symbol, "timeframe": tf})
            res.message = f"{type(exc).__name__}: {exc}"
        self.repo.log_collection(run_id=run_id, event="candles", status=res.status, instrument_id=inst_id,
                                 symbol=inst.symbol, timeframe=tf, started_at=started, finished_at=_now(),
                                 rows_received=res.received, rows_stored=res.stored,
                                 issues_found=sum(res.issues.values()), message=res.message,
                                 details={"issues": res.issues, "excluded_forming": res.excluded_forming})
        return res

    def _snapshot(self, df: pd.DataFrame, kind: str, run_id: str, symbol: str, tf: str) -> Path | None:
        """Immutable per-run parquet snapshot: data/raw|processed/<YYYYMMDD>/<run_id>/<SYMBOL>_<TF>.parquet."""
        if df is None or df.empty:
            return None
        path = self.data_dir / kind / datetime.now(timezone.utc).strftime("%Y%m%d") / run_id / f"{symbol}_{tf}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, index=False)
        return path


def _now() -> datetime:
    return datetime.now(timezone.utc)
