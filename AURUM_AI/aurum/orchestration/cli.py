"""CLI for Module 08.

python -m aurum.cli orchestration worker [--interval 10]   bridge, deliveries, schedules, dispatch, time-outs, health
python -m aurum.cli orchestration tick                     one worker pass (health checks included)
python -m aurum.cli orchestration status                   overview counts, integrations, alerts
python -m aurum.cli orchestration health                   check every integration now
python -m aurum.cli orchestration workflows                registry
python -m aurum.cli orchestration n8n                      n8n connection and capabilities
python -m aurum.cli orchestration setup-test-workflow --n8n-workflow-id ID   register + activate the harmless test echo
python -m aurum.cli orchestration test-run [--wait 60]     trigger the harmless test-echo workflow and follow it
python -m aurum.cli orchestration verify-audit
python -m aurum.cli orchestration purge [--apply]          retention clean-up (dry run unless --apply)

Workflow registration, versions, activation and configuration changes are made by a signed-in person (web app or API),
so they are audited against a named user. The two console commands that act as a person (setup-test-workflow, test-run)
are audited as ``console:<OS user>``: whoever can run them already has the server and its database. They only touch the
harmless test workflow. Nothing here approves or executes a trade.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

import typer

from aurum.ui import Table, console, error, kv_table as _kv_table

orchestration_app = typer.Typer(help="Module 08 workflow orchestration (n8n). Never approves or executes trades.",
                                no_args_is_help=True)


def _svc() -> Any:
    from aurum.config import get_settings
    from aurum.database.connection import engine_from_settings
    from aurum.orchestration.service import OrchestrationService
    s = get_settings()
    return OrchestrationService(engine_from_settings(s), s)


def kv_table(rows: dict[str, Any], title: str) -> Any:
    return _kv_table(title, [(k, "" if v is None else v) for k, v in rows.items()])


def _console_actor() -> Any:
    import getpass

    from aurum.auth import roles
    from aurum.orchestration.common import Actor
    try:
        who = getpass.getuser()
    except Exception:  # noqa: BLE001
        who = "unknown"
    return Actor(id=f"console:{who}"[:64], name=f"console:{who}"[:80], kind="user", role="admin",
                 perms=roles.permissions_for(roles.Role.ADMIN))


def _guard(fn: Any) -> Any:
    from aurum.orchestration.common import OrchError
    try:
        return fn()
    except OrchError as exc:
        error(f"{exc.code}: {exc.message}")
        raise typer.Exit(1) from None


@orchestration_app.command("worker")
def worker(interval: int = typer.Option(None, help="Seconds between ticks (default ORCHESTRATION_WORKER_INTERVAL_SECONDS).")) -> None:
    """Run the Module 08 worker until stopped (Ctrl+C)."""
    from aurum.logging_config import setup_logging
    from aurum.orchestration.worker import Worker
    svc = _svc()
    setup_logging(svc.settings)
    _guard(svc.ensure_ready)
    console.print(f"Module 08 worker - environment [bold]{svc.env}[/bold], n8n {svc.settings.n8n_base_url}. Ctrl+C to stop.")
    try:
        Worker(svc).run_forever(float(interval or svc.settings.orchestration_worker_interval_seconds))
    except KeyboardInterrupt:
        console.print("Stopped.")


@orchestration_app.command("tick")
def tick() -> None:
    """One worker pass, including health checks."""
    from aurum.orchestration.worker import Worker
    svc = _svc()
    out = _guard(lambda: Worker(svc).tick(force_health=True))
    console.print_json(json.dumps(out, default=str))


@orchestration_app.command("status")
def status() -> None:
    """Exit code 1 when the schema is missing or the environment pin does not match."""
    svc = _svc()
    o = _guard(svc.overview)
    console.print(kv_table({"Environment": o["environment"], "Workflows": o["workflows"]["total"],
                            "Active": o["workflows"]["active"], "Running": o["executions"]["running"],
                            "Queued": o["executions"]["queued"], "Retry pending": o["executions"]["retry_pending"],
                            "Dead letters open": o["dead_letters_open"], "Deliveries pending": o["deliveries_pending"]},
                           title="Module 08"))
    t = Table(title="Integrations")
    for c in ("Integration", "Status", "Detail"):
        t.add_column(c)
    for k, v in sorted(o["integrations"].items()):
        t.add_row(k, v["status"], (v["detail"] or "")[:90])
    console.print(t)
    for a in o["alerts"]:
        console.print(f"[yellow]{a['level']}[/yellow]: {a['message']}")


@orchestration_app.command("health")
def health() -> None:
    svc = _svc()
    out = _guard(svc.check_all)
    for k, v in sorted(out.items()):
        console.print(f"{k:12} {v}")


@orchestration_app.command("workflows")
def workflows() -> None:
    svc = _svc()
    d = _guard(svc.list_workflows)
    t = Table(title="Workflow registry")
    for c in ("Workflow", "State", "Version", "Category", "n8n id", "Placeholder"):
        t.add_column(c)
    for w in d["items"]:
        t.add_row(w["workflow_id"], w["state"], w["active_version"] or "-", w["category"], w["n8n_workflow_id"] or "-",
                  "yes" if w["placeholder"] else "")
    console.print(t)


@orchestration_app.command("n8n")
def n8n() -> None:
    """n8n connection: health, API key, capabilities (no secret is printed)."""
    svc = _svc()
    h = svc.wf_engine.health()
    console.print(kv_table({"Status": h.status, "Detail": h.detail, "Latency ms": f"{h.latency_ms:.0f}" if h.latency_ms else "-"},
                           title="n8n"))
    console.print_json(json.dumps(svc.wf_engine.describe(), default=str))


@orchestration_app.command("setup-test-workflow")
def setup_test_workflow(n8n_workflow_id: str = typer.Option(..., help="Id of the imported 'AURUM - Core test echo' workflow in n8n."),
                        activate: bool = typer.Option(True, help="Activate it in AURUM after validation.")) -> None:
    """Links the harmless test-echo registration to its n8n workflow, validates it against n8n and activates it."""
    from aurum.orchestration.service import TEST_WORKFLOW_ID
    svc = _svc()
    who = _console_actor()
    _guard(svc.ensure_ready)
    w = _guard(lambda: svc.get_workflow(TEST_WORKFLOW_ID))
    if w["state"] in ("ACTIVE", "DEGRADED"):
        _guard(lambda: svc.set_workflow_state(who, TEST_WORKFLOW_ID, "PAUSED", "re-linking to n8n"))
        w = svc.get_workflow(TEST_WORKFLOW_ID)
    if w.get("n8n_workflow_id") != n8n_workflow_id:
        _guard(lambda: svc.update_workflow(who, TEST_WORKFLOW_ID, {"n8n_workflow_id": n8n_workflow_id,
                                                                   "row_version": w["row_version"]}))
    out = _guard(lambda: svc.validate_version(who, TEST_WORKFLOW_ID))
    v = out["version"]
    console.print(kv_table({"Version": v["version"], "Status": v["status"],
                            "Validation": json.dumps(v.get("validation") or {}, default=str)[:300]}, title="Validation"))
    if activate:
        if v["status"] != "ACTIVE":
            _guard(lambda: svc.activate_version(who, TEST_WORKFLOW_ID, v["version_id"], "console setup of the test workflow"))
        if svc.get_workflow(TEST_WORKFLOW_ID)["state"] != "ACTIVE":
            _guard(lambda: svc.set_workflow_state(who, TEST_WORKFLOW_ID, "ACTIVE", "console setup of the test workflow"))
    console.print(f"{TEST_WORKFLOW_ID}: {svc.get_workflow(TEST_WORKFLOW_ID)['state']}")


@orchestration_app.command("setup-wf08-01")
def setup_wf08_01(n8n_workflow_id: str = typer.Option(..., help="Id of the imported 'AURUM - WF08-01 Market Monitoring "
                                                                 "Scheduler' workflow in n8n (from its URL)."),
                  schedule: str = typer.Option("1-59/5 * * * *", help="Cron (UTC by default): 1 minute after every M5 close."),
                  timezone: str = typer.Option("UTC", help="Time zone the cron expression is read in."),
                  no_schedule: bool = typer.Option(False, "--no-schedule", help="Do not create the schedule."),
                  activate: bool = typer.Option(True, help="Activate WF08-01 in AURUM after validation.")) -> None:
    """Installs WF08-01: links the reserved registration to n8n, adds and validates version 1.0.0, activates it and
    creates the candle-close schedule. Safe to re-run."""
    from aurum.orchestration.market_monitoring import install
    svc = _svc()
    out = _guard(lambda: install(svc, _console_actor(), n8n_workflow_id, schedule=None if no_schedule else schedule,
                                 schedule_timezone=timezone, activate=activate))
    for step in out["steps"]:
        console.print(f" - {step}")
    console.print(kv_table({"Workflow": out["workflow_id"], "State": out["state"], "Version": out["version"],
                            "Validation": json.dumps(out.get("validation") or {}, default=str)[:300],
                            "Schedule": (out["schedule"] or {}).get("expression"),
                            "Next run (UTC)": (out["schedule"] or {}).get("next_run_at")}, title="WF08-01"))


@orchestration_app.command("test-run")
def test_run(wait: int = typer.Option(60, help="Seconds to wait for the callback."),
             message: str = typer.Option("hello from AURUM", help="Echoed back by the test workflow."),
             simulate: str = typer.Option("success", help="success or failure (the workflow reports a simulated failure).")) -> None:
    """Triggers the harmless test-echo workflow (it must be ACTIVE) and follows it until it finishes."""
    from aurum.orchestration.service import TEST_WORKFLOW_ID
    svc = _svc()
    inp: dict[str, Any] = {"message": message}
    if simulate != "success":
        inp["simulate"] = simulate
    view, _ = _guard(lambda: svc.trigger(_console_actor(), {"workflow_id": TEST_WORKFLOW_ID, "trigger_type": "admin",
                                                             "input": inp, "idempotency_key": f"cli:{uuid.uuid4()}"}))
    eid = view["execution_id"]
    console.print(f"Execution {eid} queued.")
    console.print(svc.dispatch_due(only=eid))
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        e = svc.get_execution(eid)
        if e["status"] in ("SUCCEEDED", "FAILED", "DEAD_LETTERED", "CANCELLED", "TIMED_OUT"):
            break
        time.sleep(1)
    e = svc.get_execution(eid)
    console.print(kv_table({"Status": e["status"], "n8n execution": e["n8n_execution_id"] or "-",
                            "Output": json.dumps(e["output"]) if e["output"] else "-",
                            "Error": f"{e['error_category']}: {e['error_message']}" if e["error_category"] else "-"},
                           title="Test run"))
    if e["status"] != ("FAILED" if simulate == "failure" else "SUCCEEDED"):
        raise typer.Exit(1)


@orchestration_app.command("verify-audit")
def verify_audit() -> None:
    ok, n, err = _svc().repo.verify_audit()
    console.print(f"{n} rows, chain {'valid' if ok else 'BROKEN: ' + str(err)}")
    if not ok:
        raise typer.Exit(1)


@orchestration_app.command("purge")
def purge(apply: bool = typer.Option(False, "--apply", help="Delete for real (default is a dry run).")) -> None:
    from aurum.orchestration.common import SYSTEM
    console.print(_guard(lambda: _svc().purge(SYSTEM, dry_run=not apply)))
