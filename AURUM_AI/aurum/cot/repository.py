"""Persistence for Module 02. Upserts are idempotent; source payload changes are preserved as revisions."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any, Iterable

import pandas as pd
from sqlalchemy import and_, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from aurum.cot.config import CotConfig
from aurum.cot.fields import REPORTABLE
from aurum.cot.models import (CotContractMapping, CotDataQualityIssue, CotDataset, CotIngestionLog, CotMetric,
                              CotPosition, CotReport, CotReportRevision)
from aurum.cot.parsers import ParsedReport
from aurum.cot.release import ReleaseInfo
from aurum.database.connection import session_scope
from aurum.database.models import utcnow


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CotRepository:
    def __init__(self, engine: Engine):
        self.engine = engine
        self.dialect = engine.dialect.name

    def _insert(self, table):
        return pg_insert(table) if self.dialect == "postgresql" else sqlite_insert(table)

    def schema_ready(self) -> bool:
        from sqlalchemy import inspect
        return inspect(self.engine).has_table("cot_reports")

    # ------------------------------------------------------------------ configuration
    def sync_config(self, cfg: CotConfig) -> tuple[dict[str, int], dict[tuple[str, str], int]]:
        """Upsert datasets + mappings from cot.yaml. Mappings removed from the file are deactivated, never deleted."""
        with session_scope(self.engine) as s:
            for ds in cfg.datasets.values():
                vals = dict(report_type=ds.key, dataset_name=ds.name, dataset_id=ds.dataset_id,
                            dataset_id_status=ds.dataset_id_status, endpoint=ds.endpoint(cfg.socrata.base_url),
                            report_scope=ds.report_scope, history_start=ds.history_start,
                            description=ds.description, active=True)
                stmt = self._insert(CotDataset).values(**vals, created_at=utcnow(), updated_at=utcnow())
                stmt = stmt.on_conflict_do_update(index_elements=["report_type"],
                                                  set_={**{k: v for k, v in vals.items() if k != "report_type"},
                                                        "updated_at": utcnow()})
                s.execute(stmt)
            keep = set()
            for m in cfg.mappings:
                vals = dict(aurum_symbol=m.aurum_symbol, report_type=m.report_type,
                            cftc_contract_name=m.cftc_contract_name, cftc_contract_code=m.cftc_contract_code,
                            exchange=m.exchange, contract_description=m.contract_description, currency=m.currency,
                            mapping_status=m.mapping_status, verification_source=m.verification_source,
                            verified_at=m.verified_at, primary_category=m.primary_category, active=True)
                stmt = self._insert(CotContractMapping).values(**vals, created_at=utcnow(), updated_at=utcnow())
                stmt = stmt.on_conflict_do_update(
                    index_elements=["aurum_symbol", "report_type"],
                    set_={**{k: v for k, v in vals.items() if k not in ("aurum_symbol", "report_type")},
                          "updated_at": utcnow()})
                s.execute(stmt)
                keep.add((m.aurum_symbol, m.report_type))
            for row in s.execute(select(CotContractMapping)).scalars():
                if (row.aurum_symbol, row.report_type) not in keep and row.active:
                    row.active = False
            ds_ids = {r.report_type: r.id for r in s.execute(select(CotDataset)).scalars()}
            map_ids = {(r.aurum_symbol, r.report_type): r.id
                       for r in s.execute(select(CotContractMapping).where(CotContractMapping.active)).scalars()}
        return ds_ids, map_ids

    def datasets(self) -> list[dict[str, Any]]:
        with self.engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(select(CotDataset).order_by(CotDataset.id))]

    def mappings(self, active_only: bool = True) -> list[dict[str, Any]]:
        q = select(CotContractMapping).order_by(CotContractMapping.aurum_symbol, CotContractMapping.report_type)
        if active_only:
            q = q.where(CotContractMapping.active)
        with self.engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(q)]

    # ------------------------------------------------------------------ reports
    def upsert_report(self, s: Session, rep: ParsedReport, dataset_db_id: int, mapping_id: int | None,
                      rel: ReleaseInfo, retrieved_at: datetime, source: str, source_file: str | None) -> tuple[str, int]:
        """Insert or update one report + its positions. Returns (inserted|updated|unchanged, report id)."""
        existing = s.execute(select(CotReport).where(and_(
            CotReport.dataset_id == dataset_db_id, CotReport.contract_code == rep.contract_code,
            CotReport.report_date == rep.report_date))).scalar_one_or_none()
        action = "inserted"
        if existing is None:
            row = CotReport(dataset_id=dataset_db_id, contract_mapping_id=mapping_id, report_type=rep.report_type,
                            contract_code=rep.contract_code, contract_market_name=rep.contract_name,
                            report_date=rep.report_date, release_date=rel.release_date,
                            release_timestamp=rel.release_timestamp, release_method=rel.method,
                            release_uncertain=rel.uncertain, retrieval_timestamp=retrieved_at, processed_at=_now(),
                            source=source, source_file=source_file, source_record_id=rep.source_record_id,
                            open_interest=rep.open_interest, source_payload=_jsonable(rep.payload),
                            payload_hash=rep.payload_hash)
            s.add(row)
            s.flush()
        else:
            row = existing
            if existing.payload_hash == rep.payload_hash:
                action = "unchanged"
            else:
                action = "updated"
                s.add(CotReportRevision(cot_report_id=row.id, previous_payload=row.source_payload,
                                        previous_hash=row.payload_hash, new_hash=rep.payload_hash,
                                        previous_retrieval_timestamp=row.retrieval_timestamp))
                row.source_payload = _jsonable(rep.payload)
                row.payload_hash = rep.payload_hash
                row.open_interest = rep.open_interest
                row.contract_market_name = rep.contract_name
                row.source, row.source_file, row.source_record_id = source, source_file, rep.source_record_id
                row.retrieval_timestamp = retrieved_at
            # release timing / mapping can be corrected by configuration without changing the source payload
            row.contract_mapping_id = mapping_id
            row.release_date, row.release_timestamp = rel.release_date, rel.release_timestamp
            row.release_method, row.release_uncertain = rel.method, rel.uncertain
            row.processed_at = _now()
        if action != "unchanged":
            reportable = REPORTABLE[rep.report_type]
            for cat, p in rep.positions.items():
                vals = dict(cot_report_id=row.id, trader_category=cat, long_positions=p.long,
                            short_positions=p.short, spreading_positions=p.spreading, net_positions=p.net,
                            is_reportable=cat in reportable or cat == "total_reportable",
                            open_interest=rep.open_interest)
                stmt = self._insert(CotPosition).values(**vals, created_at=utcnow(), updated_at=utcnow())
                stmt = stmt.on_conflict_do_update(
                    index_elements=["cot_report_id", "trader_category"],
                    set_={k: v for k, v in vals.items() if k not in ("cot_report_id", "trader_category")} |
                    {"updated_at": utcnow()})
                s.execute(stmt)
        return action, row.id

    def report_frame(self, mapping_id: int | None = None, report_type: str | None = None,
                     as_of: datetime | None = None, start: date | None = None, end: date | None = None) -> pd.DataFrame:
        """Reports (no payload). ``as_of`` keeps only reports already public at that UTC instant (look-ahead guard)."""
        cols = [CotReport.id, CotReport.report_type, CotReport.contract_code, CotReport.contract_market_name,
                CotReport.report_date, CotReport.release_date, CotReport.release_timestamp, CotReport.release_method,
                CotReport.release_uncertain, CotReport.retrieval_timestamp, CotReport.open_interest,
                CotReport.source, CotReport.source_file, CotReport.source_record_id, CotReport.contract_mapping_id]
        q = select(*cols)
        if mapping_id is not None:
            q = q.where(CotReport.contract_mapping_id == mapping_id)
        if report_type:
            q = q.where(CotReport.report_type == report_type)
        if as_of is not None:
            q = q.where(CotReport.release_timestamp <= as_of)
        if start:
            q = q.where(CotReport.report_date >= start)
        if end:
            q = q.where(CotReport.report_date <= end)
        with self.engine.connect() as c:
            return pd.DataFrame([dict(r._mapping) for r in c.execute(q.order_by(CotReport.report_date))],
                                columns=[c.key for c in cols])

    def positions_frame(self, mapping_id: int, as_of: datetime | None = None) -> pd.DataFrame:
        q = (select(CotReport.id.label("report_id"), CotReport.report_date, CotReport.release_timestamp,
                    CotReport.open_interest.label("report_open_interest"), CotPosition.trader_category,
                    CotPosition.long_positions, CotPosition.short_positions, CotPosition.spreading_positions,
                    CotPosition.net_positions)
             .join(CotPosition, CotPosition.cot_report_id == CotReport.id)
             .where(CotReport.contract_mapping_id == mapping_id))
        if as_of is not None:
            q = q.where(CotReport.release_timestamp <= as_of)
        with self.engine.connect() as c:
            rows = [dict(r._mapping) for r in c.execute(q.order_by(CotReport.report_date))]
        return pd.DataFrame(rows, columns=["report_id", "report_date", "release_timestamp", "report_open_interest",
                                           "trader_category", "long_positions", "short_positions",
                                           "spreading_positions", "net_positions"])

    def report_detail(self, report_id: int) -> dict[str, Any] | None:
        with self.engine.connect() as c:
            r = c.execute(select(CotReport).where(CotReport.id == report_id)).first()
            if r is None:
                return None
            out = dict(r._mapping)
            out["positions"] = [dict(p._mapping) for p in c.execute(
                select(CotPosition).where(CotPosition.cot_report_id == report_id).order_by(CotPosition.id))]
            out["metrics"] = [dict(m._mapping) for m in c.execute(
                select(CotMetric).where(CotMetric.cot_report_id == report_id).order_by(CotMetric.id))]
            out["revisions"] = [dict(v._mapping) for v in c.execute(
                select(CotReportRevision).where(CotReportRevision.cot_report_id == report_id))]
        return out

    # ------------------------------------------------------------------ metrics
    def replace_metrics(self, rows: list[dict[str, Any]]) -> int:
        """Replace the metric rows of the given reports atomically (delete + bulk insert in one transaction).
        Metrics are fully derived data, so replacing them is safe and keeps recalculation reproducible."""
        if not rows:
            return 0
        report_ids = sorted({r["cot_report_id"] for r in rows})
        with session_scope(self.engine) as s:
            for chunk in _chunks(report_ids, 500):
                s.execute(CotMetric.__table__.delete().where(CotMetric.cot_report_id.in_(chunk)))
            s.execute(CotMetric.__table__.insert(), rows)        # executemany
        return len(rows)

    def metrics_frame(self, mapping_id: int | None = None, category: str | None = None,
                      as_of: datetime | None = None, start: date | None = None, end: date | None = None) -> pd.DataFrame:
        q = (select(*CotMetric.__table__.columns, CotReport.contract_mapping_id, CotReport.report_type,
                    CotReport.release_date)
             .join(CotReport, CotReport.id == CotMetric.cot_report_id))
        if mapping_id is not None:
            q = q.where(CotReport.contract_mapping_id == mapping_id)
        if category:
            q = q.where(CotMetric.trader_category == category)
        if as_of is not None:
            q = q.where(CotMetric.release_timestamp <= as_of)
        if start:
            q = q.where(CotMetric.report_date >= start)
        if end:
            q = q.where(CotMetric.report_date <= end)
        with self.engine.connect() as c:
            rows = [dict(r._mapping) for r in c.execute(q.order_by(CotMetric.report_date, CotMetric.trader_category))]
        return pd.DataFrame(rows)

    # ------------------------------------------------------------------ quality issues
    def record_issue(self, issue_type: str, severity: str, description: str, *, report_type: str | None = None,
                     contract_code: str | None = None, aurum_symbol: str | None = None,
                     report_date: date | None = None, details: dict | None = None, s: Session | None = None) -> None:
        fp = hashlib.sha256("|".join(str(x) for x in (report_type, contract_code, aurum_symbol, report_date, issue_type,
                                                       (details or {}).get("field"))).encode()).hexdigest()
        now = utcnow()
        vals = dict(fingerprint=fp, report_type=report_type, contract_code=contract_code, aurum_symbol=aurum_symbol,
                    report_date=report_date, issue_type=issue_type, severity=severity, description=description,
                    details=_jsonable(details) if details else None, detected_at=now, last_seen_at=now,
                    occurrences=1, resolved=False)
        stmt = self._insert(CotDataQualityIssue).values(**vals)
        stmt = stmt.on_conflict_do_update(index_elements=["fingerprint"], set_={
            "last_seen_at": now, "occurrences": CotDataQualityIssue.occurrences + 1, "severity": severity,
            "description": description, "details": vals["details"], "resolved": False})
        if s is not None:
            s.execute(stmt)
        else:
            with session_scope(self.engine) as ss:
                ss.execute(stmt)

    def resolve_issues(self, issue_type: str, aurum_symbol: str | None = None, report_type: str | None = None) -> None:
        q = update(CotDataQualityIssue).where(CotDataQualityIssue.issue_type == issue_type)
        if aurum_symbol:
            q = q.where(CotDataQualityIssue.aurum_symbol == aurum_symbol)
        if report_type:
            q = q.where(CotDataQualityIssue.report_type == report_type)
        with session_scope(self.engine) as s:
            s.execute(q.values(resolved=True))

    def issues(self, severity: str | None = None, symbol: str | None = None, include_resolved: bool = False,
               limit: int = 500, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        q = select(CotDataQualityIssue)
        if severity:
            q = q.where(CotDataQualityIssue.severity == severity)
        if symbol:
            q = q.where(CotDataQualityIssue.aurum_symbol == symbol)
        if not include_resolved:
            q = q.where(CotDataQualityIssue.resolved.is_(False))
        with self.engine.connect() as c:
            total = c.execute(select(func.count()).select_from(q.subquery())).scalar_one()
            rows = [dict(r._mapping) for r in c.execute(
                q.order_by(CotDataQualityIssue.last_seen_at.desc(), CotDataQualityIssue.id.desc())
                .limit(limit).offset(offset))]
        return rows, total

    # ------------------------------------------------------------------ ingestion logs
    def log_start(self, job_id: str, job_type: str, report_type: str | None, start: date | None,
                  end: date | None) -> int:
        with session_scope(self.engine) as s:
            row = CotIngestionLog(job_id=job_id, job_type=job_type, report_type=report_type, period_start=start,
                                  period_end=end, started_at=_now(), status="running")
            s.add(row)
            s.flush()
            return row.id

    def log_finish(self, log_id: int, status: str, **fields: Any) -> None:
        with session_scope(self.engine) as s:
            row = s.get(CotIngestionLog, log_id)
            row.finished_at = _now()
            row.duration_seconds = (row.finished_at - row.started_at).total_seconds()
            row.status = status
            for k, v in fields.items():
                setattr(row, k, _jsonable(v) if k == "details" else v)

    def completed_periods(self, report_type: str) -> set[tuple[date, date]]:
        """Backfill chunks already ingested successfully (for resume)."""
        q = select(CotIngestionLog.period_start, CotIngestionLog.period_end).where(and_(
            CotIngestionLog.report_type == report_type, CotIngestionLog.job_type.in_(("backfill", "update")),
            CotIngestionLog.status == "success"))
        with self.engine.connect() as c:
            return {(r[0], r[1]) for r in c.execute(q)}

    def logs(self, limit: int = 100, offset: int = 0, job_type: str | None = None) -> tuple[list[dict[str, Any]], int]:
        q = select(CotIngestionLog)
        if job_type:
            q = q.where(CotIngestionLog.job_type == job_type)
        with self.engine.connect() as c:
            total = c.execute(select(func.count()).select_from(q.subquery())).scalar_one()
            rows = [dict(r._mapping) for r in c.execute(
                q.order_by(CotIngestionLog.started_at.desc(), CotIngestionLog.id.desc()).limit(limit).offset(offset))]
        return rows, total

    # ------------------------------------------------------------------ status
    def status_summary(self) -> dict[str, Any]:
        with self.engine.connect() as c:
            per = [dict(r._mapping) for r in c.execute(
                select(CotReport.report_type, func.count().label("reports"),
                       func.min(CotReport.report_date).label("first_report_date"),
                       func.max(CotReport.report_date).label("latest_report_date"),
                       func.max(CotReport.release_date).label("latest_release_date"),
                       func.max(CotReport.retrieval_timestamp).label("last_retrieval"))
                .group_by(CotReport.report_type))]
            positions = c.execute(select(func.count()).select_from(CotPosition)).scalar_one()
            metrics = c.execute(select(func.count()).select_from(CotMetric)).scalar_one()
            last_ok = c.execute(select(func.max(CotIngestionLog.finished_at)).where(
                CotIngestionLog.status == "success")).scalar_one()
            failed = c.execute(select(func.count()).select_from(CotIngestionLog).where(
                CotIngestionLog.status == "failed")).scalar_one()
            open_issues = {r[0]: r[1] for r in c.execute(
                select(CotDataQualityIssue.severity, func.count()).where(CotDataQualityIssue.resolved.is_(False))
                .group_by(CotDataQualityIssue.severity))}
        return {"datasets": per, "positions": positions, "metrics": metrics, "last_successful_ingestion": last_ok,
                "failed_ingestions": failed, "open_issues": open_issues}


def _jsonable(d: Any) -> Any:
    return json.loads(json.dumps(d, default=str))


def _chunks(rows: list, n: int) -> Iterable[list]:
    for i in range(0, len(rows), n):
        yield rows[i:i + n]
