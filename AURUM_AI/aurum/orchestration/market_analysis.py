"""WF08-02 Market Analysis Orchestrator - the backend half.

WF08-01 publishes ``ai.analysis.requested`` when a candle closes. WF08-02 answers those requests. The n8n workflow only
sequences calls; the analysis itself is one backend operation (Module 05, which already reads Modules 01-04), so there is
no second copy of the analysis engine inside n8n:

* ``config``       - the versioned ``market_analysis`` section (off switch, AI review, data rules).
* ``preflight``    - may this run proceed? (switch on, database reachable, Module 05 schema ready).
* ``pending``      - the symbols that have analysis requests no analysis has answered yet.
* ``data_quality`` - does Module 01 hold the closed candles Module 05 needs for one symbol, and how old are they?
* ``run``          - run Module 05 for one symbol, store the result (Module 05 does), and publish
                     ``ai.analysis.completed`` or ``ai.analysis.failed``.

A request is answered once an analysis event for its symbol exists whose ``occurred_at`` is not earlier than the request
was published; ``run`` stamps its result event with the newest request it served. Requests that arrive while a run is in
progress therefore stay pending for the next run. The result event's idempotency key makes a repeated run for the same
requests a no-op in the event store.

Nothing here places, approves or executes a trade, and nothing talks to MT5. Module 05 produces candidates for human
review only.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import select

from aurum.config import TIMEFRAME_SECONDS
from aurum.orchestration import SCHEMA_VERSION
from aurum.orchestration.catalogue import CATALOGUE, M05, Envelope
from aurum.orchestration.config_model import MarketAnalysisCfg
from aurum.orchestration.db_models import OrchEvent
from aurum.orchestration.repository import jsonable

WORKFLOW_ID = "wf08-02-market-analysis"
WEBHOOK_PATH = "aurum-wf08-02-market-analysis"
VERSION = "1.0.0"
REQUEST_EVENT = "ai.analysis.requested"
COMPLETED_EVENT, FAILED_EVENT = "ai.analysis.completed", "ai.analysis.failed"
SUBSCRIPTION_NAME = "WF08-02 answers analysis requests"

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "symbols": {"type": "array", "items": {"type": "string", "maxLength": 16}, "maxItems": 20},
        "dryRun": {"type": "boolean"},
        "force": {"type": "boolean"},                 # analyse the given symbols even without a pending request
        "reason": {"type": "string", "maxLength": 200},
        "event": {"type": "object"},                  # a single request event (when coalescing is off)
        "batch": {"type": "object"},                  # {correlation_id, event_type} for a coalesced trigger
    },
}
OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["completed", "partial", "skipped"]},
        "reason": {"type": "string"},
        "analysed": {"type": "array"},
        "skipped": {"type": "array"},
    },
}


def _utc(t: datetime) -> datetime:
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


def _iso(t: datetime | None) -> str | None:
    return _utc(t).isoformat().replace("+00:00", "Z") if t else None


class MarketAnalyst:
    """Stateless over the database: safe to call from any API worker. ``svc`` is the OrchestrationService."""

    def __init__(self, svc: Any, decision: Callable[[], Any] | None = None, candles: Any = None):
        from aurum.database.repository import Repository
        self.svc = svc
        self.engine = svc.repo.engine
        self._decision_factory = decision
        self._decision: Any = None
        self.candles = candles or Repository(self.engine)

    # ---- wiring -------------------------------------------------------------------------------------------------------------
    def cfg(self) -> MarketAnalysisCfg:
        return self.svc.config().market_analysis

    @property
    def decision(self) -> Any:
        if self._decision is None:
            if self._decision_factory is not None:
                self._decision = self._decision_factory()
            else:
                from aurum.decision.service import DecisionService
                self._decision = DecisionService(self.svc.settings, self.engine)
        return self._decision

    def config_view(self) -> dict[str, Any]:
        c = self.cfg()
        return {"workflow_id": WORKFLOW_ID, "enabled": c.enabled, "kill_switch": not c.enabled, "ai_review": c.ai_review,
                "coalesce": c.coalesce, "lookback_hours": c.lookback_hours, "max_symbols_per_run": c.max_symbols_per_run,
                "required_timeframes": list(c.required_timeframes), "max_data_age_hours": c.max_data_age_hours,
                "block_when_stale": c.block_when_stale, "request_event": REQUEST_EVENT}

    # ---- preflight ------------------------------------------------------------------------------------------------------------
    def preflight(self) -> dict[str, Any]:
        c = self.cfg()
        checks: dict[str, Any] = {"enabled": c.enabled}
        reasons: list[str] = []
        if not c.enabled:
            reasons.append("Market analysis is switched off (market_analysis.enabled = false).")
        try:
            status = self.decision.status()
            checks["database"] = "ok"
            checks["module_05_schema"] = bool(status.get("schema_ready"))
            checks["ai_reviewer"] = (status.get("ai_reviewer") or {}).get("enabled", False)
            if not status.get("schema_ready"):
                reasons.append("Module 05 tables are missing; run the database migrations first.")
        except Exception as exc:  # noqa: BLE001 - any failure here means: do not proceed
            checks["database"] = f"unavailable ({type(exc).__name__})"
            reasons.append("Module 05 or the AURUM database is not reachable.")
        return {"proceed": not reasons, "checks": checks, "reasons": reasons, "checked_at": _iso(self.svc.now())}

    # ---- pending requests -----------------------------------------------------------------------------------------------------
    def _requests(self, symbol: str | None = None) -> dict[str, list[dict[str, Any]]]:
        """Unanswered request events per symbol, oldest first."""
        since = _utc(self.svc.now()) - timedelta(hours=self.cfg().lookback_hours)
        with self.svc.repo.read() as s:
            rows = s.execute(select(OrchEvent.event_id, OrchEvent.event_type, OrchEvent.occurred_at, OrchEvent.published_at,
                                    OrchEvent.payload, OrchEvent.correlation_id)
                             .where(OrchEvent.event_type.in_([REQUEST_EVENT, COMPLETED_EVENT, FAILED_EVENT]),
                                    OrchEvent.environment == self.svc.env, OrchEvent.published_at >= since)
                             .order_by(OrchEvent.seq)).all()
        answered: dict[str, datetime] = {}
        requests: dict[str, list[dict[str, Any]]] = {}
        for event_id, etype, occurred, published, payload, corr in rows:
            sym = str((payload or {}).get("symbol") or "").upper()
            if not sym or (symbol and sym != symbol):
                continue
            if etype == REQUEST_EVENT:
                tfs = (payload or {}).get("timeframes") or ([payload["timeframe"]] if (payload or {}).get("timeframe") else [])
                requests.setdefault(sym, []).append({"event_id": event_id, "published_at": _utc(published),
                                                     "timeframes": [str(t).upper() for t in tfs], "correlation_id": corr})
            else:
                answered[sym] = max(answered.get(sym, _utc(occurred)), _utc(occurred))
        return {sym: [r for r in reqs if sym not in answered or r["published_at"] > answered[sym]]
                for sym, reqs in requests.items()}

    def pending(self, symbols: list[str] | None = None, force: bool = False) -> dict[str, Any]:
        c = self.cfg()
        want = [s.upper() for s in symbols] if symbols else None
        open_requests = {sym: reqs for sym, reqs in self._requests().items() if reqs}
        items: list[dict[str, Any]] = []
        for sym in (want or sorted(open_requests)):
            reqs = open_requests.get(sym, [])
            if not reqs and not (force and want):
                continue
            order = {t: i for i, t in enumerate(TIMEFRAME_SECONDS)}
            tfs = sorted({t for r in reqs for t in r["timeframes"]}, key=lambda t: order.get(t, 99))
            items.append({"symbol": sym, "requests": len(reqs), "timeframes": tfs, "forced": not reqs,
                          "oldest_request_at": _iso(reqs[0]["published_at"]) if reqs else None,
                          "newest_request_at": _iso(reqs[-1]["published_at"]) if reqs else None})
        deferred = items[c.max_symbols_per_run:]
        items = items[:c.max_symbols_per_run]
        return {"enabled": c.enabled, "items": items if c.enabled else [], "count": len(items) if c.enabled else 0,
                "deferred": [d["symbol"] for d in deferred]}

    # ---- data quality ---------------------------------------------------------------------------------------------------------
    def data_quality(self, symbol: str) -> dict[str, Any]:
        c = self.cfg()
        symbol = symbol.upper()
        now = _utc(self.svc.now())
        out: dict[str, Any] = {"symbol": symbol, "ok": False, "stale": False, "reasons": [], "timeframes": {},
                               "checked_at": _iso(now)}
        try:
            known = [s.upper() for s in self.decision.symbols()]
        except Exception as exc:  # noqa: BLE001
            out["reasons"].append(f"Module 05 is not available ({type(exc).__name__}).")
            return out
        if symbol not in known:
            out["reasons"].append(f"{symbol} is not an instrument Module 05 analyses.")
            return out
        iid = self.candles.instrument_id(symbol)
        if iid is None:
            out["reasons"].append(f"Module 01 has no instrument {symbol}.")
            return out
        for tf in c.required_timeframes:
            opened = self.candles.latest_candle_ts(iid, tf, complete_only=True)
            if opened is None:
                out["timeframes"][tf] = {"available": False}
                out["reasons"].append(f"Module 01 has no closed {tf} candle for {symbol}.")
                continue
            closed = _utc(opened) + timedelta(seconds=TIMEFRAME_SECONDS[tf])
            age_h = (now - closed).total_seconds() / 3600
            stale = age_h > c.max_data_age_hours
            out["timeframes"][tf] = {"available": True, "latest_candle_open": _iso(opened), "age_hours": round(age_h, 2),
                                     "stale": stale}
            if stale:
                out["stale"] = True
                if c.block_when_stale:
                    out["reasons"].append(f"The newest {tf} candle for {symbol} is {age_h:.0f} hours old.")
        out["ok"] = not out["reasons"]
        return out

    # ---- run one symbol -------------------------------------------------------------------------------------------------------
    def run(self, symbol: str, *, dry_run: bool = False, force: bool = False, execution_id: str | None = None) -> dict[str, Any]:
        c = self.cfg()
        symbol = symbol.upper()
        base: dict[str, Any] = {"symbol": symbol, "requests": 0}
        if not c.enabled:
            return {**base, "status": "skipped", "reason": "Market analysis is switched off."}
        reqs = self._requests(symbol).get(symbol, [])
        base["requests"] = len(reqs)
        if not reqs and not force:
            return {**base, "status": "skipped", "reason": "No unanswered analysis request for this symbol."}
        if dry_run:
            return {**base, "status": "dry_run", "reason": "Dry run: would run Module 05 analysis."}
        # The result event is stamped with the newest request it serves, which is what marks those requests answered.
        through = reqs[-1]["published_at"] if reqs else _utc(self.svc.now())
        correlation = execution_id or (reqs[-1]["correlation_id"] if reqs else str(uuid.uuid4()))
        causation = reqs[-1]["event_id"] if reqs else None
        key = f"wf08-02:{symbol}:{through.isoformat()}"
        started = time.monotonic()
        try:
            result = self.decision.run(symbol, ai=c.ai_review)
        except Exception as exc:  # noqa: BLE001 - reported to the workflow and recorded as ai.analysis.failed
            error = f"{type(exc).__name__}: {exc}"[:300]
            event_id = self._publish(FAILED_EVENT, {"symbol": symbol, "error": error}, through, key, correlation, causation)
            return {**base, "status": "failed", "reason": error, "event_id": event_id,
                    "duration_seconds": round(time.monotonic() - started, 2)}
        signals = result.get("signals") or []
        verdict = ((result.get("analysis") or {}).get("decision") or {})
        event_id = self._publish(COMPLETED_EVENT, {"symbol": symbol, "run_id": str(result.get("run_id")),
                                                   "candidates": len(signals)}, through, key, correlation, causation)
        return {**base, "status": "completed", "analysis_status": result.get("status"), "run_id": str(result.get("run_id")),
                "candidates": len(signals), "decision": verdict.get("decision"),
                "signal_ids": [str(x.get("id")) for x in signals][:10], "event_id": event_id,
                "duration_seconds": round(time.monotonic() - started, 2),
                "reason": f"Module 05 analysed {symbol}: {len(signals)} candidate(s), decision {verdict.get('decision') or 'n/a'}."}

    def _publish(self, event_type: str, payload: dict[str, Any], occurred_at: datetime, key: str, correlation_id: str,
                 causation_id: str | None) -> str | None:
        """Module 05's result events, written by this backend on its behalf (n8n may not publish them)."""
        spec = CATALOGUE[event_type]
        env = Envelope(event_id=str(uuid.uuid4()), event_type=event_type, event_version=spec.versions[-1],
                       occurred_at=occurred_at, source_module=M05, correlation_id=str(correlation_id)[:64],
                       idempotency_key=key[:160], environment=self.svc.env, payload=jsonable(payload),
                       published_at=self.svc.now(), causation_id=causation_id, schema_version=SCHEMA_VERSION)
        with self.svc.repo.write() as s:
            ev = self.svc._insert_event(s, env, "internal")
            return ev.event_id if ev else None


# ---- installation (used by `python -m aurum.cli orchestration setup-wf08-02`) ----------------------------------------------
def install(svc: Any, actor: Any, n8n_workflow_id: str, *, activate: bool = True, subscribe: bool = True) -> dict[str, Any]:
    """Turns the reserved WF08-02 placeholder into a real workflow: links it to the n8n workflow, adds version 1.0.0 with
    its contract, validates and activates it, and subscribes it to ``ai.analysis.requested``. Safe to re-run."""
    steps: list[str] = []
    svc.ensure_ready()
    w = svc.get_workflow(WORKFLOW_ID)
    if w["state"] in ("ACTIVE", "DEGRADED") and (w.get("n8n_workflow_id") != n8n_workflow_id
                                                  or w.get("webhook_path") != WEBHOOK_PATH):
        svc.set_workflow_state(actor, WORKFLOW_ID, "PAUSED", "re-linking WF08-02 to n8n")
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
            "description": "WF08-02 Market Analysis Orchestrator: answers analysis requests with one Module 05 run per "
                           "symbol and publishes the result event."})
        if isinstance(version.get("version"), dict):        # tolerate a wrapped response
            version = version["version"]
        steps.append(f"added version {VERSION}")
    if version["status"] == "DRAFT":
        version = svc.validate_version(actor, WORKFLOW_ID, version["version_id"])["version"]
        steps.append(f"validated ({version['status']})")
    if activate:
        if version["status"] != "ACTIVE":
            svc.activate_version(actor, WORKFLOW_ID, version["version_id"], "WF08-02 installation")
            steps.append(f"activated version {VERSION}")
        if svc.get_workflow(WORKFLOW_ID)["state"] != "ACTIVE":
            svc.set_workflow_state(actor, WORKFLOW_ID, "ACTIVE", "WF08-02 installation")
            steps.append("workflow ACTIVE")
    sub = None
    if subscribe:
        existing = [x for x in svc.list_subscriptions()["items"]
                    if x.get("workflow_id") == WORKFLOW_ID and x.get("event_pattern") == REQUEST_EVENT]
        if existing:
            sub = existing[0]
            steps.append("subscription already present")
        else:
            sub = svc.create_subscription(actor, {"name": SUBSCRIPTION_NAME, "event_pattern": REQUEST_EVENT,
                                                  "target_kind": "workflow", "workflow_id": WORKFLOW_ID})
            steps.append(f"subscribed to {REQUEST_EVENT}")
    final = svc.get_workflow(WORKFLOW_ID)
    return {"workflow_id": WORKFLOW_ID, "state": final["state"], "placeholder": final.get("placeholder"),
            "version": VERSION, "validation": version.get("validation"), "subscription": sub, "steps": steps}
