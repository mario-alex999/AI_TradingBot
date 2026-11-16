"""The hosted (n8n Cloud) workflow files saved in n8n/workflows: well-formed, inactive, AURUM-only, and free of secrets.

Each file is the workflow that runs in n8n Cloud with its address and tokens replaced by placeholders, so it can be
re-imported after filling in the *AURUM settings* node.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from aurum.orchestration import approval_notifications, approval_responses, error_recovery, market_analysis, market_monitoring, trade_handoff, trade_monitoring, trade_opportunities

DIR = Path(__file__).resolve().parents[2] / "n8n" / "workflows"
API = '={{ $("AURUM settings").first().json.aurumApi }}'
FILES = {
    "aurum_wf08_01_market_monitoring.cloud.json": (market_monitoring.WEBHOOK_PATH, "Loop over instruments",
        {"/webhooks/n8n/callback", "/webhooks/n8n/events"},
        ["Check candle completion", "Check duplicate analysis", "Request analysis (publish event)"]),
    "aurum_wf08_02_market_analysis.json": (market_analysis.WEBHOOK_PATH, "Loop over symbols",
        {"/webhooks/n8n/callback", "/market-analysis/run"},
        ["Validate analysis requests (pending symbols)", "Check data quality", "Run Module 05 analysis",
         "Analysis response valid?"]),
    "aurum_wf08_03_trade_opportunity.json": (trade_opportunities.WEBHOOK_PATH, "Loop over ideas",
        {"/webhooks/n8n/callback", "/trade-opportunities/assess"},
        ["Validate trade idea ids (new ideas)", "Retrieve trade idea and check status", "Idea open and not expired?",
         "Module 06 risk assessment", "Risk response valid?", "Decision?"]),
    "aurum_wf08_04_approval_notification.json": (approval_notifications.WEBHOOK_PATH, "Loop over ideas",
        {"/webhooks/n8n/callback", "/approval-notifications/notify"},
        ["List risk-approved ideas", "Retrieve trade idea and risk assessment", "Still valid?",
         "Prepare and send notification (Module 07)", "Notification recorded?",
         "Record: notification status (waiting for Module 07 approval)"]),
    "aurum_wf08_05_approval_response.json": (approval_responses.WEBHOOK_PATH, "Loop over responses",
        {"/webhooks/n8n/callback", "/approval-responses/record"},
        ["Validate approval ids (responses to record)", "Retrieve current status and verify the decision",
         "Decided and not recorded yet?", "Record the response", "Status: approved and verified?", "Status: rejected?",
         "Status: expired?", "Record: approval (Module 07 hands it to WF08-06)", "Record: rejection (dashboard notified)",
         "Record: expiry (dashboard notified)", "Record: no valid approval (nothing passed on)"]),
    "aurum_wf08_06_trade_handoff.json": (trade_handoff.WEBHOOK_PATH, "Loop over handoffs",
        {"/webhooks/n8n/callback", "/trade-handoff/record"},
        ["Validate approval id (execution request to follow)", "Retrieve approved trade and verify approval, risk and expiry",
         "Execution request found and not recorded yet?", "Record execution status", "Status: valid and waiting for Module 09?",
         "Status: acknowledged by Module 09?", "Status: execution failed?",
         "Record: execution request queued (no acknowledgement yet)", "Record: execution acknowledgement",
         "Record: execution failed (nothing retried)", "Record: hand-off not valid (must not be executed)"]),
    "aurum_wf08_07_trade_monitoring.json": (trade_monitoring.WEBHOOK_PATH, "Loop over trades",
        {"/webhooks/n8n/callback", "/trade-monitoring/record"},
        ["Retrieve open trade records", "Split out trades", "Request trade status and check for risk and lifecycle events",
         "Anything changed since the last record?", "Record status update", "Needs attention?",
         "Record: status update (dashboard alerted)", "Record: status update", "Record: unchanged"]),
    "aurum_wf08_08_error_recovery.json": (error_recovery.WEBHOOK_PATH, "Loop over failures",
        {"/webhooks/n8n/callback", "/error-recovery/record", "/error-recovery/report"},
        ["n8n Error Trigger", "Identify failed workflow and capture error details", "Classify error and record it in the backend",
         "Is retry safe? (crash)", "Identify failed workflow runs", "Capture error details and classify error",
         "Record error in the backend", "Is retry safe?", "Recovery verified?", "Record: retry with limits (scheduled by AURUM)",
         "Record: recovery verified", "Record: escalated and alerted (not retried)"]),
}


@pytest.fixture(params=sorted(FILES))
def wf(request):
    path = DIR / request.param
    return request.param, json.loads(path.read_text(encoding="utf-8")), path.read_text(encoding="utf-8")


def test_well_formed_inactive_and_fully_connected(wf):
    name, w, _ = wf
    assert w["active"] is False and w["settings"]["executionOrder"] == "v1"
    names = [n["name"] for n in w["nodes"]]
    assert len(names) == len(set(names)) and len({n["id"] for n in w["nodes"]}) == len(names)
    targets = set()
    for src, conn in w["connections"].items():
        assert src in names, src
        for branch in conn["main"]:
            for t in branch:
                assert t["node"] in names, t["node"]
                targets.add(t["node"])
    unreachable = [n["name"] for n in w["nodes"] if n["name"] not in targets
                   and n["type"] not in ("n8n-nodes-base.webhook", "n8n-nodes-base.errorTrigger", "n8n-nodes-base.stickyNote")]
    assert unreachable == [], unreachable
    hook = next(n for n in w["nodes"] if n["type"] == "n8n-nodes-base.webhook")
    assert hook["parameters"]["path"] == FILES[name][0] and hook["parameters"]["httpMethod"] == "POST"


def test_only_calls_the_configured_aurum_api_and_only_safe_endpoints(wf):
    name, w, text = wf
    allowed_post = FILES[name][2]
    for n in w["nodes"]:
        if n["type"] != "n8n-nodes-base.httpRequest":
            continue
        url = n["parameters"]["url"]
        assert url.startswith(API + "/"), (n["name"], url)
        if n["parameters"].get("method", "GET") != "GET":
            assert url[len(API):] in allowed_post, (n["name"], url)
        auth = n["parameters"]["headerParameters"]["parameters"]
        assert auth == [{"name": "Authorization", "value": '=Bearer {{ $("AURUM settings").first().json.serviceToken }}'}]
    assert not re.search(r"(?i)/(approve|reject|execute|orders?|trade-management)\b", text)
    assert "host.docker.internal" not in text and "localhost" not in text
    kinds = {n["type"] for n in w["nodes"]}                               # no broker, MT5, code or shell node anywhere
    assert kinds <= {"n8n-nodes-base.webhook", "n8n-nodes-base.errorTrigger", "n8n-nodes-base.set", "n8n-nodes-base.if", "n8n-nodes-base.respondToWebhook",
                     "n8n-nodes-base.httpRequest", "n8n-nodes-base.splitOut", "n8n-nodes-base.splitInBatches",
                     "n8n-nodes-base.aggregate", "n8n-nodes-base.stickyNote"}, kinds


def test_no_secrets_only_placeholders(wf):
    _, w, text = wf
    settings = next(n for n in w["nodes"] if n["name"] == "AURUM settings")
    values = {a["name"]: a["value"] for a in settings["parameters"]["assignments"]["assignments"]}
    assert values["serviceToken"] == "REPLACE_WITH_AURUM_SERVICE_TOKEN"
    assert values["webhookToken"] == "REPLACE_WITH_AURUM_WEBHOOK_TOKEN"
    assert not any("credentials" in n for n in w["nodes"])
    assert not re.search(r"[A-Za-z0-9_-]{40,}", re.sub(r"REPLACE_WITH_[A-Z_]+", "", text))    # nothing token-shaped


def test_webhook_token_is_checked_before_anything_else(wf):
    _, w, _ = wf
    hook = next(n for n in w["nodes"] if n["type"] == "n8n-nodes-base.webhook")["name"]
    assert [t["node"] for t in w["connections"][hook]["main"][0]] == ["AURUM settings"]
    after = [t["node"] for t in w["connections"]["AURUM settings"]["main"][0]]
    if after == ["Started by the n8n Error Trigger?"]:          # WF08-08: two ways in
        crash, aurum = w["connections"]["Started by the n8n Error Trigger?"]["main"]
        assert [t["node"] for t in aurum] == ["Webhook token valid?"]
        # The crash branch is only reachable from n8n's own Error Trigger, which cannot be called over HTTP.
        cond = next(n for n in w["nodes"] if n["name"] == "Started by the n8n Error Trigger?")
        assert "$('n8n Error Trigger').isExecuted" in json.dumps(cond["parameters"])
        assert [t["node"] for t in w["connections"]["n8n Error Trigger"]["main"][0]] == ["AURUM settings"]
    else:
        assert after == ["Webhook token valid?"]
    yes, no = w["connections"]["Webhook token valid?"]["main"]
    assert [t["node"] for t in no] == ["Reject (wrong webhook token)"]
    assert all(next(n for n in w["nodes"] if n["name"] == t["node"])["type"] == "n8n-nodes-base.respondToWebhook" for t in yes)
    reject = next(n for n in w["nodes"] if n["name"] == "Reject (wrong webhook token)")
    assert reject["parameters"]["options"]["responseCode"] == 401


def test_document_steps_present_and_every_branch_returns_to_the_loop(wf):
    name, w, _ = wf
    _, loop, _, steps = FILES[name]
    names = {n["name"] for n in w["nodes"]}
    for step in steps + [loop, "Callback: started", "Callback: completed (run recorded)", "Collect results"]:
        assert step in names, step
    done, each = w["connections"][loop]["main"]
    assert [t["node"] for t in done] == ["Collect results"] and len(each) == 1
    records = [n["name"] for n in w["nodes"] if n["name"].startswith("Record:")]
    for r in records:                                   # every outcome record continues with the next item
        assert [t["node"] for t in w["connections"][r]["main"][0]] == [loop], r
