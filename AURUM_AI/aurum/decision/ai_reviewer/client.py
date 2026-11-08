"""Calls Claude for one candidate. Never raises for an AI problem: every failure comes back as ``unavailable`` with a
distinct reason, because a candidate must still be published without its review (fail safe).

Spending shares the monthly ledger of aurum/ai_review, so AI_REVIEW_MONTHLY_BUDGET_USD covers both features. There are no
automatic retries: a timed-out attempt may have been billed. A failed review is retried by the next engine run, within
the hourly call limit.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from pydantic import ValidationError
from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from aurum.ai_review import budget
from aurum.ai_review.db_models import AiReviewBudgetMonth
from aurum.config import Settings
from aurum.database.connection import session_scope
from aurum.decision.config import DecisionConfig
from aurum.decision.db_models import CandidateAiReview

from .audit import ReviewAudit, payload_hash, scrub
from .prompt import SYSTEM_PROMPT, render
from .providers import OpenAiCompatible, ProviderError
from .schema import OUTPUT_SCHEMA, Review, clamp_adjustment, unsupported_numbers

log = logging.getLogger(__name__)


class AiReviewer:
    def __init__(self, settings: Settings, engine: Engine, cfg: DecisionConfig, client: Any = None):
        self.settings, self.engine, self.cfg, self._client = settings, engine, cfg, client
        self.audit = ReviewAudit(engine)

    @property
    def prompt_version(self) -> str:
        return str(self.cfg.ai_review["prompt_version"])

    def blocked_reason(self) -> str | None:
        """Why no call can be made at all, or None."""
        s = self.settings
        if not s.ai_review_enabled:
            return "disabled"
        if s.ai_provider != "anthropic":
            if s.ai_provider == "openai" and not s.openai_api_key:
                return "no_api_key"
            if s.ai_provider == "local" and not s.ai_provider_base_url:
                return "no_base_url"
            if not s.ai_provider_model:
                return "no_model"
            return "tables_missing" if not inspect(self.engine).has_table(CandidateAiReview.__tablename__) else None
        if s.anthropic_api_key is None or not s.anthropic_api_key.get_secret_value():
            return "no_api_key"
        if s.ai_review_monthly_budget_usd is None:
            return "no_budget"
        if s.ai_review_model not in budget.PRICES:
            return "model_not_priced"
        names = inspect(self.engine)
        if not (names.has_table(AiReviewBudgetMonth.__tablename__) and names.has_table(CandidateAiReview.__tablename__)):
            return "tables_missing"
        return None

    def state(self) -> dict[str, Any]:
        s = self.settings
        reason = self.blocked_reason()
        ready_tables = reason != "tables_missing" and inspect(self.engine).has_table(AiReviewBudgetMonth.__tablename__)
        used = budget.committed_micro(self.engine, budget.month_key()) if ready_tables else 0
        return {"enabled": s.ai_review_enabled, "blocked_reason": reason, "provider": s.ai_provider,
                "model": s.ai_review_model if s.ai_provider == "anthropic" else s.ai_provider_model,
                "budget_enforced": s.ai_provider == "anthropic",
                "budget_usd": s.ai_review_monthly_budget_usd, "budget_used_usd": budget.micro_to_usd(used),
                "max_calls_per_hour": s.ai_review_max_calls_per_hour,
                "last_error": self.audit.last_error() if inspect(self.engine).has_table(CandidateAiReview.__tablename__) else None}

    def review(self, candidate_uid: str, input_hash: str, payload: dict[str, Any]) -> dict[str, Any]:
        version = self.prompt_version
        reason = self.blocked_reason()
        if reason in ("disabled", "no_api_key", "no_budget", "model_not_priced", "no_base_url", "no_model"):
            return self._not_called(candidate_uid, input_hash, version, reason)
        if reason == "tables_missing":
            return {"status": "unavailable", "reason": reason}
        cached = self.audit.reusable(candidate_uid, input_hash, version)
        if cached:
            return {**cached, "cached": True}
        if self.audit.calls_in_last_hour() >= self.settings.ai_review_max_calls_per_hour:
            return self._not_called(candidate_uid, input_hash, version, "hourly_limit")
        if self.settings.ai_provider != "anthropic":
            return self._call_other(candidate_uid, input_hash, version, render(payload))
        return self._call(candidate_uid, input_hash, version, render(payload))

    def _accept(self, text: str, prompt: str) -> tuple[Review | None, str | None]:
        try:
            review = Review.model_validate_json(text)
        except ValidationError:
            return None, "invalid_output"
        if unsupported_numbers(review.model_dump(), prompt):
            return None, "unsupported_claim"
        return review, None

    def _call_other(self, uid: str, input_hash: str, version: str, prompt: str) -> dict[str, Any]:
        """OpenAI or a local server. There is no price table for these, so the monthly budget does not cover them: only the hourly
        limit applies, and ``state`` says so. The provider stays off until it is chosen in .env."""
        s = self.settings
        secrets = [s.openai_api_key.get_secret_value()] if s.openai_api_key else []
        fields = {"candidate_uid": uid, "input_hash": input_hash, "prompt_version": version, "model": s.ai_provider_model,
                  "payload_hash": payload_hash(prompt), "api_called": True, "cost_microusd": 0}
        started = time.monotonic()
        try:
            provider = self._client if self._client is not None else OpenAiCompatible(s, int(self.cfg.ai_review["max_retries"]))
            reply = provider.complete(SYSTEM_PROMPT, prompt, OUTPUT_SCHEMA)
        except ProviderError as exc:
            return self.audit.record(**fields, status="unavailable", reason=exc.reason)
        latency = int((time.monotonic() - started) * 1000)
        used = {"input_tokens": reply.input_tokens, "output_tokens": reply.output_tokens, "latency_ms": latency}
        review, problem = self._accept(reply.text, prompt)
        if problem:
            return self.audit.record(**fields, status="unavailable", reason=problem, **used)
        stored = scrub(review.model_dump(), secrets)
        adjustment = clamp_adjustment(review.confidence_adjustment)
        stored["confidence_adjustment"] = adjustment
        return self.audit.record(**fields, status="reviewed", reason=None, verdict=review.verdict, adjustment=adjustment, review=stored, **used)

    def _not_called(self, uid: str, input_hash: str, version: str, reason: str) -> dict[str, Any]:
        return self.audit.record_unavailable_once(uid, input_hash, version, reason)

    def _client_for_call(self) -> Any:
        if self._client is not None:
            return self._client
        import anthropic
        s = self.settings
        return anthropic.Anthropic(api_key=s.anthropic_api_key.get_secret_value(), timeout=s.ai_review_timeout_seconds,
                                   max_retries=int(self.cfg.ai_review["max_retries"]))

    def _call(self, uid: str, input_hash: str, version: str, prompt: str) -> dict[str, Any]:
        s = self.settings
        model = s.ai_review_model
        chars = len(SYSTEM_PROMPT) + len(str(OUTPUT_SCHEMA)) + len(prompt)
        reserved = budget.worst_case_micro(model, chars, s.ai_review_max_tokens)
        month = budget.month_key()
        budget.ensure_month(self.engine, month)
        with session_scope(self.engine) as db:
            ok = budget.try_reserve(db, month, reserved, budget.usd_to_micro(s.ai_review_monthly_budget_usd))
        if not ok:
            return self._not_called(uid, input_hash, version, "over_budget")

        audit_fields = {"candidate_uid": uid, "input_hash": input_hash, "prompt_version": version, "model": model,
                        "payload_hash": payload_hash(prompt), "budget_month": month, "api_called": True}

        def settle(cost: int, reason: str | None = None, **extra: Any) -> dict[str, Any]:
            with session_scope(self.engine) as db:
                budget.adjust(db, month, cost - reserved)
            status = "reviewed" if reason is None else "unavailable"
            log.info("AI review finished", extra={"candidate": uid, "status": status, "reason": reason})
            return self.audit.record(**audit_fields, status=status, reason=reason, cost_microusd=cost, **extra)

        started = time.monotonic()
        try:
            client = self._client_for_call()
        except ImportError:
            return settle(0, "sdk_missing")
        import anthropic
        try:
            message = client.messages.create(
                model=model, max_tokens=s.ai_review_max_tokens, system=SYSTEM_PROMPT, thinking={"type": "adaptive"},
                output_config={"effort": s.ai_review_effort, "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
                messages=[{"role": "user", "content": prompt}])
        except anthropic.APITimeoutError:      # may have been billed: the reservation stands
            return settle(reserved, "timeout")
        except anthropic.APIConnectionError:   # may have been billed: the reservation stands
            return settle(reserved, "connection_error")
        except anthropic.AuthenticationError:
            return settle(0, "auth_error")
        except anthropic.RateLimitError:
            return settle(0, "rate_limited")
        except anthropic.APIStatusError as exc:
            return settle(0, f"api_status_{exc.status_code}")
        except Exception as exc:  # noqa: BLE001 - keep the reservation: the outcome of the call is unknown
            log.exception("AI review call failed", extra={"candidate": uid, "error": type(exc).__name__})
            return settle(reserved, "unexpected_error")

        latency = int((time.monotonic() - started) * 1000)
        usage = message.usage
        cost = budget.token_cost_micro(model, usage.input_tokens, usage.output_tokens, usage.cache_creation_input_tokens or 0,
                                       usage.cache_read_input_tokens or 0)
        used = {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens, "latency_ms": latency}
        if message.stop_reason == "refusal":
            return settle(cost, "refused", **used)
        if message.stop_reason == "max_tokens":
            return settle(cost, "truncated", **used)
        text = "".join(b.text for b in message.content if b.type == "text")
        review, problem = self._accept(text, prompt)
        if problem:
            return settle(cost, problem, **used)
        secret = s.anthropic_api_key.get_secret_value()
        stored = scrub(review.model_dump(), [secret])
        adjustment = clamp_adjustment(review.confidence_adjustment)
        stored["confidence_adjustment"] = adjustment
        return settle(cost, verdict=review.verdict, adjustment=adjustment, review=stored, **used)
