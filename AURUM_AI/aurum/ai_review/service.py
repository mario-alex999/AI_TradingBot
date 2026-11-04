"""Runs AI reviews: checks the switches, reserves budget, calls Claude, records the outcome and settles the cost."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, inspect, select, update
from sqlalchemy.engine import Engine

from aurum.config import Settings
from aurum.database.connection import session_scope
from aurum.database.models import utcnow

from . import budget
from .db_models import AiReview
from .prompt import OUTPUT_SCHEMA, SYSTEM_PROMPT, Verdict, build_context, prompt_chars

log = logging.getLogger(__name__)
DISCLAIMER = "AI-written research on stored data for a demo account. Advisory only: AURUM places no orders."


class AiReviewError(RuntimeError):
    """Base class. ``str(exc)`` is safe to show to the user."""


class AiReviewUnavailable(AiReviewError):
    """The feature is off or incomplete. Nothing was sent or spent."""


class NoMarketData(AiReviewError):
    """There is nothing to review. Nothing was sent or spent."""


class BudgetExceeded(AiReviewError):
    """The worst-case cost does not fit in what is left of this month's budget. Nothing was sent or spent."""


class ReviewFailed(AiReviewError):
    """The call was attempted and produced no usable review. ``review`` is the stored failed row."""

    def __init__(self, message: str, review: dict[str, Any]):
        super().__init__(message)
        self.review = review


def _utc(t: datetime | None) -> datetime:
    return datetime.now(timezone.utc) if t is None else (t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t)


class AiReviewService:
    def __init__(self, settings: Settings, engine: Engine, intelligence: Any = None, analysis: Any = None,
                 client: Any = None):
        self.settings, self.engine = settings, engine
        self._intelligence, self._analysis, self._client = intelligence, analysis, client

    # ------------------------------------------------------------------------------------------- readiness
    def unavailable_reason(self) -> str | None:
        """Why a review cannot run right now, or None when it can."""
        s = self.settings
        if not s.ai_review_enabled:
            return "AI review is off. Set AI_REVIEW_ENABLED=true in .env to turn it on."
        if s.anthropic_api_key is None or not s.anthropic_api_key.get_secret_value():
            return "ANTHROPIC_API_KEY is not set in .env."
        if s.ai_review_monthly_budget_usd is None:
            return "AI_REVIEW_MONTHLY_BUDGET_USD is not set in .env. A budget is required."
        if s.ai_review_model not in budget.PRICES:
            return (f"AI_REVIEW_MODEL '{s.ai_review_model}' has no known price, so its cost cannot be capped. "
                    f"Supported: {', '.join(sorted(budget.PRICES))}.")
        if not self._schema_ready():
            return "AI review tables are missing. Run `python -m aurum.cli init-db`."
        return None

    def _schema_ready(self) -> bool:
        return inspect(self.engine).has_table(AiReview.__tablename__)

    def status(self) -> dict[str, Any]:
        s = self.settings
        reason = self.unavailable_reason()
        month = budget.month_key()
        schema = self._schema_ready()
        committed = budget.committed_micro(self.engine, month) if schema else 0
        cap = None if s.ai_review_monthly_budget_usd is None else budget.usd_to_micro(s.ai_review_monthly_budget_usd)
        count = 0
        if schema:
            with session_scope(self.engine) as db:
                count = db.execute(select(func.count()).select_from(AiReview).where(
                    AiReview.budget_month == month, AiReview.status == "completed")).scalar_one()
        return {"enabled": s.ai_review_enabled, "ready": reason is None, "reason": reason,
                "api_key_configured": bool(s.anthropic_api_key and s.anthropic_api_key.get_secret_value()),
                "model": s.ai_review_model, "effort": s.ai_review_effort, "max_tokens": s.ai_review_max_tokens,
                "month": month, "budget_usd": s.ai_review_monthly_budget_usd,
                "committed_usd": budget.micro_to_usd(committed),
                "remaining_usd": None if cap is None else budget.micro_to_usd(max(cap - committed, 0)),
                "completed_reviews_this_month": count}

    # ------------------------------------------------------------------------------------------- inputs
    def _sources(self) -> tuple[Any, Any]:
        if self._intelligence is None:
            from aurum.market_intelligence.service import MarketIntelligenceService
            self._intelligence = MarketIntelligenceService(self.settings, self.engine)
        if self._analysis is None:
            from aurum.analysis.services.analysis_service import TechnicalAnalysisService
            self._analysis = TechnicalAnalysisService(self.settings, self.engine)
        return self._intelligence, self._analysis

    def gather(self, symbol: str, as_of: datetime | None = None) -> tuple[str, datetime, str]:
        """(symbol, as_of, context JSON). Local work only: no API call and no cost."""
        symbol, t = symbol.upper(), _utc(as_of)
        intelligence, analysis = self._sources()
        report = intelligence.analyze(symbol, t)
        if not (report.get("integrations", {}).get("module_01") or {}).get("available"):
            raise NoMarketData(f"No stored candles for {symbol}. Run `python -m aurum.cli collect` first.")
        return symbol, t, build_context(symbol, t, report, analysis.analyze_confluence(symbol, as_of=t).to_dict())

    def estimate(self, symbol: str, as_of: datetime | None = None) -> dict[str, Any]:
        """Dry run: what a review would send and reserve. Makes no API call and needs no key."""
        symbol, t, context = self.gather(symbol, as_of)
        model = self.settings.ai_review_model
        worst = (budget.worst_case_micro(model, prompt_chars(context), self.settings.ai_review_max_tokens)
                 if model in budget.PRICES else None)
        return {"symbol": symbol, "as_of": t, "context_chars": len(context), "model": model,
                "worst_case_usd": None if worst is None else budget.micro_to_usd(worst)}

    # ------------------------------------------------------------------------------------------- run
    def run(self, symbol: str, as_of: datetime | None = None) -> dict[str, Any]:
        reason = self.unavailable_reason()
        if reason:
            raise AiReviewUnavailable(reason)
        s = self.settings
        symbol, t, context = self.gather(symbol, as_of)
        reserved = budget.worst_case_micro(s.ai_review_model, prompt_chars(context), s.ai_review_max_tokens)
        uid, month = self._reserve(symbol, t, len(context), reserved)
        return self._execute(uid, month, reserved, context)

    def _reserve(self, symbol: str, as_of: datetime, context_chars: int, reserved: int) -> tuple[str, str]:
        s = self.settings
        month, uid = budget.month_key(), str(uuid.uuid4())
        cap = budget.usd_to_micro(s.ai_review_monthly_budget_usd)
        budget.ensure_month(self.engine, month)
        with session_scope(self.engine) as db:     # the reservation and its pending row commit together
            if budget.try_reserve(db, month, reserved, cap):
                db.add(AiReview(review_uid=uid, budget_month=month, symbol=symbol, as_of=as_of, status="pending",
                                model=s.ai_review_model, effort=s.ai_review_effort, context_chars=context_chars,
                                reserved_microusd=reserved, cost_microusd=reserved, cost_is_estimate=True))
                return uid, month
        left = max(cap - budget.committed_micro(self.engine, month), 0)
        raise BudgetExceeded(f"This review could cost up to ${budget.micro_to_usd(reserved):.4f}, but only "
                             f"${budget.micro_to_usd(left):.4f} of the ${s.ai_review_monthly_budget_usd:.2f} budget for "
                             f"{month} is left. No API call was made.")

    def _execute(self, uid: str, month: str, reserved: int, context: str) -> dict[str, Any]:
        import anthropic
        s = self.settings
        try:
            client = self._client or anthropic.Anthropic(api_key=s.anthropic_api_key.get_secret_value(),
                                                         timeout=s.ai_review_timeout_seconds,
                                                         max_retries=0)  # a timed-out attempt may be billed: no silent repeats
            message = client.messages.create(
                model=s.ai_review_model, max_tokens=s.ai_review_max_tokens, system=SYSTEM_PROMPT,
                thinking={"type": "adaptive"},
                output_config={"effort": s.ai_review_effort, "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
                messages=[{"role": "user", "content": context}])
        except anthropic.APIConnectionError as exc:   # includes timeouts: the request may have been billed
            self._fail(uid, month, reserved, reserved, "connection",
                       f"Could not reach the Anthropic API ({type(exc).__name__}).")
        except anthropic.AuthenticationError:
            self._fail(uid, month, reserved, 0, "auth", "The Anthropic API rejected ANTHROPIC_API_KEY.")
        except anthropic.RateLimitError:
            self._fail(uid, month, reserved, 0, "rate_limited", "The Anthropic API is rate limiting this key. Try again later.")
        except anthropic.APIStatusError as exc:
            self._fail(uid, month, reserved, 0, "api_error",
                       f"Anthropic API error {exc.status_code}: {self._clean(exc.message)}")
        except Exception as exc:  # noqa: BLE001 - keep the reservation: the outcome of the call is unknown
            log.exception("AI review call failed", extra={"review_uid": uid})
            self._fail(uid, month, reserved, reserved, "unexpected", f"Unexpected error ({type(exc).__name__}).")

        usage = message.usage
        cost = budget.token_cost_micro(s.ai_review_model, usage.input_tokens, usage.output_tokens,
                                       usage.cache_creation_input_tokens or 0, usage.cache_read_input_tokens or 0)
        tokens = {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens}
        if message.stop_reason == "refusal":
            self._fail(uid, month, reserved, cost, "refused", "The model declined this request.", exact=True, **tokens)
        if message.stop_reason == "max_tokens":
            self._fail(uid, month, reserved, cost, "truncated", "The answer hit AI_REVIEW_MAX_TOKENS before it finished.",
                       exact=True, **tokens)
        text = "".join(b.text for b in message.content if b.type == "text")
        try:
            verdict = Verdict.model_validate_json(text).model_dump()
        except ValidationError:
            self._fail(uid, month, reserved, cost, "invalid_output", "The answer did not match the expected format.",
                       exact=True, **tokens)
        return self._settle(uid, month, reserved, cost, status="completed", verdict=verdict, cost_is_estimate=False, **tokens)

    def _clean(self, text: str) -> str:
        return text.replace(self.settings.anthropic_api_key.get_secret_value(), "***")[:300]

    def _fail(self, uid: str, month: str, reserved: int, cost: int, code: str, message: str, exact: bool = False,
              **tokens: Any) -> None:
        row = self._settle(uid, month, reserved, cost, status="failed", error_code=code, error_message=message,
                           cost_is_estimate=not exact and cost > 0, **tokens)
        log.warning("AI review failed", extra={"review_uid": uid, "error_code": code})
        raise ReviewFailed(message, row)

    def _settle(self, uid: str, month: str, reserved: int, cost: int, **fields: Any) -> dict[str, Any]:
        with session_scope(self.engine) as db:
            budget.adjust(db, month, cost - reserved)
            db.execute(update(AiReview).where(AiReview.review_uid == uid)
                       .values(cost_microusd=cost, completed_at=utcnow(), **fields))
            return self._to_dict(db.execute(select(AiReview).where(AiReview.review_uid == uid)).scalar_one())

    # ------------------------------------------------------------------------------------------- history
    def history(self, symbol: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        if not self._schema_ready():
            return []
        q = select(AiReview).order_by(AiReview.created_at.desc(), AiReview.id.desc()).limit(limit)
        if symbol:
            q = q.where(AiReview.symbol == symbol.upper())
        with session_scope(self.engine) as db:
            return [self._to_dict(r) for r in db.execute(q).scalars()]

    @staticmethod
    def _to_dict(r: AiReview) -> dict[str, Any]:
        return {"review_uid": r.review_uid, "symbol": r.symbol, "as_of": r.as_of, "status": r.status, "model": r.model,
                "effort": r.effort, "verdict": r.verdict, "error_code": r.error_code, "error_message": r.error_message,
                "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
                "cost_usd": budget.micro_to_usd(r.cost_microusd), "cost_is_estimate": r.cost_is_estimate,
                "created_at": r.created_at, "completed_at": r.completed_at, "disclaimer": DISCLAIMER}
