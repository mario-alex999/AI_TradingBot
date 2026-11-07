"""Persistence for Module 05: idempotent candidate upserts, status history and point-in-time reads."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, inspect, select, update
from sqlalchemy.engine import Engine

from aurum.database.connection import session_scope
from aurum.database.models import utcnow

from . import lifecycle
from .candidates.setups import Candidate
from .db_models import (BacktestRun, BacktestTrade, CandidateEvidence, CandidateGateResult, CandidateOutcome, CandidateStatusHistory,
                        DecisionRun, TradeCandidate)
from .gates import FAIL, GateResult
from .scoring import Score, grade_of


def _clean(value: Any) -> Any:
    """JSON-safe copy of evidence that may hold datetimes."""
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def to_dict(r: TradeCandidate) -> dict[str, Any]:
    return {c.name: getattr(r, c.name) for c in TradeCandidate.__table__.columns}


class DecisionRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    def schema_ready(self) -> bool:
        names = inspect(self.engine)
        return names.has_table(TradeCandidate.__tablename__) and names.has_table(BacktestRun.__tablename__)

    # --------------------------------------------------------------------------------------- runs
    def save_run(self, **fields: Any) -> str:
        uid = str(uuid.uuid4())
        with session_scope(self.engine) as s:
            s.add(DecisionRun(run_uid=uid, **{**fields, "availability": _clean(fields.get("availability")),
                                              "summary": _clean(fields.get("summary"))}))
        return uid

    def recent_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        with session_scope(self.engine) as s:
            rows = s.execute(select(DecisionRun).order_by(DecisionRun.id.desc()).limit(limit)).scalars().all()
            return [{"symbol": r.symbol, "as_of": r.as_of, "mode": r.mode, "status": r.status, "summary": r.summary,
                     "duration_ms": r.duration_ms, "error": r.error, "created_at": r.created_at} for r in rows]

    def get_run(self, uid: str) -> dict[str, Any] | None:
        with session_scope(self.engine) as s:
            r = s.execute(select(DecisionRun).where(DecisionRun.run_uid == uid)).scalar_one_or_none()
            return None if r is None else {c.name: getattr(r, c.name) for c in DecisionRun.__table__.columns}

    def list_runs(self, symbol: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        q = select(DecisionRun).order_by(DecisionRun.id.desc()).limit(limit)
        if symbol:
            q = q.where(DecisionRun.symbol == symbol)
        with session_scope(self.engine) as s:
            return [{c.name: getattr(r, c.name) for c in DecisionRun.__table__.columns} for r in s.execute(q).scalars()]

    def update_summary(self, uid: str, summary: dict[str, Any], status: str | None = None) -> None:
        with session_scope(self.engine) as s:
            row = s.execute(select(DecisionRun).where(DecisionRun.run_uid == uid)).scalar_one()
            row.summary = _clean(summary)
            if status:
                row.status = status

    # --------------------------------------------------------------------------------------- candidates
    def get(self, uid: str) -> dict[str, Any] | None:
        with session_scope(self.engine) as s:
            row = s.execute(select(TradeCandidate).where(TradeCandidate.candidate_uid == uid)).scalar_one_or_none()
            return None if row is None else to_dict(row)

    def active(self, symbol: str | None = None) -> list[dict[str, Any]]:
        q = select(TradeCandidate).where(TradeCandidate.status.in_(lifecycle.ACTIVE))
        if symbol:
            q = q.where(TradeCandidate.symbol == symbol)
        with session_scope(self.engine) as s:
            return [to_dict(r) for r in s.execute(q).scalars()]

    def listing(self, status: str | None = None, symbol: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        q = select(TradeCandidate).order_by(TradeCandidate.updated_at.desc(), TradeCandidate.id.desc()).limit(limit)
        if status:
            q = q.where(TradeCandidate.status == status)
        if symbol:
            q = q.where(TradeCandidate.symbol == symbol)
        with session_scope(self.engine) as s:
            return [to_dict(r) for r in s.execute(q).scalars()]

    def counts(self) -> dict[str, int]:
        with session_scope(self.engine) as s:
            return {k: v for k, v in s.execute(select(TradeCandidate.status, func.count()).group_by(TradeCandidate.status))}

    def upsert(self, c: Candidate, s_: Score, gates: list[GateResult], volume: float | None, volume_reason: str | None, run_uid: str,
               input_hash: str, availability: dict[str, Any], published_confidence: float, now: datetime) -> dict[str, Any]:
        """Create or refresh a candidate. ``frozen`` in the result means a final decision stands and nothing changed."""
        failed = next((g.gate for g in gates if g.result == FAIL), None)
        values = {"timeframe": c.timeframe, "as_of": c.as_of, "entry_low": c.entry_low, "entry_high": c.entry_high,
                  "stop_loss": c.stop_loss, "take_profit": c.take_profit, "take_profit2": c.take_profit2, "risk_reward": c.risk_reward,
                  "suggested_volume": volume, "volume_reason": volume_reason, "deterministic_score": s_.confidence,
                  "confidence": published_confidence, "grade": s_.grade, "expires_at": c.expires_at, "invalidation": c.invalidation,
                  "market_condition": c.market_condition, "last_seen_run": run_uid, "input_hash": input_hash,
                  "availability": _clean(availability), "updated_at": now}
        with session_scope(self.engine) as s:
            row = s.execute(select(TradeCandidate).where(TradeCandidate.idempotent_key == c.key)).scalar_one_or_none()
            if row is None:
                row = TradeCandidate(candidate_uid=str(uuid.uuid4()), idempotent_key=c.key, symbol=c.symbol, template=c.template,
                                     direction=c.direction, status="new", first_seen_run=run_uid, created_at=now, **values)
                s.add(row)
                s.flush()
                self._history(s, row.candidate_uid, None, "new", "system", None, now)
            elif row.status in lifecycle.ACTIVE or (row.status == "rejected" and (row.rejected_by or "").startswith("gate:")):
                for key, value in values.items():
                    setattr(row, key, value)
            else:
                row.last_seen_run = run_uid
                return {**to_dict(row), "frozen": True}

            if failed and row.status != "rejected":
                self._move(s, row, "rejected", f"gate:{failed}", f"Blocked by the {failed} gate.", now, rejected_by=f"gate:{failed}")
            elif failed:
                row.rejected_by = f"gate:{failed}"
            elif row.status == "rejected":
                self._move(s, row, "new", "system", "The blocking gate cleared.", now, rejected_by=None)
            uid = row.candidate_uid
            s.execute(delete(CandidateEvidence).where(CandidateEvidence.candidate_uid == uid))
            s.execute(delete(CandidateGateResult).where(CandidateGateResult.candidate_uid == uid))
            s.add_all(CandidateEvidence(candidate_uid=uid, component=p.name, points=p.points, max_points=p.max_points,
                                        sentence=p.sentence, sources=_clean(p.sources)) for p in s_.components)
            s.add_all(CandidateGateResult(candidate_uid=uid, gate=g.gate, result=g.result, evidence=_clean(g.evidence),
                                          thresholds=_clean(g.thresholds), checked_at=now) for g in gates)
            return {**to_dict(row), "frozen": False}

    def replace_gates(self, uid: str, gates: list[GateResult], now: datetime) -> None:
        with session_scope(self.engine) as s:
            s.execute(delete(CandidateGateResult).where(CandidateGateResult.candidate_uid == uid))
            s.add_all(CandidateGateResult(candidate_uid=uid, gate=g.gate, result=g.result, evidence=_clean(g.evidence),
                                          thresholds=_clean(g.thresholds), checked_at=now) for g in gates)

    @staticmethod
    def _history(s: Any, uid: str, old: str | None, new: str, actor: str, note: str | None, now: datetime) -> None:
        s.add(CandidateStatusHistory(candidate_uid=uid, from_status=old, to_status=new, actor=actor, note=note, at=now))

    def _move(self, s: Any, row: TradeCandidate, target: str, actor: str, note: str | None, now: datetime, **fields: Any) -> None:
        lifecycle.check(row.status, target)
        self._history(s, row.candidate_uid, row.status, target, actor, note, now)
        row.status = target
        row.updated_at = now
        for key, value in fields.items():
            setattr(row, key, value)

    def move(self, uid: str, target: str, actor: str, note: str | None, now: datetime, **fields: Any) -> dict[str, Any]:
        with session_scope(self.engine) as s:
            row = s.execute(select(TradeCandidate).where(TradeCandidate.candidate_uid == uid)).scalar_one()
            self._move(s, row, target, actor, note, now, **fields)
            return to_dict(row)

    def set_confidence(self, uid: str, confidence: float, cfg: Any, now: datetime) -> None:
        with session_scope(self.engine) as s:
            s.execute(update(TradeCandidate).where(TradeCandidate.candidate_uid == uid)
                      .values(confidence=confidence, grade=grade_of(confidence, cfg), updated_at=now))

    def evidence(self, uid: str) -> list[dict[str, Any]]:
        with session_scope(self.engine) as s:
            rows = s.execute(select(CandidateEvidence).where(CandidateEvidence.candidate_uid == uid).order_by(CandidateEvidence.id)).scalars()
            return [{"component": r.component, "points": r.points, "max_points": r.max_points, "sentence": r.sentence, "sources": r.sources} for r in rows]

    def evidence_sentences(self, uids: list[str]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {u: [] for u in uids}
        if not uids:
            return out
        with session_scope(self.engine) as s:
            for r in s.execute(select(CandidateEvidence).where(CandidateEvidence.candidate_uid.in_(uids)).order_by(CandidateEvidence.id)).scalars():
                out[r.candidate_uid].append(r.sentence)
        return out

    def gates(self, uid: str) -> list[dict[str, Any]]:
        with session_scope(self.engine) as s:
            rows = s.execute(select(CandidateGateResult).where(CandidateGateResult.candidate_uid == uid).order_by(CandidateGateResult.id)).scalars()
            return [{"gate": r.gate, "result": r.result, "evidence": r.evidence, "thresholds": r.thresholds, "checked_at": r.checked_at} for r in rows]

    def history(self, uid: str) -> list[dict[str, Any]]:
        with session_scope(self.engine) as s:
            rows = s.execute(select(CandidateStatusHistory).where(CandidateStatusHistory.candidate_uid == uid).order_by(CandidateStatusHistory.id)).scalars()
            return [{"from": r.from_status, "to": r.to_status, "actor": r.actor, "note": r.note, "at": r.at} for r in rows]

    # --------------------------------------------------------------------------------------- outcomes
    def outcome(self, uid: str) -> dict[str, Any] | None:
        with session_scope(self.engine) as s:
            r = s.get(CandidateOutcome, uid)
            return None if r is None else {"touched": r.touched, "filled": r.filled, "result": r.result, "r_multiple": r.r_multiple,
                                           "mfe_r": r.mfe_r, "mae_r": r.mae_r, "resolved_at": r.resolved_at}

    def unresolved(self) -> list[dict[str, Any]]:
        q = select(TradeCandidate).outerjoin(CandidateOutcome, CandidateOutcome.candidate_uid == TradeCandidate.candidate_uid).where(
            (CandidateOutcome.candidate_uid.is_(None)) | (CandidateOutcome.result == "open"))
        with session_scope(self.engine) as s:
            return [to_dict(r) for r in s.execute(q).scalars()]

    def save_outcome(self, uid: str, **fields: Any) -> None:
        with session_scope(self.engine) as s:
            row = s.get(CandidateOutcome, uid)
            if row is None:
                s.add(CandidateOutcome(candidate_uid=uid, **fields))
            else:
                for key, value in fields.items():
                    setattr(row, key, value)
                row.updated_at = utcnow()

    def resolved(self, symbol: str | None = None, template: str | None = None) -> list[dict[str, Any]]:
        """Filled and finished candidates that were not blocked by a gate."""
        q = select(TradeCandidate.template, TradeCandidate.symbol, TradeCandidate.deterministic_score, CandidateOutcome.r_multiple,
                   CandidateOutcome.result).join(CandidateOutcome, CandidateOutcome.candidate_uid == TradeCandidate.candidate_uid).where(
            CandidateOutcome.filled.is_(True), CandidateOutcome.result != "open",
            (TradeCandidate.rejected_by.is_(None)) | (~TradeCandidate.rejected_by.like("gate:%")))
        if symbol:
            q = q.where(TradeCandidate.symbol == symbol)
        if template:
            q = q.where(TradeCandidate.template == template)
        with session_scope(self.engine) as s:
            return [{"template": t, "symbol": sym, "confidence": conf, "r_multiple": r, "result": res} for t, sym, conf, r, res in s.execute(q)]

    # --------------------------------------------------------------------------------------- backtests
    def save_backtest(self, config_fingerprint: str, symbols: list[str], start: datetime, end: datetime, split: datetime | None,
                      report: dict[str, Any], trades: list[Any]) -> str:
        uid = str(uuid.uuid4())
        with session_scope(self.engine) as s:
            s.add(BacktestRun(run_uid=uid, config_fingerprint=config_fingerprint, symbols=symbols, period_start=start, period_end=end,
                              split_date=split, metrics=_clean(report)))
            s.flush()
            s.add_all(BacktestTrade(run_uid=uid, symbol=t.symbol, template=t.template, timeframe=t.timeframe, direction=t.direction,
                                    as_of=t.as_of, session=t.session, confidence=t.confidence, result=t.result, r_multiple=t.r_multiple,
                                    resolved_at=t.resolved_at) for t in trades)
        return uid

    def list_backtests(self, limit: int = 20) -> list[dict[str, Any]]:
        with session_scope(self.engine) as s:
            rows = s.execute(select(BacktestRun).order_by(BacktestRun.id.desc()).limit(limit)).scalars()
            return [{"id": r.run_uid, "symbols": r.symbols, "from": r.period_start, "to": r.period_end, "created_at": r.created_at,
                     "metrics": r.metrics["metrics"]} for r in rows]

    def get_backtest(self, uid: str) -> dict[str, Any] | None:
        with session_scope(self.engine) as s:
            r = s.execute(select(BacktestRun).where(BacktestRun.run_uid == uid)).scalar_one_or_none()
            return None if r is None else {**r.metrics, "run": {"id": r.run_uid, "symbols": r.symbols, "from": r.period_start, "to": r.period_end, "created_at": r.created_at}}

    def latest_backtest(self) -> dict[str, Any] | None:
        with session_scope(self.engine) as s:
            r = s.execute(select(BacktestRun).order_by(BacktestRun.id.desc()).limit(1)).scalars().first()
            if r is None:
                return None
            return {**r.metrics, "run": {"id": r.run_uid, "symbols": r.symbols, "from": r.period_start, "to": r.period_end, "created_at": r.created_at}}
