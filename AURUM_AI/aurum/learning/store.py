"""Persists a lineage as JSON so the cycle continues across runs (data/learning/<id>.json)."""
from __future__ import annotations

import json
from collections import deque
from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path
from typing import Any

from .engine import AgentRecord, Lineage
from .models import AgentEvent, LearningConfig, Trade


def _dt(v: str | None) -> datetime | None:
    return datetime.fromisoformat(v) if v else None


def _trade_out(t: Trade) -> dict[str, Any]:
    d = asdict(t)
    d["open_time"] = t.open_time.isoformat()
    d["close_time"] = t.close_time.isoformat()
    return d


def _trade_in(d: dict[str, Any]) -> Trade:
    return Trade(**{**d, "open_time": _dt(d["open_time"]), "close_time": _dt(d["close_time"])})


def to_dict(lin: Lineage) -> dict[str, Any]:
    """JSON-ready view; also what the API returns (keys camelised by the API layer)."""
    agents = []
    for a in lin.agents:
        agents.append({
            "id": a.id,
            "generation": a.generation,
            "parent": a.parent,
            "status": a.status,
            "created_at": a.created_at.isoformat(),
            "ended_at": a.ended_at.isoformat() if a.ended_at else None,
            "pattern": [list(c) for c in a.pattern] if a.pattern else None,
            "discovery_stats": a.discovery_stats,
            "trial_progress": {"matched": len(a.trial_matched), "seen": a.trial_seen},
            "trial_matched": [_trade_out(t) for t in a.trial_matched],
            "failed_trials": a.failed_trials,
            "rejected": [[list(c) for c in p] for p in a.rejected],
            "trials": a.trials,
            "events": [{"at": e.at.isoformat(), "kind": e.kind, "message": e.message, "data": e.data} for e in a.events],
            "no_pattern_logged_at": a._no_pattern_logged_at,
        })
    return {
        "id": lin.id,
        "status": lin.status,
        "created_at": lin.created_at.isoformat(),
        "config": {**asdict(lin.config), "features": list(lin.config.features)},
        "trades_observed": lin.trades_observed,
        "last_close_time": lin.last_close_time.isoformat() if lin.last_close_time else None,
        "active_agent": lin.active_agent.id if lin.active_agent else None,
        "agents": agents,
        "window": [_trade_out(t) for t in lin.window],
        "seen_tickets": list(lin.seen_tickets),
        "read_only": True,
    }


def from_dict(d: dict[str, Any]) -> Lineage:
    cfg_fields = {f.name for f in fields(LearningConfig)}
    cfg = LearningConfig(**{k: (tuple(v) if k == "features" else v) for k, v in d["config"].items() if k in cfg_fields})
    lin = Lineage(id=d["id"], created_at=_dt(d["created_at"]), config=cfg)
    lin.trades_observed = d.get("trades_observed", 0)
    lin.last_close_time = _dt(d.get("last_close_time"))
    lin.window = deque(_trade_in(t) for t in d.get("window", []))
    lin.seen_tickets = deque(d.get("seen_tickets", []), maxlen=20000)
    for a in d.get("agents", []):
        rec = AgentRecord(
            id=a["id"], generation=a["generation"], parent=a["parent"], created_at=_dt(a["created_at"]),
            status=a["status"], pattern=tuple(tuple(c) for c in a["pattern"]) if a.get("pattern") else None,
            discovery_stats=a.get("discovery_stats"),
            trial_matched=[_trade_in(t) for t in a.get("trial_matched", [])],
            trial_seen=a.get("trial_progress", {}).get("seen", 0),
            failed_trials=a.get("failed_trials", 0),
            rejected=[tuple(tuple(c) for c in p) for p in a.get("rejected", [])],
            trials=a.get("trials", []),
            events=[AgentEvent(at=_dt(e["at"]), kind=e["kind"], message=e["message"], data=e.get("data", {})) for e in a.get("events", [])],
            ended_at=_dt(a.get("ended_at")),
        )
        rec._no_pattern_logged_at = a.get("no_pattern_logged_at", -1)
        lin.agents.append(rec)
    return lin


class LineageStore:
    def __init__(self, folder: str | Path):
        self.folder = Path(folder)

    def path(self, lineage_id: str) -> Path:
        return self.folder / f"{lineage_id}.json"

    def load(self, lineage_id: str) -> Lineage | None:
        p = self.path(lineage_id)
        return from_dict(json.loads(p.read_text(encoding="utf-8"))) if p.exists() else None

    def save(self, lin: Lineage) -> Path:
        self.folder.mkdir(parents=True, exist_ok=True)
        p = self.path(lin.id)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(to_dict(lin), indent=1, default=str), encoding="utf-8")
        tmp.replace(p)  # atomic: a crash never leaves a half-written lineage
        return p

    def delete(self, lineage_id: str) -> bool:
        p = self.path(lineage_id)
        if p.exists():
            p.unlink()
            return True
        return False
