"""Which candidates become signals: one per idea, and no weak counter-trend reversals (17 Nov 2026 review: one EURUSD
idea counted three times, five grade-D reversal longs against a bearish D1 and H4, 0 TP / 12 SL)."""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace as NS

import pytest
from decision_helpers import T

from aurum.decision.candidates.setups import Candidate
from aurum.decision.config import build_config
from aurum.decision.selection import covered_by, select

CFG = build_config()


def cand(template="order_block_continuation", direction="long", low=1.0995, high=1.1005, tf="H1", zone="z1"):
    stop = low - 0.001 if direction == "long" else high + 0.001
    tp = high + 0.004 if direction == "long" else low - 0.004
    return Candidate("EURUSD", template, tf, direction, zone, T, low, high, stop, tp, None, 2.5, T + timedelta(hours=8),
                     "x", "TRENDING")


def ctx(d1="bearish", h4="bearish", atr=0.0010):
    htf = lambda b: NS(structure={"external": NS(bias=b)})  # noqa: E731
    return NS(analyses={"D1": htf(d1), "H4": htf(h4), "H1": NS(atr=atr, structure={}), "M15": NS(atr=atr / 2, structure={})})


def sc(grade, conf=50.0):
    return NS(grade=grade, confidence=conf)


# ---- one signal per idea
def test_same_zone_from_several_templates_is_one_signal():
    a, b, c = cand(), cand("fair_value_gap_retracement", low=1.0997, high=1.1003), cand("premium_discount_ote", tf="M15")
    kept, dropped = select(ctx("bullish", "bullish"), CFG, [(a, sc("B", 70)), (b, sc("C", 55)), (c, sc("C", 50))])
    assert [k.template for k, _ in kept] == ["order_block_continuation"]              # the strongest (first) stays
    assert [d.template for d in dropped] == ["fair_value_gap_retracement", "premium_discount_ote"]
    assert all("Same idea as order_block_continuation H1" in d.reason for d in dropped)


def test_zones_closer_than_half_an_atr_count_as_the_same_idea():
    near = cand(low=1.1009, high=1.1015)                       # 4 pips above, ATR 10 pips: within 0.5 ATR
    far = cand(low=1.1030, high=1.1040, zone="z2")             # 25 pips above: a different idea
    kept, _ = select(ctx("bullish", "bullish"), CFG, [(cand(), sc("B")), (near, sc("C")), (far, sc("C"))])
    assert [(k.entry_low, k.zone_id) for k, _ in kept] == [(1.0995, "z1"), (1.1030, "z2")]


def test_opposite_directions_are_different_ideas():
    kept, dropped = select(ctx(None, None), CFG, [(cand(), sc("B")), (cand(direction="short"), sc("B"))])
    assert len(kept) == 2 and dropped == []


def test_dedupe_can_be_switched_off():
    off = build_config({"selection": {"dedupe": False}})
    kept, _ = select(ctx("bullish", "bullish"), off, [(cand(), sc("B")), (cand("fair_value_gap_retracement"), sc("B"))])
    assert len(kept) == 2


# ---- counter-trend reversals
@pytest.mark.parametrize("template", ["choch_reversal", "liquidity_sweep_reversal", "range_edge_rejection"])
def test_a_weak_reversal_against_both_higher_timeframes_is_dropped(template):
    kept, dropped = select(ctx("bearish", "bearish"), CFG, [(cand(template), sc("D", 37))])
    assert kept == [] and "Counter-trend long against D1, H4 at grade D; needs A/B" in dropped[0].reason


@pytest.mark.parametrize("grade", ["A", "B"])
def test_a_strong_reversal_against_the_trend_is_kept(grade):
    kept, _ = select(ctx("bearish", "bearish"), CFG, [(cand("choch_reversal"), sc(grade))])
    assert len(kept) == 1


def test_a_reversal_against_only_one_higher_timeframe_is_kept():
    kept, _ = select(ctx("bearish", "bullish"), CFG, [(cand("choch_reversal"), sc("D"))])
    assert len(kept) == 1


def test_continuation_templates_are_not_affected():
    kept, _ = select(ctx("bearish", "bearish"), CFG, [(cand("order_block_continuation"), sc("D"))])
    assert len(kept) == 1          # the structure-conflict gate handles continuation templates


# ---- across runs
def test_a_live_signal_from_an_earlier_run_covers_the_same_zone():
    live = [{"symbol": "EURUSD", "direction": "long", "idempotent_key": "other", "entry_low": 1.0996, "entry_high": 1.1004,
             "candidate_uid": "u1"}]
    c = cand("fair_value_gap_retracement")
    assert covered_by(c, live, 0.0005)["candidate_uid"] == "u1"
    assert covered_by(cand(direction="short"), live, 0.0005) is None                    # other direction
    same_key = [{**live[0], "idempotent_key": c.key}]
    assert covered_by(c, same_key, 0.0005) is None                                      # its own row: an update, not a copy
