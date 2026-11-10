"""WF08-01 Market Monitoring Scheduler - the backend half.

The n8n workflow (``n8n/workflows/aurum_wf08_01_market_monitoring.json``) only sequences calls; every decision it
branches on is made here, so the rules live in one tested place:

* ``config``          - the versioned ``market_monitoring`` section (symbols, timeframes, settle delay, off switch).
* ``preflight``       - may this run proceed? (switch on, database reachable, Module 01 has candles).
* ``eligible``        - the symbol x timeframe pairs to check this run.
* ``candle_status``   - the latest CLOSED candle for one pair, and whether it has settled (Module 01 stored it).
* ``dispatch_status`` - has an analysis request already been published for exactly that candle?

Duplicate prevention has two layers: ``dispatch_status`` lets the workflow skip known candles, and the request event
carries the idempotency key ``wf08-01:<symbol>:<timeframe>:<candle open UTC>``, which the event store accepts only
once - so two overlapping runs still cannot request the same candle twice.

Read-only: nothing here writes, trades or talks to MT5. Candles come from Module 01's repository only.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.engine import Engine

from aurum.config import TIMEFRAME_SECONDS
from aurum.orchestration.config_model import MarketMonitoringCfg, OrchestrationConfig

WORKFLOW_ID = "wf08-01-market-monitoring"
WEBHOOK_PATH = "aurum-wf08-01-market-monitoring"
VERSION = "1.0.0"
DEFAULT_SCHEDULE = "1-59/5 * * * *"          # 1 minute after every M5 close (also every M15/H1/H4/D1 boundary)
SCHEDULE_NAME = "WF08-01 market monitoring (every 5 minutes, after candle close)"

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "symbols": {"type": "array", "items": {"type": "string", "maxLength": 16}, "maxItems": 20},
        "timeframes": {"type": "array", "items": {"type": "string", "enum": ["D1", "H4", "H1", "M15", "M5"]}},
        "dryRun": {"type": "boolean"},
        "reason": {"type": "string", "maxLength": 200},
        "event": {"type": "object"},
    },
}
OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["completed", "partial", "skipped"]},
        "reason": {"type": "string"},
        "checked": {"type": "integer"},
        "requested": {"type": "array"},
        "skipped": {"type": "array"},
    },
}


def idempotency_key(symbol: str, timeframe: str, candle_open: datetime) -> str:
    return f"wf08-01:{symbol.upper()}:{timeframe.upper()}:{_utc(candle_open):%Y-%m-%dT%H:%MZ}"


def _utc(t: datetime) -> datetime:
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


def _iso(t: datetime | None) -> str | None:
    return _utc(t).isoformat().replace("+00:00", "Z") if t else None


class MarketMonitor:
    """Stateless over the database: safe to call from any API worker."""

    def __init__(self, engine: Engine, config: Callable[[], OrchestrationConfig],
                 clock: Callable[[], datetime] | None = None, candles: Any = None):
        from aurum.database.repository import Repository
        self.engine = engine
        self._config = config
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.candles = candles or Repository(engine)          # Module 01's candle store - the only market data source

    # ---- configuration --------------------------------------------------------------------------------------------------
    def cfg(self) -> MarketMonitoringCfg:
        return self._config().market_monitoring

    def config_view(self) -> dict[str, Any]:
        c = self.cfg()
        return {"workflow_id": WORKFLOW_ID, "enabled": c.enabled, "kill_switch": not c.enabled, "symbols": c.symbols,
                "timeframes": c.timeframes, "settle_seconds": c.settle_seconds, "stale_factor": c.stale_factor,
                "request_event": c.request_event, "schedule_hint": DEFAULT_SCHEDULE}

    # ---- preflight (system health for this workflow) --------------------------------------------------------------------
    def preflight(self) -> dict[str, Any]:
        c = self.cfg()
        checks: dict[str, Any] = {"enabled": c.enabled}
        reasons: list[str] = []
        if not c.enabled:
            reasons.append("Market monitoring is switched off (market_monitoring.enabled = false).")
        try:
            series = self.candles.available_series()
            checks["database"] = "ok"
            checks["module_01_series"] = len(series)
            if c.require_module_01_data and not series:
                reasons.append("Module 01 has not stored any candles yet; run the collector first.")
        except Exception as exc:  # noqa: BLE001 - any database failure means: do not proceed
            checks["database"] = f"unavailable ({type(exc).__name__})"
            reasons.append("The AURUM database is not reachable.")
        return {"proceed": not reasons, "checks": checks, "reasons": reasons, "checked_at": _iso(self.clock())}

    # ---- eligible pairs ---------------------------------------------------------------------------------------------------
    def eligible(self, symbols: list[str] | None = None, timeframes: list[str] | None = None) -> dict[str, Any]:
        c = self.cfg()
        want_s = [s.upper() for s in symbols] if symbols else c.symbols
        want_t = [t.upper() for t in timeframes] if timeframes else list(c.timeframes)
        excluded: list[dict[str, str]] = []
        for s in want_s:
            if s not in c.symbols:
                excluded.append({"symbol": s, "reason": "not in market_monitoring.symbols"})
        for t in want_t:
            if t not in c.timeframes:
                excluded.append({"timeframe": t, "reason": "not in market_monitoring.timeframes"})
        syms = [s for s in want_s if s in c.symbols]
        tfs = [t for t in want_t if t in c.timeframes]
        items = [{"symbol": s, "timeframe": t} for s in syms for t in tfs]
        return {"enabled": c.enabled, "items": items if c.enabled else [], "count": len(items) if c.enabled else 0,
                "excluded": excluded}

    # ---- candle completion ------------------------------------------------------------------------------------------------
    def candle_status(self, symbol: str, timeframe: str) -> dict[str, Any]:
        c = self.cfg()
        symbol, timeframe = symbol.upper(), timeframe.upper()
        now = _utc(self.clock())
        out: dict[str, Any] = {"symbol": symbol, "timeframe": timeframe, "ready": False, "reason": "",
                               "latest_candle_open": None, "latest_candle_close": None, "age_seconds": None,
                               "stale": False, "checked_at": _iso(now)}
        if timeframe not in TIMEFRAME_SECONDS:
            out["reason"] = f"Unsupported timeframe {timeframe}."
            return out
        iid = self.candles.instrument_id(symbol)
        if iid is None:
            out["reason"] = f"Module 01 has no instrument {symbol}."
            return out
        opened = self.candles.latest_candle_ts(iid, timeframe, complete_only=True)   # closed candles only
        if opened is None:
            out["reason"] = f"Module 01 has no closed {timeframe} candle for {symbol}."
            return out
        period = timedelta(seconds=TIMEFRAME_SECONDS[timeframe])
        closed = _utc(opened) + period
        age = (now - closed).total_seconds()
        out.update(latest_candle_open=_iso(opened), latest_candle_close=_iso(closed), age_seconds=round(age, 1),
                   stale=age > c.stale_factor * period.total_seconds())
        if age < c.settle_seconds:
            out["reason"] = (f"The candle closed {age:.0f}s ago; waiting {c.settle_seconds}s for Module 01 to settle it.")
            return out
        out["ready"] = True
        out["reason"] = ("Latest closed candle is ready." if not out["stale"] else
                         "Latest closed candle is ready but old: collection may be behind or the market is closed.")
        return out

    # ---- duplicate prevention ---------------------------------------------------------------------------------------------
    def dispatch_status(self, symbol: str, timeframe: str, candle_open: datetime) -> dict[str, Any]:
        from aurum.database.connection import session_scope
        from aurum.orchestration.db_models import OrchEvent
        key = idempotency_key(symbol, timeframe, candle_open)
        with session_scope(self.engine) as s:
            row = s.execute(select(OrchEvent.event_id, OrchEvent.published_at)
                            .where(OrchEvent.idempotency_key == key)).first()
        return {"symbol": symbol.upper(), "timeframe": timeframe.upper(), "candle_open": _iso(candle_open),
                "idempotency_key": key, "already_requested": row is not None,
                "event_id": row[0] if row else None, "requested_at": _iso(row[1]) if row else None,
                "request_event": self.cfg().request_event}


# ---- installation (used by `python -m aurum.cli orchestration setup-wf08-01`) ----------------------------------------------
def install(svc: Any, actor: Any, n8n_workflow_id: str, *, schedule: str | None = DEFAULT_SCHEDULE,
            schedule_timezone: str = "UTC", activate: bool = True) -> dict[str, Any]:
    """Turns the reserved WF08-01 placeholder into a real workflow: links it to the imported n8n workflow, adds version
    1.0.0 with its input/output contract, validates it (against n8n when the API key is configured), activates it and
    creates the candle-close schedule. Re-running is safe: each step is skipped when already done."""
    steps: list[str] = []
    svc.ensure_ready()
    w = svc.get_workflow(WORKFLOW_ID)
    if w["state"] in ("ACTIVE", "DEGRADED") and (w.get("n8n_workflow_id") != n8n_workflow_id
                                                  or w.get("webhook_path") != WEBHOOK_PATH):
        svc.set_workflow_state(actor, WORKFLOW_ID, "PAUSED", "re-linking WF08-01 to n8n")
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
            "description": "WF08-01 Market Monitoring Scheduler: candle-close checks, duplicate prevention, one analysis "
                           "request event per new closed candle."})
        if isinstance(version.get("version"), dict):        # tolerate a wrapped response
            version = version["version"]
        steps.append(f"added version {VERSION}")
    if version["status"] == "DRAFT":
        version = svc.validate_version(actor, WORKFLOW_ID, version["version_id"])["version"]
        steps.append(f"validated ({version['status']})")
    if activate:
        if version["status"] != "ACTIVE":
            svc.activate_version(actor, WORKFLOW_ID, version["version_id"], "WF08-01 installation")
            steps.append(f"activated version {VERSION}")
        if svc.get_workflow(WORKFLOW_ID)["state"] != "ACTIVE":
            svc.set_workflow_state(actor, WORKFLOW_ID, "ACTIVE", "WF08-01 installation")
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
    return {"workflow_id": WORKFLOW_ID, "state": final["state"], "placeholder": final.get("placeholder"),
            "version": VERSION, "validation": version.get("validation"), "schedule": sched, "steps": steps}
