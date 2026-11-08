"""Audit trail for the reviewer: every API call is stored with its hash, usage and outcome, with secrets scrubbed."""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from aurum.database.connection import session_scope
from aurum.database.models import utcnow
from aurum.decision.db_models import CandidateAiReview


def scrub(value: Any, secrets: list[str]) -> Any:
    """A deep copy of ``value`` with every secret replaced."""
    text = json.dumps(value, default=str)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return json.loads(text)


def payload_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode()).hexdigest()


class ReviewAudit:
    def __init__(self, engine: Engine):
        self.engine = engine

    def reusable(self, candidate_uid: str, input_hash: str, prompt_version: str) -> dict[str, Any] | None:
        """A stored review of the same candidate, inputs and prompt. Failures are not reused, so they can be retried."""
        with session_scope(self.engine) as s:
            row = s.execute(select(CandidateAiReview).where(
                CandidateAiReview.candidate_uid == candidate_uid, CandidateAiReview.input_hash == input_hash,
                CandidateAiReview.prompt_version == prompt_version, CandidateAiReview.status == "reviewed")
                .order_by(CandidateAiReview.id.desc())).scalars().first()
            return None if row is None else to_dict(row)

    def calls_in_last_hour(self) -> int:
        since = utcnow() - timedelta(hours=1)
        with session_scope(self.engine) as s:
            return s.execute(select(func.count()).select_from(CandidateAiReview).where(
                CandidateAiReview.api_called.is_(True), CandidateAiReview.created_at >= since)).scalar_one()

    def record(self, **fields: Any) -> dict[str, Any]:
        with session_scope(self.engine) as s:
            row = CandidateAiReview(**fields)
            s.add(row)
            s.flush()
            return to_dict(row)

    def record_unavailable_once(self, candidate_uid: str, input_hash: str, prompt_version: str, reason: str) -> dict[str, Any]:
        """Runs repeat every few minutes: one row per distinct reason is enough when no call was made."""
        with session_scope(self.engine) as s:
            row = s.execute(select(CandidateAiReview).where(
                CandidateAiReview.candidate_uid == candidate_uid, CandidateAiReview.input_hash == input_hash,
                CandidateAiReview.prompt_version == prompt_version, CandidateAiReview.reason == reason,
                CandidateAiReview.api_called.is_(False))).scalars().first()
            if row is None:
                row = CandidateAiReview(candidate_uid=candidate_uid, input_hash=input_hash, prompt_version=prompt_version,
                                        status="unavailable", reason=reason, api_called=False, cost_microusd=0)
                s.add(row)
                s.flush()
            return to_dict(row)

    def latest(self, candidate_uid: str) -> dict[str, Any] | None:
        with session_scope(self.engine) as s:
            row = s.execute(select(CandidateAiReview).where(CandidateAiReview.candidate_uid == candidate_uid)
                            .order_by(CandidateAiReview.id.desc())).scalars().first()
            return None if row is None else to_dict(row)

    def last_error(self) -> dict[str, Any] | None:
        with session_scope(self.engine) as s:
            row = s.execute(select(CandidateAiReview).where(CandidateAiReview.status == "unavailable", CandidateAiReview.api_called.is_(True))
                            .order_by(CandidateAiReview.id.desc())).scalars().first()
            return None if row is None else {"reason": row.reason, "at": row.created_at}


def to_dict(r: CandidateAiReview) -> dict[str, Any]:
    return {"status": r.status, "reason": r.reason, "verdict": r.verdict, "adjustment": r.adjustment, "review": r.review,
            "model": r.model, "prompt_version": r.prompt_version, "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
            "latency_ms": r.latency_ms, "cost_microusd": r.cost_microusd, "created_at": r.created_at}
