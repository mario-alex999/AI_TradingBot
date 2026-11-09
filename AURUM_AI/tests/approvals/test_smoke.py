from m07_harness import FakeExecution


def test_happy_path_end_to_end(h):
    out = h.ingest(1)
    aid = out["approval_id"]
    assert h.view(aid)["status"] == "PENDING"
    h.svc.start_review(aid, h.users["tom"])
    res = h.approve(aid)
    assert res["status"] == "EXECUTION_PENDING", res
    m09 = FakeExecution(h)
    q = m09.take()
    assert len(q) == 1 and q[0]["payload"]["approval_id"] == aid
    m09.report(q[0]["execution_request_id"], "submitted")
    m09.report(q[0]["execution_request_id"], "filled", position_id="555", fill_price="1.10012", filled_volume="0.10")
    assert h.view(aid)["status"] == "EXECUTED"
    ok, n, err = h.svc.repo.verify_audit()
    assert ok, err
