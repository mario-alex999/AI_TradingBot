"""Trade idea intake: the payload contract (schema 1.0), validation, and the Module 05 adapter.

Validation rejects missing fields, unsupported instruments, invalid direction, non-positive or inconsistent prices, malformed
timestamps, expired ideas and malformed evidence references. AI confidence is kept exactly as supplied; it is never treated
as a probability of profit and never influences a decision.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from aurum.approvals import SCHEMA_VERSION

SUPPORTED_SCHEMAS = ("1.0",)
DIRECTIONS = {"BUY": "BUY", "SELL": "SELL", "LONG": "BUY", "SHORT": "SELL"}
TIMEFRAMES = ("D1", "H4", "H1", "M15", "M5")
EVIDENCE_MODULES = ("module_01", "module_02", "module_03", "module_04", "module_05", "module_06")
_SKEW = timedelta(minutes=2)       # clock difference tolerated on created_at
REQUIRED = ("idea_id", "symbol", "market", "direction", "stop_loss", "take_profits", "timeframe", "strategy", "created_at",
            "expires_at", "analysis_version", "correlation_id", "schema_version")


class IntakeError(ValueError):
    def __init__(self, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details


@dataclass(frozen=True)
class IdeaPayload:
    """A validated trade idea. Prices are exact Decimals."""
    idea_id: str
    symbol: str
    market: str
    direction: str
    entry_low: Decimal
    entry_high: Decimal
    entry: Decimal
    stop_loss: Decimal
    take_profits: tuple[Decimal, ...]
    risk_reward: Decimal | None
    timeframe: str
    strategy: str
    higher_timeframe_context: dict[str, Any] | None
    ai_summary: str | None
    ai_confidence: Decimal | None
    evidence: tuple[dict[str, Any], ...]
    market_conditions: dict[str, Any] | None
    created_at: datetime
    expires_at: datetime
    analysis_version: str
    source_refs: tuple[dict[str, Any], ...]
    correlation_id: str
    schema_version: str
    source: str
    source_ref: str | None
    raw: dict[str, Any] = field(compare=False)

    @property
    def take_profit(self) -> Decimal:
        return self.take_profits[0]

    def params(self) -> dict[str, str | None]:
        """The trade parameters a person approves. Any change to these is material."""
        return {"symbol": self.symbol, "direction": self.direction, "entry": str(self.entry), "stop_loss": str(self.stop_loss),
                "take_profit": str(self.take_profit)}

    def payload_hash(self) -> str:
        return content_hash(self.raw)


def content_hash(obj: Any) -> str:
    from aurum.risk.repository import stable_hash
    return stable_hash(obj)


def _dec(v: Any, name: str, required: bool = True) -> Decimal | None:
    if v is None or v == "":
        if required:
            raise IntakeError("MISSING_FIELD", f"{name} is required.", {"field": name})
        return None
    if isinstance(v, bool):
        raise IntakeError("INVALID_PRICE", f"{name} is not a number.", {"field": name})
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError) as exc:
        raise IntakeError("INVALID_PRICE", f"{name} is not a number.", {"field": name}) from exc
    if not d.is_finite():
        raise IntakeError("INVALID_PRICE", f"{name} must be a finite number.", {"field": name})
    return d


def _time(v: Any, name: str) -> datetime:
    if isinstance(v, datetime):
        t = v
    else:
        try:
            t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except ValueError as exc:
            raise IntakeError("INVALID_TIMESTAMP", f"{name} is not an ISO-8601 timestamp.", {"field": name}) from exc
    if t.tzinfo is None:
        raise IntakeError("INVALID_TIMESTAMP", f"{name} must include a timezone (UTC).", {"field": name})
    return t.astimezone(timezone.utc)


def validate(raw: dict[str, Any], *, supported_symbols: dict[str, str], now: datetime, source: str,
             source_ref: str | None = None) -> IdeaPayload:
    missing = [f for f in REQUIRED if raw.get(f) in (None, "", [])]
    if raw.get("entry") in (None, "") and (raw.get("entry_low") in (None, "") or raw.get("entry_high") in (None, "")):
        missing.append("entry (or entry_low and entry_high)")
    if missing:
        raise IntakeError("MISSING_FIELD", f"Missing required fields: {', '.join(missing)}.", {"fields": missing})
    if str(raw["schema_version"]) not in SUPPORTED_SCHEMAS:
        raise IntakeError("UNSUPPORTED_SCHEMA", f"Schema version {raw['schema_version']} is not supported "
                                                f"(supported: {', '.join(SUPPORTED_SCHEMAS)}).")
    symbol = str(raw["symbol"]).upper().strip()
    if symbol not in supported_symbols:
        raise IntakeError("UNSUPPORTED_INSTRUMENT", f"{symbol} is not an enabled instrument.",
                          {"supported": sorted(supported_symbols)})
    direction = DIRECTIONS.get(str(raw["direction"]).upper().strip())
    if direction is None:
        raise IntakeError("INVALID_DIRECTION", "direction must be BUY or SELL.")
    timeframe = str(raw["timeframe"]).upper()
    if timeframe not in TIMEFRAMES:
        raise IntakeError("INVALID_TIMEFRAME", f"timeframe must be one of {', '.join(TIMEFRAMES)}.")

    if raw.get("entry") not in (None, ""):
        entry = _dec(raw["entry"], "entry")
        low = _dec(raw.get("entry_low"), "entry_low", False) or entry
        high = _dec(raw.get("entry_high"), "entry_high", False) or entry
    else:
        low, high = _dec(raw["entry_low"], "entry_low"), _dec(raw["entry_high"], "entry_high")
        entry = (low + high) / 2
    stop = _dec(raw["stop_loss"], "stop_loss")
    tps_raw = raw["take_profits"] if isinstance(raw["take_profits"], list) else [raw["take_profits"]]
    tps = tuple(_dec(t, "take_profits") for t in tps_raw)
    for name, value in (("entry", entry), ("entry_low", low), ("entry_high", high), ("stop_loss", stop),
                        *((f"take_profits[{i}]", t) for i, t in enumerate(tps))):
        if value <= 0:
            raise IntakeError("INVALID_PRICE", f"{name} must be positive.", {"field": name})
    if low > high or not (low <= entry <= high):
        raise IntakeError("INVALID_PRICE", "The entry must lie inside the entry range (entry_low <= entry <= entry_high).")
    sign = 1 if direction == "BUY" else -1
    if sign * (entry - stop) <= 0:
        raise IntakeError("INVALID_PRICE", f"For a {direction}, the stop-loss must be on the "
                                           f"{'lower' if sign > 0 else 'upper'} side of the entry.")
    if any(sign * (t - entry) <= 0 for t in tps):
        raise IntakeError("INVALID_PRICE", f"For a {direction}, every take-profit must be on the "
                                           f"{'upper' if sign > 0 else 'lower'} side of the entry.")
    rr = _dec(raw.get("risk_reward"), "risk_reward", False)
    if rr is not None and rr <= 0:
        raise IntakeError("INVALID_PRICE", "risk_reward must be positive.")
    conf = _dec(raw.get("ai_confidence"), "ai_confidence", False)
    if conf is not None and not (0 <= conf <= 100):
        raise IntakeError("INVALID_CONFIDENCE", "ai_confidence must be between 0 and 100 (or omitted).")

    created, expires = _time(raw["created_at"], "created_at"), _time(raw["expires_at"], "expires_at")
    if expires <= created:
        raise IntakeError("INVALID_TIMESTAMP", "expires_at must be after created_at.")
    if created > now.replace(microsecond=0) + _SKEW:
        raise IntakeError("INVALID_TIMESTAMP", "created_at is in the future.")
    if expires <= now:
        raise IntakeError("IDEA_EXPIRED", f"The trade idea expired at {expires.isoformat()}.")

    evidence = raw.get("evidence") or []
    if not isinstance(evidence, list):
        raise IntakeError("INVALID_EVIDENCE", "evidence must be a list.")
    for i, ev in enumerate(evidence):
        if not isinstance(ev, dict) or not str(ev.get("description") or "").strip():
            raise IntakeError("INVALID_EVIDENCE", f"evidence[{i}] needs a description.", {"index": i})
        mod = ev.get("source_module")
        if mod is not None and mod not in EVIDENCE_MODULES:
            raise IntakeError("INVALID_EVIDENCE", f"evidence[{i}].source_module {mod!r} is not a known module.", {"index": i})
        if ev.get("finding_id") is not None and not isinstance(ev.get("finding_id"), str):
            raise IntakeError("INVALID_EVIDENCE", f"evidence[{i}].finding_id must be a string.", {"index": i})
    refs = raw.get("source_refs") or []
    if not isinstance(refs, list) or any(not isinstance(r, dict) or not r.get("module") for r in refs):
        raise IntakeError("INVALID_EVIDENCE", "source_refs must be a list of objects with a module.")

    return IdeaPayload(
        idea_id=str(raw["idea_id"])[:64], symbol=symbol, market=str(raw["market"]).lower()[:16], direction=direction,
        entry_low=low, entry_high=high, entry=entry, stop_loss=stop, take_profits=tps, risk_reward=rr, timeframe=timeframe,
        strategy=str(raw["strategy"])[:64], higher_timeframe_context=raw.get("higher_timeframe_context"),
        ai_summary=(str(raw["ai_summary"])[:4000] if raw.get("ai_summary") else None), ai_confidence=conf,
        evidence=tuple(evidence), market_conditions=raw.get("market_conditions"), created_at=created, expires_at=expires,
        analysis_version=str(raw["analysis_version"])[:64], source_refs=tuple(refs), correlation_id=str(raw["correlation_id"])[:64],
        schema_version=str(raw["schema_version"]), source=source, source_ref=source_ref, raw=raw)


# ---- Module 05 adapter -----------------------------------------------------------------------------------------------------
def from_module05(candidate: dict[str, Any], evidence: list[dict[str, Any]], ai_review: dict[str, Any] | None,
                  market: str, entry: Decimal | None, engine_version: str = "module05") -> dict[str, Any]:
    """Maps a stored Module 05 candidate to the intake contract. ``entry`` is Module 06's normalised entry (zone mid-point on
    the tick); Module 07 does not compute it. Module 05 prices are stored as floats, so they are converted through ``str``."""
    tps = [candidate["take_profit"]] + ([candidate["take_profit2"]] if candidate.get("take_profit2") else [])
    review = (ai_review or {}).get("review") or {}
    return {
        "idea_id": candidate["candidate_uid"], "symbol": candidate["symbol"], "market": market,
        "direction": candidate["direction"], "entry_low": str(candidate["entry_low"]), "entry_high": str(candidate["entry_high"]),
        "entry": str(entry) if entry is not None else None, "stop_loss": str(candidate["stop_loss"]),
        "take_profits": [str(t) for t in tps], "risk_reward": str(candidate["risk_reward"]), "timeframe": candidate["timeframe"],
        "higher_timeframe_context": {"market_condition": candidate.get("market_condition"),
                                     "evidence": [e["sentence"] for e in evidence if e.get("component") == "htf_alignment"]},
        "strategy": candidate["template"],
        "ai_summary": review.get("summary"),
        "ai_confidence": str(candidate["confidence"]) if candidate.get("confidence") is not None else None,
        "evidence": [{"description": e["sentence"], "component": e.get("component"), "points": e.get("points"),
                      "max_points": e.get("max_points"), "source_module": "module_05",
                      "sources": e.get("sources") or []} for e in evidence],
        "market_conditions": {"condition": candidate.get("market_condition"), "invalidation": candidate.get("invalidation"),
                              "grade": candidate.get("grade")},
        "created_at": candidate["as_of"], "expires_at": candidate["expires_at"],
        "analysis_version": f"{engine_version}:{candidate['input_hash'][:16]}",
        "source_refs": [{"module": "module_05", "candidate_uid": candidate["candidate_uid"],
                         "run_uid": candidate.get("last_seen_run")}],
        "correlation_id": candidate["candidate_uid"], "schema_version": SCHEMA_VERSION,
    }
