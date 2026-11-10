"""The Module 08 console commands run against a test service (no real n8n)."""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from aurum.orchestration import cli


@pytest.fixture
def run(h, monkeypatch):
    monkeypatch.setattr(cli, "_svc", lambda: h.svc)
    runner = CliRunner()
    return lambda *args: runner.invoke(cli.orchestration_app, list(args))


def test_read_only_commands(run):
    for args in (["status"], ["workflows"], ["health"], ["verify-audit"], ["purge"], ["tick"]):
        r = run(*args)
        assert r.exit_code == 0, (args, r.output, r.exception)
    assert "aurum-core" in run("workflows").output
    assert "dry_run" in run("purge").output


def test_setup_and_test_run_against_the_controlled_adapter(h, run):
    h.n8n.workflows["wfLIVE1"] = {"id": "wfLIVE1", "active": True, "webhook_paths": ["aurum-core-test-echo"]}
    r = run("setup-test-workflow", "--n8n-workflow-id", "wfLIVE1")
    assert r.exit_code == 0, r.output
    assert h.svc.get_workflow("aurum-core-test-echo")["state"] == "ACTIVE"
    with h.svc.repo.read() as s:
        from sqlalchemy import select

        from aurum.orchestration.db_models import OrchAuditLog
        actors = set(s.execute(select(OrchAuditLog.actor).where(OrchAuditLog.action.like("workflow.%"))).scalars())
    assert any(a.startswith("console:") for a in actors)
    # No callback arrives from the controlled adapter, so the run does not finish within the wait: exit code 1.
    r = run("test-run", "--wait", "1")
    assert r.exit_code == 1 and "WAITING_CALLBACK" in r.output
