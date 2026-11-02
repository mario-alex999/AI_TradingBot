"""Export stored candles to data/exports/<SYMBOL>/<TF>.csv|.parquet (UTC timestamps).

Existing files are never overwritten unless ``on_exists="overwrite"`` is passed explicitly:
  skip      (default) leave the existing file and report it
  version   write <TF>_<YYYYmmddTHHMMSSZ>.<ext> next to it
  overwrite replace the file
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from aurum.database.repository import Repository

log = logging.getLogger(__name__)
EXPORT_COLUMNS = ["symbol", "timeframe", "ts_utc", "open", "high", "low", "close", "tick_volume", "real_volume",
                  "spread", "is_complete", "quality_status", "quality_flags", "broker_symbol", "mt5_time", "source",
                  "collection_run_id", "ingested_at", "updated_at"]
ON_EXISTS = ("skip", "version", "overwrite")


@dataclass
class ExportResult:
    symbol: str
    timeframe: str
    fmt: str
    path: Path
    rows: int
    action: str          # written | overwritten | versioned | skipped_exists | empty


class MarketDataExporter:
    def __init__(self, repo: Repository, export_dir: Path):
        self.repo = repo
        self.export_dir = export_dir

    def export(self, symbols: list[str] | None = None, timeframes: list[str] | None = None,
               formats: tuple[str, ...] = ("csv", "parquet"), on_exists: str = "skip",
               include_invalid: bool = False, include_incomplete: bool = False) -> list[ExportResult]:
        if on_exists not in ON_EXISTS:
            raise ValueError(f"on_exists must be one of {ON_EXISTS}")
        bad = set(formats) - {"csv", "parquet"}
        if bad:
            raise ValueError(f"Unsupported export format(s): {sorted(bad)}")
        series = [(s, tf) for s, tf, _ in self.repo.available_series()
                  if (not symbols or s in symbols) and (not timeframes or tf in timeframes)]
        results: list[ExportResult] = []
        for sym, tf in series:
            df = self.repo.load_candles(sym, tf, include_invalid, include_incomplete)
            df = df[[c for c in EXPORT_COLUMNS if c in df.columns]]
            for fmt in formats:
                results.append(self._write(df, sym, tf, fmt, on_exists))
        return results

    def _write(self, df: pd.DataFrame, sym: str, tf: str, fmt: str, on_exists: str) -> ExportResult:
        path = self.export_dir / sym / f"{tf}.{fmt}"
        if df.empty:
            return ExportResult(sym, tf, fmt, path, 0, "empty")
        action = "written"
        if path.exists():
            if on_exists == "skip":
                log.warning("Export exists - not overwritten (use --on-exists overwrite|version)", extra={"path": str(path)})
                return ExportResult(sym, tf, fmt, path, len(df), "skipped_exists")
            if on_exists == "version":
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                path, action = path.with_name(f"{tf}_{stamp}.{fmt}"), "versioned"
            else:
                action = "overwritten"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        if fmt == "csv":
            out = df.copy()
            for c in ("ts_utc", "ingested_at", "updated_at"):
                if c in out:
                    out[c] = pd.to_datetime(out[c], utc=True).map(lambda t: t.isoformat())
            out.to_csv(tmp, index=False)
        else:
            df.to_parquet(tmp, index=False)
        tmp.replace(path)   # atomic: no half-written export files
        log.info("Exported", extra={"path": str(path), "rows": len(df), "action": action})
        return ExportResult(sym, tf, fmt, path, len(df), action)
