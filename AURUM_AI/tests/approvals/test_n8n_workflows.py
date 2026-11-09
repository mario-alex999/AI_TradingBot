"""The shipped n8n workflows for Module 07 only notify and read. n8n can never approve, reject or manage a trade."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

WORKFLOWS = sorted((Path(__file__).resolve().parents[2] / "n8n" / "workflows").glob("aurum_approval_*.json"))
DECISION_PATHS = re.compile(r"/(approve|reject|request-modification|cancel|revalidate-risk|review)\b|/trade-management/requests")
SECRETS = re.compile(r"(?i)(password|bearer\s+[a-z0-9]|api[_-]?key\s*[:=]|token\s*[:=]\s*['\"][^'\"]+)")


def test_workflows_exist():
    names = {p.name for p in WORKFLOWS}
    assert {"aurum_approval_events_receiver.json", "aurum_approval_expiry_reminder.json"} <= names


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_workflow_is_notify_or_read_only(path: Path):
    wf = json.loads(path.read_text(encoding="utf-8"))
    assert wf["active"] is False, "imported workflows start inactive; a person turns them on"
    names = {n["name"] for n in wf["nodes"]}
    for src, conn in wf["connections"].items():
        assert src in names
        for branch in conn["main"]:
            for target in branch:
                assert target["node"] in names
    for node in wf["nodes"]:
        if node["type"] == "n8n-nodes-base.httpRequest":
            p = node["parameters"]
            assert p.get("method", "GET") == "GET", f"{node['name']} must only read"
            assert not DECISION_PATHS.search(p["url"]), f"{node['name']} calls a decision endpoint"
            # Credentials are chosen in the n8n UI (Header Auth), never stored in the workflow file.
            assert "credentials" not in node
    assert not SECRETS.search(path.read_text(encoding="utf-8"))
