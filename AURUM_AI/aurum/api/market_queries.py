"""Read-only queries that turn Module 01's stored data into the shapes the frontend expects.

Nothing here touches MT5 or writes to the database. Every figure is computed from stored rows:
quotes come from the latest stored tick, the daily range from today's stored candles, the daily
change against the previous completed D1 close.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.engine import Engine

from aurum.database.connection import session_scope
from aurum.database.models import (DataCollectionLog, DataQualityIssue, Instrument, InstrumentSpec, MarketCandle,
                                   MarketTick)
from aurum.database.repository import Repository

TIMEFRAMES = ("D1", "H4", "H1", "M15", "M5")
TF_SECONDS = {"D1": 86_400, "H4": 14_400, "H1": 3_600, "M15": 900, "M5": 300}

# Module 01 asset classes -> frontend asset classes
ASSET_CLASS = {"forex": "forex", "fx": "forex", "metal": "commodities", "metals": "commodities", "energy": "commodities",
               "commodity": "commodities", "commodities": "commodities", "index": "indices", "indices": "indices",
               "crypto": "crypto", "cryptocurrency": "crypto"}

# A quote is "live" while its latest tick is this fresh; older means the feed is not updating.
LIVE_TICK_SECONDS = 120


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    d = _utc(dt)
    return d.isoformat().replace("+00:00", "Z") if d else None


def asset_class(v: str | None) -> str:
    return ASSET_CLASS.get((v or "").lower(), "forex")


class MarketQueries:
    def __init__(self, engine: Engine):
        self.engine = engine
        self.repo = Repository(engine)

    # ------------------------------------------------------------------ instruments & specs
    def instruments(self) -> list[dict[str, Any]]:
        with session_scope(self.engine) as s:
            rows = s.execute(select(Instrument).order_by(Instrument.symbol)).scalars().all()
            return [{"symbol": i.symbol, "name": i.description or i.symbol, "assetClass": asset_class(i.asset_class),
                     "enabled": bool(i.is_available)} for i in rows]

    def specifications(self) -> list[dict[str, Any]]:
        specs = {s["symbol"]: s for s in self.repo.latest_specs()}
        out = []
        with session_scope(self.engine) as s:
            for i in s.execute(select(Instrument).order_by(Instrument.symbol)).scalars():
                sp = specs.get(i.symbol, {})
                out.append({"symbol": i.symbol, "brokerSymbol": sp.get("broker_symbol") or i.broker_symbol,
                            "assetClass": asset_class(i.asset_class), "enabled": bool(i.is_available),
                            "digits": sp.get("digits"), "point": sp.get("point"), "contractSize": sp.get("contract_size"),
                            "tickSize": sp.get("tick_size"), "tickValue": sp.get("tick_value")})
        return out

    # ------------------------------------------------------------------ quotes
    def quotes(self, symbols: list[str] | None = None, now: datetime | None = None) -> list[dict[str, Any]]:
        now = now or datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        specs = {s["symbol"]: s for s in self.repo.latest_specs()}
        out: list[dict[str, Any]] = []
        with session_scope(self.engine) as s:
            q = select(Instrument).order_by(Instrument.symbol)
            if symbols:
                q = q.where(Instrument.symbol.in_([x.upper() for x in symbols]))
            for inst in s.execute(q).scalars().all():
                tick = s.execute(select(MarketTick).where(MarketTick.instrument_id == inst.id)
                                 .order_by(MarketTick.ts_utc.desc()).limit(1)).scalar()
                if tick is None or tick.bid is None:
                    continue  # no live price yet: the instrument is simply absent from quotes
                sp = specs.get(inst.symbol, {})
                digits = sp.get("digits") if sp.get("digits") is not None else 5
                point = sp.get("point") or 10 ** -digits
                # today's range from the finest stored series, including the forming candle
                intraday = (select(func.max(MarketCandle.high), func.min(MarketCandle.low), func.sum(MarketCandle.tick_volume))
                            .where(MarketCandle.instrument_id == inst.id, MarketCandle.timeframe == "M5",
                                   MarketCandle.ts_utc >= day_start, MarketCandle.quality_status != "invalid"))
                hi, lo, vol = s.execute(intraday).one()
                prev_close = s.execute(select(MarketCandle.close).where(
                    MarketCandle.instrument_id == inst.id, MarketCandle.timeframe == "D1", MarketCandle.ts_utc < day_start,
                    MarketCandle.is_complete.is_(True), MarketCandle.quality_status != "invalid")
                    .order_by(MarketCandle.ts_utc.desc()).limit(1)).scalar()
                bid, ask = float(tick.bid), float(tick.ask if tick.ask is not None else tick.bid)
                age = (now - _utc(tick.ts_utc)).total_seconds()
                spread = tick.spread_points if tick.spread_points is not None else round((ask - bid) / point)
                out.append({
                    "symbol": inst.symbol,
                    "bid": bid,
                    "ask": ask,
                    "spread": round(float(spread), 1),
                    "dailyChangePct": round((bid - prev_close) / prev_close * 100, 4) if prev_close else 0.0,
                    "dailyHigh": max(float(hi), bid) if hi is not None else bid,
                    "dailyLow": min(float(lo), bid) if lo is not None else bid,
                    "volume": int(vol) if vol is not None else None,
                    "status": "open" if age <= LIVE_TICK_SECONDS else "closed",
                    "digits": int(digits),
                    "updatedAt": _iso(tick.ts_utc),
                })
        return out

    def latest_tick_time(self) -> datetime | None:
        with session_scope(self.engine) as s:
            return _utc(s.execute(select(func.max(MarketTick.ts_utc))).scalar())

    # ------------------------------------------------------------------ candles
    def candles(self, symbol: str, timeframe: str, limit: int = 500) -> list[dict[str, Any]]:
        if timeframe not in TF_SECONDS:
            raise ValueError(f"Unsupported timeframe {timeframe!r}. Use one of {', '.join(TIMEFRAMES)}.")
        with session_scope(self.engine) as s:
            inst_id = s.execute(select(Instrument.id).where(Instrument.symbol == symbol.upper())).scalar()
            if inst_id is None:
                raise KeyError(symbol)
            rows = s.execute(select(MarketCandle).where(
                MarketCandle.instrument_id == inst_id, MarketCandle.timeframe == timeframe,
                MarketCandle.quality_status != "invalid", MarketCandle.open.is_not(None))
                .order_by(MarketCandle.ts_utc.desc()).limit(limit)).scalars().all()
            return [{"time": int(_utc(c.ts_utc).timestamp()), "open": c.open, "high": c.high, "low": c.low, "close": c.close,
                     "volume": int(c.tick_volume or 0)} for c in reversed(rows)]

    # ------------------------------------------------------------------ data quality
    def data_quality(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        with session_scope(self.engine) as s:
            symbols = [r for r in s.execute(select(Instrument.symbol).order_by(Instrument.symbol)).scalars()]
            last_ok = s.execute(select(func.max(DataCollectionLog.finished_at)).where(
                DataCollectionLog.event == "run", DataCollectionLog.status.in_(("success", "partial")))).scalar()
            total = int(s.execute(select(func.count()).select_from(MarketCandle)).scalar_one())
            open_issues = select(DataQualityIssue.issue_type, DataQualityIssue.severity, func.count()).where(
                DataQualityIssue.resolved.is_(False)).group_by(DataQualityIssue.issue_type, DataQualityIssue.severity)
            counts: dict[str, int] = {}
            errors = 0
            for itype, sev, n in s.execute(open_issues):
                counts[itype] = counts.get(itype, 0) + int(n)
                if sev == "error":
                    errors += int(n)
            failed_runs = int(s.execute(select(func.count()).select_from(DataCollectionLog).where(
                DataCollectionLog.status == "failed", DataCollectionLog.started_at >= now - timedelta(days=1))).scalar_one())
            last_run = s.execute(select(DataCollectionLog).where(DataCollectionLog.event == "run")
                                 .order_by(DataCollectionLog.started_at.desc()).limit(1)).scalar()
            latency = None
            if last_run and last_run.finished_at:
                latency = int((_utc(last_run.finished_at) - _utc(last_run.started_at)).total_seconds() * 1000)

            by_tf = []
            present_tfs = []
            for tf in TIMEFRAMES:
                n, latest, ingested = s.execute(select(func.count(), func.max(MarketCandle.ts_utc), func.max(MarketCandle.ingested_at))
                                                .where(MarketCandle.timeframe == tf)).one()
                if n:
                    present_tfs.append(tf)
                tf_issues = int(s.execute(select(func.count()).select_from(DataQualityIssue).where(
                    DataQualityIssue.timeframe == tf, DataQualityIssue.resolved.is_(False),
                    DataQualityIssue.severity.in_(("error", "warning")))).scalar_one())
                stale = latest is not None and (now - _utc(latest)).total_seconds() > TF_SECONDS[tf] * 3 and tf != "D1"
                by_tf.append({
                    "timeframe": tf,
                    "latestCandle": _iso(latest),
                    "storedCandles": int(n),
                    "lastCollection": _iso(ingested),
                    "availability": "missing" if not n else ("partial" if stale else "available"),
                    "quality": "unknown" if not n else ("warning" if tf_issues or stale else "connected"),
                })
        result = None
        if last_run:
            result = {"success": "passed", "partial": "warning"}.get(last_run.status, "failed")
        return {
            "instrumentsMonitored": symbols,
            "timeframes": present_tfs,
            "lastSuccessfulCollection": _iso(last_ok),
            "candlesCollected": total,
            "validationErrors": errors,
            "missingCandles": counts.get("missing_candles", 0),
            "duplicateCandles": counts.get("duplicate_candle", 0) + counts.get("conflicting_duplicate", 0),
            "invalidOhlc": counts.get("invalid_ohlc", 0) + counts.get("non_positive_price", 0) + counts.get("missing_values", 0),
            "unexpectedGaps": counts.get("abnormal_gap", 0),
            "collectionErrors": failed_runs,
            "latencyMs": latency,
            "lastValidationResult": result,
            "byTimeframe": by_tf,
        }

    # ------------------------------------------------------------------ collection logs
    def collection_logs(self, page: int = 1, page_size: int = 12, search: str | None = None, instrument: str | None = None,
                        timeframe: str | None = None, status: str | None = None) -> dict[str, Any]:
        status_map = {"passed": ("success",), "warning": ("partial", "skipped"), "failed": ("failed",)}
        q = select(DataCollectionLog).where(DataCollectionLog.event == "candles")
        if instrument:
            q = q.where(DataCollectionLog.symbol == instrument.upper())
        if timeframe:
            q = q.where(DataCollectionLog.timeframe == timeframe.upper())
        if status in status_map:
            q = q.where(DataCollectionLog.status.in_(status_map[status]))
        if search:
            like = f"%{search.strip()}%"
            q = q.where(or_(DataCollectionLog.symbol.ilike(like), DataCollectionLog.timeframe.ilike(like),
                            DataCollectionLog.message.ilike(like)))
        page = max(1, page)
        page_size = min(max(1, page_size), 200)
        with session_scope(self.engine) as s:
            total = int(s.execute(select(func.count()).select_from(q.subquery())).scalar_one())
            rows = s.execute(q.order_by(DataCollectionLog.started_at.desc()).offset((page - 1) * page_size).limit(page_size)).scalars().all()
            items = []
            for r in rows:
                v = {"success": "passed", "partial": "warning", "skipped": "warning"}.get(r.status, "failed")
                duration = int((_utc(r.finished_at) - _utc(r.started_at)).total_seconds() * 1000) if r.finished_at else 0
                items.append({"id": str(r.id), "timestamp": _iso(r.started_at), "instrument": r.symbol or "—",
                              "timeframe": r.timeframe or "—", "recordsRetrieved": int(r.rows_received or 0),
                              "recordsStored": int(r.rows_stored or 0), "validationStatus": v, "durationMs": duration,
                              "errorMessage": r.message if v != "passed" and r.message else None})
        return {"items": items, "total": total, "page": page, "pageSize": page_size}

    # ------------------------------------------------------------------ feed state for /health
    def feed_state(self, interval_s: int, now: datetime | None = None) -> dict[str, str]:
        """MT5 feed and collection status inferred from what was stored, and how recently."""
        now = now or datetime.now(timezone.utc)
        last_tick = self.latest_tick_time()
        with session_scope(self.engine) as s:
            last_run = s.execute(select(DataCollectionLog).where(DataCollectionLog.event == "run")
                                 .order_by(DataCollectionLog.started_at.desc()).limit(1)).scalar()
            running = s.execute(select(func.count()).select_from(DataCollectionLog).where(and_(
                DataCollectionLog.event == "run", DataCollectionLog.finished_at.is_(None),
                DataCollectionLog.started_at >= now - timedelta(minutes=15)))).scalar_one()
        mt5 = "unknown" if last_tick is None else ("connected" if (now - last_tick).total_seconds() <= LIVE_TICK_SECONDS else "disconnected")
        if running:
            collection = "collecting"
        elif last_run is None:
            collection = "unknown"
        elif last_run.status == "failed":
            collection = "failed"
        elif (now - _utc(last_run.started_at)).total_seconds() > interval_s * 3:
            collection = "warning"
        else:
            collection = "connected"
        return {"mt5": mt5, "collection": collection}
