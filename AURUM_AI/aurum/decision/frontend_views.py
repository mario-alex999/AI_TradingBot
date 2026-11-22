"""Maps stored candidates to the frontend's TradeSignal contract (aurum-frontend/src/types/trading.ts)."""
from __future__ import annotations

from typing import Any


def signal(row: dict[str, Any], evidence: list[str], outcome: dict[str, Any] | None = None) -> dict[str, Any]:
    """The TradeSignal fields, in snake_case; ``camelize`` handles the rest. ``entry_price`` is the limit entry the
    risk-reward and the outcome use: the middle of the entry zone."""
    return {"id": row["candidate_uid"], "instrument": row["symbol"], "direction": row["direction"],
            "entry_zone": [row["entry_low"], row["entry_high"]], "entry_price": (row["entry_low"] + row["entry_high"]) / 2,
            "stop_loss": row["stop_loss"], "take_profit": row["take_profit"],
            "risk_reward": row["risk_reward"], "strategy": row["template"], "timeframe": row["timeframe"],
            "market_condition": row["market_condition"], "confidence": round(row["confidence"]), "evidence": evidence,
            "timestamp": row["as_of"], "status": row["status"], "grade": row["grade"], "rejected_by": row["rejected_by"],
            "outcome": outcome_view(outcome)}


# What happened to the signal in the market (hypothetical: AURUM places no trades), from Module 01 M5 candles.
OUTCOME_STATE = {"tp": "take_profit", "sl": "stop_loss", "ambiguous": "ambiguous"}


def outcome_view(o: dict[str, Any] | None) -> dict[str, Any]:
    """``state``: pending (not checked yet), waiting (entry not reached), running (triggered, no exit yet),
    take_profit, stop_loss, ambiguous (target and stop in the same candle: counted as a loss), closed_at_limit
    (triggered, closed after the holding limit) or not_triggered (expired before the entry was reached)."""
    if o is None:
        return {"state": "pending", "touched": False, "filled": False, "result": None, "r_multiple": None, "mfe_r": None,
                "mae_r": None, "filled_at": None, "resolved_at": None, "exit_price": None}
    if o["result"] == "open":
        state = "running" if o["filled"] else "waiting"
    elif o["result"] == "expired":
        state = "closed_at_limit" if o["filled"] else "not_triggered"
    else:
        state = OUTCOME_STATE.get(o["result"], o["result"])
    return {"state": state, **o}


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
