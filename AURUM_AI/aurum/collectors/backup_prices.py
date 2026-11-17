"""Cloud backup price feed (Twelve Data), used only while the MT5 PC sends no prices.

AURUM's prices normally come from the broker through the MT5 collector on the Windows PC. When that PC is off, the site
would freeze on its last price. This feed fills the gap from the cloud:

* It runs from the Module 08 worker tick (once a minute on the hosted API) and does nothing unless an instrument's latest
  MT5 tick is older than ``BACKUP_PRICES_MT5_STALE_MINUTES``, its market is open, it has a ``backup_symbol`` in
  ``config/instruments.yaml`` and ``TWELVE_DATA_API_KEY`` is set. Each instrument is fetched at most once every
  ``BACKUP_PRICES_INTERVAL_MINUTES``; all due instruments share one request.
* It stores 5-minute candles, builds the higher timeframes from them, and stores the latest price as a tick. Every row it
  writes has ``source = "twelvedata"``.
* It never replaces broker data: a slot holding a complete MT5 candle is left alone. When the PC is back, the MT5
  collector resumes from its own last candle, backfills the gap and replaces every backup candle it covers.
* The backup price is not the broker's price. Its ask is estimated from the broker's recent median spread. Module 06 can
  size a trade on it, but the assessment then goes to REVIEW with a warning (rule R051).

It reads market data only. It cannot place, change or close a trade, and it never connects to MT5.
"""
from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.engine import Engine

from aurum.config import TIMEFRAME_SECONDS, InstrumentConfig, Settings
from aurum.database.connection import session_scope
from aurum.database.models import BackupPriceStatus, Instrument, InstrumentSpec, MarketCandle, MarketTick
from aurum.database.repository import Repository
from aurum.services.validation_service import is_market_open

log = logging.getLogger(__name__)

SOURCE = "twelvedata"
M5 = timedelta(minutes=5)
MAX_BARS = 288                      # one day of 5-minute candles: the most fetched in one go
PLAN_CODES = {403}                  # the plan does not include the symbol: pause it
UNKNOWN_CODES = {400, 404}          # the provider does not know the symbol: pause it too


def _utc(t: datetime | None) -> datetime | None:
    if t is None:
        return None
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


def _iso(t: datetime | None) -> str | None:
    return _utc(t).isoformat().replace("+00:00", "Z") if t is not None else None


class ProviderError(Exception):
    def __init__(self, code: int | None, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass
class Series:
    symbol: str
    bars: list[dict[str, Any]] = field(default_factory=list)   # oldest first: ts_utc, open, high, low, close
    error: ProviderError | None = None


class TwelveDataClient:
    """GET /time_series for one or more symbols. The key travels in the Authorization header, never in the URL."""

    def __init__(self, api_key: str, base_url: str = "https://api.twelvedata.com", timeout: float = 20,
                 transport: httpx.BaseTransport | None = None):
        self._key, self.base_url, self.timeout, self._transport = api_key, base_url.rstrip("/"), timeout, transport

    def time_series(self, symbols: list[str], outputsize: int, interval: str = "5min") -> dict[str, Series]:
        params = {"symbol": ",".join(symbols), "interval": interval, "outputsize": str(outputsize), "timezone": "UTC",
                  "order": "ASC"}
        try:
            with httpx.Client(timeout=self.timeout, transport=self._transport) as http:
                resp = http.get(f"{self.base_url}/time_series", params=params,
                                headers={"Authorization": f"apikey {self._key}"})
        except httpx.HTTPError as exc:
            raise ProviderError(None, f"Twelve Data did not answer ({type(exc).__name__}).") from exc
        try:
            body = resp.json()
        except ValueError as exc:
            raise ProviderError(resp.status_code, f"Twelve Data returned HTTP {resp.status_code} without JSON.") from exc
        if not isinstance(body, dict):
            raise ProviderError(resp.status_code, "Twelve Data returned an unexpected answer.")
        if len(symbols) == 1:
            body = {symbols[0]: body}        # one symbol: its data, or its error, is the whole answer
        elif body.get("status") == "error" and not any(s in body for s in symbols):
            # A whole-request error: invalid key (401), out of credits (429) or a bad request.
            raise ProviderError(int(body.get("code") or resp.status_code), str(body.get("message", ""))[:300])
        if resp.status_code >= 400 and not any(isinstance(body.get(s), dict) for s in symbols):
            raise ProviderError(resp.status_code, f"Twelve Data returned HTTP {resp.status_code}.")
        return {s: self._series(s, body.get(s)) for s in symbols}

    @staticmethod
    def _series(symbol: str, part: Any) -> Series:
        if not isinstance(part, dict):
            return Series(symbol, error=ProviderError(None, "Twelve Data returned nothing for this symbol."))
        if part.get("status") == "error":
            return Series(symbol, error=ProviderError(int(part.get("code") or 0) or None, str(part.get("message", ""))[:300]))
        bars = []
        for v in part.get("values") or []:
            try:
                ts = datetime.fromisoformat(str(v["datetime"])).replace(tzinfo=timezone.utc)
                bar = {"ts_utc": ts, **{k: float(v[k]) for k in ("open", "high", "low", "close")}}
            except (KeyError, TypeError, ValueError):
                continue                                   # a malformed bar is skipped, never guessed
            if bar["low"] <= min(bar["open"], bar["close"]) and bar["high"] >= max(bar["open"], bar["close"]) > 0:
                bars.append(bar)
        bars.sort(key=lambda b: b["ts_utc"])
        return Series(symbol, bars)


def bucket_start(ts: datetime, timeframe: str, server_tz: ZoneInfo) -> datetime:
    """Open time (UTC) of the ``timeframe`` candle holding ``ts``, aligned like the broker's: on the MT5 server clock."""
    local = _utc(ts).astimezone(server_tz)
    if timeframe == "D1":
        start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        step = TIMEFRAME_SECONDS[timeframe]
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        secs = int((local - midnight).total_seconds()) // step * step
        start = midnight + timedelta(seconds=secs)
    return start.astimezone(timezone.utc)


class BackupPriceFeed:
    def __init__(self, engine: Engine, settings: Settings, instruments: dict[str, InstrumentConfig],
                 client: TwelveDataClient | None = None, clock: Callable[[], datetime] | None = None):
        self.engine, self.settings, self.instruments = engine, settings, instruments
        self.repo = Repository(engine)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        key = settings.twelve_data_api_key.get_secret_value() if settings.twelve_data_api_key else None
        self.client = client or (TwelveDataClient(key, settings.twelve_data_base_url) if key else None)
        self.server_tz = ZoneInfo(settings.mt5_server_timezone)

    # ------------------------------------------------------------------ state
    @property
    def configured(self) -> bool:
        return self.client is not None

    def _targets(self) -> list[InstrumentConfig]:
        return [self.instruments[s] for s in self.settings.enabled_instruments
                if s in self.instruments and self.instruments[s].backup_symbol]

    def _ids(self) -> dict[str, int]:
        with session_scope(self.engine) as s:
            return dict(s.execute(select(Instrument.symbol, Instrument.id)).all())

    def _last_tick(self, iid: int, source: str) -> datetime | None:
        with session_scope(self.engine) as s:
            return _utc(s.execute(select(func.max(MarketTick.ts_utc)).where(
                MarketTick.instrument_id == iid, MarketTick.source == source)).scalar())

    def _status_rows(self) -> dict[int, BackupPriceStatus]:
        with session_scope(self.engine) as s:
            rows = s.execute(select(BackupPriceStatus)).scalars().all()
            s.expunge_all()
            return {r.instrument_id: r for r in rows}

    def status(self) -> dict[str, Any]:
        """What the feed is doing for each instrument. Never shows the key."""
        now = self.clock()
        ids, rows = self._ids(), self._status_rows()
        stale = timedelta(minutes=self.settings.backup_prices_mt5_stale_minutes)
        items = []
        for inst in self._targets():
            iid = ids.get(inst.symbol)
            mt5 = self._last_tick(iid, "mt5") if iid else None
            backup = self._last_tick(iid, SOURCE) if iid else None
            row = rows.get(iid) if iid else None
            mt5_live = mt5 is not None and now - mt5 <= stale
            items.append({
                "symbol": inst.symbol, "backupSymbol": inst.backup_symbol, "mt5LastPriceAt": _iso(mt5),
                "mt5Live": mt5_live, "marketOpen": is_market_open(now, inst.trading_hours),
                "priceSource": "mt5" if mt5_live or (mt5 and (backup is None or mt5 >= backup)) else
                               ("backup" if backup else None),
                "backupLastPriceAt": _iso(backup),
                "lastAttemptAt": _iso(row.last_attempt_at) if row else None,
                "lastSuccessAt": _iso(row.last_success_at) if row else None,
                "lastOutcome": row.last_outcome if row else None, "lastError": row.last_error if row else None,
                "pausedUntil": _iso(row.unavailable_until) if row and row.unavailable_until and
                _utc(row.unavailable_until) > now else None})
        return {"provider": "twelvedata", "enabled": self.settings.backup_prices_enabled, "configured": self.configured,
                "intervalMinutes": self.settings.backup_prices_interval_minutes,
                "mt5StaleMinutes": self.settings.backup_prices_mt5_stale_minutes, "instruments": items}

    # ------------------------------------------------------------------ run
    def run(self, force: bool = False) -> dict[str, Any]:
        """One pass: fetch the instruments that are due and store their prices. Safe to call every minute."""
        if not self.settings.backup_prices_enabled:
            return {"outcome": "off", "reason": "BACKUP_PRICES_ENABLED is false."}
        if not self.configured:
            return {"outcome": "off", "reason": "TWELVE_DATA_API_KEY is not set."}
        now = self.clock()
        ids, rows = self._ids(), self._status_rows()
        stale = timedelta(minutes=self.settings.backup_prices_mt5_stale_minutes)
        skipped: dict[str, str] = {}
        due: list[tuple[InstrumentConfig, int]] = []
        for inst in self._targets():
            iid = ids.get(inst.symbol)
            if iid is None:
                skipped[inst.symbol] = "not_in_database"
                continue
            mt5 = self._last_tick(iid, "mt5")
            row = rows.get(iid)
            if mt5 is not None and now - mt5 <= stale:
                skipped[inst.symbol] = "mt5_live"
            elif not is_market_open(now, inst.trading_hours):
                skipped[inst.symbol] = "market_closed"
            elif row is not None and row.unavailable_until is not None and _utc(row.unavailable_until) > now and not force:
                skipped[inst.symbol] = "paused"
            elif not self._claim(iid, inst.backup_symbol, now, force):
                skipped[inst.symbol] = "not_due"
            else:
                due.append((inst, iid))
        if not due:
            return {"outcome": "idle", "skipped": skipped}
        size = max(self._outputsize(iid, now) for _, iid in due)
        try:
            series = self.client.time_series([i.backup_symbol for i, _ in due], size)
        except ProviderError as exc:
            for inst, iid in due:
                self._record(iid, now, "failed", f"{exc.code or ''} {exc.message}".strip())
            log.warning("Backup price request failed: %s %s", exc.code, exc.message)
            return {"outcome": "failed", "error": exc.message, "code": exc.code, "skipped": skipped}
        results = {}
        for inst, iid in due:
            results[inst.symbol] = self._store(inst, iid, series.get(inst.backup_symbol), now)
        outcome = "updated" if all(r["outcome"] == "updated" for r in results.values()) else (
            "partial" if any(r["outcome"] == "updated" for r in results.values()) else "failed")
        return {"outcome": outcome, "instruments": results, "skipped": skipped}

    def _claim(self, iid: int, symbol: str, now: datetime, force: bool) -> bool:
        """Mark the instrument as being fetched unless another pass did so within the interval (overlapping ticks)."""
        due_before = now - timedelta(minutes=self.settings.backup_prices_interval_minutes) + timedelta(seconds=5)
        with session_scope(self.engine) as s:
            if s.get(BackupPriceStatus, iid) is None:
                s.add(BackupPriceStatus(instrument_id=iid, provider=SOURCE, provider_symbol=symbol, updated_at=now))
                s.flush()
            cond = [BackupPriceStatus.instrument_id == iid]
            if not force:
                cond.append(or_(BackupPriceStatus.last_attempt_at.is_(None), BackupPriceStatus.last_attempt_at <= due_before))
            res = s.execute(update(BackupPriceStatus).where(and_(*cond))
                            .values(last_attempt_at=now, provider_symbol=symbol, updated_at=now))
            return res.rowcount == 1

    def _outputsize(self, iid: int, now: datetime) -> int:
        """Enough 5-minute bars to cover the gap since the newest stored M5 candle (any source), within one day."""
        last = self.repo.latest_candle_ts(iid, "M5", complete_only=False)
        if last is None:
            return MAX_BARS
        return max(3, min(MAX_BARS, int((now - _utc(last)) / M5) + 2))

    def _record(self, iid: int, now: datetime, outcome: str, error: str | None = None, price_at: datetime | None = None,
                pause: bool = False) -> None:
        with session_scope(self.engine) as s:
            row = s.get(BackupPriceStatus, iid)
            if row is None:
                return
            row.last_outcome, row.last_error, row.updated_at = outcome, error, now
            if outcome == "updated":
                row.last_success_at, row.last_price_at, row.unavailable_until = now, price_at, None
            if pause:
                row.unavailable_until = now + timedelta(hours=self.settings.backup_prices_unavailable_hours)

    def _store(self, inst: InstrumentConfig, iid: int, series: Series | None, now: datetime) -> dict[str, Any]:
        if series is None or series.error is not None:
            e = series.error if series is not None else ProviderError(None, "No data returned.")
            if e.code in PLAN_CODES:
                outcome = "not_on_plan"
            elif e.code in UNKNOWN_CODES:
                outcome = "unknown_symbol"
            elif e.code == 429:
                outcome = "rate_limited"
            else:
                outcome = "failed"
            pause = outcome in ("not_on_plan", "unknown_symbol")
            self._record(iid, now, outcome, f"{e.code or ''} {e.message}".strip(), pause=pause)
            return {"outcome": outcome, "error": e.message, "paused": pause}
        bars = [b for b in series.bars if b["ts_utc"] <= now]
        if not bars:
            self._record(iid, now, "no_data", "Twelve Data returned no candles.")
            return {"outcome": "no_data"}
        run_id = f"backup-{now:%Y%m%dT%H%M%S}"
        m5 = [{**b, "timeframe": "M5", "is_complete": b["ts_utc"] + M5 <= now, "broker_symbol": inst.backup_symbol,
               "run_id": run_id} for b in bars]
        written = {"M5": self.repo.upsert_backup_candles(iid, m5, SOURCE)}
        for tf in self.settings.enabled_timeframes:
            if tf != "M5" and tf in TIMEFRAME_SECONDS and TIMEFRAME_SECONDS[tf] > 300:
                written[tf] = self._aggregate(inst, iid, tf, bars[0]["ts_utc"], now, run_id)
        last = bars[-1]
        price_at = now if last["ts_utc"] + M5 > now else last["ts_utc"] + M5
        bid = last["close"]
        spread_points, point = self._broker_spread(iid)
        ask = bid + spread_points * point if spread_points is not None and point else bid
        self.repo.upsert_tick(iid, {"ts_utc": price_at, "bid": bid, "ask": ask, "last": bid, "volume": None,
                                    "spread_points": spread_points, "broker_symbol": inst.backup_symbol,
                                    "mt5_time_msc": int(price_at.timestamp() * 1000)}, run_id, source=SOURCE)
        self._record(iid, now, "updated", None, price_at)
        return {"outcome": "updated", "bars": len(bars), "written": written, "price": bid, "priceAt": _iso(price_at)}

    def _aggregate(self, inst: InstrumentConfig, iid: int, tf: str, since: datetime, now: datetime, run_id: str) -> int:
        """Rebuild the ``tf`` candles touched by the new 5-minute bars from every stored M5 candle in each bucket."""
        first = bucket_start(since, tf, self.server_tz)
        with session_scope(self.engine) as s:
            m5 = s.execute(select(MarketCandle.ts_utc, MarketCandle.open, MarketCandle.high, MarketCandle.low,
                                  MarketCandle.close).where(and_(
                MarketCandle.instrument_id == iid, MarketCandle.timeframe == "M5", MarketCandle.ts_utc >= first,
                MarketCandle.quality_status != "invalid", MarketCandle.open.is_not(None))).order_by(MarketCandle.ts_utc)).all()
        buckets: dict[datetime, list[Any]] = {}
        for r in m5:
            buckets.setdefault(bucket_start(_utc(r.ts_utc), tf, self.server_tz), []).append(r)
        rows = []
        for start, bars in sorted(buckets.items()):
            end = start + timedelta(days=1) if tf == "D1" else start + timedelta(seconds=TIMEFRAME_SECONDS[tf])
            rows.append({"timeframe": tf, "ts_utc": start, "open": bars[0].open, "close": bars[-1].close,
                         "high": max(b.high for b in bars), "low": min(b.low for b in bars), "is_complete": end <= now,
                         "broker_symbol": inst.backup_symbol, "run_id": run_id})
        return self.repo.upsert_backup_candles(iid, rows, SOURCE)

    def _broker_spread(self, iid: int) -> tuple[float | None, float | None]:
        """The broker's recent median M5 spread (points) and point size, used to estimate an ask from the backup price."""
        with session_scope(self.engine) as s:
            spreads = [v for (v,) in s.execute(select(MarketCandle.spread).where(and_(
                MarketCandle.instrument_id == iid, MarketCandle.timeframe == "M5", MarketCandle.source == "mt5",
                MarketCandle.spread.is_not(None))).order_by(MarketCandle.ts_utc.desc()).limit(500)).all()]
            point = s.execute(select(InstrumentSpec.point).where(InstrumentSpec.instrument_id == iid)
                              .order_by(InstrumentSpec.captured_at.desc()).limit(1)).scalar()
        return (float(statistics.median(spreads)) if spreads else None), (float(point) if point else None)


def feed_from_settings(engine: Engine, settings: Settings) -> BackupPriceFeed:
    from aurum.config import load_instruments
    return BackupPriceFeed(engine, settings, load_instruments(settings.resolve(settings.instruments_config)))
