"""Data access layer. All writes are idempotent upserts keyed on natural keys, so collection can be
re-run any number of times without creating duplicates."""
from __future__ import annotations

import hashlib
import json
import logging
import math
from datetime import datetime
from typing import Any, Iterable

import pandas as pd
from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

from aurum.database.connection import session_scope
from aurum.database.models import (DataCollectionLog, DataQualityIssue, Instrument, InstrumentSpec, MarketCandle,
                                   MarketTick, utcnow)

log = logging.getLogger(__name__)
PRICE_COLS = ("open", "high", "low", "close")


def _clean(v: Any) -> Any:
    if v is None:
        return None
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):
        try:
            v = v.item()
        except (ValueError, AttributeError):
            pass
    if isinstance(v, float) and math.isnan(v):
        return None
    if v is pd.NaT:
        return None
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    return v


def _jsonable(d: Any) -> Any:
    return json.loads(json.dumps(d, default=str)) if d is not None else None


class Repository:
    def __init__(self, engine: Engine):
        self.engine = engine
        self.dialect = engine.dialect.name
        self._chunk = 50 if self.dialect == "sqlite" else 1000   # SQLite variable limit

    def _insert(self, table):
        return pg_insert(table) if self.dialect == "postgresql" else sqlite_insert(table)

    # ------------------------------------------------------------- instruments
    def upsert_instrument(self, symbol: str, broker_symbol: str, asset_class: str, description: str = "",
                          is_available: bool = True) -> int:
        stmt = self._insert(Instrument).values(symbol=symbol, broker_symbol=broker_symbol, asset_class=asset_class,
                                               description=description, is_available=is_available,
                                               created_at=utcnow(), updated_at=utcnow())
        stmt = stmt.on_conflict_do_update(index_elements=["symbol"], set_={
            "broker_symbol": stmt.excluded.broker_symbol, "asset_class": stmt.excluded.asset_class,
            "description": stmt.excluded.description, "is_available": stmt.excluded.is_available,
            "updated_at": stmt.excluded.updated_at})
        with session_scope(self.engine) as s:
            s.execute(stmt)
            return int(s.execute(select(Instrument.id).where(Instrument.symbol == symbol)).scalar_one())

    def instrument_id(self, symbol: str) -> int | None:
        with session_scope(self.engine) as s:
            return s.execute(select(Instrument.id).where(Instrument.symbol == symbol)).scalar()

    def list_instruments(self) -> list[dict[str, Any]]:
        with session_scope(self.engine) as s:
            return [{"id": i.id, "symbol": i.symbol, "broker_symbol": i.broker_symbol, "asset_class": i.asset_class,
                     "is_available": i.is_available} for i in s.execute(select(Instrument)).scalars()]

    def insert_spec(self, instrument_id: int, spec: dict[str, Any], captured_at: datetime, run_id: str) -> None:
        cols = {c.name for c in InstrumentSpec.__table__.columns} - {"id"}
        row = {k: _clean(v) for k, v in spec.items() if k in cols}
        row.update(instrument_id=instrument_id, captured_at=captured_at, collection_run_id=run_id,
                   raw=_jsonable(spec.get("raw")))
        stmt = self._insert(InstrumentSpec).values(**row).on_conflict_do_nothing(
            index_elements=["instrument_id", "captured_at"])
        with session_scope(self.engine) as s:
            s.execute(stmt)

    def latest_specs(self) -> list[dict[str, Any]]:
        sub = (select(InstrumentSpec.instrument_id, func.max(InstrumentSpec.captured_at).label("mx"))
               .group_by(InstrumentSpec.instrument_id).subquery())
        q = (select(Instrument.symbol, Instrument.asset_class, InstrumentSpec)
             .join(InstrumentSpec, InstrumentSpec.instrument_id == Instrument.id)
             .join(sub, and_(sub.c.instrument_id == InstrumentSpec.instrument_id, sub.c.mx == InstrumentSpec.captured_at)))
        with session_scope(self.engine) as s:
            return [{"symbol": sym, "asset_class": ac, **{c.name: getattr(sp, c.name)
                                                           for c in InstrumentSpec.__table__.columns if c.name != "raw"}}
                    for sym, ac, sp in s.execute(q)]

    # ------------------------------------------------------------- candles
    def latest_candle_ts(self, instrument_id: int, timeframe: str, complete_only: bool = True) -> datetime | None:
        q = select(func.max(MarketCandle.ts_utc)).where(MarketCandle.instrument_id == instrument_id,
                                                         MarketCandle.timeframe == timeframe)
        if complete_only:
            q = q.where(MarketCandle.is_complete.is_(True))
        with session_scope(self.engine) as s:
            v = s.execute(q).scalar()
        if v is not None and v.tzinfo is None:  # func.max bypasses the TypeDecorator on SQLite
            v = pd.Timestamp(v, tz="UTC").to_pydatetime()
        elif isinstance(v, str):
            v = pd.Timestamp(v).tz_localize("UTC").to_pydatetime()
        return v

    def upsert_candles(self, instrument_id: int, candles: pd.DataFrame, run_id: str) -> tuple[int, list[dict]]:
        """Insert or update candles. A stored *complete* candle whose prices change is reported as a
        revision (returned, for logging as a quality issue) before being updated to the broker's latest values.
        Returns (rows_written, revisions)."""
        if candles.empty:
            return 0, []
        candles = candles[candles["ts_utc"].notna()]
        timeframe = str(candles["timeframe"].iloc[0])
        now = utcnow()
        rows = []
        for r in candles.to_dict("records"):
            rows.append({
                "instrument_id": instrument_id, "timeframe": r["timeframe"], "ts_utc": _clean(r["ts_utc"]),
                **{c: _clean(r.get(c)) for c in PRICE_COLS},
                "tick_volume": _int(r.get("tick_volume")), "real_volume": _int(r.get("real_volume")),
                "spread": _int(r.get("spread")), "is_complete": bool(r.get("is_complete", True)),
                "quality_status": r.get("quality_status", "valid"), "quality_flags": r.get("quality_flags") or None,
                "broker_symbol": r["broker_symbol"], "mt5_time": int(_clean(r["mt5_time"])), "source": "mt5",
                "collection_run_id": run_id, "ingested_at": now, "updated_at": now})
        revisions = self._find_revisions(instrument_id, timeframe, rows)
        written = 0
        with session_scope(self.engine) as s:
            for i in range(0, len(rows), self._chunk):
                chunk = rows[i:i + self._chunk]
                stmt = self._insert(MarketCandle).values(chunk)
                ex = stmt.excluded
                stmt = stmt.on_conflict_do_update(
                    index_elements=["instrument_id", "timeframe", "ts_utc"],
                    set_={k: getattr(ex, k) for k in ("open", "high", "low", "close", "tick_volume", "real_volume",
                                                      "spread", "is_complete", "quality_status", "quality_flags",
                                                      "broker_symbol", "mt5_time", "collection_run_id", "updated_at")})
                s.execute(stmt)
                written += len(chunk)
        return written, revisions

    def _find_revisions(self, instrument_id: int, timeframe: str, rows: list[dict]) -> list[dict]:
        ts = [r["ts_utc"] for r in rows]
        q = select(MarketCandle).where(MarketCandle.instrument_id == instrument_id, MarketCandle.timeframe == timeframe,
                                       MarketCandle.ts_utc >= min(ts), MarketCandle.ts_utc <= max(ts),
                                       MarketCandle.is_complete.is_(True))
        with session_scope(self.engine) as s:
            existing = {c.ts_utc: c for c in s.execute(q).scalars()}
        revisions = []
        for r in rows:
            old = existing.get(r["ts_utc"])
            if old is None or not r["is_complete"]:
                continue
            changed = {c: (getattr(old, c), r[c]) for c in PRICE_COLS if not _same(getattr(old, c), r[c])}
            if changed:
                revisions.append({"ts_utc": r["ts_utc"], "changes": {k: {"old": a, "new": b} for k, (a, b) in changed.items()},
                                  "old_run_id": old.collection_run_id})
        return revisions

    def load_candles(self, symbol: str, timeframe: str, include_invalid: bool = False,
                     include_incomplete: bool = False) -> pd.DataFrame:
        q = (select(Instrument.symbol, MarketCandle).join(Instrument, Instrument.id == MarketCandle.instrument_id)
             .where(Instrument.symbol == symbol, MarketCandle.timeframe == timeframe).order_by(MarketCandle.ts_utc))
        if not include_invalid:
            q = q.where(MarketCandle.quality_status != "invalid")
        if not include_incomplete:
            q = q.where(MarketCandle.is_complete.is_(True))
        cols = [c.name for c in MarketCandle.__table__.columns if c.name != "instrument_id"]
        with session_scope(self.engine) as s:
            data = [{"symbol": sym, **{c: getattr(m, c) for c in cols}} for sym, m in s.execute(q)]
        df = pd.DataFrame(data, columns=["symbol", *cols])
        if not df.empty:
            for c in ("ts_utc", "ingested_at", "updated_at"):
                df[c] = pd.to_datetime(df[c], utc=True)
        return df

    def available_series(self) -> list[tuple[str, str, int]]:
        q = (select(Instrument.symbol, MarketCandle.timeframe, func.count())
             .join(Instrument, Instrument.id == MarketCandle.instrument_id)
             .group_by(Instrument.symbol, MarketCandle.timeframe).order_by(Instrument.symbol, MarketCandle.timeframe))
        with session_scope(self.engine) as s:
            return [(a, b, int(c)) for a, b, c in s.execute(q)]

    # ------------------------------------------------------------- ticks
    def upsert_tick(self, instrument_id: int, tick: dict[str, Any], run_id: str) -> None:
        row = {"instrument_id": instrument_id, "ts_utc": tick["ts_utc"], "bid": tick.get("bid"), "ask": tick.get("ask"),
               "last": tick.get("last"), "volume": tick.get("volume"), "spread_points": tick.get("spread_points"),
               "broker_symbol": tick["broker_symbol"], "mt5_time_msc": int(tick["mt5_time_msc"]),
               "collection_run_id": run_id, "ingested_at": utcnow()}
        stmt = self._insert(MarketTick).values(**row).on_conflict_do_nothing(index_elements=["instrument_id", "ts_utc"])
        with session_scope(self.engine) as s:
            s.execute(stmt)

    # ------------------------------------------------------------- logs & issues
    def log_collection(self, **fields: Any) -> None:
        fields["details"] = _jsonable(fields.get("details"))
        with session_scope(self.engine) as s:
            s.add(DataCollectionLog(**fields))

    def record_issues(self, issues: Iterable[Any], instrument_id: int | None, symbol: str | None,
                      timeframe: str | None, run_id: str) -> int:
        """Upsert quality issues; repeated detections increment ``occurrences`` instead of duplicating."""
        n = 0
        now = utcnow()
        with session_scope(self.engine) as s:
            for iss in issues:
                fp = fingerprint(symbol, timeframe, iss.issue_type, iss.ts_utc, iss.details)
                stmt = self._insert(DataQualityIssue).values(
                    fingerprint=fp, instrument_id=instrument_id, symbol=symbol, timeframe=timeframe,
                    ts_utc=iss.ts_utc, issue_type=iss.issue_type, severity=iss.severity,
                    details=_jsonable(iss.details), first_run_id=run_id, last_run_id=run_id,
                    first_seen_at=now, last_seen_at=now, occurrences=1, resolved=False)
                stmt = stmt.on_conflict_do_update(index_elements=["fingerprint"], set_={
                    "last_run_id": stmt.excluded.last_run_id, "last_seen_at": stmt.excluded.last_seen_at,
                    "details": stmt.excluded.details,
                    "occurrences": DataQualityIssue.__table__.c.occurrences + 1})
                s.execute(stmt)
                n += 1
        return n

    def issue_count(self) -> int:
        with session_scope(self.engine) as s:
            return int(s.execute(select(func.count()).select_from(DataQualityIssue)).scalar_one())


def fingerprint(symbol: str | None, timeframe: str | None, issue_type: str, ts: datetime | None,
                details: dict[str, Any] | None = None) -> str:
    """Stable identity of an issue. Issues without a timestamp (e.g. no_data) use their key details."""
    extra = "" if ts is not None else json.dumps({k: v for k, v in (details or {}).items()
                                                   if k in ("message", "symbol", "timeframe")}, sort_keys=True)
    key = f"{symbol}|{timeframe}|{issue_type}|{ts.isoformat() if ts else ''}|{extra}"
    return hashlib.sha256(key.encode()).hexdigest()


def _int(v: Any) -> int | None:
    v = _clean(v)
    return None if v is None else int(v)


def _same(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is b
    return math.isclose(float(a), float(b), rel_tol=0, abs_tol=1e-9)
