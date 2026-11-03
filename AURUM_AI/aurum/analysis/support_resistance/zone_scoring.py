"""Explainable 0-100 score for a support/resistance zone."""
from __future__ import annotations

import math

from aurum.analysis.config import SupportResistanceConfig
from aurum.analysis.models.swing import Swing


def score_zone(members: list[Swing], last_index: int, cfg: SupportResistanceConfig) -> tuple[float, dict[str, float]]:
    touches = min(len(members), 6) / 6 * 40                                   # more reactions, stronger zone
    age = last_index - max(s.confirmed_index for s in members)
    recency = 30 * math.pow(0.5, age / max(cfg.recency_half_life_bars, 1))    # recent reactions matter more
    strength = min(sum(s.strength for s in members) / len(members) / 20, 1.0) * 20
    flip = 10.0 if {s.kind for s in members} == {"high", "low"} else 0.0      # role reversal adds weight
    parts = {"touches": round(touches, 2), "recency": round(recency, 2), "swing_strength": round(strength, 2),
             "role_flip": flip}
    return round(sum(parts.values()), 2), parts
