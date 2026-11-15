"""WF08-03 Trade Opportunity Processor - the backend half.

WF08-02 publishes ``ai.analysis.completed`` when Module 05 has analysed a symbol. WF08-03 takes each trade idea Module 05
has open and hands it to Module 06 for a risk assessment. **Module 06 makes the risk decision.** Nothing here, and nothing
in the n8n workflow, calculates whether a trade is safe:

* ``config``      - the versioned ``trade_opportunities`` section (off switch, batch size).
* ``preflight``   - may this run proceed? (switch on, database reachable, Module 05 and Module 06 schemas ready).
* ``pending``     - Module 05 ideas that are still open and have no Module 06 assessment for their current levels.
* ``idea_status`` - one idea: does it exist, is it still open, has it expired, was it already assessed?
* ``assess``      - ask Module 06 to assess one idea and report its decision (PASS, REVIEW, BLOCK or ERROR).

Duplicate prevention: the assessment's idempotency key is ``wf08-03:<idea id>:<hash of its price levels>``. Module 06
returns the stored assessment for a repeated key, and ``pending`` skips ideas that already have one, so an idea is
assessed once per set of levels; a re-priced idea is assessed again.

Module 06 stores the assessment and publishes its own ``risk.assessment.*`` event, which is what WF08-04 (human approval
notification) follows. Nothing here approves, places or executes a trade, and nothing talks to MT5.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select

from aurum.orchestration.config_model import TradeOpportunityCfg

WORKFLOW_ID = "wf08-03-risk-approval"                # the reserved Module 08 registration for this workflow
WEBHOOK_PATH = "aurum-wf08-03-trade-opportunity"
VERSION = "1.0.0"
TRIGGER_EVENT = "ai.analysis.completed"
SUBSCRIPTION_NAME = "WF08-03 processes ideas after an analysis"
OUTCOMES = {"PASS": "approved", "REVIEW": "review", "BLOCK": "rejected", "ERROR": "error"}

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "ideaIds": {"type": "array", "items": {"type": "string", "maxLength": 64}, "maxItems": 50},
        "dryRun": {"type": "boolean"},
        "reason": {"type": "string", "maxLength": 200},
        "event": {"type": "object"},                  # a single analysis event (when coalescing is off)
        "batch": {"type": "object"},                  # {correlation_id, event_type} for a coalesced trigger
    },
}
OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["completed", "partial", "skipped"]},
        "reason": {"type": "string"},
        "approved": {"type": "array"},
        "rejected": {"type": "array"},
        "skipped": {"type": "array"},
    },
}


def _utc(t: datetime) -> datetime:
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


def _iso(t: datetime | None) -> str | None:
    return _utc(t).isoformat().replace("+00:00", "Z") if t else None


def assessment_key(idea: dict[str, Any]) -> str:
    """One assessment per idea and set of price levels."""
    levels = [str(idea.get(k)) for k in ("entry_low", "entry_high", "stop_loss", "take_profit", "direction")]
    return f"wf08-03:{idea['candidate_uid']}:{hashlib.sha256(json.dumps(levels).encode()).hexdigest()[:16]}"


class OpportunityProcessor:
    """Stateless over the database: safe to call from any API worker. ``svc`` is the OrchestrationService."""

    def __init__(self, svc: Any, ideas: Any = None, risk: Callable[[], Any] | None = None):
        self.svc = svc
        self.engine = svc.repo.engine
        self._ideas = ideas
        self._risk_factory = risk
        self._risk: Any = None

    # ---- wiring -------------------------------------------------------------------------------------------------------------
    def cfg(self) -> TradeOpportunityCfg:
        return self.svc.config().trade_opportunities

    @property
    def ideas(self) -> Any:
        if self._ideas is None:
            from aurum.decision.repository import DecisionRepository
            self._ideas = DecisionRepository(self.engine)
        return self._ideas

    @property
    def risk(self) -> Any:
        if self._risk is None:
            if self._risk_factory is not None:
                self._risk = self._risk_factory()
            else:
                from aurum.risk.service import RiskService
                self._risk = RiskService(self.engine, self.svc.settings)
        return self._risk

    def config_view(self) -> dict[str, Any]:
        c = self.cfg()
        return {"workflow_id": WORKFLOW_ID, "enabled": c.enabled, "kill_switch": not c.enabled, "coalesce": c.coalesce,
                "max_ideas_per_run": c.max_ideas_per_run, "trigger_event": TRIGGER_EVENT}

    # ---- preflight ------------------------------------------------------------------------------------------------------------
    def preflight(self) -> dict[str, Any]:
        c = self.cfg()
        checks: dict[str, Any] = {"enabled": c.enabled}
        reasons: list[str] = []
        if not c.enabled:
            reasons.append("Trade opportunity processing is switched off (trade_opportunities.enabled = false).")
        try:
            checks["module_05_schema"] = bool(self.ideas.schema_ready())
            if not checks["module_05_schema"]:
                reasons.append("Module 05 tables are missing; run the database migrations first.")
            health = self.risk.health()
            checks["database"] = "ok"
            checks["module_06"] = str(health.get("status") or health.get("overall_status") or "ok")
            if health.get("schema_ready") is False:
                reasons.append("Module 06 tables are missing; run the database migrations first.")
        except Exception as exc:  # noqa: BLE001 - any failure here means: do not proceed
            checks["database"] = f"unavailable ({type(exc).__name__})"
            reasons.append("Module 05, Module 06 or the AURUM database is not reachable.")
        return {"proceed": not reasons, "checks": checks, "reasons": reasons, "checked_at": _iso(self.svc.now())}

    # ---- ideas ----------------------------------------------------------------------------------------------------------------
    def _assessed(self, keys: list[str]) -> dict[str, dict[str, Any]]:
        """Existing Module 06 assessments for these idempotency keys."""
        if not keys:
            return {}
        from aurum.database.connection import session_scope
        from aurum.risk.db_models import RiskAssessment
        with session_scope(self.engine) as s:
            rows = s.execute(select(RiskAssessment.idempotency_key, RiskAssessment.assessment_id, RiskAssessment.decision)
                             .where(RiskAssessment.idempotency_key.in_(keys))).all()
        return {k: {"assessment_id": a, "decision": d} for k, a, d in rows}

    def _view(self, idea: dict[str, Any], assessed: dict[str, dict[str, Any]]) -> dict[str, Any]:
        from aurum.decision import lifecycle
        now = _utc(self.svc.now())
        expires = idea.get("expires_at")
        expired = bool(expires and _utc(expires) <= now) or idea.get("status") == "expired"
        key = assessment_key(idea)
        prior = assessed.get(key)
        reasons: list[str] = []
        if idea.get("status") not in lifecycle.ACTIVE:
            reasons.append(f"The idea is {idea.get('status')}, not open.")
        if expired and idea.get("status") in lifecycle.ACTIVE:
            reasons.append("The setup has expired.")
        if prior:
            reasons.append(f"Module 06 already assessed this idea ({prior['decision']}).")
        return {"idea_id": idea["candidate_uid"], "exists": True, "symbol": idea.get("symbol"), "direction": idea.get("direction"),
                "timeframe": idea.get("timeframe"), "strategy": idea.get("template"), "status": idea.get("status"),
                "active": idea.get("status") in lifecycle.ACTIVE, "expired": expired, "expires_at": _iso(expires),
                "already_assessed": prior is not None, "assessment_id": prior["assessment_id"] if prior else None,
                "previous_decision": prior["decision"] if prior else None, "assessment_key": key,
                "ok": not reasons, "reasons": reasons}

    def _idea(self, idea_id: str) -> dict[str, Any] | None:
        from aurum.database.connection import session_scope
        from aurum.decision.db_models import TradeCandidate
        from aurum.decision.repository import to_dict
        with session_scope(self.engine) as s:
            row = s.execute(select(TradeCandidate).where(TradeCandidate.candidate_uid == idea_id)).scalar_one_or_none()
            return to_dict(row) if row is not None else None

    def pending(self, idea_ids: list[str] | None = None) -> dict[str, Any]:
        """Open ideas with no assessment for their current levels. With ``idea_ids`` the listed ideas are returned whatever
        their state, so the workflow records why each one is skipped."""
        c = self.cfg()
        if idea_ids:
            found = [self._idea(i) for i in idea_ids]
            rows = [f for f in found if f is not None]
            missing = [i for i, f in zip(idea_ids, found) if f is None]
        else:
            rows, missing = self.ideas.active(), []
        assessed = self._assessed([assessment_key(r) for r in rows])
        views = [self._view(r, assessed) for r in rows]
        items = views if idea_ids else [v for v in views if v["ok"]]
        items += [{"idea_id": i, "exists": False, "ok": False, "reasons": ["Module 05 has no such trade idea."]} for i in missing]
        deferred = items[c.max_ideas_per_run:]
        items = items[:c.max_ideas_per_run]
        return {"enabled": c.enabled, "items": items if c.enabled else [], "count": len(items) if c.enabled else 0,
                "deferred": len(deferred)}

    def idea_status(self, idea_id: str) -> dict[str, Any]:
        idea = self._idea(idea_id)
        if idea is None:
            return {"idea_id": idea_id, "exists": False, "ok": False, "reasons": ["Module 05 has no such trade idea."]}
        return self._view(idea, self._assessed([assessment_key(idea)]))

    # ---- assess one idea ------------------------------------------------------------------------------------------------------
    def assess(self, idea_id: str, *, dry_run: bool = False, execution_id: str | None = None) -> dict[str, Any]:
        c = self.cfg()
        base: dict[str, Any] = {"idea_id": idea_id}
        if not c.enabled:
            return {**base, "status": "skipped", "outcome": "skipped", "reason": "Trade opportunity processing is switched off."}
        st = self.idea_status(idea_id)
        base.update(symbol=st.get("symbol"), direction=st.get("direction"), timeframe=st.get("timeframe"))
        if not st["ok"]:
            return {**base, "status": "skipped", "outcome": "skipped", "reason": " ".join(st["reasons"]),
                    "assessment_id": st.get("assessment_id"), "decision": st.get("previous_decision")}
        if dry_run:
            return {**base, "status": "dry_run", "outcome": "dry_run", "reason": "Dry run: would ask Module 06 for a risk assessment."}
        started = time.monotonic()
        try:
            a = self.risk.assess({"trade_idea_id": idea_id}, "wf08-03", execution_id, st["assessment_key"])
        except Exception as exc:  # noqa: BLE001 - reported to the workflow; Module 06 made no decision
            return {**base, "status": "failed", "outcome": "error",
                    "reason": f"Module 06 could not assess the idea: {getattr(exc, 'message', None) or exc}"[:300],
                    "duration_seconds": round(time.monotonic() - started, 2)}
        decision = str(a.get("decision") or "ERROR").upper()
        outcome = OUTCOMES.get(decision, "error")
        return {**base, "status": "completed", "decision": decision, "outcome": outcome, "assessment_id": a.get("assessment_id"),
                "final_volume": a.get("final_volume"), "risk_pct": a.get("risk_pct"), "risk_reward": a.get("risk_reward"),
                "reason": _explain(decision, a), "replayed": bool(a.get("replayed")),
                "duration_seconds": round(time.monotonic() - started, 2)}


def _explain(decision: str, a: dict[str, Any]) -> str:
    """Module 06's own reasons, shortened for the workflow record."""
    names: list[str] = []
    for key in ("violations", "failed_checks", "blocking_rules", "warnings"):
        for item in a.get(key) or []:
            text = (item.get("message") or item.get("title") or item.get("rule_id")) if isinstance(item, dict) else str(item)
            if text and text not in names:
                names.append(str(text))
    detail = "; ".join(names[:4])
    head = {"PASS": "Module 06 passed the idea.", "REVIEW": "Module 06 passed the idea with points for human review.",
            "BLOCK": "Module 06 blocked the idea.", "ERROR": "Module 06 could not complete the assessment."}.get(
                decision, f"Module 06 returned {decision}.")
    return (f"{head} {detail}" if detail else head)[:400]


# ---- installation (used by `python -m aurum.cli orchestration setup-wf08-03`) ----------------------------------------------
def install(svc: Any, actor: Any, n8n_workflow_id: str, *, activate: bool = True, subscribe: bool = True) -> dict[str, Any]:
    """Turns the reserved WF08-03 placeholder into a real workflow: links it to the n8n workflow, adds version 1.0.0 with
    its contract, validates and activates it, and subscribes it to ``ai.analysis.completed``. Safe to re-run."""
    steps: list[str] = []
    svc.ensure_ready()
    w = svc.get_workflow(WORKFLOW_ID)
    if w["state"] in ("ACTIVE", "DEGRADED") and (w.get("n8n_workflow_id") != n8n_workflow_id
                                                  or w.get("webhook_path") != WEBHOOK_PATH):
        svc.set_workflow_state(actor, WORKFLOW_ID, "PAUSED", "re-linking WF08-03 to n8n")
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
            "description": "WF08-03 Trade Opportunity Processor: hands each open Module 05 idea to Module 06 for a risk "
                           "assessment and records Module 06's decision."})
        if isinstance(version.get("version"), dict):        # tolerate a wrapped response
            version = version["version"]
        steps.append(f"added version {VERSION}")
    if version["status"] == "DRAFT":
        version = svc.validate_version(actor, WORKFLOW_ID, version["version_id"])["version"]
        steps.append(f"validated ({version['status']})")
    if activate:
        if version["status"] != "ACTIVE":
            svc.activate_version(actor, WORKFLOW_ID, version["version_id"], "WF08-03 installation")
            steps.append(f"activated version {VERSION}")
        if svc.get_workflow(WORKFLOW_ID)["state"] != "ACTIVE":
            svc.set_workflow_state(actor, WORKFLOW_ID, "ACTIVE", "WF08-03 installation")
            steps.append("workflow ACTIVE")
    sub = None
    if subscribe:
        existing = [x for x in svc.list_subscriptions()["items"]
                    if x.get("workflow_id") == WORKFLOW_ID and x.get("event_pattern") == TRIGGER_EVENT]
        if existing:
            sub = existing[0]
            steps.append("subscription already present")
        else:
            sub = svc.create_subscription(actor, {"name": SUBSCRIPTION_NAME, "event_pattern": TRIGGER_EVENT,
                                                  "target_kind": "workflow", "workflow_id": WORKFLOW_ID})
            steps.append(f"subscribed to {TRIGGER_EVENT}")
    final = svc.get_workflow(WORKFLOW_ID)
    return {"workflow_id": WORKFLOW_ID, "state": final["state"], "placeholder": final.get("placeholder"),
            "version": VERSION, "validation": version.get("validation"), "subscription": sub, "steps": steps}
