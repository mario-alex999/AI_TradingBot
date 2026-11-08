"""Hand-built Module 03/04 findings for Module 05 tests. All values are synthetic fixtures, never market data."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any

import pandas as pd

from aurum.config import InstrumentConfig, Settings, TradingHours
from aurum.database.connection import engine_from_settings, init_database
from aurum.decision.config import build_config
from aurum.decision.context import DecisionContext, _availability
from aurum.decision.service import DecisionService

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "market_intelligence"))      # mi_helpers

T = datetime(2026, 3, 4, 13, 0, tzinfo=timezone.utc)      # a Wednesday, London/New York overlap
CFG = build_config()
SPEC = {"symbol": "EURUSD", "point": 0.00001, "digits": 5, "tick_size": 0.00001, "tick_value": 1.0, "contract_size": 100000.0,
        "volume_min": 0.01, "volume_max": 50.0, "volume_step": 0.01}
FX = InstrumentConfig("EURUSD", "EURUSD", "forex", "Euro",
                      TradingHours.from_dict({"weekly_close": "FRI 21:00", "weekly_open": "SUN 21:00"}))
VERDICT = {"verdict": "agree", "confidence_adjustment": 0, "summary": "Evidence lines up.", "bullish_case": "Structure is up.", "bearish_case": "Volatility is high.", "supporting_points": ["a"],
           "risks": ["b"], "data_gaps": [], "invalidation_check": "Close below the stop.", "lesson_candidate": ""}


def ob(direction: str = "bullish", top: float = 1.1005, bottom: float = 1.0995, state: str = "fresh", id_: str = "ob1", at: datetime = T) -> NS:
    return NS(id=id_, direction=direction, state=state, current_top=top, current_bottom=bottom, initial_top=top, initial_bottom=bottom,
              source_time=at - timedelta(hours=5), detected_at=at - timedelta(hours=4))


def bos(direction: str = "bullish", kind: str = "BOS", at: datetime = T, index: int = 95, displacement: bool = True, confirmed: bool = True) -> NS:
    return NS(event_type=kind, direction=direction, broken_level=1.1000, confirmed=confirmed, displacement=displacement, break_index=index,
              break_candle_time=at - timedelta(hours=2), confirmed_at=at - timedelta(hours=2))


def pool(level: float = 1.1040, side: str = "buy_side", id_: str = "p1", kind: str = "equal_highs") -> NS:
    return NS(id=id_, level=level, side=side, state="active", kind=kind, timeframe="H1")


def gap(direction: str = "bullish", low: float = 1.0995, high: float = 1.1005, id_: str = "g1", state: str = "unfilled", at: datetime = T) -> NS:
    return NS(id=id_, direction=direction, state=state, current_lower=low, current_upper=high, candle_times=[at - timedelta(hours=6)],
              detected_at=at - timedelta(hours=4))


def external(bias: str = "bullish", state: str = "trending") -> NS:
    return NS(bias=bias, state=state, last_swing_high={"price": 1.12}, last_swing_low={"price": 1.08})


def analysis(tf: str = "H1", bias: str = "bullish", **kw: Any) -> NS:
    base = dict(status="ok", timeframe=tf, as_of=T, atr=0.001, current_price=1.1010, candles_used=100, structure_events=[], order_blocks=[],
                fair_value_gaps=[], liquidity_pools=[], liquidity_events=[], support_resistance=[], premium_discount=None, ote=None,
                structure={"external": external(bias)}, score=None, data_quality={}, messages=[], last_candle=None)
    return NS(**{**base, **kw})


def intel(state: str = "NORMAL", regime: str = "NORMAL", **kw: Any) -> dict[str, Any]:
    return {"event_risk": {"state": state, "coverage": {"complete": True}}, "volatility": {"regime": regime}, "conditions": {"tags": ["TRENDING"]},
            "sessions": {"market_open": True, "active_sessions": ["london", "new_york"], "active_kill_zones": ["ny_open"]}, "levels": {"levels": []},
            "liquidity": {"events": []}, **kw}


ACCOUNT_EMPTY = {"available": False, "reason": "No account snapshot stored.", "open_positions": [], "limits": {}}


def account(**kw: Any) -> dict[str, Any]:
    return {"available": True, "captured_at": T, "equity": 10_000.0, "currency": "USD", "daily_drawdown_pct": 0.0, "account_risk_pct": 0.0,
            "exposure_by_instrument": {}, "open_positions": [], "limits": {"max_per_trade_pct": 1.0}, **kw}


def context(entry: NS | None = None, consensus: str = "bullish", **kw: Any) -> DecisionContext:
    a = entry or analysis()
    analyses = {"D1": analysis("D1"), "H4": analysis("H4"), "H1": a, "M15": analysis("M15", status="insufficient_data")}
    fields = dict(symbol="EURUSD", as_of=T, instrument=FX, spec=SPEC, analyses=analyses, confluence=NS(alignment={"consensus": consensus}),
                  intelligence=intel(), cot={"available": True, "lean": "bullish", "stance": "long"}, account=account(), patterns=[],
                  spread={"spread_points": 8.0, "median_spread_points": 8.0})
    fields.update(kw)
    ctx = DecisionContext(**fields)
    ctx.availability = _availability(ctx)
    ctx.input_hash = "hash"
    return ctx


def ob_setup(**kw: Any) -> DecisionContext:
    """A context on which order_block_continuation finds a clean long."""
    a = analysis(order_blocks=[ob()], structure_events=[bos()], liquidity_pools=[pool()], premium_discount=NS(equilibrium=1.105))
    return context(a, **kw)


def reply(text: str | None = None, stop: str = "end_turn", verdict: dict[str, Any] | None = None) -> NS:
    body = json.dumps(verdict or VERDICT) if text is None else text
    return NS(stop_reason=stop, content=[NS(type="text", text=body)],
              usage=NS(input_tokens=1000, output_tokens=200, cache_creation_input_tokens=0, cache_read_input_tokens=None))


class FakeMessages:
    def __init__(self, result: Any = None):
        self.result, self.calls = reply() if result is None else result, []

    def create(self, **kw: Any) -> Any:
        self.calls.append(kw)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class Candles:
    """Stand-in for Module 01's Repository, over a dict of frames."""

    def __init__(self, frames: dict[tuple[str, str], pd.DataFrame] | None = None):
        self.frames = frames or {}
        self.reads = 0

    def load_candles(self, symbol: str, timeframe: str, **_: Any) -> pd.DataFrame:
        self.reads += 1
        empty = pd.DataFrame({"ts_utc": pd.to_datetime([], utc=True), "high": [], "low": [], "close": []})
        return self.frames.get((symbol, timeframe), empty).copy()

    def latest_specs(self) -> list[dict[str, Any]]:
        return [SPEC]


class Builder:
    """Hands the service a prepared context, and remembers nothing else."""

    def __init__(self, ctx: DecisionContext):
        self.ctx = ctx

    def build(self, symbol: str, as_of: datetime | None = None) -> DecisionContext:
        return self.ctx


def settings(tmp_path: Any, **kw: Any) -> Settings:
    base = dict(_env_file=None, database_url=f"sqlite:///{tmp_path}/d.db", data_dir=tmp_path, log_dir=tmp_path / "logs", ai_review_enabled=False)
    return Settings(**{**base, **kw})


def service(tmp_path: Any, ctx: DecisionContext | None = None, result: Any = None, now: datetime = T, cfg: Any = None, ai: bool = False,
            init: bool = True, **kw: Any) -> tuple[DecisionService, FakeMessages]:
    if ai:
        kw = {"ai_review_enabled": True, "anthropic_api_key": "sk-ant-test-secret", "ai_review_monthly_budget_usd": 5.0, **kw}
    s = settings(tmp_path, **kw)
    engine = engine_from_settings(s)
    if init:
        init_database(engine)
    messages = FakeMessages(result)
    technical = NS(candles=Candles(), instruments={"EURUSD": FX})
    svc = DecisionService(s, engine, cfg or CFG, technical=technical, intelligence=object(), builder=Builder(ctx or ob_setup()),
                          ai_client=NS(messages=messages), now=lambda: svc_clock["t"])
    svc_clock = {"t": now}
    svc.clock = svc_clock  # type: ignore[attr-defined]
    return svc, messages
