"""The benchmark: a frozen baseline, cost scenarios applied to gross results, splits by kind of trade, and a verdict."""
from __future__ import annotations

from datetime import timedelta

import pytest
from decision_helpers import T, service

from aurum.decision.backtest import benchmark as bm
from aurum.decision.backtest.metrics import SimTrade
from aurum.decision.backtest.replay import ReplayResult


def trade(result="tp", gross=2.0, grade="B", template="fair_value_gap_retracement", htf="with", spread_r=0.1, hours=0,
          symbol="EURUSD"):
    at = T + timedelta(hours=hours)
    return SimTrade(symbol, template, "M15", "long", at, "london", 70.0, result, gross - 0.15, at + timedelta(hours=1),
                    "TRENDING", grade=grade, gross_r=gross, spread_r=spread_r, htf=htf, stop_atr=0.6, risk_reward=2.5)


def test_the_baseline_is_frozen_to_the_original_rules():
    assert bm.PROFILES["baseline"] == {"levels": {"min_stop_atr": 0.0},
                                      "selection": {"dedupe": False, "counter_trend_min_grades": ["A", "B", "C", "D", "F"]}}


def test_costs_are_applied_to_the_gross_result():
    t = trade(gross=2.0, spread_r=0.1)
    assert bm.net_r(t, bm.COST_SCENARIOS["base"]) == pytest.approx(2.0 - 0.1 - 0.05)
    assert bm.net_r(t, bm.COST_SCENARIOS["stress"]) == pytest.approx(2.0 - 0.2 - 0.2 - 0.1)
    old = trade(); old.gross_r = None                                        # an older stored trade: its own net result
    assert bm.net_r(old, bm.COST_SCENARIOS["stress"]) == old.r_multiple


def test_summary_counts_wins_losses_profit_factor_and_drawdown_in_r():
    ts = [trade("tp", 3.0, hours=0), trade("sl", -1.0, hours=1), trade("sl", -1.0, hours=2), trade("tp", 2.0, hours=3)]
    s = bm.summarize(ts, {"spread_multiple": 0, "slippage_r": 0, "commission_r": 0})
    assert (s.trades, s.wins, s.losses, s.tp, s.sl) == (4, 2, 2, 2, 2)
    assert s.net_r == 3.0 and s.profit_factor == 2.5 and s.win_rate == 50.0 and s.max_drawdown_r == 2.0


def test_results_are_split_by_grade_band_and_alignment():
    ts = [trade("tp", 3, grade="A"), trade("sl", -1, grade="C"), trade("sl", -1, grade="D", template="choch_reversal", htf="against")]
    b = bm.breakdown(ts, bm.COST_SCENARIOS["low"])
    assert set(b["grade_band"]) == {"A/B", "C/D"} and b["grade_band"]["C/D"]["trades"] == 2
    assert b["htf_alignment"]["against"]["trades"] == 1 and b["template"]["choch_reversal"]["net_r"] < 0


def test_a_candidate_must_beat_the_baseline_on_net_r_profit_factor_and_drawdown():
    baseline = [trade("tp", 4.0, hours=0), trade("sl", -1.0, hours=1), trade("sl", -1.0, hours=2)]
    fewer_but_cleaner = [trade("tp", 2.5, hours=0), trade("sl", -1.0, hours=1)]
    v = bm.verdict(fewer_but_cleaner, baseline)
    assert v["beats_baseline"] is False and any("net" in r for r in v["reasons"])     # higher PF alone is not enough
    assert bm.verdict(baseline + [trade("tp", 2.0, hours=3)], baseline)["beats_baseline"] is True


def test_service_benchmark_replays_each_profile_on_the_same_history(tmp_path, monkeypatch):
    svc, _ = service(tmp_path)
    calls = []

    def fake(begin, finish, symbols, templates=None, progress=None, cfg=None):
        calls.append((cfg.levels["min_stop_atr"], cfg.selection["dedupe"], templates,
                      cfg.template("choch_reversal")["enabled"]))
        return [trade("tp", 2.0), trade("sl", -1.0, hours=1)], ReplayResult(steps=10, candidates=5)

    monkeypatch.setattr(svc, "replay_trades", fake)
    rep = svc.benchmark(T, T + timedelta(days=2), ["current", "no_choch", "choch_only", "min_stop_1atr"])
    assert list(rep["profiles"]) == ["baseline", "current", "no_choch", "choch_only", "min_stop_1atr"]
    assert calls[0] == (0.0, False, None, True)                     # baseline: original rules
    assert calls[2][3] is False and calls[3][2] == ["choch_reversal"] and calls[4][0] == 1.0
    assert "verdict" in rep["profiles"]["current"] and "verdict" not in rep["profiles"]["choch_only"]
    assert set(rep["profiles"]["current"]["scenarios"]) == {"low", "base", "high", "stress"}
    with pytest.raises(ValueError):
        svc.benchmark(T, T + timedelta(days=1), ["nope"])
