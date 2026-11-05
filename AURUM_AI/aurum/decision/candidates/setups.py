"""The four setup templates. Each is a small rule set over Module 03/04 findings and cites what it used.

``generate`` also returns why each template produced nothing, so "no setup" is an explanation rather than a blank.
Only findings confirmed by ``as_of`` are used: Module 03 is point-in-time, and every time filter below is a second guard.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable

from aurum.config import TIMEFRAME_SECONDS
from aurum.decision.config import DecisionConfig
from aurum.decision.context import DecisionContext

from . import levels as lv

BIAS = {"long": "bullish", "short": "bearish"}
DIRECTION = {"bullish": "long", "bearish": "short"}
ACTIVE_OB = ("fresh", "partially_mitigated")
ACTIVE_GAP = ("unfilled", "partially_filled")
SIGNIFICANT_POOLS = ("equal_highs", "equal_lows", "previous_day_high", "previous_day_low")


@dataclass
class Candidate:
    symbol: str
    template: str
    timeframe: str
    direction: str
    zone_id: str
    as_of: datetime
    entry_low: float
    entry_high: float
    stop_loss: float
    take_profit: float
    take_profit2: float | None
    risk_reward: float
    expires_at: datetime
    invalidation: str
    market_condition: str
    sources: list[dict[str, Any]] = field(default_factory=list)
    fixed_key: str | None = None      # set when rebuilt from a stored row, which keeps its key

    @property
    def entry_mid(self) -> float:
        return (self.entry_low + self.entry_high) / 2

    @property
    def key(self) -> str:
        """Same setup on the same zone and direction: an update of the existing candidate, never a new one."""
        if self.fixed_key:
            return self.fixed_key
        raw = "|".join((self.symbol, self.template, self.timeframe, self.direction, self.zone_id))
        return hashlib.sha256(raw.encode()).hexdigest()[:32]


@dataclass
class Skip:
    template: str
    timeframe: str
    reason: str


def _source(module: str, finding_id: str | None, kind: str, source_time: Any = None, confirmed: Any = None) -> dict[str, Any]:
    return {"module": module, "finding_id": finding_id, "kind": kind, "source_time": source_time, "confirmation_time": confirmed}


def _market_condition(ctx: DecisionContext) -> str:
    return ctx.tags[0] if ctx.tags else "UNKNOWN"


def _bias_direction(ctx: DecisionContext) -> str | None:
    return DIRECTION.get(ctx.consensus or "")


def _recent_displacement_bos(a: Any, bias: str, max_age: int) -> Any | None:
    last = a.candles_used - 1
    hits = [e for e in a.structure_events if e.event_type == "BOS" and e.direction == bias and e.confirmed and e.displacement
            and last - e.break_index <= max_age]
    return hits[-1] if hits else None


def _build(ctx: DecisionContext, cfg: DecisionConfig, a: Any, template: str, direction: str, zone_id: str, low: float,
           high: float, stop_level: float, expiry_candles: int, sources: list[dict[str, Any]],
           targets: list[lv.Target] | None = None) -> Candidate | str:
    """A candidate, or the reason there is none."""
    buffer = lv.stop_buffer(ctx, a.atr, cfg)
    stop = lv.stop_beyond(stop_level, direction, buffer)
    entry = (low + high) / 2
    if (direction == "long" and stop >= entry) or (direction == "short" and stop <= entry):
        return "Stop is on the wrong side of the entry."
    if abs(entry - stop) > cfg.levels["max_stop_atr"] * a.atr:
        return f"Stop is wider than {cfg.levels['max_stop_atr']} ATR."
    tp, tp2 = lv.pick_targets(targets if targets is not None else lv.target_candidates(ctx, a, direction, entry, cfg),
                              entry, stop, cfg.gates["min_rr"])
    if tp is None:
        return f"No target gives a risk-reward of at least {cfg.gates['min_rr']}."
    expires = ctx.as_of + timedelta(seconds=expiry_candles * TIMEFRAME_SECONDS[a.timeframe])
    word = "below" if direction == "long" else "above"
    return Candidate(ctx.symbol, template, a.timeframe, direction, zone_id, ctx.as_of, low, high, stop, tp.price,
                     tp2.price if tp2 else None, round(lv.risk_reward(entry, stop, tp.price), 2), expires,
                     f"A {a.timeframe} close {word} {stop:.5f}, or no fill by {expires:%Y-%m-%d %H:%M} UTC.",
                     _market_condition(ctx), [*sources, _source("module_05", tp.finding_id, f"target: {tp.source}")])


def order_block_continuation(ctx: DecisionContext, a: Any, cfg: DecisionConfig) -> Candidate | str:
    t = cfg.template("order_block_continuation")
    direction = _bias_direction(ctx)
    if direction is None:
        return f"Higher-timeframe consensus is {ctx.consensus}, not directional."
    bias = BIAS[direction]
    bos = _recent_displacement_bos(a, bias, t["max_break_age_candles"])
    if bos is None:
        return f"No recent confirmed {bias} BOS with displacement."
    pd_zone = a.premium_discount
    if pd_zone is None:
        return "No premium/discount range."
    price, atr = a.current_price, a.atr
    near = []
    for ob in a.order_blocks:
        if ob.direction != bias or ob.state not in ACTIVE_OB or ob.detected_at > ctx.as_of:
            continue
        mid = (ob.current_top + ob.current_bottom) / 2
        in_value = mid < pd_zone.equilibrium if direction == "long" else mid > pd_zone.equilibrium
        distance = (price - ob.current_top) if direction == "long" else (ob.current_bottom - price)
        retracing = -abs(ob.current_top - ob.current_bottom) <= distance <= atr
        if in_value and retracing:
            near.append((abs(distance), ob))
    if not near:
        return f"No active {bias} order block in {'discount' if direction == 'long' else 'premium'} near price."
    ob = min(near, key=lambda x: x[0])[1]
    distal = ob.initial_bottom if direction == "long" else ob.initial_top
    return _build(ctx, cfg, a, "order_block_continuation", direction, ob.id, ob.current_bottom, ob.current_top, distal,
                  t["expiry_candles"], [_source("module_03", ob.id, "order_block", ob.source_time, ob.detected_at),
                                        _source("module_03", None, "BOS with displacement", bos.break_candle_time, bos.confirmed_at)])


def fair_value_gap_retracement(ctx: DecisionContext, a: Any, cfg: DecisionConfig) -> Candidate | str:
    t = cfg.template("fair_value_gap_retracement")
    direction = _bias_direction(ctx)
    if direction is None:
        return f"Higher-timeframe consensus is {ctx.consensus}, not directional."
    bias = BIAS[direction]
    bos = _recent_displacement_bos(a, bias, cfg.template("order_block_continuation")["max_break_age_candles"])
    if bos is None:
        return f"No recent confirmed {bias} BOS with displacement."
    near = []
    for gap in a.fair_value_gaps:
        if gap.direction != bias or gap.state not in ACTIVE_GAP or gap.detected_at > ctx.as_of:
            continue
        distance = (a.current_price - gap.current_upper) if direction == "long" else (gap.current_lower - a.current_price)
        if -(gap.current_upper - gap.current_lower) <= distance <= t["max_distance_atr"] * a.atr:
            near.append((abs(distance), gap))
    if not near:
        return f"No unfilled {bias} FVG near price."
    gap = min(near, key=lambda x: x[0])[1]
    distal = gap.current_lower if direction == "long" else gap.current_upper
    return _build(ctx, cfg, a, "fair_value_gap_retracement", direction, gap.id, gap.current_lower, gap.current_upper, distal, t["expiry_candles"],
                  [_source("module_03", gap.id, "fair_value_gap", gap.candle_times[0], gap.detected_at),
                   _source("module_03", None, "BOS with displacement", bos.break_candle_time, bos.confirmed_at)])


def premium_discount_ote(ctx: DecisionContext, a: Any, cfg: DecisionConfig) -> Candidate | str:
    t = cfg.template("premium_discount_ote")
    direction = _bias_direction(ctx)
    if direction is None:
        return f"Higher-timeframe consensus is {ctx.consensus}, not directional."
    bias = BIAS[direction]
    bos = _recent_displacement_bos(a, bias, cfg.template("order_block_continuation")["max_break_age_candles"])
    if bos is None:
        return f"No recent confirmed {bias} BOS with displacement."
    ote, leg = a.ote, a.premium_discount
    if ote is None or leg is None or ote.direction != bias or leg.leg_direction != bias:
        return f"No {bias} OTE band on the current leg."
    low, high, sources = ote.lower, ote.upper, [_source("module_03", None, "OTE band")]
    overlaps = [(max(g.current_lower, ote.lower), min(g.current_upper, ote.upper), g) for g in a.fair_value_gaps
                if g.direction == bias and g.state in ACTIVE_GAP and g.detected_at <= ctx.as_of]
    overlaps = [o for o in overlaps if o[0] < o[1]]
    if overlaps:
        low, high, gap = max(overlaps, key=lambda o: o[1] - o[0])
        sources.append(_source("module_03", gap.id, "fair_value_gap", gap.candle_times[0], gap.detected_at))
    if not (low - a.atr <= a.current_price <= high + a.atr):
        return "Price is not near the OTE band."
    anchor = leg.range_low if direction == "long" else leg.range_high
    return _build(ctx, cfg, a, "premium_discount_ote", direction, f"ote:{leg.range_low:.6f}:{leg.range_high:.6f}", low, high, anchor,
                  t["expiry_candles"], [*sources, _source("module_03", None, "BOS with displacement", bos.break_candle_time, bos.confirmed_at)])


def bos_continuation(ctx: DecisionContext, a: Any, cfg: DecisionConfig) -> Candidate | str:
    t = cfg.template("bos_continuation")
    direction = _bias_direction(ctx)
    if direction is None:
        return f"Higher-timeframe consensus is {ctx.consensus}, not directional."
    bias = BIAS[direction]
    bos = _recent_displacement_bos(a, bias, t["max_break_age_candles"])
    if bos is None:
        return f"No recent confirmed {bias} BOS with displacement."
    level, band = bos.broken_level, t["retest_zone_atr"] * a.atr
    if abs(a.current_price - level) > 2 * band + a.atr:
        return "Price has left the broken level, so there is no retest."
    low, high = level - band, level + band
    stop = level - t["stop_atr"] * a.atr if direction == "long" else level + t["stop_atr"] * a.atr
    return _build(ctx, cfg, a, "bos_continuation", direction, f"bos:{bos.break_candle_time}", low, high, stop, t["expiry_candles"],
                  [_source("module_03", None, "BOS with displacement", bos.break_candle_time, bos.confirmed_at)])


def choch_reversal(ctx: DecisionContext, a: Any, cfg: DecisionConfig) -> Candidate | str:
    t = cfg.template("choch_reversal")
    last = a.candles_used - 1
    recent = [e for e in a.structure_events if e.event_type == "CHOCH" and e.confirmed and last - e.break_index <= t["max_choch_age_candles"]
              and e.confirmed_at <= ctx.as_of]
    if not recent:
        return "No recent confirmed CHoCH."
    choch = recent[-1]
    if not choch.displacement:
        return "The CHoCH had no displacement."
    bias = choch.direction
    direction = DIRECTION[bias]
    zones = [(ob.current_bottom, ob.current_top, ob.initial_bottom if direction == "long" else ob.initial_top, ob.id, "order_block", ob.source_time, ob.detected_at)
             for ob in a.order_blocks if ob.direction == bias and ob.state in ACTIVE_OB and ob.detected_at <= ctx.as_of]
    zones += [(g.current_lower, g.current_upper, g.current_lower if direction == "long" else g.current_upper, g.id, "fair_value_gap", g.candle_times[0], g.detected_at)
              for g in a.fair_value_gaps if g.direction == bias and g.state in ACTIVE_GAP and g.detected_at <= ctx.as_of]
    zones = [z for z in zones if abs((z[0] + z[1]) / 2 - a.current_price) <= 2 * a.atr]
    if not zones:
        return f"No active {bias} OB or FVG supports the CHoCH near price."
    low, high, distal, zone_id, kind, source_time, confirmed = max(zones, key=lambda z: z[6])
    return _build(ctx, cfg, a, "choch_reversal", direction, f"choch:{choch.break_candle_time}:{zone_id}", low, high, distal, t["expiry_candles"],
                  [_source("module_03", None, "CHoCH", choch.break_candle_time, choch.confirmed_at), _source("module_03", zone_id, kind, source_time, confirmed)])


def session_breakout(ctx: DecisionContext, a: Any, cfg: DecisionConfig) -> Candidate | str:
    t = cfg.template("session_breakout")
    active = set(ctx.sessions.get("active_sessions") or [])
    if not active & set(t["sessions"]):
        return f"Not in {' or '.join(t['sessions'])}."
    levels = {x.get("level_type"): x.get("price") for x in ((ctx.intelligence or {}).get("levels") or {}).get("levels") or []}
    high, low = levels.get("previous_asian_high"), levels.get("previous_asian_low")
    if high is None or low is None:
        return "No Asian range from Module 04."
    price = a.current_price
    if low <= price <= high:
        return "Price is still inside the Asian range."
    direction = "long" if price > high else "short"
    bias = BIAS[direction]
    bos = _recent_displacement_bos(a, bias, t["max_break_age_candles"])
    if bos is None:
        return f"No confirmed {bias} BOS with displacement out of the Asian range."
    edge, band = (high if direction == "long" else low), t["retest_zone_atr"] * a.atr
    stop = edge - t["stop_atr"] * a.atr if direction == "long" else edge + t["stop_atr"] * a.atr
    session = sorted(active & set(t["sessions"]))[0]
    day = ctx.as_of.strftime("%Y-%m-%d")
    return _build(ctx, cfg, a, "session_breakout", direction, f"asian:{day}:{edge:.6f}", edge - band, edge + band, stop, t["expiry_candles"],
                  [_source("module_04", None, f"Asian range broken in {session}", None, ctx.as_of),
                   _source("module_03", None, "BOS with displacement", bos.break_candle_time, bos.confirmed_at)])


def _sweeps(ctx: DecisionContext, a: Any) -> list[dict[str, Any]]:
    """Significant sweeps from Module 03 pools and Module 04 session/day levels, oldest first."""
    out = [{"id": e.pool_id, "kind": e.pool_kind, "side": "high" if e.side == "buy_side" else "low", "level": e.level,
            "extreme": e.extreme, "at": e.confirmed_at, "module": "module_03"}
           for e in a.liquidity_events if e.event == "sweep" and e.pool_kind in SIGNIFICANT_POOLS and e.confirmed_at <= ctx.as_of]
    for e in ((ctx.intelligence or {}).get("liquidity") or {}).get("events") or []:
        if e.get("event_type") == "wick_sweep" and e.get("timeframe") == a.timeframe and e.get("confirmation_time") \
                and e["confirmation_time"] <= ctx.as_of and e.get("side") in ("high", "low") and e.get("extreme") is not None:
            out.append({"id": f"{e['level_type']}@{e['level']}", "kind": e["level_type"], "side": e["side"], "level": e["level"],
                        "extreme": e["extreme"], "at": e["confirmation_time"], "module": "module_04"})
    return sorted(out, key=lambda s: s["at"])


def liquidity_sweep_reversal(ctx: DecisionContext, a: Any, cfg: DecisionConfig) -> Candidate | str:
    t = cfg.template("liquidity_sweep_reversal")
    window = timedelta(seconds=t["max_sweep_age_candles"] * TIMEFRAME_SECONDS[a.timeframe])
    sweeps = [s for s in _sweeps(ctx, a) if ctx.as_of - s["at"] <= window]
    if not sweeps:
        return "No recent sweep of a significant liquidity pool."
    for sweep in reversed(sweeps):
        direction = "short" if sweep["side"] == "high" else "long"
        bias = BIAS[direction]
        choch = next((e for e in reversed(a.structure_events) if e.event_type == "CHOCH" and e.direction == bias
                      and e.confirmed and sweep["at"] <= e.confirmed_at <= ctx.as_of), None)
        if choch is None:
            continue
        zones = [(ob.current_bottom, ob.current_top, ob.id, "order_block", ob.source_time, ob.detected_at)
                 for ob in a.order_blocks if ob.direction == bias and ob.state in ACTIVE_OB and sweep["at"] <= ob.detected_at <= ctx.as_of]
        zones += [(g.current_lower, g.current_upper, g.id, "fair_value_gap", g.candle_times[0], g.detected_at)
                  for g in a.fair_value_gaps if g.direction == bias and g.state in ACTIVE_GAP and sweep["at"] <= g.detected_at <= ctx.as_of]
        if not zones:
            continue
        low, high, zone_id, kind, source_time, confirmed = zones[-1]
        return _build(ctx, cfg, a, "liquidity_sweep_reversal", direction, f"{sweep['id']}:{zone_id}", low, high, sweep["extreme"],
                      t["expiry_candles"], [_source(sweep["module"], sweep["id"], f"sweep of {sweep['kind']}", None, sweep["at"]),
                                            _source("module_03", None, "CHoCH", choch.break_candle_time, choch.confirmed_at),
                                            _source("module_03", zone_id, kind, source_time, confirmed)])
    return "No sweep was followed by a confirmed CHoCH and a fresh entry zone."


def range_edge_rejection(ctx: DecisionContext, a: Any, cfg: DecisionConfig) -> Candidate | str:
    t = cfg.template("range_edge_rejection")
    external = a.structure.get("external")
    if external is None or external.state != "ranging":
        return f"External structure is {getattr(external, 'state', 'unknown')}, not ranging."
    if ctx.regime in t["expanding_regimes"]:
        return f"Volatility regime is {ctx.regime}, so the range may be breaking."
    if ctx.regime == "UNKNOWN":
        return "Volatility regime is unknown."
    price, atr = a.current_price, a.atr
    strong = [z for z in a.support_resistance if z.score >= t["min_zone_score"]]
    near = [z for z in strong if min(abs(price - z.top), abs(price - z.bottom)) <= t["max_distance_atr"] * atr
            or z.bottom <= price <= z.top]
    if not near:
        return f"No S/R zone scoring {t['min_zone_score']}+ near price."
    zone = max(near, key=lambda z: z.score)
    direction = {"support": "long", "resistance": "short"}.get(zone.role) or ("long" if zone.mid <= price else "short")
    distal = zone.bottom if direction == "long" else zone.top
    opposite = [z for z in strong if (z.bottom > zone.top if direction == "long" else z.top < zone.bottom)]
    ordered = sorted(opposite, key=lambda z: abs(z.mid - zone.mid))
    targets = [lv.Target(z.bottom if direction == "long" else z.top, "opposite range edge", z.id) for z in ordered]
    eq = a.premium_discount.equilibrium if a.premium_discount else None
    if eq is not None and (eq > zone.top if direction == "long" else eq < zone.bottom):
        targets.append(lv.Target(eq, "range equilibrium"))
    targets.sort(key=lambda x: abs(x.price - zone.mid))
    return _build(ctx, cfg, a, "range_edge_rejection", direction, zone.id, zone.bottom, zone.top, distal, t["expiry_candles"],
                  [_source("module_03", zone.id, f"S/R zone (score {zone.score:.0f})", zone.first_time, zone.last_time)], targets)


TEMPLATES: dict[str, Callable[[DecisionContext, Any, DecisionConfig], Candidate | str]] = {
    "order_block_continuation": order_block_continuation,
    "fair_value_gap_retracement": fair_value_gap_retracement,
    "premium_discount_ote": premium_discount_ote,
    "bos_continuation": bos_continuation,
    "choch_reversal": choch_reversal,
    "session_breakout": session_breakout,
    "liquidity_sweep_reversal": liquidity_sweep_reversal,
    "range_edge_rejection": range_edge_rejection,
}


def generate(ctx: DecisionContext, cfg: DecisionConfig, templates: Iterable[str] | None = None,
             timeframes: Iterable[str] | None = None) -> tuple[list[Candidate], list[Skip]]:
    """Run every enabled template on its entry timeframes, or only on ``timeframes`` when given."""
    wanted = set(templates) if templates else None
    found: list[Candidate] = []
    skipped: list[Skip] = []
    for name, fn in TEMPLATES.items():
        if not cfg.template(name)["enabled"] or (wanted is not None and name not in wanted):
            continue
        for tf in timeframes or cfg.template(name).get("timeframes") or cfg.entry_timeframes:
            a = ctx.analyses.get(tf)
            if a is None or a.status != "ok" or not a.atr or a.current_price is None:
                skipped.append(Skip(name, tf, f"Module 03 has no usable {tf} analysis."))
                continue
            out = fn(ctx, a, cfg)
            if isinstance(out, str):
                skipped.append(Skip(name, tf, out))
            else:
                found.append(out)
    return found, skipped
