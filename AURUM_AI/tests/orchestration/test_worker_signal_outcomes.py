"""The Module 08 worker records what each AI signal did in the market (Module 05 outcome labelling) every 5 minutes, so the
AI Signals page can show triggered / take profit / stop loss on the hosted set-up, where no Module 05 loop runs."""
from __future__ import annotations

from test_wf08_03 import idea

from aurum.decision.db_models import CandidateOutcome
from aurum.orchestration.worker import Worker


def test_outcomes_run_every_five_minutes_even_with_a_fresh_worker_per_tick(h, monkeypatch):
    ran = []
    monkeypatch.setattr(Worker, "_signal_outcomes", lambda self: ran.append(1) or {"tp": 1})
    out = Worker(h.svc).tick()                                         # nothing labelled yet: due
    assert out["signal_outcomes"] == {"tp": 1}
    uid = idea(h)
    with h.svc.repo.write() as s:
        s.execute(CandidateOutcome.__table__.insert().values(candidate_uid=uid, touched=True, filled=True, result="tp",
                                                             updated_at=h.clock()))
    h.clock.advance(minutes=2)
    assert "signal_outcomes" not in Worker(h.svc).tick()               # labelled 2 minutes ago: not due
    h.clock.advance(minutes=4)
    assert "signal_outcomes" in Worker(h.svc).tick() and len(ran) == 2


def test_a_failing_outcome_pass_does_not_stop_the_other_steps(h, monkeypatch):
    def boom(self):
        raise RuntimeError("candles unavailable")
    monkeypatch.setattr(Worker, "_signal_outcomes", boom)
    out = Worker(h.svc).tick()
    assert "error" in out["signal_outcomes"] and "schedule" in out
