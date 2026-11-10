"""Failure classification and retry policies.

Every failure gets an ``error_category`` and one of three kinds:
- ``transient``: safe to retry automatically (network unreachable before sending, HTTP 429, 502/503/504, engine busy);
- ``permanent``: retrying cannot help (bad payload, authentication, unknown workflow, business rule such as an expired
  approval or a Module 06 BLOCK). Never retried automatically;
- ``unknown``: the request may have been processed (a time-out after sending, a dropped connection). Module 08 never
  assumes it did not happen: it retries automatically only when the receiver deduplicates by execution id
  (``idempotent_dispatch``); otherwise a person decides, after checking the engine.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

TRANSIENT, PERMANENT, UNKNOWN = "transient", "permanent", "unknown"

# Categories a workflow (or Module 08) can report, and whether they are business outcomes rather than infrastructure faults.
BUSINESS_CATEGORIES = frozenset({"BUSINESS_RULE", "RISK_BLOCKED", "APPROVAL_EXPIRED", "APPROVAL_NOT_APPROVED", "VALIDATION_FAILED"})
PERMANENT_CATEGORIES = frozenset({"INVALID_PAYLOAD", "AUTH_FAILED", "PERMISSION_DENIED", "WORKFLOW_NOT_FOUND",
                                  "UNSUPPORTED_WORKFLOW", "INVALID_SCHEMA", "ENGINE_NOT_CONFIGURED", "ENVIRONMENT_MISMATCH",
                                  "CONFLICT"}) | BUSINESS_CATEGORIES
TRANSIENT_CATEGORIES = frozenset({"NETWORK_UNREACHABLE", "RATE_LIMITED", "SERVER_ERROR", "ENGINE_UNAVAILABLE",
                                  "WORKFLOW_PAUSED", "DEPENDENCY_UNAVAILABLE", "WORKFLOW_ERROR"})
UNKNOWN_CATEGORIES = frozenset({"OUTCOME_UNKNOWN", "TIMED_OUT"})


@dataclass(frozen=True)
class Classified:
    kind: str
    category: str
    message: str
    http_status: int | None = None
    retry_after: float | None = None


def classify_http(status: int, retry_after: str | None = None, body_hint: str | None = None) -> Classified:
    hint = f": {body_hint}" if body_hint else ""
    ra = _retry_after(retry_after)
    if 200 <= status < 300:
        return Classified("ok", "OK", "accepted", status)
    if status == 429:
        return Classified(TRANSIENT, "RATE_LIMITED", f"HTTP 429 rate limited{hint}", status, ra)
    if status in (502, 503, 504):
        return Classified(TRANSIENT, "SERVER_ERROR", f"HTTP {status}{hint}", status, ra)
    if status == 500:
        # An engine 500 after accepting the body may or may not have started the workflow.
        return Classified(UNKNOWN, "OUTCOME_UNKNOWN", f"HTTP 500{hint}", status)
    if status in (401, 403):
        return Classified(PERMANENT, "AUTH_FAILED", f"HTTP {status}: authentication or permission refused", status)
    if status == 404:
        return Classified(PERMANENT, "WORKFLOW_NOT_FOUND", f"HTTP 404: no active workflow at this webhook path{hint}", status)
    if status == 409:
        return Classified(PERMANENT, "CONFLICT", f"HTTP 409{hint}", status)
    if status in (400, 413, 415, 422):
        return Classified(PERMANENT, "INVALID_PAYLOAD", f"HTTP {status}{hint}", status)
    if status >= 500:
        return Classified(TRANSIENT, "SERVER_ERROR", f"HTTP {status}{hint}", status, ra)
    return Classified(PERMANENT, "UNSUPPORTED_WORKFLOW", f"HTTP {status}{hint}", status)


def classify_exception(exc: BaseException) -> Classified:
    """httpx errors: connect failures never reached the receiver; read failures may have been processed."""
    name = type(exc).__name__
    if name in ("ConnectError", "ConnectTimeout", "PoolTimeout", "UnsupportedProtocol", "InvalidURL", "LocalProtocolError"):
        return Classified(TRANSIENT, "NETWORK_UNREACHABLE", f"{name}: the request was not delivered")
    if name in ("ReadTimeout", "WriteTimeout", "RemoteProtocolError", "ReadError", "WriteError", "TimeoutException"):
        return Classified(UNKNOWN, "OUTCOME_UNKNOWN", f"{name}: the request may have been processed")
    return Classified(UNKNOWN, "OUTCOME_UNKNOWN", f"{name}")


def kind_of(category: str | None, retryable: bool | None = None) -> str:
    if category in BUSINESS_CATEGORIES:
        return PERMANENT
    if retryable is False:
        return PERMANENT
    if category in UNKNOWN_CATEGORIES:
        return UNKNOWN
    if category in TRANSIENT_CATEGORIES or retryable is True:
        return TRANSIENT
    return PERMANENT


def _retry_after(v: str | None) -> float | None:
    try:
        return max(0.0, float(v)) if v else None
    except ValueError:
        return None


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 5
    base_seconds: float = 15
    factor: float = 2.0
    max_backoff_seconds: float = 900
    max_elapsed_seconds: float = 6 * 3600
    jitter: float = 0.1
    retry_on_workflow_failure: bool = False      # a workflow-reported failure (WORKFLOW_ERROR) is retried only if opted in
    retry_on_timeout: bool = False               # automatic retry after a time-out needs idempotent_dispatch too

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None, defaults: "RetryPolicy | None" = None) -> "RetryPolicy":
        base = defaults or cls()
        d = d or {}
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        merged = {**base.__dict__, **known}
        p = cls(**merged)
        if p.max_attempts < 1 or p.max_attempts > 20 or p.base_seconds < 0 or p.factor < 1 or p.max_backoff_seconds < 0:
            raise ValueError("Retry policy out of range: max_attempts 1-20, base_seconds >= 0, factor >= 1.")
        return p

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    def delay(self, attempt: int, retry_after: float | None = None, rng: random.Random | None = None) -> timedelta:
        """Delay before attempt ``attempt + 1`` (``attempt`` = attempts made so far, >= 1)."""
        raw = min(self.max_backoff_seconds, self.base_seconds * (self.factor ** max(0, attempt - 1)))
        if self.jitter:
            raw *= 1 + (rng or random).uniform(-self.jitter, self.jitter)
        if retry_after is not None:
            raw = max(raw, min(retry_after, self.max_backoff_seconds))
        return timedelta(seconds=max(0.0, raw))

    def may_retry(self, attempts_made: int, first_attempt_at: datetime | None, now: datetime) -> bool:
        if attempts_made >= self.max_attempts:
            return False
        if first_attempt_at is not None and (now - first_attempt_at).total_seconds() > self.max_elapsed_seconds:
            return False
        return True
