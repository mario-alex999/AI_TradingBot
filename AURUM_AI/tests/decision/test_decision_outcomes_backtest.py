"""Outcome labelling, backtest metrics and the no-look-ahead replay on synthetic candles."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pandas as pd
import pytest
from decision_helpers import CFG, FX, SPEC, Candles, T, settings
from mi_helpers import cfg as mi_cfg, resample, walk

from aurum.analysis.config import AnalysisConfig
from aurum.analysis.services.analysis_service import TechnicalAnalysisService
from aurum.decision.adapters import IntelligenceAdapter, NullAccount, NullLearning, SpreadSource
from aurum.decision.backtest.cache import CachedCandles, ReplayTechnical
from aurum.decision.backtest.metrics import SimTrade, report
from aurum.decision.backtest.replay import candidates_at, cost_in_r, replay_symbol, time_steps
from aurum.decision.candidates.setups import generate
from aurum.decision.config import build_config
from aurum.decision.context import ContextBuilder
from aurum.decision.outcomes import AMBIGUOUS_R, label
from aurum.market_intelligence.candles import FrameCandleSource
from aurum.market_intelligence.service import MarketIntelligenceService


def candles(rows, start=T):
    return pd.DataFrame({"ts_utc": [start + timedelta(minutes=5 * i) for i in range(len(rows))], "high": [r[0] for r in rows], "low": [r[1] for r in rows],
                         "close": [r[2] for r in rows]})


def outcome(rows, direction="long", stop=1.0950, target=1.1100, hold=96, expires=T + timedelta(hours=6)):
    return label(direction, 1.0995, 1.1005, stop, target, expires, candles(rows), hold)


# ---- labelling
def test_target_after_fill_is_a_win():
    o = outcome([(1.1010, 1.0999, 1.1005), (1.1110, 1.1000, 1.1100)])
    assert o.filled and o.result == "tp" and o.r_multiple == pytest.approx(2.0)


def test_stop_after_fill_is_a_loss():
    o = outcome([(1.1010, 1.0999, 1.1005), (1.1005, 1.0940, 1.0950)])
    assert o.result == "sl" and o.r_multiple == -1.0


def test_same_candle_stop_and_target_is_ambiguous_and_counts_as_a_loss():
    o = outcome([(1.1010, 1.0999, 1.1005), (1.1110, 1.0940, 1.1000)])
    assert o.result == "ambiguous" and o.r_multiple == AMBIGUOUS_R == -1.0


def test_target_on_the_fill_candle_cannot_prove_a_win():
    assert outcome([(1.1110, 1.0999, 1.1050)]).result == "ambiguous"


def test_short_is_a_mirror_image():
    o = label("short", 1.0995, 1.1005, 1.1050, 1.0900, T + timedelta(hours=6), candles([(1.1001, 1.0990, 1.0995), (1.0995, 1.0890, 1.0900)]), 96)
    assert o.result == "tp" and o.r_multiple == pytest.approx(2.0)


def test_zone_never_reached_expires():
    o = outcome([(1.1100, 1.1050, 1.1060), (1.1100, 1.1050, 1.1060)], expires=T + timedelta(minutes=5))
    assert not o.touched and not o.filled and o.result == "expired" and o.r_multiple is None


def test_touch_without_reaching_the_midpoint_is_not_a_fill():
    o = outcome([(1.1100, 1.1003, 1.1050), (1.1100, 1.1050, 1.1060)])
    assert o.touched and not o.filled and o.result == "open"


def test_max_hold_closes_at_market():
    o = outcome([(1.1010, 1.0999, 1.1000), (1.1010, 1.0999, 1.1050)], hold=1)
    assert o.result == "expired" and o.r_multiple == pytest.approx(1.0)


def test_not_enough_candles_stays_open():
    o = outcome([(1.1010, 1.0999, 1.1000)])
    assert o.filled and o.result == "open" and o.r_multiple is None


def test_mfe_and_mae_are_recorded():
    o = outcome([(1.1010, 1.0999, 1.1005), (1.1050, 1.0975, 1.1000), (1.1005, 1.0940, 1.0950)])
    assert o.mfe_r == pytest.approx(1.0) and o.mae_r == pytest.approx(-1.2)       # the last wick ran past the stop


def test_zero_risk_is_not_a_trade():
    assert label("long", 1.10, 1.10, 1.10, 1.11, T, candles([(1.2, 1.0, 1.1)]), 10).r_multiple is None


# ---- metrics
def trade(r, template="a", symbol="EURUSD", confidence=65.0, at=T, result=None):
    return SimTrade(symbol, template, "H1", "long", at, "london", confidence, result or ("tp" if r > 0 else "sl"), r, at + timedelta(hours=1))


def test_report_matches_the_performance_contract():
    rep = report([trade(2.0), trade(-1.0), trade(2.0, "b")], 1.0, 10_000, None, 30)
    assert set(rep) >= {"kind", "metrics", "equityCurve", "monthly", "byInstrument", "bySession", "byStrategy"}
    m = rep["metrics"]
    assert rep["kind"] == "backtest" and m["totalTrades"] == 3 and m["winningTrades"] == 2 and m["losingTrades"] == 1
    assert m["winRate"] == pytest.approx(66.67) and m["profitFactor"] == 4.0
    assert m["averageProfit"] == 200 and m["averageLoss"] == -100 and m["expectancy"] == 100
    assert {x["name"]: x["pnl"] for x in rep["byStrategy"]} == {"a": 100.0, "b": 200.0}
    assert rep["details"]["by_template"]["a"]["expectancy_r"] == 0.5


def test_equity_compounds_and_drawdown_is_measured():
    rep = report([trade(-1.0), trade(-1.0)], 1.0, 10_000, None, 1)
    assert rep["equityCurve"][-1]["equity"] == pytest.approx(9801.0)
    assert rep["metrics"]["maxDrawdownPct"] == pytest.approx(1.99, abs=0.01)
    assert rep["metrics"]["profitFactor"] == 0.0


def test_no_losses_means_no_profit_factor():
    assert report([trade(1.0)], 1.0, 10_000, None, 1)["metrics"]["profitFactor"] is None


def test_small_samples_are_called_out():
    warnings = report([trade(1.0)], 1.0, 10_000, None, 30)["details"]["warnings"]
    assert any("Small sample: 1" in w for w in warnings) and any("Small sample for a" in w for w in warnings)


def test_empty_report_is_honest():
    rep = report([], 1.0, 10_000, None, 30)
    assert rep["metrics"]["totalTrades"] == 0 and rep["equityCurve"] == [] and rep["metrics"]["sharpe"] is None


def test_sample_split_and_confidence_buckets():
    late = T + timedelta(days=10)
    rep = report([trade(1.0), trade(-1.0, at=late, confidence=85.0)], 1.0, 10_000, T + timedelta(days=5), 1)
    samples = rep["details"]["samples"]
    assert samples["in_sample"]["trades"] == 1 and samples["out_of_sample"]["expectancy_r"] == -1.0
    assert set(rep["details"]["by_confidence"]) == {"60-80", "80-100"}
    assert set(rep["details"]["by_timeframe"]) == {"H1"} and set(rep["details"]["by_regime"]) == {"unknown"}


def test_costs_are_charged_in_r():
    c = NS(entry_mid=1.1000, stop_loss=1.0990)
    assert cost_in_r(c, 0.0001, 0.05) == pytest.approx(0.15)
    assert cost_in_r(c, None, 0.05) == pytest.approx(0.05)
    assert cost_in_r(c, 0.0001, 0.05, 0.02) == pytest.approx(0.17)


def test_time_steps():
    assert len(time_steps(T, T + timedelta(hours=3), "H1")) == 4


# ---- replay on synthetic history, with the real Module 03 and Module 04
START, END = datetime(2026, 1, 5, tzinfo=timezone.utc), datetime(2026, 2, 21, tzinfo=timezone.utc)
SECONDS = {"M5": 300, "M15": 900, "H1": 3600, "H4": 14400, "D1": 86400}
RULES = {"M15": "15min", "H1": "1h", "H4": "4h", "D1": "1D"}


class NoCot:
    def context(self, symbol, as_of):
        return {"available": False, "reason": "none in this test"}


@pytest.fixture(scope="module")
def frames():
    m5 = walk(START, END, 5, seed=3, step=0.00030, symbol="EURUSD", tf="M5")
    out = {("EURUSD", "M5"): m5}
    for tf, rule in RULES.items():
        out[("EURUSD", tf)] = resample(m5, rule, tf)
    for f in out.values():
        f["spread"] = 8
        f["source"] = "fixture"
    return out


def builder(tmp_path, frames, wrap=None):
    s = settings(tmp_path)
    from aurum.database.connection import engine_from_settings, init_database
    engine = engine_from_settings(s)
    init_database(engine)
    tech = TechnicalAnalysisService(s, engine, AnalysisConfig(), Candles(frames), NoCot(), instruments={"EURUSD": FX})
    if wrap:
        tech = wrap(tech)
    mi = MarketIntelligenceService(s, engine, mi_cfg(), candles=FrameCandleSource({k: v for k, v in frames.items() if k[1] != "H4"}),
                                   instruments={"EURUSD": FX})
    return ContextBuilder(CFG, tech, IntelligenceAdapter(mi, include_module03=False), NullAccount(), NullLearning(),
                          SpreadSource(tech.candles, cache=False), lambda sym: SPEC)


def cut(frames, t):
    return {k: v[v["ts_utc"] + pd.to_timedelta(SECONDS[k[1]], unit="s") <= pd.Timestamp(t)].reset_index(drop=True) for k, v in frames.items()}


def fingerprint(ctx):
    found, skipped = generate(ctx, CFG)
    return ([(c.key, c.entry_low, c.entry_high, c.stop_loss, c.take_profit, c.expires_at, s.confidence) for c, s in candidates_at(ctx, CFG)],
            [(s.template, s.timeframe, s.reason) for s in skipped], ctx.consensus, ctx.regime, ctx.spread)


STEPS = [datetime(2026, 2, 10, h, tzinfo=timezone.utc) for h in (9, 13, 17)] + [datetime(2026, 2, 12 + d, 14, tzinfo=timezone.utc) for d in (0, 1, 5, 6)]


def test_candidates_from_full_history_match_truncated_history(tmp_path, frames):
    """What the engine finds at t must not change when everything after t is deleted."""
    full = builder(tmp_path, frames)
    seen = 0
    for t in STEPS:
        a = fingerprint(full.build("EURUSD", t))
        (tmp_path / str(t.day) / str(t.hour)).mkdir(parents=True)
        b = fingerprint(builder(tmp_path / str(t.day) / str(t.hour), cut(frames, t)).build("EURUSD", t))
        assert a == b
        seen += len(a[0]) + len(a[1])
    assert seen > 0


def test_appending_future_data_does_not_change_an_earlier_decision(tmp_path, frames):
    """The full analysis of a past moment, regime and bias and decision included, ignores what came later."""
    from aurum.decision.recommendation import Assessed, decide, find_conflicts
    from aurum.decision.regime import assess_bias, assess_regime
    from aurum.decision.gates import run_gates
    t = STEPS[1]

    def verdict(ctx):
        assessed = [Assessed(c, s, run_gates(ctx, c, CFG)) for c, s in candidates_at(ctx, CFG)]
        r, b = assess_regime(ctx, CFG), assess_bias(ctx, CFG)
        return r, b, decide(ctx, CFG, r, b, assessed, find_conflicts(ctx, r, b, assessed))
    (tmp_path / "cut").mkdir()
    assert verdict(builder(tmp_path, frames).build("EURUSD", t)) == verdict(builder(tmp_path / "cut", cut(frames, t)).build("EURUSD", t))


def test_replay_cache_changes_nothing(tmp_path, frames):
    plain = builder(tmp_path, frames)
    (tmp_path / "f").mkdir()
    fast = builder(tmp_path / "f", frames, ReplayTechnical)
    for t in STEPS:
        assert fingerprint(plain.build("EURUSD", t)) == fingerprint(fast.build("EURUSD", t))


def test_replay_cache_reads_each_frame_once():
    inner = Candles({("EURUSD", "H1"): pd.DataFrame({"ts_utc": [T]})})
    cached = CachedCandles(inner)
    cached.load_candles("EURUSD", "H1"), cached.load_candles("EURUSD", "H1")
    assert inner.reads == 1 and cached.latest_specs() == [SPEC]


def test_replay_runs_and_blocks_duplicates(tmp_path, frames):
    b = builder(tmp_path, frames, ReplayTechnical)
    cfg = build_config({"backtest": {"step_timeframe": "H4"}})
    r = replay_symbol("EURUSD", datetime(2026, 2, 9, tzinfo=timezone.utc), datetime(2026, 2, 13, tzinfo=timezone.utc), cfg, b, frames[("EURUSD", "M5")])
    assert r.steps == 25
    assert len({(t.template, t.as_of) for t in r.trades}) == len(r.trades)
    for t in r.trades:
        assert t.resolved_at is None or t.resolved_at >= t.as_of
