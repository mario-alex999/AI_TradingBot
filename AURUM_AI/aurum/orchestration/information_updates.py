"""WF08-10 News and COT Update Scheduler - the backend half.

Coordinates the scheduled refreshes of the information Modules 02 and 04 keep: the economic calendar, official news,
central-bank and macro data (Module 04) and the CFTC Commitments of Traders reports (Module 02). n8n sequences the
calls; the modules fetch, validate and store. WF08-10 decides only *when* each refresh is due and checks afterwards
that the stored data is current.

* ``sources`` - Check Source Availability + Check COT Publication Schedule: every update task with whether its source
                is configured, whether it is due, why, and when it is next due.
* ``run``     - Trigger Market Intelligence Refresh / Trigger COT Update When Available: one task, run now.
* ``record``  - Verify Data Timestamps + Record Update Status: reads the stored data's own timestamps, records the
                status once and alerts the dashboard (through Module 07) when a source starts failing or goes stale.

When a task is due:

* **COT** follows the CFTC's real publication schedule (Module 02's release rules: normally Friday 15:30 US/Eastern,
  moved for US holidays). A report family is due once the next report should be public (release + 6 h, Module 02's
  ``update_due``) or when nothing is stored yet. If the CFTC has not published yet, it is tried again after
  ``cot_retry_hours``, never every few minutes.
* **Economic calendar**: every ``calendar_refresh_hours``, and every ``calendar_event_refresh_minutes`` while a
  high-importance event is due within ``event_window_hours`` (refresh frequency follows event importance).
* **News**: every ``news_refresh_minutes``. **Central-bank and macro data**: every ``macro_refresh_hours``, in parts
  (one bank or one indicator series at a time, the longest-unrefreshed first) until ``macro_budget_seconds`` is used;
  the rest follows on the next run, so one call stays inside the API's time limit.
* A task that failed is tried again after ``retry_after_minutes`` (COT: ``cot_retry_hours``).

WF08-10 reads public, official sources through the modules only. It touches no account, no MT5 and no trade.
"""
from __future__ import annotations

import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import func, select

from aurum.orchestration.config_model import InformationUpdatesCfg

WORKFLOW_ID = "wf08-10-news-cot-updates"
WEBHOOK_PATH = "aurum-wf08-10-news-cot-updates"
VERSION = "1.0.0"
WORKFLOW_NAME = "WF08-10 News and COT Update Scheduler"
DESCRIPTION = ("Refresh the economic calendar, official news and macro data (Module 04) and the CFTC COT reports "
               "(Module 02) when each is due, verify the stored timestamps and record the status. Reads public data only.")
DEFAULT_SCHEDULE = "17 * * * *"                  # hourly; each task decides whether it is due
SCHEDULE_NAME = "WF08-10 news and COT updates"
TIMEOUT_SECONDS = 600                            # one run may refresh six sources, each up to the API's time limit
AUDIT_ACTION = "wf08-10.update_recorded"
AUDIT_ENTITY = "information_updates"

MI_TASKS = (("calendar", "Economic calendar", "module_04"), ("news", "Official news", "module_04"),
            ("macro", "Central-bank and macro data", "module_04"))
COT_FAMILIES = (("legacy", "COT legacy report"), ("tff", "COT traders in financial futures"),
                ("disaggregated", "COT disaggregated report"))
TASKS: tuple[tuple[str, str, str], ...] = (*MI_TASKS, *[(f"cot_{k}", n, "module_02") for k, n in COT_FAMILIES])
NAMES = {k: n for k, n, _ in TASKS}
MODULE = {k: m for k, _, m in TASKS}
PROBLEMS = ("failed", "stale")                   # outcomes that are alerted (once) and can recover
OK_OUTCOMES = ("updated", "current", "waiting")

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {"tasks": {"type": "array", "items": {"type": "string", "maxLength": 32}, "maxItems": 10},
                   "force": {"type": "boolean"}, "dryRun": {"type": "boolean"},
                   "reason": {"type": "string", "maxLength": 200}},
}
OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"status": {"type": "string", "enum": ["completed", "partial", "skipped"]}, "reason": {"type": "string"},
                   "updated": {"type": "array"}, "notDue": {"type": "array"}, "problems": {"type": "array"}},
}


def _utc(t: datetime) -> datetime:
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


def _iso(t: Any) -> str | None:
    if t is None:
        return None
    if isinstance(t, datetime):
        return _utc(t).isoformat().replace("+00:00", "Z")
    return str(t)


class ModuleSources:
    """The real Modules 02 and 04, called in-process. Tests replace this with a controlled stand-in."""

    def __init__(self, engine: Any, settings: Any):
        self.engine, self.settings = engine, settings
        self._mi: Any = None
        self._cot: Any = None

    @property
    def mi(self) -> Any:
        if self._mi is None:
            from aurum.market_intelligence.service import MarketIntelligenceService
            self._mi = MarketIntelligenceService(self.settings, self.engine)
        return self._mi

    @property
    def cot(self) -> Any:
        if self._cot is None:
            from aurum.cot.client import CftcClient
            from aurum.cot.config import load_cot_config
            from aurum.cot.ingestion import CotService
            from aurum.database.connection import serverless
            cfg = load_cot_config()
            raw = (Path(tempfile.gettempdir()) / "aurum" / "raw" / "cot" if serverless()       # read-only deployment disk
                   else self.settings.resolve(self.settings.data_dir) / "raw" / "cot")
            self._cot = CotService(self.settings, cfg, self.engine, client=CftcClient(cfg, raw))
        return self._cot

    # ---- availability ----------------------------------------------------------------------------------------------
    def available(self, task: str) -> tuple[bool, str]:
        if task == "calendar":
            from aurum.market_intelligence.providers import provider_registry
            ready = [p["provider"] for p in provider_registry(self.settings, self.mi.cfg) if p.get("configured")]
            return (bool(ready), f"{len(ready)} calendar source(s) configured: {', '.join(ready)}." if ready
                    else "No economic-calendar source is configured.")
        if task == "news":
            return (bool(self.settings.news_enabled), "Official central-bank feeds." if self.settings.news_enabled
                    else "News is switched off (NEWS_ENABLED=false).")
        if task == "macro":
            on = bool(self.settings.official_data_enabled)
            return on, ("Official central-bank and statistics sources." if on
                        else "Official data is switched off (OFFICIAL_DATA_ENABLED=false).")
        fam = task.removeprefix("cot_")
        maps = self.cot.cfg.mappings_for(report_type=fam) if fam in self.cot.cfg.datasets else []
        return (bool(maps), f"CFTC {fam} report for {', '.join(m.aurum_symbol for m in maps)}." if maps
                else f"No instrument is mapped to the CFTC {fam} report.")

    def high_impact_soon(self, now: datetime, hours: int) -> list[str]:
        from aurum.market_intelligence.db_models import CalendarEvent as E
        if not self.mi.repo.schema_ready():
            return []
        from aurum.database.connection import session_scope
        with session_scope(self.engine) as s:
            rows = s.execute(select(E.name, E.currency, E.scheduled_at).where(
                E.importance == "high", E.scheduled_at >= now, E.scheduled_at <= now + timedelta(hours=hours))
                .order_by(E.scheduled_at).limit(5)).all()
        return [f"{r[1] or ''} {r[0]} at {_iso(r[2])}".strip() for r in rows]

    def cot_schedule(self, family: str, now: datetime) -> dict[str, Any]:
        """Module 02's freshness for each instrument of one report family, from the CFTC release schedule."""
        from aurum.cot.intelligence import freshness
        svc = self.cot
        if not svc._map_ids:
            svc.sync()
        series = []
        for m in svc.cfg.mappings_for(report_type=family):
            mid = svc._map_ids.get((m.aurum_symbol, family))
            rep = svc.repo.report_frame(mapping_id=mid) if mid else None
            if rep is None or rep.empty:
                series.append({"instrument": m.aurum_symbol, "status": "no_data"})
                continue
            last = rep.iloc[-1]
            fr = freshness(last["release_timestamp"], last["report_date"], svc.cfg, now)
            series.append({"instrument": m.aurum_symbol, "status": fr["status"], "latest_report_date": _iso(last["report_date"]),
                           "latest_release": _iso(last["release_timestamp"]),
                           "next_expected_release": _iso(fr["next_expected_release"])})
        order = {"no_data": 3, "stale": 2, "update_due": 1, "current": 0}
        worst = max((s["status"] for s in series), key=lambda x: order.get(x, 0), default="no_data")
        nxt = min((s["next_expected_release"] for s in series if s.get("next_expected_release")), default=None)
        return {"status": worst, "series": series, "next_expected_release": nxt}

    # ---- refresh -----------------------------------------------------------------------------------------------------
    def macro_parts(self) -> list[str]:
        return self.mi.macro_parts()

    def refresh(self, task: str, c: InformationUpdatesCfg, parts: list[str] | None = None) -> dict[str, Any]:
        if task == "calendar":
            out = self.mi.refresh_calendar()
            prov = out.get("providers") or []
            failed = [p["provider"] for p in prov if p.get("status") != "success"]
            changed = sum(int(p.get("inserted", 0)) + int(p.get("updated", 0)) for p in prov)
            st = "failed" if prov and len(failed) == len(prov) else "partial" if failed else "ok"
            return {"status": st, "changed": changed, "detail": f"{len(prov)} source(s), {changed} event(s) new or revised"
                    + (f"; failed: {', '.join(failed)}" if failed else "") + ".", "failed_sources": failed}
        if task == "news":
            out = self.mi.refresh_news()
            if out.get("status") == "disabled":
                return {"status": "disabled", "changed": 0, "detail": "News is switched off."}
            errs = out.get("errors") or []
            st = "partial" if errs and out.get("received") else "failed" if errs else "ok"
            return {"status": st, "changed": int(out.get("inserted", 0)),
                    "detail": f"{out.get('received', 0)} headline(s) received, {out.get('inserted', 0)} new"
                              + (f"; {len(errs)} feed(s) failed" if errs else "") + "."}
        if task == "macro":
            if not self.settings.official_data_enabled:
                return {"status": "disabled", "changed": 0, "detail": "Official data is switched off."}
            todo = list(parts if parts is not None else self.macro_parts())
            t0, done, failed, errors, changed = time.perf_counter(), [], [], [], 0
            for part in todo:
                if (done or failed) and time.perf_counter() - t0 >= c.macro_budget_seconds:
                    break                                    # the rest follows on the next run
                out = self.mi.refresh_macro(only=[part])
                r = (out.get("rates") or {}).get(part) or (out.get("series") or {}).get(part) or {}
                if r.get("status") == "failed":
                    failed.append(part)
                    errors.append(f"{part}: {str(r.get('error') or '')[:120]}")
                else:
                    done.append(part)
                    changed += int(r.get("inserted", 0) or 0)
            remaining = [p for p in todo if p not in done and p not in failed]
            st = "failed" if failed and not done else "partial" if failed else "ok"
            return {"status": st, "changed": changed, "parts_done": done, "parts_failed": failed, "remaining": remaining,
                    "detail": f"{len(done)} of {len(todo)} part(s) refreshed, {changed} new value(s)"
                              + (f"; failed: {'; '.join(errors)}" if failed else "")
                              + (f"; {len(remaining)} left for the next run" if remaining else "") + "."}
        fam = task.removeprefix("cot_")
        svc = self.cot
        stored = {d["report_type"] for d in svc.repo.status_summary()["datasets"]}
        job = (svc.backfill(years=c.cot_backfill_years, report_types=[fam]) if fam not in stored
               else svc.update(report_types=[fam]))
        tot = job.totals()
        st = {"success": "ok", "partial": "partial", "failed": "failed"}.get(job.status, "failed")
        errors = [c.error for c in job.chunks if c.error and c.status == "failed"]
        return {"status": st, "changed": tot["inserted"] + tot["updated"],
                "detail": (f"{'Backfill' if fam not in stored else 'Update'}: {tot['retrieved']} report(s) retrieved, "
                           f"{tot['inserted']} new, {tot['updated']} revised, {tot['rejected']} rejected; "
                           f"{job.metrics_written} metric(s) recalculated.") + (f" {errors[0][:200]}" if errors else ""),
                "fallback": job.fallback_reason}

    # ---- stored timestamps -----------------------------------------------------------------------------------------------
    def timestamps(self, task: str, now: datetime, c: InformationUpdatesCfg) -> dict[str, Any]:
        from aurum.database.connection import session_scope
        from aurum.market_intelligence import db_models as M
        if task.startswith("cot_"):
            sch = self.cot_schedule(task.removeprefix("cot_"), now)
            latest = max((s.get("latest_report_date") or "" for s in sch["series"]), default="") or None
            return {"state": {"current": "fresh", "update_due": "waiting"}.get(sch["status"], "stale"),
                    "latest_data_at": latest, "next_expected_release": sch["next_expected_release"],
                    "series": sch["series"],
                    "detail": f"Latest COT report date {latest or 'none'}; Module 02 status '{sch['status']}'."}
        if not self.mi.repo.schema_ready():
            return {"state": "stale", "latest_data_at": None, "detail": "Module 04 tables are not initialised."}
        with session_scope(self.engine) as s:
            if task == "calendar":
                last_ok = s.execute(select(func.max(M.CalendarSource.last_success_at))).scalar()
                ahead = s.execute(select(func.max(M.CalendarEvent.scheduled_at))).scalar()
                cov = self.mi.calendar_coverage(now)
                fresh = bool(last_ok) and now - _utc(last_ok) <= timedelta(hours=c.calendar_stale_hours)
                return {"state": "fresh" if fresh else "stale", "latest_data_at": _iso(last_ok),
                        "events_until": _iso(ahead), "coverage": cov.get("level"),
                        "detail": f"Last successful calendar download {_iso(last_ok) or 'never'}; events stored up to "
                                  f"{_iso(ahead) or 'none'}; coverage '{cov.get('level')}'."}
            run_type = "news" if task == "news" else "macro"
            last_run = s.execute(select(func.max(M.MiRun.generated_at)).where(
                M.MiRun.run_type == run_type, M.MiRun.status.in_(("ok", "partial")))).scalar()
            if task == "news":
                newest = s.execute(select(func.max(M.MarketNews.published_at))).scalar()
                limit = timedelta(hours=c.news_stale_hours)
                what = f"newest headline published {_iso(newest) or 'none'}"
            else:
                newest = s.execute(select(func.max(M.CentralBankRate.retrieved_at))).scalar()
                limit = timedelta(hours=c.macro_stale_hours)
                what = f"policy rates last retrieved {_iso(newest) or 'never'}"
        fresh = bool(last_run) and now - _utc(last_run) <= limit
        return {"state": "fresh" if fresh else "stale", "latest_data_at": _iso(newest), "last_refresh_at": _iso(last_run),
                "detail": f"Last successful {run_type} refresh {_iso(last_run) or 'never'}; {what}."}


class InformationUpdates:
    """Stateless over the database: safe to call from any API worker. ``svc`` is the OrchestrationService."""

    def __init__(self, svc: Any, sources: ModuleSources | None = None, approvals: Callable[[], Any] | None = None,
                 deliver: Callable[[Any], dict[str, int]] | None = None):
        self.svc = svc
        self.engine = svc.repo.engine
        self.src = sources or ModuleSources(self.engine, svc.settings)
        self._approvals_factory, self._deliver = approvals, deliver
        self._approvals: Any = None

    def cfg(self) -> InformationUpdatesCfg:
        return self.svc.config().information_updates

    @property
    def approvals(self) -> Any:
        if self._approvals is None:
            if self._approvals_factory is not None:
                self._approvals = self._approvals_factory()
            else:
                from aurum.approvals.wiring import build_service
                from aurum.risk.service import RiskService
                engine, settings = self.engine, self.svc.settings
                self._approvals = build_service(engine, settings, risk=lambda: RiskService(engine, settings))
        return self._approvals

    def now(self) -> datetime:
        return _utc(self.svc.now())

    def config_view(self) -> dict[str, Any]:
        c = self.cfg()
        return {"workflow_id": WORKFLOW_ID, "enabled": c.enabled, "kill_switch": not c.enabled, "tasks": [k for k, _, _ in TASKS],
                "calendar_refresh_hours": c.calendar_refresh_hours, "calendar_event_refresh_minutes": c.calendar_event_refresh_minutes,
                "event_window_hours": c.event_window_hours, "news_refresh_minutes": c.news_refresh_minutes,
                "macro_refresh_hours": c.macro_refresh_hours, "macro_budget_seconds": c.macro_budget_seconds,
                "cot_retry_hours": c.cot_retry_hours,
                "retry_after_minutes": c.retry_after_minutes, "notify_dashboard": c.notify_dashboard,
                "schedule_hint": DEFAULT_SCHEDULE}

    def preflight(self) -> dict[str, Any]:
        c = self.cfg()
        reasons = [] if c.enabled else ["The news and COT updates are switched off (information_updates.enabled = false)."]
        return {"proceed": not reasons, "checks": {"enabled": c.enabled}, "reasons": reasons, "checked_at": _iso(self.now())}

    # ---- history -------------------------------------------------------------------------------------------------------
    def _records(self, task: str, limit: int = 20) -> list[tuple[datetime, dict[str, Any]]]:
        from aurum.orchestration.db_models import OrchAuditLog as L
        with self.svc.repo.read() as s:
            rows = s.execute(select(L.at, L.details).where(L.action == AUDIT_ACTION, L.entity_type == AUDIT_ENTITY,
                                                           L.entity_id == task).order_by(L.seq.desc()).limit(limit)).all()
        return [(_utc(at), d or {}) for at, d in rows]

    # ---- Check Source Availability + Check COT Publication Schedule ------------------------------------------------------
    def due(self, task: str) -> dict[str, Any]:
        c, now = self.cfg(), self.now()
        recs = [(at, d) for at, d in self._records(task, 200 if task == "macro" else 20) if d.get("outcome") != "dry_run"]
        last_at, last = recs[0] if recs else (None, {})
        last_ok = next((at for at, d in recs if d.get("outcome") in OK_OUTCOMES), None)
        try:
            ok, why = self.src.available(task)
        except Exception as exc:  # noqa: BLE001 - an unreadable source configuration is reported, not raised
            ok, why = False, f"The source configuration could not be read ({type(exc).__name__}: {str(exc)[:160]})."
        out = {"task": task, "name": NAMES[task], "module": MODULE[task], "available": ok, "source": why,
               "last_recorded_at": _iso(last_at), "last_outcome": last.get("outcome"), "last_success_at": _iso(last_ok)}
        if not ok:
            return {**out, "due": False, "reason": why, "next_due_at": None}
        if last.get("outcome") == "failed" and not task.startswith("cot_"):
            nxt = last_at + timedelta(minutes=c.retry_after_minutes)
            return {**out, "due": now >= nxt, "next_due_at": _iso(nxt),
                    "reason": "The last attempt failed; " + ("trying again." if now >= nxt else f"next try {_iso(nxt)}.")}
        if task.startswith("cot_"):
            sch = self.src.cot_schedule(task.removeprefix("cot_"), now)
            out["publication"] = sch
            if sch["status"] == "current":
                return {**out, "due": False, "next_due_at": sch["next_expected_release"],
                        "reason": f"Up to date with the CFTC schedule; the next report is expected {sch['next_expected_release']}."}
            retry = last_at + timedelta(hours=c.cot_retry_hours) if last_at else None
            if retry and now < retry:
                return {**out, "due": False, "next_due_at": _iso(retry),
                        "reason": f"Module 02 status '{sch['status']}', last tried {_iso(last_at)} "
                                  f"({last.get('outcome')}); next try {_iso(retry)}."}
            why = {"no_data": "No COT report is stored yet.", "stale": "The stored COT report is out of date.",
                   "update_due": "A newer CFTC report should now be published."}[sch["status"]]
            return {**out, "due": True, "next_due_at": None, "reason": why}
        if task == "macro":
            stale, parts = self._macro_due(recs)
            out["parts_due"] = stale
            if stale:
                return {**out, "due": True, "next_due_at": None,
                        "reason": f"{len(stale)} of {len(parts)} macro part(s) not refreshed within {c.macro_refresh_hours} h."}
            last = self._macro_last(recs)
            nxt = min(last.values()) + timedelta(hours=c.macro_refresh_hours) if last else None
            return {**out, "due": False, "next_due_at": _iso(nxt),
                    "reason": f"Not due (every {c.macro_refresh_hours} h); next {_iso(nxt)}."}
        if task == "calendar":
            soon = self.src.high_impact_soon(now, c.event_window_hours)
            every = (timedelta(minutes=c.calendar_event_refresh_minutes) if soon
                     else timedelta(hours=c.calendar_refresh_hours))
            out["high_impact_soon"] = soon
            basis = (f"high-importance event(s) within {c.event_window_hours} h ({soon[0]}): every "
                     f"{c.calendar_event_refresh_minutes} min" if soon else f"every {c.calendar_refresh_hours} h")
        else:
            every, basis = timedelta(minutes=c.news_refresh_minutes), f"every {c.news_refresh_minutes} min"
        nxt = last_ok + every if last_ok else None
        if nxt is None or now >= nxt:
            return {**out, "due": True, "next_due_at": None,
                    "reason": ("Never refreshed by WF08-10." if last_ok is None else f"Due ({basis}).")}
        return {**out, "due": False, "next_due_at": _iso(nxt), "reason": f"Not due ({basis}); next {_iso(nxt)}."}

    @staticmethod
    def _macro_last(recs: list[tuple[datetime, dict[str, Any]]]) -> dict[str, datetime]:
        """When each macro part (bank or series) was last refreshed by WF08-10. Module 04 keeps an unchanged value's
        original retrieval time, so its own tables cannot answer this."""
        last: dict[str, datetime] = {}
        for at, d in recs:                                    # newest first
            for p in d.get("parts_done") or []:
                last.setdefault(p, at)
        return last

    def _macro_due(self, recs: list[tuple[datetime, dict[str, Any]]] | None = None) -> tuple[list[str], list[str]]:
        """(parts due, oldest first; all parts)."""
        if recs is None:
            recs = [(at, d) for at, d in self._records("macro", 200) if d.get("outcome") != "dry_run"]
        parts, last = self.src.macro_parts(), self._macro_last(recs)
        limit = self.now() - timedelta(hours=self.cfg().macro_refresh_hours)
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        stale = sorted((p for p in parts if last.get(p, epoch) <= limit), key=lambda p: last.get(p, epoch))
        return stale, parts

    def sources(self, only: list[str] | None = None, force: bool = False) -> dict[str, Any]:
        c = self.cfg()
        if not c.enabled:
            return {"enabled": False, "items": [], "count": 0, "tasks": []}
        rows = [self.due(k) for k, _, _ in TASKS if not only or k in only]
        items = [{"task": r["task"], "name": r["name"], "reason": r["reason"]} for r in rows
                 if r["available"] and (r["due"] or force)]
        return {"enabled": True, "items": items, "count": len(items), "tasks": rows,
                "not_due": [{"task": r["task"], "reason": r["reason"], "next_due_at": r["next_due_at"]}
                            for r in rows if not (r["available"] and (r["due"] or force))]}

    # ---- Trigger Market Intelligence Refresh / Trigger COT Update When Available ----------------------------------------
    def run(self, task: str, *, run_id: str | None = None, dry_run: bool = False) -> dict[str, Any]:
        if task not in NAMES:
            return {"task": task, "status": "skipped", "changed": 0, "detail": "Unknown task."}
        c = self.cfg()
        if not c.enabled:
            return {"task": task, "status": "skipped", "changed": 0, "detail": "The news and COT updates are switched off."}
        if dry_run:
            d = self.due(task)
            return {"task": task, "status": "dry_run", "changed": 0, "detail": f"Dry run: would refresh. {d['reason']}"}
        t0 = time.perf_counter()
        try:
            parts = None
            if task == "macro":
                stale, every = self._macro_due()
                parts = stale or every                     # forced or not due: refresh everything, oldest first
            res = self.src.refresh(task, c, parts)
        except Exception as exc:  # noqa: BLE001 - a failing source is a finding, recorded by `record`
            res = {"status": "failed", "changed": 0, "detail": f"The refresh failed ({type(exc).__name__}: {str(exc)[:240]})."}
        return {"task": task, "name": NAMES[task], "run_id": run_id, "duration_ms": round((time.perf_counter() - t0) * 1000), **res}

    # ---- Verify Data Timestamps + Record Update Status -----------------------------------------------------------------
    def record(self, task: str, *, run_id: str | None = None, dry_run: bool = False,
               result: dict[str, Any] | None = None) -> dict[str, Any]:
        if task not in NAMES:
            return {"task": task, "status": "skipped", "outcome": "skipped", "detail": "Unknown task."}
        c, now = self.cfg(), self.now()
        if not c.enabled:
            return {"task": task, "status": "skipped", "outcome": "skipped", "detail": "The news and COT updates are switched off."}
        result = result or {}
        run_status = str(result.get("status") or "")
        try:
            ts = self.src.timestamps(task, now, c)
        except Exception as exc:  # noqa: BLE001
            ts = {"state": "stale", "latest_data_at": None, "detail": f"Timestamps could not be read ({type(exc).__name__})."}
        if run_status == "failed":
            outcome = "failed"
        elif run_status == "disabled":
            outcome = "skipped"
        elif ts["state"] == "fresh":
            outcome = "updated" if int(result.get("changed") or 0) > 0 else "current"
        elif ts["state"] == "waiting":
            outcome = "waiting"                       # the CFTC has not published the next report yet
        else:
            outcome = "stale"
        detail = " ".join(x for x in (str(result.get("detail") or ""), ts.get("detail") or "") if x)[:900]
        prev = next((d for _, d in self._records(task) if d.get("outcome") not in ("dry_run", "skipped")), {})
        newly = outcome in PROBLEMS and prev.get("outcome") != outcome
        recovered = outcome in OK_OUTCOMES and prev.get("outcome") in PROBLEMS
        report = {"task": task, "name": NAMES[task], "module": MODULE[task], "outcome": outcome, "run_status": run_status or None,
                  "latest_data_at": ts.get("latest_data_at"), "verified": ts, "detail": detail, "newly_failing": newly,
                  "recovered": recovered}
        if dry_run:
            return {**report, "status": "dry_run", "notified": 0}
        with self.svc.repo.write() as s:
            self.svc.repo.audit(s, actor="wf08-10", actor_kind="system", action=AUDIT_ACTION, entity_type=AUDIT_ENTITY,
                                entity_id=task, at=now, outcome="ok" if outcome not in PROBLEMS else "failed",
                                details={"run_id": run_id, "outcome": outcome, "run_status": run_status or None,
                                         "changed": int(result.get("changed") or 0), "latest_data_at": ts.get("latest_data_at"),
                                         "parts_done": [str(p)[:32] for p in (result.get("parts_done") or [])][:50],
                                         "detail": detail})
        notified, note_error = 0, None
        if newly and c.notify_dashboard:
            what = "failed to refresh" if outcome == "failed" else "is out of date"
            try:
                nid = self.approvals.notify_system_event(
                    "orchestration.information_update", "normal", f"{NAMES[task]} {what}"[:160], detail,
                    f"wf08-10:{task}:{outcome}:{run_id or _iso(now)}")
                notified = 1 if nid else 0
            except Exception as exc:  # noqa: BLE001 - the status is recorded; a failed notification is reported
                note_error = type(exc).__name__
        delivered = self._send() if notified else {}
        return {**report, "status": "recorded", "notified": notified, "notification_error": note_error,
                "delivered_now": delivered, "recorded_at": _iso(now)}

    def _send(self) -> dict[str, Any]:
        try:
            if self._deliver is not None:
                return self._deliver(self.approvals)
            from aurum.approvals.worker import Worker
            return Worker(self.approvals, self.svc.settings).deliver_notifications()
        except Exception as exc:  # noqa: BLE001 - the notification stays queued for the next delivery
            return {"error": f"{type(exc).__name__}: {exc}"[:200]}        # type: ignore[dict-item]


# ---- installation (used by `python -m aurum.cli orchestration setup-wf08-10`) ----------------------------------------------
def install(svc: Any, actor: Any, n8n_workflow_id: str, *, schedule: str | None = DEFAULT_SCHEDULE,
            schedule_timezone: str = "UTC", activate: bool = True) -> dict[str, Any]:
    """Registers WF08-10, links it to the n8n workflow, adds version 1.0.0, validates and activates it and creates its
    schedule. It follows no events: it runs on its schedule and each task decides whether it is due. Safe to re-run."""
    from aurum.orchestration.common import OrchError
    steps: list[str] = []
    svc.ensure_ready()
    try:
        w = svc.get_workflow(WORKFLOW_ID)
    except OrchError:
        svc.register_workflow(actor, {"workflow_id": WORKFLOW_ID, "name": WORKFLOW_NAME, "category": "other",
                                      "trigger_types": ["schedule", "manual", "admin"], "owner": "module_08",
                                      "description": DESCRIPTION, "timeout_seconds": TIMEOUT_SECONDS})
        steps.append("registered")
        w = svc.get_workflow(WORKFLOW_ID)
    if (w.get("timeout_seconds") or 0) < TIMEOUT_SECONDS:
        svc.update_workflow(actor, WORKFLOW_ID, {"timeout_seconds": TIMEOUT_SECONDS, "row_version": w["row_version"]})
        steps.append(f"run time limit set to {TIMEOUT_SECONDS} s")
        w = svc.get_workflow(WORKFLOW_ID)
    if w["state"] in ("ACTIVE", "DEGRADED") and (w.get("n8n_workflow_id") != n8n_workflow_id
                                                  or w.get("webhook_path") != WEBHOOK_PATH):
        svc.set_workflow_state(actor, WORKFLOW_ID, "PAUSED", "re-linking WF08-10 to n8n")
        steps.append("paused to re-link")
        w = svc.get_workflow(WORKFLOW_ID)
    if w.get("n8n_workflow_id") != n8n_workflow_id or w.get("webhook_path") != WEBHOOK_PATH:
        svc.update_workflow(actor, WORKFLOW_ID, {"n8n_workflow_id": n8n_workflow_id, "webhook_path": WEBHOOK_PATH,
                                                 "row_version": w["row_version"]})
        steps.append(f"linked to n8n workflow {n8n_workflow_id} (webhook {WEBHOOK_PATH})")
        w = svc.get_workflow(WORKFLOW_ID)
    version = next((v for v in w.get("versions", []) if v["version"] == VERSION), None)
    if version is None:
        version = svc.add_version(actor, WORKFLOW_ID, {
            "version": VERSION, "input_schema": INPUT_SCHEMA, "output_schema": OUTPUT_SCHEMA,
            "description": "WF08-10 News and COT Update Scheduler: refreshes each source when due and records its status."})
        if isinstance(version.get("version"), dict):        # tolerate a wrapped response
            version = version["version"]
        steps.append(f"added version {VERSION}")
    if version["status"] == "DRAFT":
        version = svc.validate_version(actor, WORKFLOW_ID, version["version_id"])["version"]
        steps.append(f"validated ({version['status']})")
    if activate:
        if version["status"] != "ACTIVE":
            svc.activate_version(actor, WORKFLOW_ID, version["version_id"], "WF08-10 installation")
            steps.append(f"activated version {VERSION}")
        if svc.get_workflow(WORKFLOW_ID)["state"] != "ACTIVE":
            svc.set_workflow_state(actor, WORKFLOW_ID, "ACTIVE", "WF08-10 installation")
            steps.append("workflow ACTIVE")
    sched = None
    if schedule:
        existing = [x for x in svc.list_schedules()["items"] if x["workflow_id"] == WORKFLOW_ID]
        if existing:
            sched = existing[0]
            steps.append(f"schedule already present ({sched['expression']} {sched['timezone']})")
        else:
            sched = svc.create_schedule(actor, {"name": SCHEDULE_NAME, "workflow_id": WORKFLOW_ID, "kind": "cron",
                                                "expression": schedule, "timezone": schedule_timezone, "input": {},
                                                "enabled": True})
            steps.append(f"schedule created ({schedule} {schedule_timezone})")
    final = svc.get_workflow(WORKFLOW_ID)
    return {"workflow_id": WORKFLOW_ID, "state": final["state"], "version": VERSION, "validation": version.get("validation"),
            "subscriptions": [], "schedule": sched, "steps": steps}
