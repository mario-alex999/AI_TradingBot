"""COT ingestion: historical backfill, weekly incremental update and metric (re)calculation.

* Backfill is split into calendar-year chunks per report family. Each successful chunk is recorded in
  ``cot_ingestion_logs``; with ``resume=True`` completed chunks are skipped (the current year is always
  refreshed because the CFTC appends to it weekly). Every write is an upsert, so re-running never duplicates.
* Update fetches from three weeks before the newest stored report (to pick up CFTC revisions) to today.
* Each chunk is written in one transaction: a failure rolls the chunk back and is logged; other chunks continue.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.engine import Engine

from aurum.config import Settings
from aurum.cot import validation as V
from aurum.cot.client import CftcClient, CftcError
from aurum.cot.config import CotConfig, DatasetConfig
from aurum.cot.parsers import parse_record
from aurum.cot.positioning import compute_metrics
from aurum.cot.release import release_info
from aurum.cot.repository import CotRepository
from aurum.database.connection import session_scope

log = logging.getLogger(__name__)
Progress = Callable[[str], None]


@dataclass
class ChunkResult:
    report_type: str
    period_start: date
    period_end: date
    status: str = "success"
    source: str | None = None
    retrieved: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    rejected: int = 0
    issues: int = 0
    skipped: bool = False
    error: str | None = None


@dataclass
class JobSummary:
    job_id: str
    job_type: str
    chunks: list[ChunkResult] = field(default_factory=list)
    metrics_written: int = 0
    fallback_reason: str | None = None

    @property
    def status(self) -> str:
        done = [c for c in self.chunks if not c.skipped]
        if any(c.status == "failed" for c in done):
            return "partial" if any(c.status == "success" for c in done) else "failed"
        return "success"

    def totals(self) -> dict[str, int]:
        keys = ("retrieved", "inserted", "updated", "unchanged", "rejected", "issues")
        return {k: sum(getattr(c, k) for c in self.chunks) for k in keys}


class CotService:
    def __init__(self, settings: Settings, cfg: CotConfig, engine: Engine, client: CftcClient | None = None,
                 today: Callable[[], date] = date.today):
        self.settings = settings
        self.cfg = cfg
        self.repo = CotRepository(engine)
        raw_dir = settings.resolve(settings.data_dir) / "raw" / "cot"
        self.client = client or CftcClient(cfg, Path(raw_dir))
        self.today = today
        self._ds_ids: dict[str, int] = {}
        self._map_ids: dict[tuple[str, str], int] = {}

    # ------------------------------------------------------------------ setup
    def sync(self) -> None:
        self._ds_ids, self._map_ids = self.repo.sync_config(self.cfg)

    def _datasets(self, report_types: list[str] | None) -> list[DatasetConfig]:
        keys = report_types or list(self.cfg.datasets)
        unknown = [k for k in keys if k not in self.cfg.datasets]
        if unknown:
            raise ValueError(f"Unknown report type(s) {unknown}; expected {list(self.cfg.datasets)}")
        return [self.cfg.datasets[k] for k in keys if self.cfg.mappings_for(report_type=k)]

    # ------------------------------------------------------------------ jobs
    def backfill(self, years: float = 4, start: date | None = None, end: date | None = None,
                 report_types: list[str] | None = None, resume: bool = True,
                 progress: Progress | None = None) -> JobSummary:
        end = end or self.today()
        start = start or (end - timedelta(days=round(365.25 * years)))
        if start > end:
            raise ValueError("start must be on or before end")
        self.sync()
        job = JobSummary(str(uuid.uuid4()), "backfill")
        for ds in self._datasets(report_types):
            s = max(start, ds.history_start)
            if s > end:
                job.chunks.append(ChunkResult(ds.key, start, end, status="success", skipped=True,
                                              error=f"{ds.name} starts {ds.history_start}"))
                continue
            done = self.repo.completed_periods(ds.key) if resume else set()
            for y in range(s.year, end.year + 1):
                cs, ce = max(s, date(y, 1, 1)), min(end, date(y, 12, 31))
                is_open = y >= self.today().year - (1 if self.today().month == 1 else 0)
                if resume and not is_open and any(a <= cs and b >= ce for a, b in done):
                    job.chunks.append(ChunkResult(ds.key, cs, ce, skipped=True))
                    if progress:
                        progress(f"{ds.key} {y}: already ingested - skipped (resume)")
                    continue
                job.chunks.append(self._ingest_chunk(job, ds, cs, ce, progress))
        job.fallback_reason = self.client.last_fallback_reason
        job.metrics_written = self.calculate(progress=progress).metrics_written
        return job

    def update(self, report_types: list[str] | None = None, progress: Progress | None = None) -> JobSummary:
        self.sync()
        job = JobSummary(str(uuid.uuid4()), "update")
        status = {d["report_type"]: d for d in self.repo.status_summary()["datasets"]}
        end = self.today()
        for ds in self._datasets(report_types):
            latest = status.get(ds.key, {}).get("latest_report_date")
            if latest is None:
                if progress:
                    progress(f"{ds.key}: no stored data - running a 4-year backfill instead")
                sub = self.backfill(years=4, report_types=[ds.key], progress=progress)
                job.chunks.extend(sub.chunks)
                continue
            start = latest - timedelta(days=21)
            job.chunks.append(self._ingest_chunk(job, ds, start, end, progress, job_type="update"))
        job.fallback_reason = self.client.last_fallback_reason
        job.metrics_written = self.calculate(progress=progress).metrics_written
        return job

    def calculate(self, symbols: list[str] | None = None, progress: Progress | None = None) -> JobSummary:
        if not self._map_ids:
            self.sync()
        job = JobSummary(str(uuid.uuid4()), "calculate")
        log_id = self.repo.log_start(job.job_id, "calculate", None, None, None)
        try:
            total = 0
            for m in self.cfg.mappings:
                if symbols and m.aurum_symbol not in symbols:
                    continue
                mid = self._map_ids.get((m.aurum_symbol, m.report_type))
                if mid is None:
                    continue
                pos = self.repo.positions_frame(mid)
                rows = compute_metrics(pos, self.cfg.analytics)
                total += self.repo.replace_metrics(rows)
                self._series_quality(m, pos, rows)
                if progress:
                    progress(f"{m.aurum_symbol} {m.report_type}: {len(rows)} metric rows")
            job.metrics_written = total
            self.repo.log_finish(log_id, "success", records_inserted=total)
        except Exception as exc:
            self.repo.log_finish(log_id, "failed", error_message=f"{type(exc).__name__}: {exc}")
            raise
        return job

    # ------------------------------------------------------------------ internals
    def _ingest_chunk(self, job: JobSummary, ds: DatasetConfig, start: date, end: date,
                      progress: Progress | None, job_type: str = "backfill") -> ChunkResult:
        res = ChunkResult(ds.key, start, end)
        log_id = self.repo.log_start(job.job_id, job_type, ds.key, start, end)
        mappings = {m.cftc_contract_code: m for m in self.cfg.mappings_for(report_type=ds.key)}
        try:
            fetched = self.client.fetch(ds, mappings.keys(), start, end, progress)
            res.source = fetched.source
            res.retrieved = len(fetched.records)
            retrieved_at = datetime.now(timezone.utc)
            for iss in V.schema_check(ds.key, fetched.records):
                self.repo.record_issue(iss.issue_type, iss.severity, iss.description, report_type=ds.key,
                                       details=iss.details)
                res.issues += 1
            parsed = []
            for rec in fetched.records:
                src_file = rec.pop("_source_file", None)
                p = parse_record(ds.key, rec)
                parsed.append((p, src_file))
            unique, dup_issues = V.duplicate_check([p for p, _ in parsed])
            files_of = {id(p): f for p, f in parsed}
            with session_scope(self.repo.engine) as s:
                for iss in dup_issues:
                    code = iss.details.get("contract_code")
                    m = mappings.get(code or "")
                    self._issue(s, iss, ds.key, code, m.aurum_symbol if m else None)
                    res.issues += 1
                for p in unique:
                    m = mappings.get(p.contract_code or "")
                    issues, storable = V.validate_report(p, m)
                    for iss in issues:
                        self._issue(s, iss, ds.key, p.contract_code, m.aurum_symbol if m else None)
                    res.issues += len(issues)
                    if not storable:
                        res.rejected += 1
                        continue
                    rel = release_info(p.report_date, self.cfg.release)
                    if rel.uncertain:
                        self._issue(s, V.Issue("release_date_uncertain", V.WARNING,
                                               f"Official release date for the {p.report_date} report could not be "
                                               f"established (known publication disruption); using {rel.release_date} "
                                               f"(rule-based). Treat availability timing with caution.",
                                               p.report_date, {"field": "release"}), ds.key, p.contract_code,
                                    m.aurum_symbol if m else None)
                        res.issues += 1
                    action, _ = self.repo.upsert_report(
                        s, p, self._ds_ids[ds.key], self._map_ids.get((m.aurum_symbol, ds.key)) if m else None,
                        rel, retrieved_at, fetched.source, files_of.get(id(p)))
                    setattr(res, action, getattr(res, action) + 1)
            self.repo.log_finish(log_id, "success", source=res.source, records_retrieved=res.retrieved,
                                 records_inserted=res.inserted, records_updated=res.updated,
                                 records_unchanged=res.unchanged, records_rejected=res.rejected,
                                 details={**fetched.detail, "issues": res.issues,
                                          "fallback": self.client.last_fallback_reason})
            if progress:
                progress(f"{ds.key} {start}..{end}: {res.retrieved} retrieved, {res.inserted} new, "
                         f"{res.updated} revised, {res.unchanged} unchanged, {res.rejected} rejected")
        except (CftcError, Exception) as exc:  # noqa: BLE001 - chunk isolation; logged and reported
            res.status, res.error = "failed", f"{type(exc).__name__}: {exc}"
            self.repo.log_finish(log_id, "failed", source=res.source, records_retrieved=res.retrieved,
                                 error_message=res.error)
            self.repo.record_issue("ingestion_failed", V.ERROR, f"{job_type} of {ds.name} {start}..{end} failed: "
                                   f"{res.error}", report_type=ds.key, details={"field": f"{start}..{end}"})
            log.error("COT chunk failed", extra={"dataset": ds.key, "start": str(start), "end": str(end),
                                                 "error": res.error})
            if progress:
                progress(f"{ds.key} {start}..{end}: FAILED - {res.error}")
        return res

    def _issue(self, s, iss: V.Issue, report_type: str, code: str | None, symbol: str | None) -> None:
        self.repo.record_issue(iss.issue_type, iss.severity, iss.description, report_type=report_type,
                               contract_code=code, aurum_symbol=symbol, report_date=iss.report_date,
                               details=iss.details, s=s)

    def _series_quality(self, m, pos, rows: list[dict[str, Any]]) -> None:
        """Series-level checks after calculation: gaps, staleness, unusual changes of the primary category."""
        if pos.empty:
            iss = V.stale_check(None, datetime.now(timezone.utc), self.cfg.analytics.stale_after_days)
            self.repo.record_issue(iss.issue_type, iss.severity, iss.description, report_type=m.report_type,
                                   contract_code=m.cftc_contract_code, aurum_symbol=m.aurum_symbol, details=iss.details)
            return
        self.repo.resolve_issues("no_data", m.aurum_symbol, m.report_type)
        dates = sorted(set(pos["report_date"]))
        for iss in V.series_checks(dates):
            self.repo.record_issue(iss.issue_type, iss.severity, iss.description, report_type=m.report_type,
                                   contract_code=m.cftc_contract_code, aurum_symbol=m.aurum_symbol,
                                   report_date=iss.report_date, details=iss.details)
        latest_release = max(pos["release_timestamp"])
        stale = V.stale_check(latest_release.to_pydatetime() if hasattr(latest_release, "to_pydatetime")
                              else latest_release, datetime.now(timezone.utc), self.cfg.analytics.stale_after_days)
        if stale:
            self.repo.record_issue(stale.issue_type, stale.severity, stale.description, report_type=m.report_type,
                                   contract_code=m.cftc_contract_code, aurum_symbol=m.aurum_symbol,
                                   details=stale.details)
        else:
            self.repo.resolve_issues("stale_data", m.aurum_symbol, m.report_type)
        prim = sorted((r for r in rows if r["trader_category"] == m.primary_category), key=lambda r: r["report_date"])
        for iss in V.unusual_change_check([r["report_date"] for r in prim], [r["net_position"] for r in prim]):
            self.repo.record_issue(iss.issue_type, iss.severity,
                                   f"{m.primary_category}: {iss.description}", report_type=m.report_type,
                                   contract_code=m.cftc_contract_code, aurum_symbol=m.aurum_symbol,
                                   report_date=iss.report_date, details=iss.details)
