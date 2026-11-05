"""Maps stored candidates to the frontend's TradeSignal contract (aurum-frontend/src/types/trading.ts)."""
from __future__ import annotations

from typing import Any


def signal(row: dict[str, Any], evidence: list[str]) -> dict[str, Any]:
    """Exactly the TradeSignal fields, in snake_case; ``camelize`` handles the rest."""
    return {"id": row["candidate_uid"], "instrument": row["symbol"], "direction": row["direction"],
            "entry_zone": [row["entry_low"], row["entry_high"]], "stop_loss": row["stop_loss"], "take_profit": row["take_profit"],
            "risk_reward": row["risk_reward"], "strategy": row["template"], "timeframe": row["timeframe"],
            "market_condition": row["market_condition"], "confidence": round(row["confidence"]), "evidence": evidence,
            "timestamp": row["as_of"], "status": row["status"]}


REVIEW = {"new": "GENERATED", "under_review": "PENDING_REVIEW", "approved": "APPROVED_FOR_FURTHER_PROCESSING", "rejected": "REJECTED", "expired": "EXPIRED"}


def review_status(row: dict[str, Any], history: list[dict[str, Any]]) -> str:
    """The review label for a stored status. Approval means accepted for further processing, and nothing else."""
    last = history[-1] if history else {}
    if row["status"] == "expired" and "beyond the stop" in (last.get("note") or ""):
        return "INVALIDATED"
    return REVIEW.get(row["status"], row["status"].upper())


def trade_idea(row: dict[str, Any], evidence: list[str], history: list[dict[str, Any]]) -> dict[str, Any]:
    active = row["status"] in ("new", "under_review")
    return {**signal(row, evidence), "take_profit2": row["take_profit2"], "invalidation": row["invalidation"], "expires_at": row["expires_at"],
            "grade": row["grade"], "estimated_volume": row["suggested_volume"], "volume_is_estimate": True,
            "review_status": review_status(row, history), "human_review": "PENDING_HUMAN_REVIEW" if active else "REVIEWED"}


def analysis_row(r: dict[str, Any]) -> dict[str, Any]:
    s = r.get("summary") or {}
    return {"id": r["run_uid"], "symbol": r["symbol"], "as_of": r["as_of"], "status": r["status"], "decision": (s.get("decision") or {}).get("decision"),
            "regime": (s.get("regime") or {}).get("regime"), "bias": (s.get("bias") or {}).get("overall"), "candidates": s.get("candidates", 0),
            "created_at": r["created_at"]}


def ai_view(review: dict[str, Any] | None) -> dict[str, Any]:
    if review is None:
        return {"status": "unavailable", "reason": "not_requested", "verdict": None, "adjustment": None, "review": None}
    return {k: review.get(k) for k in ("status", "reason", "verdict", "adjustment", "review", "model", "prompt_version", "created_at")}
