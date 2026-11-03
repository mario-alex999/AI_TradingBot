"""Learning loop: measure which confluence components actually add edge, and suggest new weights.
Weights are only SUGGESTED (stored in component_stats); a human approves changes to config.yaml."""
from __future__ import annotations
import json
from datetime import datetime, timezone
import numpy as np


def component_report(store, base_weights: dict, min_trades: int = 30) -> list[dict]:
    rows = store.q("SELECT s.breakdown, t.r_multiple FROM trades t JOIN signals s ON s.id=t.signal_id")
    if not rows:
        return []
    all_r = np.array([r["r_multiple"] for r in rows])
    out = []
    for comp, w in base_weights.items():
        if comp == "min_score":
            continue
        with_c = np.array([r["r_multiple"] for r in rows if comp in json.loads(r["breakdown"])])
        without = np.array([r["r_multiple"] for r in rows if comp not in json.loads(r["breakdown"])])
        if len(with_c) < min_trades:
            sugg = w      # not enough evidence -> keep
        else:
            edge = with_c.mean() - (without.mean() if len(without) else all_r.mean())
            sugg = float(np.clip(w * (1 + edge), w * 0.5, w * 1.5))   # bounded nudges only
        rec = dict(component=comp, trades=int(len(with_c)),
                   win_rate=round(float((with_c > 0).mean()), 3) if len(with_c) else None,
                   avg_r=round(float(with_c.mean()), 3) if len(with_c) else None,
                   suggested_weight=round(sugg, 1), updated=datetime.now(timezone.utc).isoformat())
        store.insert("component_stats", rec, replace=True)
        out.append(rec)
    return out
