"""Persistence of analysis runs, structure events and zones. Idempotent upserts (re-running an analysis
updates zone states instead of duplicating rows)."""
from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import bindparam, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

from aurum.analysis.models.analysis_result import ConfluenceReport, TimeframeAnalysis
from aurum.analysis.services.db_models import TaAnalysisRun, TaStructureEvent, TaZone
from aurum.database.connection import session_scope
from aurum.database.models import utcnow


def _json(d: Any) -> Any:
    return json.loads(json.dumps(d, default=str))


class AnalysisRepository:
    def __init__(self, engine: Engine):
        self.engine = engine
        self.dialect = engine.dialect.name

    def _insert(self, table):
        return pg_insert(table) if self.dialect == "postgresql" else sqlite_insert(table)

    def schema_ready(self) -> bool:
        from sqlalchemy import inspect
        return inspect(self.engine).has_table("ta_analysis_runs")

    def save_timeframe(self, a: TimeframeAnalysis) -> str:
        uid = str(uuid.uuid4())
        now = utcnow()
        summary = {"structure": {k: v.to_dict() for k, v in a.structure.items()},
                   "score": a.score.to_dict() if a.score else None,
                   "premium_discount": a.premium_discount.to_dict() if a.premium_discount else None,
                   "ote": a.ote.to_dict() if a.ote else None, "data_quality": a.data_quality, "messages": a.messages}
        with session_scope(self.engine) as s:
            s.add(TaAnalysisRun(run_uid=uid, run_type="timeframe", symbol=a.symbol, timeframe=a.timeframe,
                                as_of=a.as_of, generated_at=a.generated_at, engine_version=a.engine_version,
                                config_fingerprint=a.config_fingerprint, provisional=a.provisional,
                                candles_used=a.candles_used, status=a.status,
                                bias=a.score.bias if a.score else None, score=a.score.score if a.score else None,
                                grade=a.score.grade if a.score else None, summary=_json(summary)))
            for e in a.structure_events:
                stmt = self._insert(TaStructureEvent).values(
                    symbol=a.symbol, timeframe=a.timeframe, scope=e.scope, event_type=e.event_type,
                    direction=e.direction, broken_level=e.broken_level, broken_swing_time=e.broken_swing_time,
                    break_candle_time=e.break_candle_time, confirmed_at=e.confirmed_at, confirmed=e.confirmed,
                    displacement_strength=e.displacement_strength, detail=_json(e.to_dict()), first_run_uid=uid,
                    last_run_uid=uid, updated_at=now)
                s.execute(stmt.on_conflict_do_update(
                    index_elements=["symbol", "timeframe", "scope", "event_type", "direction", "break_candle_time"],
                    set_={"last_run_uid": uid, "confirmed": stmt.excluded.confirmed, "updated_at": now,
                          "detail": stmt.excluded.detail},
                    where=self._not_older(TaStructureEvent, a.as_of)))
            for kind, zid, d, top, bottom, state, det, obj in self._zones(a):
                stmt = self._insert(TaZone).values(zone_uid=zid, symbol=a.symbol, timeframe=a.timeframe, kind=kind,
                                                   direction=d, top=top, bottom=bottom, state=state, detected_at=det,
                                                   detail=_json(obj), first_run_uid=uid, last_run_uid=uid,
                                                   updated_at=now)
                s.execute(stmt.on_conflict_do_update(
                    index_elements=["symbol", "timeframe", "kind", "zone_uid"],
                    set_={"top": stmt.excluded.top, "bottom": stmt.excluded.bottom, "state": stmt.excluded.state,
                          "detail": stmt.excluded.detail, "last_run_uid": uid, "updated_at": now},
                    where=self._not_older(TaZone, a.as_of)))
        return uid

    @staticmethod
    def _not_older(table, as_of):
        """Upsert guard: a stored row is only overwritten by a run whose as_of is not older than the as_of of the run
        that last wrote it. Without it, re-analysing an earlier point in time (historical recalculation) after a
        later one rolled zone states back, e.g. a filled FVG reverted to 'unfilled'. The historical run itself is
        still recorded in ta_analysis_runs (with its full summary); only the 'latest known state' is protected."""
        # Written as SQL so the subquery refers to the *conflicting row* (<table>.last_run_uid). SQLAlchemy does not
        # correlate a subquery inside INSERT ... ON CONFLICT; uncorrelated, it returns many rows (PostgreSQL rejects
        # it, SQLite silently takes the first). The bound value keeps the column type so both dialects compare alike.
        name = table.__tablename__
        last = f"(SELECT r.as_of FROM ta_analysis_runs AS r WHERE r.run_uid = {name}.last_run_uid)"
        return text(f"{last} IS NULL OR {last} <= :guard_as_of").bindparams(
            bindparam("guard_as_of", as_of, type_=TaAnalysisRun.__table__.c.as_of.type))

    @staticmethod
    def _zones(a: TimeframeAnalysis):
        for z in a.order_blocks:
            yield "order_block", z.id, z.direction, z.current_top, z.current_bottom, z.state, z.detected_at, z.to_dict()
        for g in a.fair_value_gaps:
            yield "fair_value_gap", g.id, g.direction, g.current_upper, g.current_lower, g.state, g.detected_at, g.to_dict()
        for b in a.balanced_price_ranges:
            yield "balanced_price_range", b.id, b.direction, b.upper, b.lower, b.state, b.detected_at, b.to_dict()
        for p in a.liquidity_pools:
            yield "liquidity_pool", p.id, p.side, p.level, p.level, p.state, p.created_at, p.to_dict()
        for z in a.support_resistance:
            yield "support_resistance", z.id, z.role, z.top, z.bottom, "active", z.last_time, z.to_dict()

    def save_confluence(self, r: ConfluenceReport) -> str:
        uid = str(uuid.uuid4())
        with session_scope(self.engine) as s:
            s.add(TaAnalysisRun(run_uid=uid, run_type="confluence", symbol=r.symbol, timeframe=",".join(r.timeframes),
                                as_of=r.as_of, generated_at=r.generated_at, engine_version=_engine_version(),
                                config_fingerprint="-", provisional=any(v["provisional"] for v in r.per_timeframe.values()),
                                candles_used=None, status="ok", bias=r.score.bias, score=r.score.score,
                                grade=r.score.grade, summary=_json({"alignment": r.alignment, "findings": r.findings,
                                                                    "warnings": r.warnings, "cot": r.cot})))
        return uid

    def runs(self, symbol: str | None = None, timeframe: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        q = select(TaAnalysisRun).order_by(TaAnalysisRun.id.desc()).limit(limit)
        if symbol:
            q = q.where(TaAnalysisRun.symbol == symbol)
        if timeframe:
            q = q.where(TaAnalysisRun.timeframe == timeframe)
        cols = [c.name for c in TaAnalysisRun.__table__.columns if c.name != "summary"]
        with session_scope(self.engine) as s:
            return [{c: getattr(r, c) for c in cols} for r in s.execute(q).scalars()]

    def zones(self, symbol: str, timeframe: str, kind: str | None = None, active_only: bool = False) -> list[dict]:
        q = select(TaZone).where(TaZone.symbol == symbol, TaZone.timeframe == timeframe).order_by(TaZone.detected_at)
        if kind:
            q = q.where(TaZone.kind == kind)
        if active_only:
            q = q.where(TaZone.state.in_(["fresh", "partially_mitigated", "unfilled", "partially_filled", "active"]))
        cols = [c.name for c in TaZone.__table__.columns]
        with session_scope(self.engine) as s:
            return [{c: getattr(z, c) for c in cols} for z in s.execute(q).scalars()]


def _engine_version() -> str:
    from aurum.analysis.config import ENGINE_VERSION
    return ENGINE_VERSION
