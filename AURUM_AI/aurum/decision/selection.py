"""Which scored candidates become signals. Shared by live runs and the backtest, so both apply the same rules.

1. **Counter-trend reversals need a strong grade.** A reversal template (CHoCH, liquidity sweep, range edge) trading
   against every higher timeframe is published only at the grades in ``selection.counter_trend_min_grades`` (A, B).
2. **One signal per idea** (``one_per_idea``, applied after the gates). Several templates often find the same zone (an
   order block, the FVG inside it and the OTE band around it). Candidates in the same direction whose entry zones overlap
   (within ``selection.overlap_atr`` ATR) are one idea: the strongest that passed the gates is kept, the others are
   dropped with the reason. In the backtest the duplication gate already does this (a later same-direction candidate is
   blocked while a simulated trade runs), so the replay needs no extra step.

Dropped candidates are not stored, so they never appear as signals or in the results.
"""
from __future__ import annotations

from typing import Any, Iterable

from aurum.decision.candidates.setups import Candidate, Skip
from aurum.decision.config import DecisionConfig
from aurum.decision.context import DecisionContext

BIAS = {"long": "bullish", "short": "bearish"}


def opposing_timeframes(ctx: DecisionContext, cfg: DecisionConfig, direction: str) -> tuple[list[str], list[str]]:
    """Higher timeframes whose external structure points the other way, and those whose bias is unknown."""
    opposed, unknown = [], []
    for tf in cfg.htf_timeframes:
        external = (ctx.analyses[tf].structure if tf in ctx.analyses else {}).get("external")
        bias = getattr(external, "bias", None)
        if bias is None:
            unknown.append(tf)
        elif bias in ("bullish", "bearish") and bias != BIAS[direction]:
            opposed.append(tf)
    return opposed, unknown


def _atr(ctx: DecisionContext, c: Candidate) -> float:
    a = ctx.analyses.get(c.timeframe)
    return float(getattr(a, "atr", 0.0) or 0.0)


def overlaps(low_a: float, high_a: float, low_b: float, high_b: float, tolerance: float) -> bool:
    return low_a - tolerance <= high_b and low_b - tolerance <= high_a


def select(ctx: DecisionContext, cfg: DecisionConfig, scored: Iterable[tuple[Candidate, Any]]) -> tuple[list[tuple[Candidate, Any]], list[Skip]]:
    """``scored`` strongest first. Returns the candidates kept (same order) and the ones dropped, with reasons."""
    sel = cfg.selection
    kept: list[tuple[Candidate, Any]] = []
    dropped: list[Skip] = []
    for c, s in scored:
        if c.template in sel["counter_trend_templates"]:
            opposed, _ = opposing_timeframes(ctx, cfg, c.direction)
            if opposed and len(opposed) == len(cfg.htf_timeframes) and s.grade not in sel["counter_trend_min_grades"]:
                dropped.append(Skip(c.template, c.timeframe, f"Counter-trend {c.direction} against {', '.join(opposed)} at grade "
                                                             f"{s.grade}; needs {'/'.join(sel['counter_trend_min_grades'])}."))
                continue
        kept.append((c, s))
    return kept, dropped


def one_per_idea(ctx: DecisionContext, cfg: DecisionConfig, items: Iterable[tuple[Candidate, Any, bool]]) -> tuple[list[tuple[Candidate, Any, bool]], list[Skip]]:
    """One candidate per idea, chosen AFTER the gates. ``items`` are (candidate, score, passed_gates), strongest first.

    Candidates that passed the gates are preferred to ones that did not, then the strongest. Choosing before the gates
    (the 22 Nov 2026 version) dropped the copies of an idea and then lost the idea whenever the chosen copy failed a gate:
    the benchmark of 10-20 Nov 2026 fell from +57.9R to +20.0R. Overlap: same direction, entry zones within
    ``selection.overlap_atr`` ATR."""
    ordered = sorted(items, key=lambda x: not x[2])           # stable: passing first, each group strongest first
    kept: list[tuple[Candidate, Any, bool]] = []
    dropped: list[Skip] = []
    for c, s, ok in ordered:
        if cfg.selection["dedupe"]:
            tol = cfg.selection["overlap_atr"] * _atr(ctx, c)
            same = next((k for k, _, _ in kept if k.direction == c.direction
                         and overlaps(k.entry_low, k.entry_high, c.entry_low, c.entry_high, tol)), None)
            if same is not None:
                dropped.append(Skip(c.template, c.timeframe, f"Same idea as {same.template} {same.timeframe} "
                                                             f"(overlapping entry zone); one signal per idea."))
                continue
        kept.append((c, s, ok))
    order = {id(c): i for i, (c, _, _) in enumerate(items)}
    return sorted(kept, key=lambda x: order[id(x[0])]), dropped


def covered_by(c: Candidate, active: Iterable[dict[str, Any]], tolerance: float) -> dict[str, Any] | None:
    """A live signal from an earlier run for the same idea (same symbol and direction, overlapping zone, another key)."""
    return next((a for a in active if a["symbol"] == c.symbol and a["direction"] == c.direction
                 and a["idempotent_key"] != c.key
                 and overlaps(a["entry_low"], a["entry_high"], c.entry_low, c.entry_high, tolerance)), None)
