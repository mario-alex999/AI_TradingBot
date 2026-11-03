"""GET /api/v1/agents/learning — the Trade Learning Agent's lineage (read-only)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter

from aurum.learning.store import LineageStore, to_dict


def build_router(state: Any) -> APIRouter:
    r = APIRouter()
    s = state.settings

    @r.get("/learning")
    def learning() -> dict[str, Any]:
        from aurum.api.app import camelize
        lineage = LineageStore(Path(s.resolve(s.data_dir)) / "learning").load("L1")
        if lineage is None:
            return {"lineage": None}
        d = to_dict(lineage)
        # The UI needs the story, not the raw trade buffers.
        d.pop("window", None)
        d.pop("seen_tickets", None)
        for a in d["agents"]:
            a.pop("trial_matched", None)
        return {"lineage": camelize(d)}

    return r
