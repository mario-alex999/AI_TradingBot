"""WF08-07 Trade Monitoring Orchestrator - the backend half.

Watches the positions AURUM knows about and reports what changed. The positions come from **Module 07's position
records**: Module 09's reports when it exists, and until then the read-only account mirror that Module 01 fills from
the demo account (``sync-account``). WF08-07 asks nothing of a broker and never touches MT5.

For every position, on a schedule and after an execution event:

* reads the position as Module 07 holds it, how fresh that record is, whether Module 09 is deployed, and whether Module
  06 has a risk lock active for the instrument;
* compares it with the last status this workflow recorded and names what changed as plain facts (opened, closed,
  stop-loss or take-profit changed, volume changed, no stop-loss, unexpected state, data stale, risk lock);
* records the new status once in Module 08's audit log, and has Module 07 notify the dashboard when the change matters.

* ``config``      - the versioned ``trade_monitoring`` section (off switch, batch size, staleness limit).
* ``preflight``   - may this run proceed? (switch on, database reachable, Module 07 schema ready).
* ``open_trades`` - open positions, plus positions that closed since they were last recorded.
* ``status``      - one position: the record, its freshness, the changes since the last recorded status.
* ``record``      - record the status if it changed and notify the dashboard if necessary.

WF08-07 does not calculate trade state and takes no trade-management action: it does not move a stop, close or reduce a
position, or request any of those. Why a position closed (stop-loss, take-profit, manual) is the broker's fact; it is
reported only when the position source supplies it, which the account mirror does not. Those calculations and actions
belong to Module 09 and the monitoring module, and every management action still needs a human decision in Module 07.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy import select

from aurum.orchestration.config_model import TradeMonitoringCfg

WORKFLOW_ID = "wf08-07-trade-monitoring"
WEBHOOK_PATH = "aurum-wf08-07-trade-monitoring"
VERSION = "1.0.0"
WORKFLOW_NAME = "WF08-07 Trade Monitoring Orchestrator"
DESCRIPTION = ("Watch the positions Module 07 holds (Module 09's reports, or the read-only account mirror): record each "
               "change once and notify the dashboard. Moves no stop, closes nothing and sends no order.")
TRIGGER_EVENTS = ("execution.completed", "position.closed")
DEFAULT_SCHEDULE = "3-59/5 * * * *"              # every 5 minutes
SCHEDULE_NAME = "WF08-07 trade monitoring"
AUDIT_ACTION = "wf08-07.status_recorded"
AUDIT_ENTITY = "trade_monitoring"
KNOWN_STATES = ("OPEN", "PARTIALLY_CLOSED", "PENDING_CLOSE", "CLOSED")
NOTIFY = {"position_opened": "normal", "position_closed": "normal", "stop_loss_missing": "high", "levels_changed": "normal",
          "volume_changed": "normal", "status_changed": "normal", "unexpected_state": "high", "data_stale": "high",
          "risk_lock_active": "high"}                    # data_fresh and risk_lock_released are recorded, not announced

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "positionIds": {"type": "array", "items": {"type": "string", "maxLength": 32}, "maxItems": 100},
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
        "recorded": {"type": "array"},
        "unchanged": {"type": "integer"},
        "skipped": {"type": "array"},
    },
}


def _utc(t: datetime) -> datetime:
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


def _iso(t: datetime | None) -> str | None:
    return _utc(t).isoformat().replace("+00:00", "Z") if t else None


def _num(v: Any) -> str | None:
    """A price or volume as a canonical string, so 1.0950 and 1.095 compare equal."""
    if v is None:
        return None
    d = Decimal(str(v))
    return format(d.normalize(), "f") if d else "0"


class TradeMonitor:
    """Stateless over the database: safe to call from any API worker. ``svc`` is the OrchestrationService."""

    def __init__(self, svc: Any, approvals: Callable[[], Any] | None = None, deliver: Callable[[Any], dict[str, int]] | None = None,
                 module_09: Callable[[], dict[str, Any]] | None = None):
        self.svc = svc
        self.engine = svc.repo.engine
        self._approvals_factory, self._deliver, self._module_09 = approvals, deliver, module_09
        self._approvals: Any = None

    # ---- wiring -------------------------------------------------------------------------------------------------------------
    def cfg(self) -> TradeMonitoringCfg:
        return self.svc.config().trade_monitoring

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

    def module_09(self) -> dict[str, Any]:
        if self._module_09 is not None:
            return self._module_09()
        from aurum.orchestration.trade_handoff import TradeHandoff
        return TradeHandoff(self.svc).module_09()

    def config_view(self) -> dict[str, Any]:
        c = self.cfg()
        return {"workflow_id": WORKFLOW_ID, "enabled": c.enabled, "kill_switch": not c.enabled,
                "max_trades_per_run": c.max_trades_per_run, "stale_after_seconds": c.stale_after_seconds,
                "closed_lookback_hours": c.closed_lookback_hours, "notify_dashboard": c.notify_dashboard,
                "trigger_events": list(TRIGGER_EVENTS), "schedule_hint": DEFAULT_SCHEDULE, "module_09": self.module_09()}

    # ---- preflight ------------------------------------------------------------------------------------------------------------
    def preflight(self) -> dict[str, Any]:
        from sqlalchemy import inspect

        from aurum.approvals.db_models import ManagedPosition
        c = self.cfg()
        m09 = self.module_09()
        checks: dict[str, Any] = {"enabled": c.enabled, "module_09": m09["status"], "module_09_deployed": m09["deployed"]}
        reasons: list[str] = []
        if not c.enabled:
            reasons.append("Trade monitoring is switched off (trade_monitoring.enabled = false).")
        try:
            checks["module_07_schema"] = inspect(self.engine).has_table(ManagedPosition.__tablename__)
            checks["database"] = "ok"
            if not checks["module_07_schema"]:
                reasons.append("Module 07 tables are missing; run the database migrations first.")
        except Exception as exc:  # noqa: BLE001 - any failure here means: do not proceed
            checks["database"] = f"unavailable ({type(exc).__name__})"
            reasons.append("The AURUM database is not reachable.")
        return {"proceed": not reasons, "checks": checks, "reasons": reasons, "checked_at": _iso(self.svc.now())}

    # ---- the last recorded status of each position ------------------------------------------------------------------------------
    def _last(self, position_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not position_ids:
            return {}
        from aurum.orchestration.db_models import OrchAuditLog as L
        out: dict[str, dict[str, Any]] = {}
        with self.svc.repo.read() as s:
            for pid, details in s.execute(select(L.entity_id, L.details).where(
                    L.action == AUDIT_ACTION, L.entity_type == AUDIT_ENTITY, L.entity_id.in_([p[:64] for p in position_ids]))
                    .order_by(L.seq)).all():
                out[pid] = (details or {}).get("snapshot") or {}
        return out

    # ---- which positions to look at ---------------------------------------------------------------------------------------------
    def open_trades(self, position_ids: list[str] | None = None) -> dict[str, Any]:
        """Retrieve Open Trade Records. Module 07 refreshes its position records from its source (Module 09's reports, or
        the account mirror), then the open positions are listed together with positions that closed since this workflow
        last recorded them. With ``position_ids`` only the listed positions are returned, whatever their state."""
        from aurum.approvals.db_models import ManagedPosition as P
        from aurum.database.connection import session_scope
        c = self.cfg()
        feed: dict[str, Any] = {"source": "none", "as_of": None, "stale": True, "error": None}
        if c.enabled:
            try:
                data = self.approvals.positions()
                feed = {"source": data["source"], "as_of": _iso(data["as_of"]), "stale": bool(data["stale"]), "error": data["error"]}
            except Exception as exc:  # noqa: BLE001 - reported; the stored records are still looked at
                feed["error"] = f"Module 07 could not refresh its positions ({type(exc).__name__})."
        since = _utc(self.svc.now()) - timedelta(hours=c.closed_lookback_hours)
        with session_scope(self.engine) as s:
            if position_ids:
                rows = s.execute(select(P.position_id, P.symbol, P.status).where(P.position_id.in_(position_ids))).all()
            else:
                rows = s.execute(select(P.position_id, P.symbol, P.status).where(
                    (P.status != "CLOSED") | (P.updated_at >= since)).order_by(P.opened_at, P.position_id)).all()
        found = {pid: (sym, st) for pid, sym, st in rows}
        if position_ids:
            items = [{"position_id": p, "symbol": found[p][0] if p in found else None, "status": found[p][1] if p in found else None}
                     for p in dict.fromkeys(position_ids)]
        else:
            last = self._last([pid for pid, (_, st) in found.items() if st == "CLOSED"])
            items = [{"position_id": pid, "symbol": sym, "status": st} for pid, (sym, st) in found.items()
                     if st != "CLOSED" or (last.get(pid) and last[pid].get("status") != "CLOSED")]
        deferred = items[c.max_trades_per_run:]
        items = items[:c.max_trades_per_run]
        return {"enabled": c.enabled, "items": items if c.enabled else [], "count": len(items) if c.enabled else 0,
                "deferred": len(deferred), "open": sum(1 for i in items if i["status"] not in (None, "CLOSED")), "feed": feed}

    # ---- one position ---------------------------------------------------------------------------------------------------------
    def _locks(self, symbol: str) -> tuple[bool | None, list[str]]:
        """Module 06 risk locks that cover this instrument (Module 06 decides them; this only reads)."""
        try:
            locks = self.approvals.risk.active_locks(symbol)
            return bool(locks), [str(lk.get("reason") or lk.get("scope") or "risk lock") for lk in locks][:5]
        except Exception:  # noqa: BLE001 - unknown is reported as unknown, never as "no lock"
            return None, []

    def status(self, position_id: str) -> dict[str, Any]:
        """Request the trade status, check the position, retrieve the monitoring data and check for risk and lifecycle
        events - all from Module 07's record and Module 06's locks; nothing is asked of a broker."""
        from aurum.approvals.db_models import ManagedPosition as P
        from aurum.database.connection import session_scope
        c = self.cfg()
        out: dict[str, Any] = {"position_id": position_id, "exists": False, "ok": False, "changed": False, "events": [],
                               "reasons": []}
        with session_scope(self.engine) as s:
            row = s.get(P, position_id)
            if row is None:
                out["reasons"].append("Module 07 has no such position.")
                return out
            pos = self.approvals._position_dict(row)
            updated_at = row.updated_at
        now = _utc(self.svc.now())
        as_of = pos.get("last_updated") or updated_at
        age = (now - _utc(as_of)).total_seconds() if as_of else None
        closed = pos["status"] == "CLOSED"
        stale = (not closed) and (age is None or age > c.stale_after_seconds)
        locked, lock_reasons = (None, []) if closed else self._locks(pos["symbol"])
        prev = self._last([position_id]).get(position_id)
        snap = {"status": pos["status"], "volume": _num(pos["volume"]), "stop_loss": _num(pos["stop_loss"]),
                "take_profit": _num(pos["take_profit"]), "source": pos["source"], "stale": stale, "locked": bool(locked)}
        events = self._events(pos, snap, prev, age, lock_reasons)
        m09 = self.module_09()
        out.update(exists=True, symbol=pos["symbol"], side=pos["side"], position_status=pos["status"],
                   volume=_num(pos["volume"]), entry_price=_num(pos["entry_price"]), current_price=_num(pos["current_price"]),
                   stop_loss=_num(pos["stop_loss"]), take_profit=_num(pos["take_profit"]),
                   unrealized_pnl=_num(pos["unrealized_pnl"]), realized_pnl=_num(pos["realized_pnl"]),
                   opened_at=_iso(pos["opened_at"]), account_login=pos["account_login"], approval_id=pos["approval_id"],
                   linked_to_approval=bool(pos["approval_id"]), source=pos["source"], last_updated=_iso(as_of),
                   age_seconds=None if age is None else round(age), stale=stale,
                   risk_lock={"active": locked, "reasons": lock_reasons}, module_09=m09,
                   close_reason=None if not closed else "Not reported by the position source.",
                   snapshot=snap, previous=prev, events=events)
        if closed and not prev:
            out["reasons"].append("The position closed before this workflow ever recorded it.")
        elif prev is not None and prev == snap:
            out["reasons"].append("Nothing changed since the last recorded status.")
        else:
            out["changed"] = True
        out["ok"] = out["changed"]
        return out

    @staticmethod
    def _events(pos: dict[str, Any], snap: dict[str, Any], prev: dict[str, Any] | None, age: float | None,
                lock_reasons: list[str]) -> list[dict[str, str]]:
        """What changed since the last recorded status, as facts read from the records."""
        ev: list[dict[str, str]] = []
        label = f"{pos['side']} {snap['volume']} {pos['symbol']} (position {pos['position_id']})"

        def add(kind: str, message: str) -> None:
            ev.append({"type": kind, "severity": NOTIFY.get(kind, "info"), "message": message})

        if snap["status"] == "CLOSED":
            if prev and prev.get("status") != "CLOSED":
                pnl = f" Realised result: {_num(pos['realized_pnl'])}." if pos.get("realized_pnl") is not None else ""
                add("position_closed", f"Closed: {label}.{pnl} The reason for the close is not reported by the position source.")
            return ev
        if prev is None:
            link = "" if pos.get("approval_id") else " It is not linked to an AURUM approval."
            add("position_opened", f"Open: {label}, entry {_num(pos['entry_price'])}, stop-loss {snap['stop_loss'] or 'none'}, "
                                   f"take-profit {snap['take_profit'] or 'none'}.{link}")
        if snap["stop_loss"] is None and (prev is None or prev.get("stop_loss") is not None):
            add("stop_loss_missing", f"No stop-loss on {label}.")
        if snap["status"] not in KNOWN_STATES and (prev is None or prev.get("status") != snap["status"]):
            add("unexpected_state", f"Unexpected position state '{snap['status']}' on {label}.")
        if prev is not None:
            if prev.get("status") != snap["status"] and snap["status"] in KNOWN_STATES:
                add("status_changed", f"{label}: status {prev.get('status')} -> {snap['status']}.")
            if (prev.get("stop_loss"), prev.get("take_profit")) != (snap["stop_loss"], snap["take_profit"]):
                add("levels_changed", f"{label}: stop-loss {prev.get('stop_loss') or 'none'} -> {snap['stop_loss'] or 'none'}, "
                                      f"take-profit {prev.get('take_profit') or 'none'} -> {snap['take_profit'] or 'none'}.")
            if prev.get("volume") != snap["volume"]:
                add("volume_changed", f"{pos['side']} {pos['symbol']} (position {pos['position_id']}): volume "
                                      f"{prev.get('volume')} -> {snap['volume']}.")
        was_stale, was_locked = bool(prev and prev.get("stale")), bool(prev and prev.get("locked"))
        if snap["stale"] and not was_stale:
            seen = "never" if age is None else f"{round(age / 60)} min ago"
            add("data_stale", f"No fresh data for {label}: last update {seen} from {snap['source']}. The broker connection or "
                              f"the collector may be down.")
        elif was_stale and not snap["stale"]:
            add("data_fresh", f"Fresh data again for {label}.")
        if snap["locked"] and not was_locked:
            add("risk_lock_active", f"A Module 06 risk lock is active for {pos['symbol']}: {'; '.join(lock_reasons) or 'no reason given'}.")
        elif was_locked and not snap["locked"]:
            add("risk_lock_released", f"The Module 06 risk lock for {pos['symbol']} is no longer active.")
        return ev

    # ---- record -------------------------------------------------------------------------------------------------------------------
    def record(self, position_id: str, *, dry_run: bool = False, execution_id: str | None = None) -> dict[str, Any]:
        c = self.cfg()
        base: dict[str, Any] = {"position_id": position_id}
        if not c.enabled:
            return {**base, "status": "skipped", "reason": "Trade monitoring is switched off."}
        st = self.status(position_id)
        base.update(symbol=st.get("symbol"), side=st.get("side"), position_status=st.get("position_status"),
                    source=st.get("source"), stale=st.get("stale"))
        if not st["exists"]:
            return {**base, "status": "skipped", "reason": " ".join(st["reasons"])}
        if not st["ok"]:
            return {**base, "status": "unchanged", "reason": " ".join(st["reasons"]), "events": []}
        events = st["events"]
        announce = [e for e in events if e["severity"] in ("normal", "high")] if c.notify_dashboard else []
        report = {**base, "events": events, "event_types": [e["type"] for e in events], "snapshot": st["snapshot"],
                  "attention": any(e["severity"] == "high" for e in events), "linked_to_approval": st["linked_to_approval"]}
        if dry_run:
            return {**report, "status": "dry_run", "reason": f"Dry run: would record {len(events)} event(s) and notify "
                                                             f"the dashboard about {len(announce)}."}
        now = self.svc.now()
        with self.svc.repo.write() as s:
            from aurum.orchestration.db_models import OrchAuditLog as L
            last = s.execute(select(L.details).where(L.action == AUDIT_ACTION, L.entity_type == AUDIT_ENTITY,
                                                     L.entity_id == position_id[:64]).order_by(L.seq.desc()).limit(1)).scalar()
            if last and (last.get("snapshot") or {}) == st["snapshot"]:
                return {**report, "status": "unchanged", "reason": "Nothing changed since the last recorded status.", "events": []}
            self.svc.repo.audit(s, actor="wf08-07", actor_kind="system", action=AUDIT_ACTION, entity_type=AUDIT_ENTITY,
                                entity_id=position_id[:64], at=now, outcome="ok",
                                details={"snapshot": st["snapshot"], "previous": st["previous"], "events": events,
                                         "symbol": st["symbol"], "side": st["side"], "approval_id": st["approval_id"],
                                         "entry_price": st["entry_price"], "current_price": st["current_price"],
                                         "unrealized_pnl": st["unrealized_pnl"], "realized_pnl": st["realized_pnl"],
                                         "last_updated": st["last_updated"], "module_09": st["module_09"]["status"],
                                         "execution_id": execution_id})
        notified = 0
        errors: list[str] = []
        stamp = _iso(now) or ""
        for e in announce:
            try:
                nid = self.approvals.notify_position_event(
                    position_id, f"position.{e['type']}"[:48], e["severity"],
                    f"{e['type'].replace('_', ' ').capitalize()}: {st['side']} {st['symbol']}", e["message"],
                    f"wf08-07:{position_id}:{e['type']}:{stamp}", st.get("account_login"))
                notified += 1 if nid else 0
            except Exception as exc:  # noqa: BLE001 - the status is recorded; a failed notification is reported
                errors.append(f"{e['type']}: {type(exc).__name__}")
        delivered = self._send() if notified else {}
        return {**report, "status": "recorded", "notified": notified, "notification_errors": errors, "delivered_now": delivered,
                "recorded_at": _iso(now), "reason": "; ".join(e["message"] for e in events)[:600] or "Status recorded."}

    def _send(self) -> dict[str, int]:
        """Deliver the email and Telegram notifications that are due (Module 07's own delivery step)."""
        try:
            if self._deliver is not None:
                return self._deliver(self.approvals)
            from aurum.approvals.worker import Worker
            return Worker(self.approvals, self.svc.settings).deliver_notifications()
        except Exception as exc:  # noqa: BLE001 - delivery problems never undo the record; they are reported
            return {"error": f"{type(exc).__name__}: {exc}"[:200]}        # type: ignore[dict-item]


# ---- installation (used by `python -m aurum.cli orchestration setup-wf08-07`) ----------------------------------------------
def install(svc: Any, actor: Any, n8n_workflow_id: str, *, schedule: str | None = DEFAULT_SCHEDULE,
            schedule_timezone: str = "UTC", activate: bool = True, subscribe: bool = True) -> dict[str, Any]:
    """Registers WF08-07, links it to the n8n workflow, adds version 1.0.0, validates and activates it, subscribes it to
    the execution events and creates its schedule. Safe to re-run."""
    from aurum.orchestration.common import OrchError
    steps: list[str] = []
    svc.ensure_ready()
    try:
        w = svc.get_workflow(WORKFLOW_ID)
    except OrchError:
        svc.register_workflow(actor, {"workflow_id": WORKFLOW_ID, "name": WORKFLOW_NAME, "category": "other",
                                      "trigger_types": ["schedule", "event", "manual", "admin"], "owner": "module_08",
                                      "description": DESCRIPTION})
        steps.append("registered")
        w = svc.get_workflow(WORKFLOW_ID)
    if w["state"] in ("ACTIVE", "DEGRADED") and (w.get("n8n_workflow_id") != n8n_workflow_id
                                                  or w.get("webhook_path") != WEBHOOK_PATH):
        svc.set_workflow_state(actor, WORKFLOW_ID, "PAUSED", "re-linking WF08-07 to n8n")
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
            "description": "WF08-07 Trade Monitoring Orchestrator: records each change of an open position and notifies the dashboard."})
        if isinstance(version.get("version"), dict):        # tolerate a wrapped response
            version = version["version"]
        steps.append(f"added version {VERSION}")
    if version["status"] == "DRAFT":
        version = svc.validate_version(actor, WORKFLOW_ID, version["version_id"])["version"]
        steps.append(f"validated ({version['status']})")
    if activate:
        if version["status"] != "ACTIVE":
            svc.activate_version(actor, WORKFLOW_ID, version["version_id"], "WF08-07 installation")
            steps.append(f"activated version {VERSION}")
        if svc.get_workflow(WORKFLOW_ID)["state"] != "ACTIVE":
            svc.set_workflow_state(actor, WORKFLOW_ID, "ACTIVE", "WF08-07 installation")
            steps.append("workflow ACTIVE")
    subs: list[dict[str, Any]] = []
    if subscribe:
        have = {x.get("event_pattern"): x for x in svc.list_subscriptions()["items"] if x.get("workflow_id") == WORKFLOW_ID}
        for event in TRIGGER_EVENTS:
            if event in have:
                subs.append(have[event])
                steps.append(f"subscription to {event} already present")
            else:
                subs.append(svc.create_subscription(actor, {"name": f"WF08-07 on {event}", "event_pattern": event,
                                                            "target_kind": "workflow", "workflow_id": WORKFLOW_ID}))
                steps.append(f"subscribed to {event}")
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
            "subscriptions": subs, "schedule": sched, "steps": steps}
