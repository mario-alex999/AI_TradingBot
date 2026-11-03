"""AI trade-review agent (Claude). The rules engine FINDS setups; the agent REVIEWS them
against stored history, COT context and stored lessons, and returns a structured decision.
The agent can only veto or reduce size — it can never enlarge risk or invent a trade."""
from __future__ import annotations
import json
import os

SYSTEM = """You are the risk-review analyst for the SmartFlow Confluence trading bot.
You receive one candidate setup that a deterministic rules engine already found, plus:
COT positioning analytics (commercials, large speculators, small speculators), session context,
historical performance of similar setups from the bot's database, and lessons from past reviews.

Your job: decide TAKE, REDUCE (take at smaller size), or SKIP. Be skeptical. Reasons to SKIP or
REDUCE include: COT bias conflicts with trade direction; similar setups have negative expectancy
with a meaningful sample; high-impact news within the next 2 hours; the stop sits inside obvious
liquidity; the target is beyond a strong opposing order block or S/R level. Never recommend
increasing risk above the planned size. Ground every point in the data provided; if data is
missing, say so instead of assuming."""

SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["TAKE", "REDUCE", "SKIP"]},
        "confidence": {"type": "number"},
        "size_multiplier": {"type": "number"},
        "rationale": {"type": "string"},
        "risks": {"type": "array", "items": {"type": "string"}},
        "lesson_to_store": {"type": "string"},
    },
    "required": ["decision", "confidence", "size_multiplier", "rationale", "risks", "lesson_to_store"],
    "additionalProperties": False,
}


def review(setup: dict, cot_context: dict, history: dict, memories: list[str], news: list[dict],
           model: str = "claude-opus-5-5", effort: str = "high") -> dict:
    if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return {"decision": "TAKE", "confidence": 0.5, "size_multiplier": 1.0,
                "rationale": "AI agent offline (no API credentials) - rules engine decision used.",
                "risks": [], "lesson_to_store": ""}
    import anthropic
    client = anthropic.Anthropic()
    payload = {"setup": setup, "cot": cot_context, "similar_setup_history": history,
               "lessons": memories, "upcoming_news": news}
    try:
        resp = client.beta.messages.create(
            model=model,
            max_tokens=16000,
            system=SYSTEM,
            thinking={"type": "adaptive"},
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": json.dumps(payload, default=str)}],
        )
    except anthropic.RateLimitError:
        return _fail_safe("Rate limited")
    except anthropic.APIStatusError as e:
        return _fail_safe(f"API error {e.status_code}")
    except anthropic.APIConnectionError:
        return _fail_safe("Connection error")
    if resp.stop_reason == "refusal":
        return _fail_safe("Model declined to review")
    text = "".join(b.text for b in resp.content if b.type == "text")
    out = json.loads(text)
    out["size_multiplier"] = max(0.0, min(1.0, float(out["size_multiplier"])))
    return out


def _fail_safe(reason: str) -> dict:
    # When the reviewer is unavailable we SKIP — safer than trading unreviewed in live mode.
    return {"decision": "SKIP", "confidence": 0.0, "size_multiplier": 0.0,
            "rationale": f"Fail-safe skip: {reason}", "risks": [reason], "lesson_to_store": ""}
