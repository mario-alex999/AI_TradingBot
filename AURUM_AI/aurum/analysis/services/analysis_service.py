"""Module 03 service: loads candles through Module 01, runs the engine, adds optional COT context, caches and
(optionally) persists results. It never connects to MT5 and never places or approves trades."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.engine import Engine

from aurum.analysis.config import AnalysisConfig, load_analysis_config
from aurum.analysis.data import prepare_candles
from aurum.analysis.models.analysis_result import ConfluenceReport, TimeframeAnalysis
from aurum.analysis.multi_timeframe.confluence_engine import confluence
from aurum.analysis.multi_timeframe.timeframe_analysis import align_to_as_of
from aurum.analysis.services.analysis_cache import AnalysisCache
from aurum.analysis.services.analysis_repository import AnalysisRepository
from aurum.analysis.services.cot_adapter import CotProvider, Module02CotAdapter, NullCotProvider
from aurum.analysis.technical_engine import analyze_series
from aurum.config import TIMEFRAME_SECONDS, InstrumentConfig, Settings, get_settings, load_instruments
from aurum.database.repository import Repository

log = logging.getLogger(__name__)


class UnknownInstrumentError(KeyError):
    pass


class TechnicalAnalysisService:
    def __init__(self, settings: Settings | None = None, engine: Engine | None = None,
                 cfg: AnalysisConfig | None = None, candles: Repository | None = None,
                 cot: CotProvider | None = None, cache: AnalysisCache | None = None,
                 instruments: dict[str, InstrumentConfig] | None = None):
        from aurum.database.connection import engine_from_settings
        self.settings = settings or get_settings()
        self.engine = engine or engine_from_settings(self.settings)
        self.cfg = cfg or load_analysis_config()
        self.candles = candles or Repository(self.engine)          # Module 01's candle store - no MT5 here
        self.cot = cot or Module02CotAdapter(self.engine)
        self.cache = cache or AnalysisCache()
        self.store = AnalysisRepository(self.engine)
        self.instruments = instruments or load_instruments(self.settings.resolve(self.settings.instruments_config))

    # ------------------------------------------------------------------ helpers
    def instrument(self, symbol: str) -> InstrumentConfig:
        s = symbol.upper()
        if s not in self.instruments:
            raise UnknownInstrumentError(f"Unknown instrument '{symbol}'. Configured: {', '.join(self.instruments)}")
        return self.instruments[s]

    @staticmethod
    def check_timeframe(tf: str) -> str:
        tf = tf.upper()
        if tf not in TIMEFRAME_SECONDS:
            raise ValueError(f"Unsupported timeframe '{tf}'. Supported: {', '.join(TIMEFRAME_SECONDS)}")
        return tf

    def load_series(self, symbol: str, timeframe: str, as_of: datetime | None = None,
                    include_provisional: bool = False):
        inst = self.instrument(symbol)
        tf = self.check_timeframe(timeframe)
        as_of = _utc(as_of) or datetime.now(timezone.utc)
        df = self.candles.load_candles(inst.symbol, tf, include_invalid=True, include_incomplete=True)
        df = align_to_as_of(df, tf, as_of, include_provisional)
        if len(df) > self.cfg.max_candles:
            df = df.iloc[-self.cfg.max_candles:]
        return prepare_candles(df, inst.symbol, tf, hours=inst.trading_hours, include_provisional=include_provisional)

    def cot_context(self, symbol: str, as_of: datetime | None, include_cot: bool) -> dict[str, Any]:
        if not include_cot:
            return NullCotProvider().context(symbol, as_of)
        return self.cot.context(symbol, as_of)

    # ------------------------------------------------------------------ public API
    def analyze(self, symbol: str, timeframe: str, as_of: datetime | None = None, include_provisional: bool = False,
                include_cot: bool = True, persist: bool = False) -> TimeframeAnalysis:
        series = self.load_series(symbol, timeframe, as_of, include_provisional)
        key = ("tf", series.symbol, series.timeframe, len(series),
               series.times[-1].isoformat() if len(series) else None, series.provisional, self.cfg.fingerprint,
               include_cot)
        result = self.cache.get(key)
        if result is None:
            cot = self.cot_context(series.symbol, as_of, include_cot) if include_cot else None
            result = analyze_series(series, self.cfg, cot)
            self.cache.put(key, result)
        if persist and result.status == "ok":
            self.store.save_timeframe(result)
        return result

    def analyze_confluence(self, symbol: str, timeframes: list[str] | None = None, as_of: datetime | None = None,
                           include_provisional: bool = False, include_cot: bool = True,
                           persist: bool = False) -> ConfluenceReport:
        inst = self.instrument(symbol)
        tfs = [self.check_timeframe(t) for t in (timeframes or list(self.cfg.multi_timeframe.timeframes))]
        as_of = _utc(as_of) or datetime.now(timezone.utc)
        results = {tf: self.analyze(inst.symbol, tf, as_of, include_provisional, include_cot=False, persist=persist)
                   for tf in tfs}
        cot = self.cot_context(inst.symbol, as_of, include_cot)
        report = confluence(inst.symbol, results, self.cfg, as_of, cot)
        if persist:
            self.store.save_confluence(report)
        return report


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
