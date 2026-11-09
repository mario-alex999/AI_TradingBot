"""Trade management rules, the background worker (outbox, notifications, Module 05 write-back) and failure behaviour."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from aurum.approvals.service import ApprovalError
from aurum.approvals.worker import Worker
from m07_harness import FakeExecution


def position(h, **over):
    row = {"position_id": "9001", "account_login": "1001", "symbol": "EURUSD", "side": "BUY", "volume": Decimal("0.10"),
           "entry_price": Decimal("1.10000"), "current_price": Decimal("1.10200"), "stop_loss": Decimal("1.09800"),
           "take_profit": Decimal("1.10500"), "unrealized_pnl": Decimal("20"), "realized_pnl": None, "status": "OPEN",
           "opened_at": h.clock() - timedelta(hours=1)}
    row.update(over)
    h.positions.rows = [row]


def mreq(h, action, who="tom", key="k1", **params):
    return h.svc.create_management_request(h.users[who], {"position_id": "9001", "action": action, "params": params,
                                                          "reason": "test reason", "client_request_id": key})


# ---- trade management ------------------------------------------------------------------------------------------------------
def test_stop_widening_refused(h):
    position(h)
    with pytest.raises(ApprovalError) as e:
        mreq(h, "MOVE_STOP_LOSS", stop_loss="1.09500")
    assert e.value.code == "STOP_WIDENING_NOT_ALLOWED"


def test_stop_through_price_and_breakeven_rules(h):
    position(h)
    with pytest.raises(ApprovalError) as e:
        mreq(h, "MOVE_STOP_LOSS", stop_loss="1.10300")
    assert e.value.code == "STOP_THROUGH_PRICE"
    position(h, current_price=Decimal("1.09900"))
    with pytest.raises(ApprovalError) as e:
        mreq(h, "BREAKEVEN")
    assert e.value.code == "NOT_IN_PROFIT"


def test_partial_close_and_trailing(h):
    position(h)
    r = mreq(h, "PARTIAL_CLOSE", fraction="0.5")
    assert Decimal(r["params"]["volume"]) == Decimal("0.05")
    h.svc.reject_management(r["request_id"], h.users["alice"], {"client_request_id": "rj", "request_version": r["version"],
                                                                 "comment": "not now"})
    with pytest.raises(ApprovalError):
        mreq(h, "PARTIAL_CLOSE", key="k2", volume="0.10")              # whole volume: use CLOSE
    t = mreq(h, "TRAILING_STOP", key="k3", distance="0.00100")
    assert Decimal(t["params"]["stop_loss"]) == Decimal("1.10100")


def test_one_active_request_per_position_and_emergency_supersedes(h):
    position(h)
    first = mreq(h, "MOVE_TAKE_PROFIT", take_profit="1.10700")
    with pytest.raises(ApprovalError) as e:
        mreq(h, "CLOSE", key="k2")
    assert e.value.code == "REQUEST_ACTIVE"
    em = mreq(h, "EMERGENCY_EXIT", key="k3")
    assert em["priority"] == "emergency"
    assert h.svc.management_request(first["request_id"], h.users["tom"])["status"] == "CANCELLED"


def test_management_permissions_and_position_changes(h):
    position(h)
    with pytest.raises(ApprovalError) as e:
        mreq(h, "CLOSE", who="rita")                 # reviewers cannot request management actions
    assert e.value.status == 403
    r = mreq(h, "MOVE_STOP_LOSS", stop_loss="1.10000")
    position(h, stop_loss=Decimal("1.09900"))        # someone moved the stop meanwhile
    with pytest.raises(ApprovalError) as e:
        h.svc.approve_management(r["request_id"], h.users["tom"], {"client_request_id": "a", "request_version": r["version"],
                                                                    "confirmation": {"action": "MOVE_STOP_LOSS"}})
    assert any("changed since the request" in d for d in e.value.details)


def test_management_request_expires(h):
    position(h)
    r = mreq(h, "CLOSE")
    h.clock.advance(minutes=16)
    assert h.svc.expire_due()["management_expired"] == 1
    assert h.svc.management_request(r["request_id"], h.users["tom"])["status"] == "EXPIRED"


def test_pending_orders_reported_unavailable(h):
    position(h)
    with pytest.raises(ApprovalError) as e:
        mreq(h, "CANCEL_PENDING_ORDER")
    assert e.value.code == "NOT_AVAILABLE"


# ---- worker ------------------------------------------------------------------------------------------------------------------
class Channel:
    def __init__(self, ok=True, configured=True):
        self.ok, self._c, self.sent = ok, configured, []

    def configured(self):
        return self._c

    def send(self, to, subject, body):
        if not self.ok:
            raise ConnectionError("provider down")
        self.sent.append((to, subject))


def test_notifications_in_app_read_state(h):
    h.ingest(1)
    tom = h.users["tom"]
    items = h.svc.notifications(tom)
    assert items and not items[0]["read"] and h.svc.unread_count(tom) == len(items)
    h.svc.mark_read(tom, items[0]["id"])
    assert h.svc.unread_count(tom) == len(items) - 1
    h.svc.mark_read(tom, None)
    assert h.svc.unread_count(tom) == 0
    assert h.svc.unread_count(h.users["rita"]) > 0          # read state is per person


def test_email_and_telegram_delivery_with_retry(tmp_path, m07_template_root):
    from aurum.approvals.policy import Policy
    from m07_harness import H
    p = Policy()
    p.notifications.channels = {"in_app": True, "email": True, "telegram": True}
    h = H(tmp_path, m07_template_root, policy=p)
    email, tg = Channel(ok=False), Channel()
    w = Worker(h.svc, h.settings, channels={"email": email, "telegram": tg}, http_post=lambda u, b: 200)
    h.ingest(1)
    out = w.deliver_notifications()
    assert out["failed"] > 0 and tg.sent                     # email down does not stop Telegram
    email.ok = True
    assert w.deliver_notifications()["sent"] == 0            # backoff: not retried immediately
    h.clock.advance(minutes=1)
    assert w.deliver_notifications()["sent"] > 0 and email.sent


def test_unconfigured_channel_is_marked_not_configured(tmp_path, m07_template_root):
    from aurum.approvals.policy import Policy
    from m07_harness import H
    p = Policy()
    p.notifications.channels = {"in_app": True, "email": True, "telegram": False}
    h = H(tmp_path, m07_template_root, policy=p)
    w = Worker(h.svc, h.settings, channels={"email": Channel(configured=False)}, http_post=lambda u, b: 200)
    h.ingest(1)
    assert w.deliver_notifications()["skipped"] > 0
    assert h.svc.notifications(h.users["tom"])                # in-app still delivered


def test_outbox_dispatch_retries_then_dead_letters(h):
    h.settings.approvals_events_webhook_url = "http://n8n.test/webhook"
    calls = []
    w = Worker(h.svc, h.settings, channels={}, http_post=lambda u, b: calls.append(b) or 500)
    h.ingest(1)
    assert w.dispatch_outbox()["failed"] >= 1
    for _ in range(10):
        h.clock.advance(hours=1)
        w.dispatch_outbox()
    st = h.svc.health()
    assert st["outbox"]["dead"] >= 1
    w2 = Worker(h.svc, h.settings, channels={}, http_post=lambda u, b: 200)
    h.ingest(2)
    assert w2.dispatch_outbox()["sent"] >= 1


def test_module05_feedback_never_changes_module07_decision(h):
    applied = []

    def feedback(uid, decision, actor, note):
        applied.append((uid, decision))
        return "not_applied: gate failed now"
    w = Worker(h.svc, h.settings, ideas_feedback=feedback, channels={}, http_post=lambda u, b: 200)
    aid = h.ingest(1)["approval_id"]
    from aurum.approvals.db_models import TradeIdea
    with h.svc.repo.write() as s:                    # pretend this idea came from Module 05
        i = s.get(TradeIdea, "idea-1")
        i.source, i.source_ref = "module05", "cand-1"
    h.approve(aid)
    w.module05_feedback()
    assert applied == [("cand-1", "APPROVED")]
    assert h.view(aid)["status"] == "EXECUTION_PENDING"


def test_worker_tick_survives_failing_steps(h):
    def boom():
        raise RuntimeError("module 05 down")
    w = Worker(h.svc, h.settings, channels={}, http_post=lambda u, b: 200)
    h.svc.sync_module05 = boom
    h.ingest(1, timeframe="M5")
    h.clock.advance(minutes=6)
    out = w.tick()
    assert "error" in out["sync"] and out["expiry"]["expired"] == 1


# ---- failures ----------------------------------------------------------------------------------------------------------------
def test_module09_unavailable_leaves_requests_waiting(h):
    aid = h.ingest(1)["approval_id"]
    h.approve(aid)
    h.clock.advance(hours=3)                         # nobody picked it up
    h.svc.expire_due()
    assert h.view(aid)["status"] == "EXECUTION_PENDING"     # not executed, not lost, not re-approved


def test_restart_during_pending_approval(h, tmp_path):
    from aurum.approvals.service import ApprovalService
    aid = h.ingest(1)["approval_id"]
    fresh = ApprovalService(h.engine, h.settings, h.risk, None, None, h.positions, clock=h.clock)   # "after restart"
    assert fresh.detail(aid, h.users["tom"])["request"]["status"] == "PENDING"
    body = h.approve_body(aid)
    assert fresh.approve(aid, h.users["tom"], body)["status"] == "EXECUTION_PENDING"


def test_database_failure_mid_transaction_rolls_back(h, monkeypatch):
    aid = h.ingest(1)["approval_id"]
    body = h.approve_body(aid)
    original = h.svc.repo.emit

    def failing_emit(s, event_type, *a, **k):
        if event_type == "approval.approved":
            raise RuntimeError("database write failed")
        return original(s, event_type, *a, **k)
    monkeypatch.setattr(h.svc.repo, "emit", failing_emit)
    with pytest.raises(RuntimeError):
        h.svc.approve(aid, h.users["tom"], body)
    v = h.view(aid)
    assert v["status"] == "PENDING"                 # no approval without its decision, audit and event
    assert not h.svc.detail(aid, h.users["tom"])["decisions"]
    monkeypatch.setattr(h.svc.repo, "emit", original)
    assert h.svc.approve(aid, h.users["tom"], h.approve_body(aid))["status"] == "EXECUTION_PENDING"


def test_position_source_down_is_reported_not_guessed(h):
    h.positions.fail = True
    p = h.svc.positions(h.users["tom"])
    assert p["positions"] == [] and "unavailable" in p["error"]


def test_concurrent_approve_and_expiry(h):
    import threading
    aid = h.ingest(1, timeframe="M5")["approval_id"]
    body = h.approve_body(aid)
    h.clock.advance(minutes=5)
    out = {}

    def approve():
        try:
            out["approve"] = h.svc.approve(aid, h.users["tom"], body)["status"]
        except ApprovalError as exc:
            out["approve"] = exc.code
    t = threading.Thread(target=approve)
    t.start()
    h.svc.expire_due()
    t.join()
    assert h.view(aid)["status"] == "EXPIRED" and out["approve"] in ("EXPIRED", "NOT_PENDING")
    assert not [e for e in h.svc.repo.events(0, 500) if e["event_type"] == "trade.approved_for_execution"]
    assert FakeExecution(h).take() == []


def test_refused_module05_candidate_is_audited_once_not_every_tick(h):
    """An expired Module 05 candidate is refused at intake once; later syncs skip it instead of re-auditing it each tick."""
    from datetime import timedelta

    now = h.svc.now()
    cand = {"candidate_uid": "c-expired", "symbol": "EURUSD", "direction": "BUY", "entry_low": 1.0849, "entry_high": 1.0851,
            "stop_loss": 1.083, "take_profit": 1.09, "risk_reward": 2.5, "timeframe": "H1", "template": "trend_pullback",
            "as_of": (now - timedelta(hours=3)).isoformat(), "expires_at": (now - timedelta(hours=1)).isoformat(),
            "input_hash": "0" * 32, "status": "new"}

    class Ideas:
        def active_candidates(self): return [cand]
        def candidate(self, uid): return cand
        def evidence(self, uid): return []
        def ai_review(self, uid): return None
        def gates(self, uid): return []
        def feedback(self, *a): return "applied"

    h.svc.ideas = Ideas()
    assert h.svc.sync_module05()["errors"] == 1
    assert h.svc.sync_module05() == {"ingested": 0, "closed": 0, "errors": 0}
    rows = [a for a in h.svc.audit_log(h.users["alice"], limit=500)["items"] if a["action"] == "idea.rejected_at_intake"]
    assert len(rows) == 1
