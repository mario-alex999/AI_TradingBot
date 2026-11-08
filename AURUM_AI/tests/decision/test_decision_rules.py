"""Templates, levels, sizing, gates, score and the status machine: the deterministic core, with hand-built findings."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace as NS

import pytest
from decision_helpers import (ACCOUNT_EMPTY, CFG, SPEC, T, account, analysis, bos, context, gap, intel, ob, ob_setup, pool)

from aurum.decision.candidates import levels as lv
from aurum.decision.candidates.setups import (TEMPLATES, Candidate, bos_continuation, choch_reversal, fair_value_gap_retracement, generate,
                                              liquidity_sweep_reversal, order_block_continuation, premium_discount_ote, range_edge_rejection,
                                              session_breakout)
from aurum.decision.candidates.sizing import suggest_volume
from aurum.decision.config import DecisionConfigError, build_config
from aurum.decision.gates import (FAIL, PASS, WARN, account_risk, confidence_cap, cot, data_quality, duplication, event_risk, first_failure,
                                  market_open, net_risk_reward, risk_reward_gate, run_gates, spread_volatility, structure_conflict)
from aurum.decision.adapters import positioning_summary
from aurum.decision.backtest.replay import candidates_at
from aurum.decision.lifecycle import ACTIVE, STATUSES, TRANSITIONS, IllegalTransition, check
from aurum.decision.recommendation import Assessed, decide, evidence_summary, explain, find_conflicts
from aurum.decision.regime import assess_bias, assess_regime
from aurum.decision.scoring import calibration, grade_of, score


def candidate(**kw):
    base = dict(symbol="EURUSD", template="order_block_continuation", timeframe="H1", direction="long", zone_id="ob1", as_of=T,
                entry_low=1.0995, entry_high=1.1005, stop_loss=1.0990, take_profit=1.1040, take_profit2=None, risk_reward=4.0,
                expires_at=T + timedelta(hours=12), invalidation="x", market_condition="TRENDING")
    return Candidate(**{**base, **kw})


# ------------------------------------------------------------------------------------ config
def test_config_rejects_unknown_keys_and_bad_weights():
    with pytest.raises(DecisionConfigError):
        build_config({"gates": {"min_rrr": 2}})
    with pytest.raises(DecisionConfigError):
        build_config({"score": {"weights": {"learning": 50.0}}})


def test_shipped_yaml_loads():
    from aurum.decision.config import load_decision_config
    assert load_decision_config().gates["min_rr"] == 2.0


# ------------------------------------------------------------------------------------ templates
def test_order_block_continuation_finds_a_long_with_levels_from_the_zone():
    ctx = ob_setup()
    c = order_block_continuation(ctx, ctx.analyses["H1"], CFG)
    assert isinstance(c, Candidate) and c.direction == "long"
    assert (c.entry_low, c.entry_high) == (1.0995, 1.1005)
    assert c.stop_loss == pytest.approx(1.0995 - 0.00015 - 0.00008)       # distal edge, ATR buffer, spread
    assert c.take_profit == 1.1040 and c.risk_reward >= 2
    assert {s["kind"] for s in c.sources} >= {"order_block", "BOS with displacement"}


@pytest.mark.parametrize("change,needle", [
    (dict(consensus="mixed"), "not directional"),
    (dict(entry=analysis(order_blocks=[ob()], premium_discount=NS(equilibrium=1.105))), "No recent confirmed"),
    (dict(entry=analysis(order_blocks=[ob()], structure_events=[bos(displacement=False)], premium_discount=NS(equilibrium=1.105))), "No recent confirmed"),
    (dict(entry=analysis(order_blocks=[ob()], structure_events=[bos(index=10)], premium_discount=NS(equilibrium=1.105))), "No recent confirmed"),
    (dict(entry=analysis(order_blocks=[ob(state="mitigated")], structure_events=[bos()], premium_discount=NS(equilibrium=1.105))), "No active"),
    (dict(entry=analysis(order_blocks=[ob()], structure_events=[bos()], premium_discount=NS(equilibrium=1.09))), "No active"),   # premium, for a long
])
def test_order_block_continuation_negative_cases(change, needle):
    ctx = context(**change)
    assert needle in order_block_continuation(ctx, ctx.analyses["H1"], CFG)


def test_a_zone_detected_after_as_of_is_invisible():
    a = analysis(order_blocks=[ob(at=T + timedelta(hours=9))], structure_events=[bos()], liquidity_pools=[pool()], premium_discount=NS(equilibrium=1.105))
    ctx = context(a)
    assert "No active" in order_block_continuation(ctx, a, CFG)


def fvg_analysis(**kw):
    base = dict(structure_events=[bos()], fair_value_gaps=[gap()], liquidity_pools=[pool()],
                ote=NS(direction="bullish", lower=1.0990, upper=1.1003),
                premium_discount=NS(leg_direction="bullish", range_low=1.0985, range_high=1.1060, equilibrium=1.102))
    return analysis(**{**base, **kw})


def test_ote_prefers_the_overlap_with_an_fvg():
    a = fvg_analysis()
    c = premium_discount_ote(context(a), a, CFG)
    assert (c.entry_low, c.entry_high) == (1.0995, 1.1003) and c.stop_loss < 1.0985
    assert any(s["kind"] == "fair_value_gap" for s in c.sources)


def test_ote_works_without_an_fvg():
    a = fvg_analysis(fair_value_gaps=[gap(low=1.1006, high=1.1009)])
    c = premium_discount_ote(context(a), a, CFG)
    assert (c.entry_low, c.entry_high) == (1.0990, 1.1003)


@pytest.mark.parametrize("change,needle", [
    (dict(ote=None), "No bullish OTE"), (dict(ote=NS(direction="bearish", lower=1.09, upper=1.10)), "No bullish OTE"),
    (dict(ote=NS(direction="bullish", lower=1.09, upper=1.095)), "not near"), (dict(structure_events=[]), "No recent confirmed"),
])
def test_ote_negative_cases(change, needle):
    a = fvg_analysis(**change)
    assert needle in premium_discount_ote(context(a), a, CFG)


def test_ote_is_invalid_without_a_direction():
    a = fvg_analysis()
    assert "not directional" in premium_discount_ote(context(a, consensus="mixed"), a, CFG)


def test_fvg_retracement_long_and_short():
    a = fvg_analysis()
    c = fair_value_gap_retracement(context(a), a, CFG)
    assert c.direction == "long" and (c.entry_low, c.entry_high) == (1.0995, 1.1005) and c.stop_loss < 1.0995
    s = fvg_analysis(structure_events=[bos("bearish")], fair_value_gaps=[gap("bearish", 1.1012, 1.1020)], liquidity_pools=[pool(1.0960, "sell_side")],
                     structure={"external": NS(bias="bearish", state="trending", last_swing_low=None)})
    c = fair_value_gap_retracement(context(s, consensus="bearish"), s, CFG)
    assert c.direction == "short" and c.stop_loss > 1.1020


@pytest.mark.parametrize("change,needle", [
    (dict(fair_value_gaps=[gap(state="fully_filled")]), "No unfilled"), (dict(fair_value_gaps=[gap(low=1.09, high=1.091)]), "No unfilled"),
    (dict(fair_value_gaps=[gap("bearish")]), "No unfilled"), (dict(structure_events=[]), "No recent confirmed"),
])
def test_fvg_retracement_negative_cases(change, needle):
    a = fvg_analysis(**change)
    assert needle in fair_value_gap_retracement(context(a), a, CFG)


def bos_at(level, direction="bullish"):
    e = bos(direction)
    e.broken_level = level
    return e


def test_bos_continuation_long_and_short():
    a = analysis(structure_events=[bos_at(1.1008)], liquidity_pools=[pool(1.1060)])
    c = bos_continuation(context(a), a, CFG)
    assert c.direction == "long" and c.entry_low < 1.1008 < c.entry_high and c.stop_loss < c.entry_low
    s = analysis(structure_events=[bos_at(1.1012, "bearish")], liquidity_pools=[pool(1.0960, "sell_side")], structure={"external": NS(bias="bearish", state="trending", last_swing_low=None)})
    assert bos_continuation(context(s, consensus="bearish"), s, CFG).direction == "short"


def test_bos_continuation_needs_a_retest_and_direction():
    a = analysis(structure_events=[bos_at(1.09)], liquidity_pools=[pool(1.1060)])
    assert "no retest" in bos_continuation(context(a), a, CFG)
    assert "not directional" in bos_continuation(context(a, consensus="mixed"), a, CFG)
    b = analysis(structure_events=[bos_at(1.1008, "bearish")])
    assert "No recent confirmed" in bos_continuation(context(b), b, CFG)


def test_choch_reversal_uses_a_supporting_zone():
    a = analysis(structure_events=[bos(kind="CHOCH")], order_blocks=[ob()], liquidity_pools=[pool()])
    c = choch_reversal(context(a), a, CFG)
    assert c.direction == "long" and c.stop_loss < 1.0995 and "CHoCH" in [s["kind"] for s in c.sources]
    s = analysis(structure_events=[bos("bearish", kind="CHOCH")], order_blocks=[ob("bearish", 1.1020, 1.1012)], liquidity_pools=[pool(1.0960, "sell_side")],
                 structure={"external": NS(bias="bearish", state="trending", last_swing_low=None)})
    assert choch_reversal(context(s), s, CFG).direction == "short"


@pytest.mark.parametrize("change,needle", [
    (dict(structure_events=[]), "No recent"), (dict(structure_events=[bos(kind="CHOCH", index=5)]), "No recent"),
    (dict(structure_events=[bos(kind="CHOCH", displacement=False)]), "no displacement"), (dict(order_blocks=[ob(state="invalidated")]), "No active"),
])
def test_choch_reversal_negative_cases(change, needle):
    a = analysis(**{**dict(structure_events=[bos(kind="CHOCH")], order_blocks=[ob()], liquidity_pools=[pool()]), **change})
    assert needle in choch_reversal(context(a), a, CFG)


def asian(high=1.1000, low=1.0980):
    return {"levels": [{"level_type": "previous_asian_high", "side": "high", "price": high}, {"level_type": "previous_asian_low", "side": "low", "price": low}]}


def test_session_breakout_long_and_short():
    a = analysis("M15", structure_events=[bos()], liquidity_pools=[pool(1.1060)], current_price=1.1004)
    c = session_breakout(context(a, intelligence=intel(levels=asian())), a, CFG)
    assert c.direction == "long" and c.entry_low < 1.1000 < c.entry_high
    s = analysis("M15", structure_events=[bos("bearish")], liquidity_pools=[pool(1.0920, "sell_side")], current_price=1.0975,
                 structure={"external": NS(bias="bearish", state="trending", last_swing_low=None)})
    assert session_breakout(context(s, intelligence=intel(levels=asian())), s, CFG).direction == "short"


def test_session_breakout_negative_cases():
    a = analysis("M15", structure_events=[bos()], current_price=1.0990)
    assert "inside the Asian" in session_breakout(context(a, intelligence=intel(levels=asian())), a, CFG)
    assert "No Asian" in session_breakout(context(a), a, CFG)
    b = analysis("M15", current_price=1.1004)
    assert "No confirmed" in session_breakout(context(b, intelligence=intel(levels=asian())), b, CFG)
    quiet = intel(levels=asian())
    quiet["sessions"] = {"active_sessions": ["asian"]}
    assert "Not in" in session_breakout(context(b, intelligence=quiet), b, CFG)


def test_session_breakout_only_runs_on_its_timeframe():
    ctx = context()
    names = {(s.template, s.timeframe) for s in generate(ctx, CFG)[1]}
    assert ("session_breakout", "M15") in names and ("session_breakout", "H1") not in names


def test_all_seven_requested_strategies_exist():
    assert {"liquidity_sweep_reversal", "order_block_continuation", "fair_value_gap_retracement", "bos_continuation", "choch_reversal",
            "session_breakout", "premium_discount_ote"} <= set(TEMPLATES)


def test_generate_can_be_limited_to_timeframes():
    assert {s.timeframe for s in generate(context(), CFG, None, ["H1"])[1]} == {"H1"}


def sweep(at=T - timedelta(hours=3)):
    return NS(event="sweep", pool_id="eql", pool_kind="equal_lows", side="sell_side", level=1.0992, extreme=1.0985, confirmed_at=at)


def test_sweep_reversal_needs_sweep_then_choch_then_zone():
    a = analysis(liquidity_events=[sweep()], structure_events=[bos(kind="CHOCH")], order_blocks=[ob(at=T + timedelta(hours=3))], liquidity_pools=[pool()])
    a.order_blocks[0].detected_at = T - timedelta(hours=1)
    c = liquidity_sweep_reversal(context(a), a, CFG)
    assert c.direction == "long" and c.stop_loss < 1.0985


def test_sweep_reversal_rejects_a_choch_from_before_the_sweep():
    a = analysis(liquidity_events=[sweep(T - timedelta(hours=1))], structure_events=[bos(kind="CHOCH")], order_blocks=[ob()], liquidity_pools=[pool()])
    assert "CHoCH" in liquidity_sweep_reversal(context(a), a, CFG)


def test_sweep_reversal_reads_module_04_events_on_its_timeframe():
    event = {"event_type": "wick_sweep", "timeframe": "H1", "confirmation_time": T - timedelta(hours=3), "side": "low", "level": 1.0992,
             "extreme": 1.0985, "level_type": "previous_day_low"}
    a = analysis(structure_events=[bos(kind="CHOCH")], order_blocks=[ob()], liquidity_pools=[pool()])
    a.order_blocks[0].detected_at = T - timedelta(hours=1)
    c = liquidity_sweep_reversal(context(a, intelligence=intel(liquidity={"events": [event]})), a, CFG)
    assert c.direction == "long" and c.sources[0]["module"] == "module_04"


def zone(role, bottom, top, id_, score_=80.0):
    return NS(id=id_, role=role, bottom=bottom, top=top, mid=(bottom + top) / 2, score=score_, first_time=T - timedelta(days=3), last_time=T)


def range_analysis(**kw):
    base = dict(structure={"external": external_ranging()}, support_resistance=[zone("support", 1.1000, 1.1006, "z1"), zone("resistance", 1.1060, 1.1070, "z2")])
    return analysis(**{**base, **kw})


def external_ranging():
    return NS(bias="ranging", state="ranging", last_swing_high=None, last_swing_low=None)


def test_range_edge_trades_towards_the_other_edge():
    a = range_analysis(current_price=1.1004)
    c = range_edge_rejection(context(a), a, CFG)
    assert c.direction == "long" and c.take_profit == 1.1060 and c.stop_loss < 1.1000


@pytest.mark.parametrize("regime,needle", [("VERY_HIGH", "breaking"), ("HIGH", "breaking"), ("UNKNOWN", "unknown")])
def test_range_edge_stands_aside_when_volatility_expands(regime, needle):
    a = range_analysis(current_price=1.1004)
    assert needle in range_edge_rejection(context(a, intelligence=intel(regime=regime)), a, CFG)


def test_range_edge_needs_a_range():
    a = analysis()
    assert "not ranging" in range_edge_rejection(context(a), a, CFG)


def test_generate_explains_every_blank():
    found, skipped = generate(context(), CFG)
    assert found == [] and skipped and all(s.reason for s in skipped)
    assert any("no usable M15" in s.reason for s in skipped)


def test_idempotent_key_ignores_prices_but_not_identity():
    a, b = candidate(), candidate(stop_loss=1.09, as_of=T + timedelta(hours=1))
    assert a.key == b.key
    assert a.key != candidate(zone_id="other").key and a.key != candidate(direction="short").key
    assert candidate(fixed_key="abc").key == "abc"


# ------------------------------------------------------------------------------------ levels
def test_risk_reward_and_target_choice():
    assert lv.risk_reward(1.10, 1.09, 1.13) == pytest.approx(3.0)
    assert lv.risk_reward(1.10, 1.10, 1.13) == 0
    near, far, further = lv.Target(1.105, "a"), lv.Target(1.125, "b"), lv.Target(1.14, "c")
    assert lv.pick_targets([near, far, further], 1.10, 1.09, 2.0) == (far, further)
    assert lv.pick_targets([near], 1.10, 1.09, 2.0) == (None, None)


def test_stop_buffer_adds_the_spread():
    assert lv.stop_buffer(context(), 0.001, CFG) == pytest.approx(0.00023)
    assert lv.stop_buffer(context(spread={"spread_points": None}), 0.001, CFG) == pytest.approx(0.00015)


def test_targets_include_module_04_levels_only_on_the_far_side():
    levels = {"levels": [{"level_type": "previous_day_high", "side": "high", "price": 1.1030},
                         {"level_type": "previous_day_high", "side": "high", "price": 1.0900},
                         {"level_type": "session_high", "side": "high", "price": 1.1020}]}
    a = analysis(structure={"external": NS(bias="bullish", state="trending", last_swing_high=None)})
    found = lv.target_candidates(context(a, intelligence=intel(levels=levels)), a, "long", 1.10, CFG)
    assert [t.price for t in found] == [1.1030]


def test_net_risk_reward_subtracts_the_spread():
    c = candidate()
    assert net_risk_reward(c, 0.0001) < net_risk_reward(c, 0.0) == pytest.approx(4.0)


# ------------------------------------------------------------------------------------ sizing
def test_sizing_rounds_down_to_the_step():
    assert suggest_volume(0.5, 10_000, 0.0050, SPEC) == (0.1, None)          # 50 at risk, 500 per lot
    assert suggest_volume(0.5, 10_000, 0.0033, SPEC) == (0.15, None)         # 0.1515... is never rounded up


def test_sizing_clamps_to_the_limits():
    assert suggest_volume(0.5, 10_000_000, 0.0010, SPEC) == (50.0, None)
    lots, why = suggest_volume(0.5, 1000, 0.10, SPEC)
    assert lots == 0.01 and "minimum lot" in why


def test_sizing_respects_the_per_trade_limit():
    assert suggest_volume(2.0, 10_000, 0.0050, SPEC, 1.0)[0] == 0.2
    lots, why = suggest_volume(0.5, 1000, 0.50, SPEC, 1.0)
    assert lots is None and "RISK_MAX_PER_TRADE_PCT" in why


@pytest.mark.parametrize("equity,spec,stop,needle", [
    (None, SPEC, 0.001, "equity"), (0, SPEC, 0.001, "equity"), (1000, None, 0.001, "specification"),
    (1000, {**SPEC, "tick_value": None}, 0.001, "Tick"), (1000, {**SPEC, "volume_step": None}, 0.001, "Volume"), (1000, SPEC, 0, "Stop"),
])
def test_sizing_reports_missing_data_instead_of_guessing(equity, spec, stop, needle):
    lots, why = suggest_volume(0.5, equity, stop, spec)
    assert lots is None and needle in why


# ------------------------------------------------------------------------------------ gates
def test_data_quality_pass_stale_and_status():
    c = candidate()
    assert data_quality(context(), c, CFG, []).result == PASS
    stale = context(analysis(as_of=T - timedelta(hours=4)))
    assert data_quality(stale, c, CFG, []).result == FAIL
    closed = context(analysis(as_of=T - timedelta(hours=40)), intelligence=intel(sessions={"market_open": False}))
    assert "H1" not in " ".join(data_quality(closed, c, CFG, []).evidence["problems"])
    assert data_quality(context(analysis(status="insufficient_data")), c, CFG, []).result == FAIL


def test_data_quality_looks_only_at_the_lookback():
    c = candidate()
    assert data_quality(context(recent_quality={"D1": {"gaps": 0, "invalid": 0}}), c, CFG, []).result == PASS
    assert data_quality(context(recent_quality={"D1": {"gaps": 2, "invalid": 0}}), c, CFG, []).result == FAIL
    assert data_quality(context(recent_quality={"H1": {"gaps": 0, "invalid": 1}}), c, CFG, []).result == FAIL


def test_recent_quality_ignores_old_gaps_and_counts_new_ones():
    from aurum.decision.context import ContextBuilder
    series = NS(quality_issues=[{"issue_type": "missing_candles", "ts_utc": T - timedelta(days=300)},
                                {"issue_type": "missing_candles", "ts_utc": T - timedelta(days=3)},
                                {"issue_type": "market_closure_gap", "ts_utc": T - timedelta(days=2)}],
                excluded_rows=[{"ts_utc": T - timedelta(days=1), "reason": "invalid: x"}, {"ts_utc": T, "reason": "forming candle"}])
    builder = ContextBuilder(CFG, NS(load_series=lambda *a: series), None, None, None, None, None)
    a = analysis("D1", data_quality={"unexpected_gaps": 2, "invalid_candles": 1})
    assert builder._recent_quality("EURUSD", {"D1": a, "H1": analysis()}, T) == {"D1": {"gaps": 1, "invalid": 1}, "H1": {"gaps": 0, "invalid": 0}}


@pytest.mark.parametrize("state,result", [
    ("NORMAL", PASS), ("EVENT_APPROACHING", WARN), ("POST_EVENT_VOLATILITY", WARN), ("HIGH_EVENT_RISK", FAIL), ("RELEASE_WINDOW", FAIL),
])
def test_event_risk_states(state, result):
    assert event_risk(context(intelligence=intel(state=state)), candidate(), CFG, []).result == result


def test_unknown_event_state_is_never_normal():
    for ctx in (context(intelligence=intel(state="UNKNOWN")), context(intelligence=None)):
        g = event_risk(ctx, candidate(), CFG, [])
        assert g.result == WARN and g.confidence_cap == CFG.gates["unknown_event_confidence_cap"]


def test_market_gate():
    c = candidate()
    assert market_open(context(), c, CFG, []).result == PASS
    assert market_open(context(intelligence=intel(sessions={"market_open": False})), c, CFG, []).result == FAIL
    assert market_open(context(intelligence=None), c, CFG, []).result == WARN
    friday = T + timedelta(days=2, hours=7, minutes=30)     # 20:30 on Friday
    assert "weekly close" in market_open(context(as_of=friday), c, CFG, []).evidence["reason"]


def test_session_filter_blocks_a_template():
    cfg = build_config({"gates": {"session_filters": {"order_block_continuation": {"EURUSD": ["london"]}}}})
    assert market_open(context(), candidate(), cfg, []).result == FAIL


def test_spread_and_volatility_gate():
    c = candidate()
    assert spread_volatility(context(), c, CFG, []).result == PASS
    assert spread_volatility(context(intelligence=intel(regime="VERY_HIGH")), c, CFG, []).result == FAIL
    assert spread_volatility(context(intelligence=intel(regime="UNKNOWN")), c, CFG, []).result == WARN
    assert spread_volatility(context(spread={"spread_points": 40.0, "median_spread_points": 8.0}), c, CFG, []).result == FAIL
    assert spread_volatility(context(spread={"spread_points": None}), c, CFG, []).result == WARN
    assert spread_volatility(context(), candidate(stop_loss=1.09999), CFG, []).result == FAIL


def test_risk_reward_gate_is_net_of_spread():
    assert risk_reward_gate(context(), candidate(), CFG, []).result == PASS
    thin = candidate(take_profit=1.1021)      # 2.1R gross, under 2R after a large spread
    assert risk_reward_gate(context(spread={"spread_points": 15.0}), thin, CFG, []).result == FAIL
    assert risk_reward_gate(context(spread={"spread_points": None}), candidate(), CFG, []).result == WARN


def test_structure_conflict_fails_only_when_the_template_requires_alignment():
    ctx = context()
    ctx.analyses["H4"] = analysis("H4", bias="bearish")
    assert structure_conflict(ctx, candidate(), CFG, []).result == FAIL
    assert structure_conflict(ctx, candidate(template="liquidity_sweep_reversal"), CFG, []).result == WARN
    assert structure_conflict(context(), candidate(), CFG, []).result == PASS


def test_account_gate():
    c = candidate()
    limits = {"max_open_positions": 2, "max_daily_loss_pct": 3.0, "max_total_exposure_pct": 10.0, "max_correlated_exposure_pct": 5.0}
    assert account_risk(context(account=ACCOUNT_EMPTY), c, CFG, []).result == WARN
    assert account_risk(context(account=account(limits=limits)), c, CFG, []).result == PASS
    assert account_risk(context(account=account(limits=limits, captured_at=T - timedelta(hours=3))), c, CFG, []).result == WARN
    assert account_risk(context(account=account(limits=limits, daily_drawdown_pct=3.0)), c, CFG, []).result == FAIL
    opened = [{"symbol": "XAUUSD", "side": "buy"}] * 2
    assert account_risk(context(account=account(limits=limits, open_positions=opened)), c, CFG, []).result == FAIL
    assert account_risk(context(account=account(limits=limits, exposure_by_instrument={"XAUUSD": 11.0})), c, CFG, []).result == FAIL
    correlated = account(limits=limits, exposure_by_instrument={"EURUSD": 3.0, "GBPUSD": 3.0})
    assert "Correlated" in account_risk(context(account=correlated), c, CFG, []).evidence["breaches"][0]


def test_duplication_gate():
    c = candidate()
    assert duplication(context(), c, CFG, []).result == PASS
    assert duplication(context(account=account(open_positions=[{"symbol": "EURUSD", "side": "buy"}])), c, CFG, []).result == FAIL
    assert duplication(context(account=account(open_positions=[{"symbol": "EURUSD", "side": "sell"}])), c, CFG, []).result == PASS
    active = [{"symbol": "EURUSD", "direction": "long", "key": "other"}]
    assert duplication(context(), c, CFG, active).result == FAIL
    assert duplication(context(), c, CFG, [{**active[0], "key": c.key}]).result == PASS


def test_cot_only_ever_warns():
    c = candidate()
    assert cot(context(), c, CFG, []).result == PASS
    assert cot(context(cot={"available": True, "lean": "bearish"}), c, CFG, []).result == WARN
    assert cot(context(cot={"available": False, "reason": "none"}), c, CFG, []).result == WARN


def test_run_gates_checks_all_nine_and_reports_the_first_failure():
    results = run_gates(context(intelligence=intel(state="RELEASE_WINDOW", regime="VERY_HIGH")), candidate(), CFG)
    assert len(results) == 9 and first_failure(results) == "event_risk"
    assert first_failure(run_gates(context(), candidate(), CFG)) is None
    assert confidence_cap(run_gates(context(intelligence=intel(state="UNKNOWN")), candidate(), CFG)) == 60.0


# ------------------------------------------------------------------------------------ score
def test_score_is_the_sum_of_its_parts():
    ctx = ob_setup()
    c = generate(ctx, CFG)[0][0]
    s = score(ctx, c, CFG)
    assert s.confidence == pytest.approx(sum(p.points for p in s.components), abs=0.05)
    assert all(p.sentence for p in s.components) and all(p.points <= p.max_points for p in s.components if p.max_points)


def test_score_deducts_for_missing_inputs_and_ignores_the_account_mirror():
    ctx = ob_setup()
    c = generate(ctx, CFG)[0][0]
    full = score(ctx, c, CFG).confidence
    gone = ob_setup(cot={"available": False, "reason": "none"})
    assert score(gone, c, CFG).confidence < full
    assert score(ob_setup(account=ACCOUNT_EMPTY), c, CFG).confidence == full


def test_cot_is_scored_by_direction():
    ctx = ob_setup()
    c = generate(ctx, CFG)[0][0]
    points = lambda lean: next(p.points for p in score(ob_setup(cot={"available": True, "lean": lean}), c, CFG).components if p.name == "cot_agreement")
    assert points("bullish") == 10 and points("neutral") == 5 and points("bearish") == 0


def test_learning_patterns_move_the_score_but_stay_inside_their_weight():
    ctx = ob_setup()
    c = generate(ctx, CFG)[0][0]
    won = {"pattern": [["symbol", "EURUSD"]], "passed": True}
    points = lambda ps: next(p.points for p in score(replace(ctx, patterns=ps), c, CFG).components if p.name == "learning")
    assert points([]) == 5 and points([won]) == 10 and points([won] * 5) == 10
    assert points([{**won, "passed": False}]) == 0


def test_grades():
    assert [grade_of(x, CFG) for x in (80, 60, 45, 44.9, 10)] == ["A", "B", "C", "D", "F"]


def test_calibration_waits_for_enough_outcomes():
    rows = [{"template": "t", "confidence": 62.0, "r_multiple": 1.0 if i % 2 else -1.0} for i in range(4)]
    assert calibration(rows, 5)["t"]["status"] == "not_enough_outcomes"
    assert calibration(rows, 4)["t"]["buckets"]["60-80"]["hit_rate"] == 0.5


# ------------------------------------------------------------------------------------ regime and bias
def regime_of(entry=None, **kw):
    return assess_regime(context(entry, **kw), CFG)


def test_trend_regimes():
    assert regime_of()["regime"] == "BULLISH_TREND"
    a = analysis(structure={"external": NS(bias="bearish", state="trending")})
    assert regime_of(a)["regime"] == "BEARISH_TREND"


def vol(state):
    return {"H1": {"expansion_state": state}}


def test_range_regimes():
    ranging = {"external": NS(bias="ranging", state="ranging")}
    assert regime_of(analysis(structure=ranging))["regime"] == "RANGE"
    assert regime_of(analysis(structure=ranging), intelligence=intel(regime="LOW"))["regime"] == "CONSOLIDATION"
    assert regime_of(analysis(structure=ranging, structure_events=[bos(index=98)]))["regime"] == "BREAKOUT"


def test_volatility_regimes():
    trending = {"external": NS(bias="ranging", state="ranging")}
    high = intel(regime="VERY_HIGH", volatility={"regime": "VERY_HIGH", "timeframes": vol("expansion")})
    assert regime_of(analysis(structure=trending), intelligence=high)["regime"] == "VOLATILITY_EXPANSION"
    low = intel(volatility={"regime": "VERY_LOW", "timeframes": vol("contraction")})
    assert regime_of(analysis(structure=trending), intelligence=low)["regime"] == "VOLATILITY_CONTRACTION"


def test_reversal_and_unclear_regimes():
    recent = NS(status="confirmed", since=T - timedelta(hours=3))
    old = NS(status="confirmed", since=T - timedelta(hours=30))
    assert regime_of(analysis(structure={"external": NS(bias="bearish", state="transitioning", choch=recent)}))["regime"] == "POTENTIAL_REVERSAL"
    aged = regime_of(analysis(structure={"external": NS(bias="bearish", state="transitioning", choch=old)}))
    assert aged["regime"] == "BEARISH_TREND" and aged["quality"] == "low" and "swing sequence" in aged["conflicting"][0]
    assert regime_of(analysis(structure={"external": NS(bias="bearish", state="transitioning")}))["regime"] == "BEARISH_TREND"
    assert regime_of(analysis(structure={"external": NS(bias="ranging", state="insufficient_data")}))["regime"] == "UNCLEAR"
    assert regime_of(analysis(status="insufficient_data"))["quality"] == "insufficient"


def test_regime_reports_conflicting_evidence():
    ctx = context()
    ctx.analyses["H4"] = analysis("H4", bias="bearish")
    r = assess_regime(ctx, CFG)
    assert r["regime"] == "BULLISH_TREND" and r["conflicting"] and r["quality"] == "low"


def full_bias(**biases):
    ctx = context()
    for tf, value in biases.items():
        ctx.analyses[tf] = analysis(tf, bias=value)
    return assess_bias(ctx, CFG)


def test_bias_uses_weights_and_does_not_need_unanimity():
    b = full_bias(D1="bullish", H4="bullish", H1="bullish", M15="bearish", M5="bearish")
    assert b["overall"] == "bullish" and b["disagreeing"] == ["M15", "M5"] and b["agreement"] == 0.8
    assert full_bias(D1="bullish", H4="bullish", H1="bearish", M15="bearish", M5="bearish")["agreement"] == 0.6      # exactly enough
    assert full_bias(D1="bullish", H4="bearish", H1="bearish", M15="bullish", M5="bullish")["overall"] == "mixed"    # 8 against 7


def test_a_clear_cot_stance_counts_when_lean_is_empty():
    """Real Module 02 shape: an extreme stance arrives with lean None. It once scored as 'not directional'."""
    ctx = ob_setup(cot={"available": True, "stance": "bearish", "lean": None, "confidence": "high"})
    candidate = generate(ctx, CFG, ["order_block_continuation"])[0][0]
    part = next(p for p in score(ctx, candidate, CFG).components if p.name == "cot_agreement")
    assert part.points == 0.0 and "against" in part.sentence
    assert next(g for g in run_gates(ctx, candidate, CFG) if g.gate == "cot").result == WARN


def test_a_neutral_stance_uses_the_lean():
    ctx = ob_setup(cot={"available": True, "stance": "neutral", "lean": "bullish", "confidence": "high"})
    candidate = generate(ctx, CFG, ["order_block_continuation"])[0][0]
    part = next(p for p in score(ctx, candidate, CFG).components if p.name == "cot_agreement")
    assert "with the trade" in part.sentence


def test_bias_is_unknown_without_enough_timeframes():
    b = assess_bias(context(analysis(status="insufficient_data")), build_config({"bias": {"timeframes": ["H1", "M15"], "weights": {"H1": 3.0, "M15": 2.0}}}))
    assert b["overall"] == "unknown"


def test_a_ranging_timeframe_counts_as_known_but_casts_no_vote():
    b = full_bias(D1="ranging", H4="bullish", H1="bullish", M15="bullish", M5="bullish")
    assert b["timeframes"]["D1"]["bias"] == "ranging" and b["agreement"] == pytest.approx(10 / 15, abs=0.001) and b["overall"] == "bullish"


# ------------------------------------------------------------------------------------ final decision
def decision_for(ctx, cfg=CFG, ai=None):
    assessed = []
    for c, s in candidates_at(ctx, cfg):
        assessed.append(Assessed(c, s, run_gates(ctx, c, cfg), ai))
    regime, bias = assess_regime(ctx, cfg), assess_bias(ctx, cfg)
    conflicts = find_conflicts(ctx, regime, bias, assessed)
    return decide(ctx, cfg, regime, bias, assessed, conflicts), conflicts


def aligned(**kw):
    ctx = ob_setup(**kw)
    ctx.analyses.update({"M15": analysis("M15"), "M5": analysis("M5")})
    return ctx


EASY = build_config({"score": {"grades": {"A": 50.0, "B": 40.0, "C": 30.0, "D": 20.0}}})


def test_strong_bullish_alignment_is_a_strong_buy():
    assert decision_for(aligned(), EASY)[0]["decision"] == "STRONG_BUY_SETUP"


def test_a_warning_keeps_it_from_being_strong():
    assert decision_for(aligned(intelligence=intel(state="EVENT_APPROACHING")), EASY)[0]["decision"] == "BUY_SETUP"


def test_strong_bearish_alignment_is_a_strong_sell():
    bear = {"external": NS(bias="bearish", state="trending", last_swing_low=None)}
    a = analysis(order_blocks=[ob("bearish", 1.1016, 1.1012)], structure_events=[bos("bearish")], liquidity_pools=[pool(1.0950, "sell_side")],
                 premium_discount=NS(equilibrium=1.0990), structure=bear)
    ctx = context(a, consensus="bearish", cot={"available": True, "lean": "bearish"})
    for tf in ("D1", "H4", "M15", "M5"):
        ctx.analyses[tf] = analysis(tf, bias="bearish")
    assert decision_for(ctx, EASY)[0]["decision"] == "STRONG_SELL_SETUP"


def test_a_high_score_alone_is_not_enough():
    ctx = aligned()
    assert decision_for(ctx, build_config({"decision": {"min_grades": ["A"]}, "score": {"grades": {"A": 99.0, "B": 98.0, "C": 97.0, "D": 96.0}}}))[0]["decision"] == "WAIT"


def test_conflicting_timeframes_wait():
    ctx = aligned()
    ctx.analyses["D1"] = analysis("D1", bias="ranging")      # a bearish D1 would fail structure_conflict first
    ctx.analyses["M15"] = analysis("M15", bias="bearish")
    ctx.analyses["M5"] = analysis("M5", bias="bearish")
    ctx.analyses["H4"] = analysis("H4", bias="bullish")
    result, conflicts = decision_for(ctx, EASY)
    assert result["decision"] == "WAIT" and any(c["kind"] == "timeframes" for c in conflicts)


def test_missing_data_is_insufficient():
    ctx = context(intelligence=None)
    ctx.analyses["H1"] = analysis(status="insufficient_data")
    assert decision_for(ctx)[0]["decision"] == "INSUFFICIENT_DATA"


def test_stale_data_is_insufficient():
    ctx = aligned()
    ctx.analyses["H1"].as_of = T - timedelta(hours=5)
    assert decision_for(ctx, EASY)[0]["decision"] == "INSUFFICIENT_DATA"


def test_invalidated_order_block_gives_no_setup():
    a = analysis(order_blocks=[ob(state="invalidated")], structure_events=[bos()], premium_discount=NS(equilibrium=1.105), liquidity_pools=[pool()])
    found, skipped = generate(context(a), CFG, ["order_block_continuation"])
    assert not found and any(s.template == "order_block_continuation" for s in skipped)


def test_major_news_risk_is_no_trade():
    result, _ = decision_for(aligned(intelligence=intel(state="RELEASE_WINDOW")), EASY)
    assert result["decision"] == "NO_TRADE" and "event_risk" in result["reasons"][0]


def test_extreme_volatility_is_no_trade():
    assert decision_for(aligned(intelligence=intel(regime="VERY_HIGH")), EASY)[0]["decision"] == "NO_TRADE"


def test_poor_risk_reward_gives_no_candidate():
    a = analysis(order_blocks=[ob()], structure_events=[bos()], premium_discount=NS(equilibrium=1.105), liquidity_pools=[pool(1.1012)],
                 structure={"external": NS(bias="bullish", state="trending", last_swing_high=None)})
    assert decision_for(context(a))[0]["decision"] == "WAIT"


def test_no_valid_entry_waits():
    assert decision_for(context())[0]["decision"] == "WAIT"


def test_conflicting_cot_is_reported_and_blocks_strong():
    result, conflicts = decision_for(aligned(cot={"available": True, "lean": "bearish"}), EASY)
    assert any(c["kind"] == "cot" for c in conflicts) and result["decision"] == "BUY_SETUP"


def test_ai_rejection_is_no_trade():
    assert decision_for(aligned(), EASY, "reject")[0]["decision"] == "NO_TRADE"


def test_explanation_needs_no_provider_and_lists_every_side():
    ctx = aligned()
    regime, bias = assess_regime(ctx, CFG), assess_bias(ctx, CFG)
    text = explain(ctx, regime, bias, [], [], {"decision": "WAIT", "reasons": ["x"]})
    assert text["source"] == "deterministic" and text["bullish_evidence"] and text["decision"].startswith("WAIT")


def test_evidence_summary_splits_positive_negative_and_missing():
    ctx = aligned(account=ACCOUNT_EMPTY)
    c, s = candidates_at(ctx, CFG)[0]
    e = evidence_summary(Assessed(c, s, run_gates(ctx, c, CFG)), [{"kind": "x", "detail": "y"}])
    assert e["positive"] and e["conflicting"] == ["y"] and any("account" in m for m in e["missing"])


def test_positioning_summary_flags_commercial_against_speculator_extremes():
    cat = lambda name, p: {"category": name, "label": name, "historical_percentile_12m": p, "historical_percentile_24m": p, "historical_percentile_48m": None,
                           "net_position": 1, "weekly_net_change": 1, "cot_index": 50, "positioning_strength": "x"}
    report = {"families": [{"available": True, "report_type": "legacy", "categories": [cat("commercial", 90.0), cat("non_commercial", 10.0), cat("non_reportable", 50.0)]}]}
    s = positioning_summary(report)
    windows = s["families"][0]["windows"]
    assert windows["12m"]["commercial_long_vs_spec_short"] and windows["48m"] is None and "Not an intraday" in s["note"]
    assert positioning_summary({"families": []})["available"] is False


# ------------------------------------------------------------------------------------ status machine
def test_executed_is_unreachable():
    assert all("executed" not in targets for targets in TRANSITIONS.values())
    assert TRANSITIONS["executed"] == frozenset()
    for status in STATUSES:
        with pytest.raises(IllegalTransition):
            check(status, "executed")


@pytest.mark.parametrize("old,new", [("approved", "rejected"), ("approved", "new"), ("expired", "new"), ("expired", "approved"), ("rejected", "approved"),
                                     ("under_review", "new")])
def test_illegal_transitions(old, new):
    with pytest.raises(IllegalTransition):
        check(old, new)


def test_active_statuses():
    assert set(ACTIVE) == {"new", "under_review"}
