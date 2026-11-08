"""GET /agents/status and GET /notifications: derived from what each module stored, never invented.

An agent's status reflects its most recent stored run. A module whose tables are missing reports
``unknown``; an agent that does not exist yet in the backend reports ``disabled``.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from aurum.api.market_queries import MarketQueries, _iso, _utc
from aurum.config import Settings
from aurum.database.connection import session_scope

log = logging.getLogger(__name__)

STATUS = {"success": "completed", "ok": "completed", "completed": "completed", "partial": "warning",
          "insufficient_data": "warning", "no_data": "warning", "warning": "warning", "skipped": "warning",
          "failed": "failed", "error": "failed", "running": "running"}
LEVEL = {"completed": "info", "running": "info", "warning": "warning", "failed": "error"}


def _agent(id_: str, name: str, description: str, status: str = "unknown", last: datetime | None = None,
           result: str | None = None, errors: int | None = None, config: dict[str, Any] | None = None,
           logs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"id": id_, "name": name, "description": description, "status": status, "lastExecution": _iso(last),
            "lastResult": result, "errorCount": errors, "configuration": config or {}, "logs": logs or []}


def _safe(fn: Callable[[], dict[str, Any]], fallback: dict[str, Any]) -> dict[str, Any]:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - a module's tables may not exist on this database
        log.debug("Agent status unavailable", extra={"agent": fallback["id"], "error": type(exc).__name__})
        return fallback


class SystemQueries:
    def __init__(self, engine: Engine, settings: Settings):
        self.engine = engine
        self.settings = settings

    # ------------------------------------------------------------------ agents
    def agents(self, now: datetime | None = None) -> list[dict[str, Any]]:
        now = now or datetime.now(timezone.utc)
        defs = [
            ("market-data", "Market Data Agent", "Collects and validates MT5 OHLCV candles for configured instruments and timeframes.", self._market_data),
            ("cot", "COT Intelligence Agent", "Ingests weekly CFTC Commitments of Traders reports and computes positioning metrics.", self._cot),
            ("structure", "Market Structure Agent", "Identifies swing structure, BOS and CHoCH across timeframes.", self._analysis),
            ("smc", "ICT/SMC Analysis Agent", "Detects order blocks, fair value gaps and liquidity pools for chart overlays.", self._analysis),
            ("sessions", "Trading Session Agent", "Tracks session ranges, overlaps and liquidity conditions.", self._sessions),
            ("risk", "Risk Management Agent", "Monitors exposure and drawdown against the configured risk limits. Places no orders.", self._risk),
            ("learning", "Trade Learning Agent", "Learns from closed trades, trials what worked and replicates or terminates itself.", self._learning),
            ("strategy", "Strategy Evaluation Agent", "Backtests the setup templates and gates on stored history.", self._strategy),
            ("decision", "AI Decision Agent", "Combines evidence into trade candidates for human review. Never executes trades.", self._decision),
        ]
        out = []
        for id_, name, desc, fn in defs:
            if fn is None:  # not implemented in the backend yet: say so instead of pretending it runs
                out.append(_agent(id_, name, desc, "disabled", result="Not implemented in the backend yet.",
                                  config={"enabled": False}))
                continue
            base = _agent(id_, name, desc)
            out.append(_safe(lambda fn=fn, base=base: {**base, **fn(now)}, base))
        return out

    def _market_data(self, now: datetime) -> dict[str, Any]:
        from aurum.database.models import DataCollectionLog as L
        with session_scope(self.engine) as s:
            run = s.execute(select(L).where(L.event == "run").order_by(L.started_at.desc()).limit(1)).scalar()
            errors = s.execute(select(func.count()).select_from(L).where(L.status == "failed",
                                                                         L.started_at >= now - timedelta(hours=24))).scalar_one()
            recent = s.execute(select(L).order_by(L.started_at.desc()).limit(8)).scalars().all()
            logs = [{"timestamp": _iso(r.started_at), "level": LEVEL.get(STATUS.get(r.status, ""), "info"),
                     "message": " ".join(x for x in (r.event, r.symbol or "", r.timeframe or "", r.status,
                                                     f"· {r.rows_stored} stored" if r.rows_stored is not None else "",
                                                     f"· {r.message}" if r.message else "") if x)} for r in recent]
            if run is None:
                status, result, last = "idle", "No collection run stored yet.", None
            elif run.finished_at is None and _utc(run.started_at) >= now - timedelta(minutes=15):
                status, result, last = "running", "Collection in progress.", run.started_at
            else:
                status, last = STATUS.get(run.status, "unknown"), run.started_at
                result = run.message or f"Last run {run.status}."
        cfg = {"intervalSeconds": self.settings.collection_interval_seconds,
               "instruments": ", ".join(self.settings.enabled_instruments),
               "timeframes": ", ".join(self.settings.enabled_timeframes), "enabled": True}
        return {"status": status, "lastExecution": _iso(last), "lastResult": result, "errorCount": int(errors),
                "configuration": cfg, "logs": logs}

    def _cot(self, now: datetime) -> dict[str, Any]:
        from aurum.cot.models import CotIngestionLog as C
        with session_scope(self.engine) as s:
            rows = s.execute(select(C).order_by(C.started_at.desc()).limit(8)).scalars().all()
            errors = s.execute(select(func.count()).select_from(C).where(C.status == "failed",
                                                                         C.started_at >= now - timedelta(days=7))).scalar_one()
            if not rows:
                return {"status": "idle", "lastResult": "No COT ingestion run stored yet.", "errorCount": int(errors),
                        "configuration": {"schedule": "weekly (CFTC release)", "enabled": True}}
            last = rows[0]
            logs = [{"timestamp": _iso(r.started_at), "level": LEVEL.get(STATUS.get(r.status, ""), "info"),
                     "message": f"{r.job_type} {r.report_type or ''} {r.status} · {r.records_inserted} inserted, "
                                f"{r.records_updated} updated" + (f" · {r.error_message}" if r.error_message else "")} for r in rows]
            return {"status": STATUS.get(last.status, "unknown"), "lastExecution": _iso(last.started_at),
                    "lastResult": last.error_message or f"{last.job_type} {last.status}: {last.records_inserted} inserted.",
                    "errorCount": int(errors), "configuration": {"schedule": "weekly (CFTC release)", "enabled": True},
                    "logs": logs}

    def _analysis(self, now: datetime) -> dict[str, Any]:
        from aurum.analysis.services.db_models import TaAnalysisRun as T
        with session_scope(self.engine) as s:
            rows = s.execute(select(T).order_by(T.generated_at.desc()).limit(8)).scalars().all()
            if not rows:
                return {"status": "idle", "lastResult": "No analysis run stored yet.", "errorCount": 0,
                        "configuration": {"trigger": "python -m aurum.cli analyze", "enabled": True}}
            last = rows[0]
            logs = [{"timestamp": _iso(r.generated_at), "level": LEVEL.get(STATUS.get(r.status, ""), "info"),
                     "message": f"{r.symbol} {r.timeframe or ''} {r.status}" + (f" · bias {r.bias}" if r.bias else "")} for r in rows]
            return {"status": STATUS.get(last.status, "unknown"), "lastExecution": _iso(last.generated_at),
                    "lastResult": f"{last.symbol} {last.timeframe or ''}: {last.status}" + (f", bias {last.bias}" if last.bias else ""),
                    "errorCount": sum(STATUS.get(r.status) == "failed" for r in rows),
                    "configuration": {"engineVersion": last.engine_version, "enabled": True}, "logs": logs}

    def _sessions(self, now: datetime) -> dict[str, Any]:
        from aurum.market_intelligence.db_models import MiRun as M
        with session_scope(self.engine) as s:
            rows = s.execute(select(M).order_by(M.generated_at.desc()).limit(8)).scalars().all()
            if not rows:
                return {"status": "idle", "lastResult": "No market-intelligence run stored yet.", "errorCount": 0,
                        "configuration": {"enabled": True}}
            last = rows[0]
            logs = [{"timestamp": _iso(r.generated_at), "level": LEVEL.get(STATUS.get(r.status, ""), "info"),
                     "message": f"{r.run_type} {r.symbol or ''} {r.status}"} for r in rows]
            return {"status": STATUS.get(last.status, "unknown"), "lastExecution": _iso(last.generated_at),
                    "lastResult": f"{last.run_type} {last.symbol or ''}: {last.status}",
                    "errorCount": sum(STATUS.get(r.status) == "failed" for r in rows),
                    "configuration": {"methodologyVersion": last.methodology_version, "enabled": True}, "logs": logs}

    def _risk(self, now: datetime) -> dict[str, Any]:
        from aurum.account.queries import AccountQueries
        aq = AccountQueries(self.engine, self.settings)
        lim = aq.limits()
        configured = {k: v for k, v in lim.items() if k != "enforcedBy" and v is not None}
        snap = aq.latest_snapshot()
        if not configured:
            return {"status": "disabled", "lastResult": "No risk limits configured (RISK_MAX_* settings).",
                    "configuration": {"enabled": False}}
        if snap is None:
            return {"status": "idle", "lastResult": "No account snapshot stored yet.", "configuration": {**configured, "enabled": True}}
        found = aq.breaches(now)
        return {"status": "warning" if found else "completed", "lastExecution": _iso(snap.captured_at),
                "lastResult": "; ".join(m for _, _, m in found) or "All monitored limits respected.",
                "errorCount": len(found), "configuration": {**configured, "enabled": True},
                "logs": [{"timestamp": _iso(snap.captured_at), "level": "warning", "message": m} for _, _, m in found]}

    def _learning(self, now: datetime) -> dict[str, Any]:
        from aurum.learning.store import LineageStore
        s = self.settings
        lin = LineageStore(Path(s.resolve(s.data_dir)) / "learning").load("L1")
        if lin is None:
            return {"status": "idle", "lastResult": "No lineage yet: run python -m aurum.cli learn run.", "errorCount": 0,
                    "configuration": {"enabled": True}}
        events = sorted((e for a in lin.agents for e in a.events), key=lambda e: e.at, reverse=True)
        status = {"running": "running", "replicated": "completed", "capped": "completed", "terminated": "failed",
                  "empty": "idle"}.get(lin.status, "unknown")
        cfg = lin.config
        return {"status": status, "lastExecution": _iso(events[0].at) if events else None,
                "lastResult": events[0].message if events else f"{lin.trades_observed} trades observed.",
                "errorCount": sum(a.failed_trials for a in lin.agents),
                "configuration": {"maxFailedTrials": getattr(cfg, "max_failed_trials", 3), "enabled": True},
                "logs": [{"timestamp": _iso(e.at), "level": "warning" if e.kind in ("trial_failed", "terminated") else "info",
                          "message": e.message} for e in events[:8]]}

    # ------------------------------------------------------------------ notifications
    def _strategy(self, now: datetime) -> dict[str, Any]:
        from aurum.decision.repository import DecisionRepository
        latest = DecisionRepository(self.engine).latest_backtest()
        if latest is None:
            return {"status": "idle", "lastResult": "No backtest has been run yet.", "configuration": {"enabled": True}}
        m = latest["metrics"]
        return {"status": "completed", "lastExecution": _iso(latest["run"]["created_at"]), "errorCount": 0, "configuration": {"enabled": True},
                "lastResult": f"{m['totalTrades']} simulated trades, {m['winRate']}% win rate."}

    def _decision(self, now: datetime) -> dict[str, Any]:
        from aurum.decision.repository import DecisionRepository
        repo = DecisionRepository(self.engine)
        runs = repo.recent_runs(1)
        ai = self.settings.ai_review_enabled
        if not runs:
            return {"status": "idle", "lastResult": "No decision run yet: run python -m aurum.cli decide run.",
                    "configuration": {"enabled": True, "aiReview": ai}}
        last = runs[0]
        counts = repo.counts()
        pending = counts.get("new", 0) + counts.get("under_review", 0)
        return {"status": "completed", "lastExecution": _iso(last["created_at"]), "errorCount": 0, "configuration": {"enabled": True, "aiReview": ai},
                "lastResult": f"{last['symbol']}: {last['status']}. {pending} candidate(s) waiting for review."}

    def notifications(self, now: datetime | None = None, limit: int = 50) -> list[dict[str, Any]]:
        now = now or datetime.now(timezone.utc)
        out: list[dict[str, Any]] = []

        def add(key: str, category: str, severity: str, title: str, message: str, at: datetime | None) -> None:
            nid = hashlib.sha1(f"{category}|{key}".encode()).hexdigest()[:16]
            out.append({"id": nid, "category": category, "severity": severity, "title": title, "message": message,
                        "timestamp": _iso(at or now), "read": False})

        for fn in (self._n_feed, self._n_collection, self._n_quality, self._n_cot, self._n_risk, self._n_decision):
            try:
                fn(add, now)
            except Exception as exc:  # noqa: BLE001 - missing module tables simply contribute nothing
                log.debug("Notification source unavailable", extra={"source": fn.__name__, "error": type(exc).__name__})
        out.sort(key=lambda n: n["timestamp"], reverse=True)
        return out[:limit]

    def _n_decision(self, add, now: datetime) -> None:
        from aurum.decision.db_models import CandidateAiReview as R, CandidateStatusHistory as H, TradeCandidate as C
        since = now - timedelta(days=2)
        with session_scope(self.engine) as s:
            fresh = s.execute(select(C).where(C.status.in_(("new", "under_review")), C.grade.in_(("A", "B")), C.created_at >= since)
                              .order_by(C.created_at.desc()).limit(10)).scalars().all()
            for c in fresh:
                add(f"candidate-{c.candidate_uid}", "approval_required", "info", f"New grade {c.grade} candidate on {c.symbol}",
                    f"{c.direction} via {c.template} on {c.timeframe}, waiting for your review. AURUM AI does not place trades.", c.created_at)
            gone = s.execute(select(H.candidate_uid, H.at, C.symbol).join(C, C.candidate_uid == H.candidate_uid)
                             .where(H.to_status == "expired", H.at >= since).order_by(H.at.desc()).limit(10)).all()
            for uid, at, symbol in gone:
                add(f"expired-{uid}", "signal_generated", "info", f"Candidate expired on {symbol}", "It reached its time limit or its stop before a decision.", at)
            bad = s.execute(select(R).where(R.status == "unavailable", R.reason.in_(("over_budget", "hourly_limit", "auth_error", "timeout")),
                                            R.created_at >= since).order_by(R.id.desc()).limit(1)).scalars().first()
            if bad is not None:
                add(f"ai-{bad.reason}-{bad.created_at:%Y%m%d}", "signal_generated", "warning", "AI reviewer unavailable",
                    f"Reason: {bad.reason}. Candidates are still published, without its review and with confidence capped.", bad.created_at)

    def _n_feed(self, add, now: datetime) -> None:
        mq = MarketQueries(self.engine)
        last = mq.latest_tick_time()
        if last is not None and mq.feed_state(self.settings.collection_interval_seconds, now)["mt5"] == "disconnected":
            age = int((now - last).total_seconds() // 60)
            add(f"stale-{_iso(last)}", "mt5_disconnection", "critical", "MT5 quotes are not updating",
                f"The latest stored tick is {age} min old. Check the MT5 terminal and the stream-ticks process.", last)

    def _n_collection(self, add, now: datetime) -> None:
        from aurum.database.models import DataCollectionLog as L
        with session_scope(self.engine) as s:
            rows = s.execute(select(L).where(L.status == "failed", L.started_at >= now - timedelta(days=7))
                             .order_by(L.started_at.desc()).limit(20)).scalars().all()
            for r in rows:
                what = " ".join(x for x in (r.symbol, r.timeframe) if x) or r.event
                add(f"log-{r.id}", "collection_failure", "warning", f"Collection failed for {what}",
                    r.message or f"{r.event} collection failed.", r.started_at)

    def _n_quality(self, add, now: datetime) -> None:
        from aurum.database.models import DataQualityIssue as Q
        with session_scope(self.engine) as s:
            rows = s.execute(select(Q).where(Q.resolved.is_(False), Q.severity == "error")
                             .order_by(Q.last_seen_at.desc()).limit(20)).scalars().all()
            for r in rows:
                what = " ".join(x for x in (r.symbol, r.timeframe) if x)
                add(f"dq-{r.fingerprint}", "invalid_market_data", "warning",
                    f"{r.issue_type.replace('_', ' ').capitalize()}{' · ' + what if what else ''}",
                    f"Seen {r.occurrences}× since {_iso(r.first_seen_at)}; the affected rows are excluded from analysis.",
                    r.last_seen_at)

    def _n_cot(self, add, now: datetime) -> None:
        from aurum.cot.models import CotIngestionLog as C
        with session_scope(self.engine) as s:
            rows = s.execute(select(C).where(C.started_at >= now - timedelta(days=14), C.status != "running")
                             .order_by(C.started_at.desc()).limit(5)).scalars().all()
            for r in rows:
                if r.status == "failed":
                    add(f"cot-{r.id}", "cot_update", "warning", "COT ingestion failed",
                        r.error_message or "The CFTC download did not complete.", r.started_at)
                elif r.records_inserted or r.records_updated:
                    add(f"cot-{r.id}", "cot_update", "info", "COT data updated",
                        f"{r.records_inserted} new and {r.records_updated} revised {r.report_type or 'COT'} records.",
                        r.finished_at or r.started_at)

    def _n_risk(self, add, now: datetime) -> None:
        from aurum.account.queries import AccountQueries
        aq = AccountQueries(self.engine, self.settings)
        snap = aq.latest_snapshot()
        for key, severity, message in aq.breaches(now):
            add(f"risk-{key}-{now.date().isoformat()}", "risk_limit", severity, "Risk limit reached", message,
                snap.captured_at if snap else now)
