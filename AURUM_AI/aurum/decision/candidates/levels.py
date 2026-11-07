"""Stop and target construction from Module 03 zones and Module 04 levels. Prices are computed here, never by the AI."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aurum.decision.config import DecisionConfig
from aurum.decision.context import DecisionContext


@dataclass(frozen=True)
class Target:
    price: float
    source: str
    finding_id: str | None = None


def stop_buffer(ctx: DecisionContext, atr: float, cfg: DecisionConfig) -> float:
    """ATR multiple plus the spread. An unknown spread adds nothing here; the spread gate reports it."""
    return atr * cfg.levels["stop_buffer_atr"] + (ctx.spread_price or 0.0)


def stop_beyond(level: float, direction: str, buffer: float) -> float:
    return level - buffer if direction == "long" else level + buffer


def risk_reward(entry: float, stop: float, target: float) -> float:
    risk = abs(entry - stop)
    return abs(target - entry) / risk if risk > 0 else 0.0


def target_candidates(ctx: DecisionContext, analysis: Any, direction: str, entry: float, cfg: DecisionConfig) -> list[Target]:
    """Opposing liquidity, the external swing and Module 04 levels on the far side of the entry, nearest first."""
    above = direction == "long"
    side = "buy_side" if above else "sell_side"
    found: list[Target] = [Target(p.level, f"liquidity pool ({p.kind}, {p.timeframe})", p.id)
                           for p in analysis.liquidity_pools if p.side == side and p.state == "active"]
    external = analysis.structure.get("external")
    swing = getattr(external, "last_swing_high" if above else "last_swing_low", None)
    if swing and swing.get("price") is not None:
        found.append(Target(float(swing["price"]), f"external swing {'high' if above else 'low'}"))
    wanted = "high" if above else "low"
    levels = ((ctx.intelligence or {}).get("levels") or {}).get("levels") or []
    for lv in levels:
        if lv.get("side") == wanted and lv.get("level_type") in cfg.levels["target_level_types"] and lv.get("price") is not None:
            found.append(Target(float(lv["price"]), f"Module 04 {lv['level_type']}"))
    beyond = [t for t in found if (t.price > entry if above else t.price < entry)]
    return sorted(beyond, key=lambda t: abs(t.price - entry))


def pick_targets(targets: list[Target], entry: float, stop: float, min_rr: float) -> tuple[Target | None, Target | None]:
    """The nearest target that meets the minimum risk-reward, and the next distinct one beyond it."""
    primary = next((t for t in targets if risk_reward(entry, stop, t.price) >= min_rr), None)
    if primary is None:
        return None, None
    further = [t for t in targets if abs(t.price - entry) > abs(primary.price - entry)]
    return primary, (further[0] if further else None)
