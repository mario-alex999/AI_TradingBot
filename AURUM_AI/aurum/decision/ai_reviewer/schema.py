"""The reviewer's answer format, and the rule that it can only lower a candidate."""
from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["agree", "caution", "reject"]},
        "confidence_adjustment": {"type": "number", "description": "Points to subtract: zero or negative, down to -100."},
        "summary": {"type": "string", "description": "Two to four plain-English sentences for the operator."},
        "bullish_case": {"type": "string", "description": "The bullish reading of this evidence."},
        "bearish_case": {"type": "string", "description": "The bearish reading of this evidence."},
        "supporting_points": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "data_gaps": {"type": "array", "items": {"type": "string"}},
        "invalidation_check": {"type": "string"},
        "lesson_candidate": {"type": "string", "description": "An optional note for later review, or an empty string."},
    },
    "required": ["verdict", "confidence_adjustment", "summary", "bullish_case", "bearish_case", "supporting_points", "risks", "data_gaps", "invalidation_check",
                 "lesson_candidate"],
    "additionalProperties": False,
}


class Review(BaseModel):
    """Extra fields are rejected, so an answer carrying its own price, size or direction fails validation."""
    model_config = ConfigDict(extra="forbid")
    verdict: Literal["agree", "caution", "reject"]
    confidence_adjustment: float
    summary: str
    bullish_case: str
    bearish_case: str
    supporting_points: list[str]
    risks: list[str]
    data_gaps: list[str]
    invalidation_check: str
    lesson_candidate: str = ""


NUMBER = re.compile(r"(?<![\w.])\d+\.\d{2,}(?![\w])")


def unsupported_numbers(review: dict[str, Any], prompt: str) -> list[str]:
    """Prices or measures in the answer that appear nowhere in what the reviewer was given. Such a claim is invented."""
    known = {round(float(n), 2) for n in NUMBER.findall(prompt)} | {round(float(n), 3) for n in NUMBER.findall(prompt)}
    text = json.dumps({k: v for k, v in review.items() if k != "confidence_adjustment"}, ensure_ascii=False)
    return [n for n in NUMBER.findall(text) if round(float(n), 2) not in known and round(float(n), 3) not in known]


def clamp_adjustment(value: float) -> float:
    """Server-side guard: never above zero, never below -100."""
    return max(-100.0, min(0.0, float(value)))


def final_confidence(score: float, gate_cap: float | None, ai: dict[str, Any], unavailable_cap: float) -> float:
    """The published confidence. Every step can only lower ``score``, so the AI can never raise it."""
    value = score if gate_cap is None else min(score, gate_cap)
    if ai.get("status") == "reviewed":
        value += clamp_adjustment(ai.get("adjustment") or 0.0)
    else:
        value = min(value, unavailable_cap)
    return round(max(0.0, value), 1)
