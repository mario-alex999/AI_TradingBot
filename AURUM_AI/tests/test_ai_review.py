"""Optional AI review: off by default, hard monthly budget, failure accounting, API guard. No test calls Anthropic:
the client is a fake and the database is an isolated SQLite file."""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from cot_helpers import cot_cfg
from fastapi.testclient import TestClient
from pydantic import SecretStr

from aurum.ai_review import budget
from aurum.ai_review.db_models import AiReview
from aurum.ai_review.prompt import OUTPUT_SCHEMA, SYSTEM_PROMPT, Verdict, build_context
from aurum.ai_review.service import (AiReviewService, AiReviewUnavailable, BudgetExceeded, NoMarketData, ReviewFailed)
from aurum.api.app import create_app
from aurum.config import Settings
from aurum.database.connection import engine_from_settings, init_database, session_scope

MODEL = "claude-opus-5-5"
VERDICT = {"stance": "bullish", "confidence": "medium", "summary": "H1 and M15 lean up; D1 disagrees.",
           "supporting_evidence": ["H1 bullish 36/100"], "conflicting_evidence": ["D1 bearish 41/100"], "risks": ["ATR p99"],
           "data_gaps": ["No consensus data"], "what_would_change_this_view": "A D1 close below the last swing."}
REPORT = {"sessions": {"market_open": True, "note": "left out"}, "event_risk": {"state": "NORMAL", "historical": "left out"},
          "conditions": {"tags": ["TRENDING"]}, "scores": {}, "volatility": {}, "news": {"latest": []},
          "integrations": {"module_01": {"available": True}}, "data_quality": {"issues": []}, "statistics": {"huge": "left out"}}


class FakeMessages:
    def __init__(self, result):
        self.result, self.calls = result, []

    def create(self, **kw):
        self.calls.append(kw)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeIntelligence:
    def __init__(self, report=REPORT):
        self.report = report

    def analyze(self, symbol, as_of):
        return self.report


class FakeAnalysis:
    def analyze_confluence(self, symbol, as_of=None):
        return SimpleNamespace(to_dict=lambda: {"alignment": {"consensus": "bullish"}, "disclaimer": "left out"})


def reply(text=None, stop="end_turn", input_tokens=10_000, output_tokens=1_000):
    text = json.dumps(VERDICT) if text is None else text
    return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=text)],
                           usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens,
                                                 cache_creation_input_tokens=0, cache_read_input_tokens=None))


def settings(tmp_path, **kw):
    base = dict(_env_file=None, database_url=f"sqlite:///{tmp_path}/ai.db", ai_review_enabled=True,
                anthropic_api_key="sk-ant-test-secret", ai_review_monthly_budget_usd=5.0, data_dir=tmp_path)
    return Settings(**{**base, **kw})


def make(tmp_path, result=None, report=REPORT, init=True, **kw):
    s = settings(tmp_path, **kw)
    engine = engine_from_settings(s)
    if init:
        init_database(engine)
    messages = FakeMessages(reply() if result is None else result)
    svc = AiReviewService(s, engine, FakeIntelligence(report), FakeAnalysis(), SimpleNamespace(messages=messages))
    return svc, messages


def committed(svc):
    return budget.committed_micro(svc.engine, budget.month_key())


def status_error(cls, code):
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("error for sk-ant-test-secret", response=httpx2.Response(code, request=req), body=None)


# ---- off by default
def test_settings_default_to_off():
    s = Settings(_env_file=None)
    assert s.ai_review_enabled is False and s.ai_review_monthly_budget_usd is None and s.ai_review_model == MODEL


@pytest.mark.parametrize("change,needle", [
    ({"ai_review_enabled": False}, "AI_REVIEW_ENABLED"),
    ({"anthropic_api_key": None}, "ANTHROPIC_API_KEY"),
    ({"anthropic_api_key": ""}, "ANTHROPIC_API_KEY"),
    ({"ai_review_monthly_budget_usd": None}, "AI_REVIEW_MONTHLY_BUDGET_USD"),
    ({"ai_review_model": "claude-unpriced"}, "no known price"),
])
def test_every_switch_is_required_and_blocks_the_call(tmp_path, change, needle):
    svc, messages = make(tmp_path, **change)
    with pytest.raises(AiReviewUnavailable, match=needle):
        svc.run("EURUSD")
    assert messages.calls == [] and svc.history() == [] and committed(svc) == 0


def test_blank_env_values_mean_unset(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "  ")
    monkeypatch.setenv("AI_REVIEW_MONTHLY_BUDGET_USD", "")
    s = Settings(_env_file=None)
    assert s.anthropic_api_key is None and s.ai_review_monthly_budget_usd is None


def test_missing_tables_are_reported_not_crashed(tmp_path):
    svc, messages = make(tmp_path, init=False)
    assert "init-db" in svc.status()["reason"] and svc.history() == []
    with pytest.raises(AiReviewUnavailable, match="init-db"):
        svc.run("EURUSD")
    assert messages.calls == []


# ---- a successful review
def test_review_stores_verdict_and_settles_to_real_cost(tmp_path):
    svc, messages = make(tmp_path)
    out = svc.run("eurusd")
    assert out["symbol"] == "EURUSD" and out["status"] == "completed" and out["verdict"] == VERDICT
    assert out["cost_usd"] == pytest.approx(0.06) and out["cost_is_estimate"] is False     # 10k in at $4 + 1k out at $20
    assert committed(svc) == 60_000
    st = svc.status()
    assert st["remaining_usd"] == pytest.approx(4.94) and st["completed_reviews_this_month"] == 1
    assert svc.history("EURUSD")[0]["review_uid"] == out["review_uid"] and svc.history("GBPUSD") == []


def test_request_shape(tmp_path):
    svc, messages = make(tmp_path)
    svc.run("EURUSD")
    kw = messages.calls[0]
    assert kw["model"] == MODEL and kw["thinking"] == {"type": "adaptive"} and kw["system"] == SYSTEM_PROMPT
    assert kw["output_config"]["effort"] == "high"
    assert kw["output_config"]["format"] == {"type": "json_schema", "schema": OUTPUT_SCHEMA}
    assert "temperature" not in kw and "tools" not in kw    # the model is given nothing it could act with


def test_context_keeps_conclusions_and_leaves_out_bulk(tmp_path):
    svc, messages = make(tmp_path)
    svc.run("EURUSD")
    context = json.loads(messages.calls[0]["messages"][0]["content"])
    assert context["symbol"] == "EURUSD" and context["conditions"]["tags"] == ["TRENDING"]
    assert context["confluence"]["alignment"]["consensus"] == "bullish" and context["sessions"]["market_open"] is True
    assert "left out" not in json.dumps(context)


def test_schema_and_validator_agree():
    assert set(OUTPUT_SCHEMA["required"]) == set(OUTPUT_SCHEMA["properties"]) == set(Verdict.model_fields)
    Verdict.model_validate(VERDICT)


def test_no_data_costs_nothing(tmp_path):
    svc, messages = make(tmp_path, report={**REPORT, "integrations": {"module_01": {"available": False}}})
    with pytest.raises(NoMarketData):
        svc.run("EURUSD")
    assert messages.calls == [] and committed(svc) == 0


def test_dry_run_needs_no_key_and_spends_nothing(tmp_path):
    svc, messages = make(tmp_path, ai_review_enabled=False, anthropic_api_key=None)
    est = svc.estimate("EURUSD")
    assert est["worst_case_usd"] > 0 and messages.calls == [] and committed(svc) == 0


# ---- the budget
def test_cost_uses_list_prices():
    assert budget.token_cost_micro("claude-opus-5-5", 1_000_000, 1_000_000) == 24_000_000
    assert budget.token_cost_micro("claude-sonnet-5-5", 1_000_000, 1_000_000) == 12_000_000
    assert budget.token_cost_micro("claude-haiku-5-5", 1_000_000, 1_000_000) == 600_000
    assert budget.token_cost_micro("claude-opus-5-5", 0, 0, cache_write_tokens=1000) == 5_000     # charged at 1.25x


def test_budget_too_small_for_worst_case_blocks_the_call(tmp_path):
    svc, messages = make(tmp_path, ai_review_monthly_budget_usd=0.10)   # max_tokens alone can cost $0.16
    with pytest.raises(BudgetExceeded, match="No API call was made"):
        svc.run("EURUSD")
    assert messages.calls == [] and committed(svc) == 0 and svc.history() == []


def test_spending_stops_when_the_budget_is_reached(tmp_path):
    svc, messages = make(tmp_path, ai_review_monthly_budget_usd=0.40, ai_review_max_tokens=8000)
    runs = 0
    with pytest.raises(BudgetExceeded):
        for _ in range(100):
            svc.run("EURUSD")
            runs += 1
    assert runs == len(messages.calls) and 0 < runs < 100
    assert committed(svc) <= budget.usd_to_micro(0.40)


def test_a_new_month_starts_fresh(tmp_path):
    svc, _ = make(tmp_path, ai_review_monthly_budget_usd=0.40)
    budget.ensure_month(svc.engine, "2020-01")
    with session_scope(svc.engine) as db:
        budget.adjust(db, "2020-01", budget.usd_to_micro(0.40))
    assert svc.run("EURUSD")["status"] == "completed"


def test_concurrent_reservations_cannot_exceed_the_cap(tmp_path):
    svc, _ = make(tmp_path)
    budget.ensure_month(svc.engine, "2026-11")
    wins: list[bool] = []
    lock = threading.Lock()

    def worker():
        with session_scope(svc.engine) as db:
            ok = budget.try_reserve(db, "2026-11", 100, 500)
        with lock:
            wins.append(ok)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(wins) == 5 and budget.committed_micro(svc.engine, "2026-11") == 500


# ---- failures
@pytest.mark.parametrize("error,code", [
    (status_error(anthropic.RateLimitError, 429), "rate_limited"),
    (status_error(anthropic.AuthenticationError, 401), "auth"),
    (status_error(anthropic.InternalServerError, 500), "api_error"),
])
def test_rejected_calls_are_recorded_and_refunded(tmp_path, error, code):
    svc, _ = make(tmp_path, result=error)
    with pytest.raises(ReviewFailed) as exc:
        svc.run("EURUSD")
    assert exc.value.review["error_code"] == code and exc.value.review["cost_usd"] == 0
    assert exc.value.review["cost_is_estimate"] is False and committed(svc) == 0


def test_error_text_never_contains_the_key(tmp_path):
    svc, _ = make(tmp_path, result=status_error(anthropic.BadRequestError, 400))
    with pytest.raises(ReviewFailed) as exc:
        svc.run("EURUSD")
    assert "sk-ant-test-secret" not in str(exc.value) and "sk-ant-test-secret" not in str(exc.value.review)


def test_timeout_keeps_the_reservation_because_it_may_be_billed(tmp_path):
    error = anthropic.APITimeoutError(httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    svc, _ = make(tmp_path, result=error)
    with pytest.raises(ReviewFailed) as exc:
        svc.run("EURUSD")
    assert exc.value.review["error_code"] == "connection" and exc.value.review["cost_is_estimate"] is True
    assert committed(svc) > 0


@pytest.mark.parametrize("result,code", [(reply(stop="refusal", text=""), "refused"),
                                         (reply(stop="max_tokens", text='{"stance"'), "truncated"),
                                         (reply(text="not json"), "invalid_output"),
                                         (reply(text=json.dumps({**VERDICT, "entry": 1.1})), "invalid_output")])
def test_unusable_answers_fail_but_are_still_charged(tmp_path, result, code):
    svc, _ = make(tmp_path, result=result)
    with pytest.raises(ReviewFailed) as exc:
        svc.run("EURUSD")
    assert exc.value.review["error_code"] == code and exc.value.review["verdict"] is None
    assert committed(svc) == 60_000 and exc.value.review["cost_is_estimate"] is False


def test_a_crash_before_settling_leaves_the_money_reserved(tmp_path):
    svc, _ = make(tmp_path)
    budget.ensure_month(svc.engine, budget.month_key())
    uid, _month = svc._reserve("EURUSD", budget.utcnow(), 100, 200_000)
    assert committed(svc) == 200_000
    with session_scope(svc.engine) as db:
        assert db.query(AiReview).filter_by(review_uid=uid).one().status == "pending"


# ---- HTTP API
@pytest.fixture
def api(tmp_path):
    svc, messages = make(tmp_path, aurum_admin_api_key="admin", ai_review_monthly_budget_usd=0.40)
    with TestClient(create_app(settings=svc.settings, cfg=cot_cfg(), engine=svc.engine, ai_review_service=svc)) as c:
        yield c, messages


ADMIN = {"X-AURUM-Admin-Key": "admin"}


def test_status_is_camel_case_and_hides_the_key(api):
    client, _ = api
    r = client.get("/api/v1/ai-review/status")
    assert r.status_code == 200 and r.json()["ready"] is True and r.json()["apiKeyConfigured"] is True
    assert "sk-ant-test-secret" not in r.text


def test_post_needs_the_admin_key(api):
    client, messages = api
    assert client.post("/api/v1/ai-review/reviews", json={"symbol": "EURUSD"}).status_code == 401
    assert messages.calls == []


def test_post_runs_and_lists_a_review(api):
    client, _ = api
    r = client.post("/api/v1/ai-review/reviews", json={"symbol": "EURUSD"}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["verdict"]["whatWouldChangeThisView"] and r.json()["costUsd"] > 0
    listed = client.get("/api/v1/ai-review/reviews?symbol=EURUSD").json()
    assert [x["reviewUid"] for x in listed] == [r.json()["reviewUid"]]


def test_post_when_budget_is_gone_is_402(api):
    client, messages = api
    statuses = [client.post("/api/v1/ai-review/reviews", json={"symbol": "EURUSD"}, headers=ADMIN).status_code
                for _ in range(30)]
    assert statuses[-1] == 402 and statuses.count(200) == len(messages.calls)


def test_post_when_off_is_503(tmp_path):
    svc, _ = make(tmp_path, ai_review_enabled=False, aurum_admin_api_key="admin")
    with TestClient(create_app(settings=svc.settings, cfg=cot_cfg(), engine=svc.engine, ai_review_service=svc)) as client:
        assert client.post("/api/v1/ai-review/reviews", json={"symbol": "EURUSD"}, headers=ADMIN).status_code == 503


# ---- safety
def test_package_has_no_route_to_the_broker():
    root = Path(__file__).resolve().parent.parent / "aurum" / "ai_review"
    forbidden = re.compile(r"MetaTrader5|mt5_gateway|ReadOnlyMT5|order_send")
    assert [p.name for p in root.rglob("*.py") if forbidden.search(p.read_text(encoding="utf-8"))] == []


def test_prompt_forbids_trade_instructions_and_treats_text_as_data():
    assert "Do not give entries" in SYSTEM_PROMPT and "ignore them" in SYSTEM_PROMPT


def test_key_is_not_in_repr(tmp_path):
    s = settings(tmp_path)
    assert isinstance(s.anthropic_api_key, SecretStr) and "sk-ant-test-secret" not in repr(s)


# ---- the real SDK, with HTTP mocked
def sdk_service(tmp_path, handler):
    svc, _ = make(tmp_path)
    svc._client = anthropic.Anthropic(api_key="sk-ant-test-secret", max_retries=0,
                                      http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    return svc


def test_real_sdk_sends_the_request_and_parses_the_response(tmp_path):
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content), auth=request.headers["authorization"])
        return httpx2.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": MODEL, "stop_reason": "end_turn",
            "stop_sequence": None, "content": [{"type": "thinking", "thinking": "", "signature": "s"},
                                               {"type": "text", "text": json.dumps(VERDICT)}],
            "usage": {"input_tokens": 12_000, "output_tokens": 2_000, "cache_creation_input_tokens": 0,
                      "cache_read_input_tokens": 0}})

    out = sdk_service(tmp_path, handler).run("EURUSD")
    assert out["verdict"] == VERDICT and out["cost_usd"] == pytest.approx(0.088)
    assert seen["model"] == MODEL and seen["output_config"]["format"]["type"] == "json_schema"
    assert "sk-ant-test-secret" in seen["auth"] and seen["thinking"] == {"type": "adaptive"}


@pytest.mark.parametrize("code,error_code", [(401, "auth"), (429, "rate_limited"), (529, "api_error"), (400, "api_error")])
def test_real_sdk_errors_map_to_failures(tmp_path, code, error_code):
    def handler(request):
        return httpx2.Response(code, json={"type": "error", "error": {"type": "x", "message": "low credit balance"}})

    svc = sdk_service(tmp_path, handler)
    with pytest.raises(ReviewFailed) as exc:
        svc.run("EURUSD")
    assert exc.value.review["error_code"] == error_code and committed(svc) == 0
