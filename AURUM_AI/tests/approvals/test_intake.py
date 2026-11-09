from __future__ import annotations

from datetime import timedelta

import pytest

from aurum.approvals import intake
from m07_harness import NOW, Clock, idea

SYMBOLS = {"EURUSD": "forex", "XAUUSD": "metal"}


def bad(code, **over):
    with pytest.raises(intake.IntakeError) as e:
        intake.validate(idea(1, Clock(), **over), supported_symbols=SYMBOLS, now=NOW, source="event")
    assert e.value.code == code, e.value.message


def test_valid_idea_is_exact_decimals():
    p = intake.validate(idea(1, Clock()), supported_symbols=SYMBOLS, now=NOW, source="event")
    assert str(p.entry) == "1.10010" and p.direction == "BUY" and p.payload_hash() == p.payload_hash()


@pytest.mark.parametrize("field", ["symbol", "direction", "stop_loss", "take_profits", "timeframe", "created_at", "expires_at",
                                   "analysis_version", "correlation_id", "schema_version", "idea_id"])
def test_missing_fields(field):
    bad("MISSING_FIELD", **{field: None})


def test_rejections():
    bad("UNSUPPORTED_SCHEMA", schema_version="2.0")
    bad("UNSUPPORTED_INSTRUMENT", symbol="DOGEUSD")
    bad("INVALID_DIRECTION", direction="SIDEWAYS")
    bad("INVALID_TIMEFRAME", timeframe="M1")
    bad("INVALID_PRICE", entry="-1")
    bad("INVALID_PRICE", stop_loss="1.20000")                 # stop on the wrong side of a BUY
    bad("INVALID_PRICE", take_profits=["1.00000"])            # target on the wrong side
    bad("INVALID_PRICE", entry="abc")
    bad("INVALID_CONFIDENCE", ai_confidence=150)
    bad("INVALID_TIMESTAMP", created_at="2026-11-04T12:00:00")    # no time zone
    bad("INVALID_TIMESTAMP", created_at=(NOW + timedelta(hours=1)).isoformat())
    bad("INVALID_TIMESTAMP", expires_at=(NOW - timedelta(hours=1)).isoformat(), created_at=NOW.isoformat())
    bad("IDEA_EXPIRED", created_at=(NOW - timedelta(hours=3)).isoformat(), expires_at=(NOW - timedelta(minutes=1)).isoformat())
    bad("INVALID_EVIDENCE", evidence="not a list")
    bad("INVALID_EVIDENCE", evidence=[{"description": ""}])
    bad("INVALID_EVIDENCE", evidence=[{"description": "x", "source_module": "module_99"}])


def test_sell_and_aliases():
    p = intake.validate(idea(1, Clock(), direction="short", stop_loss="1.10210", take_profits=["1.09510"]),
                        supported_symbols=SYMBOLS, now=NOW, source="event")
    assert p.direction == "SELL"


def test_service_rejects_updates_without_new_analysis_version(h):
    from aurum.approvals.service import SYSTEM, ApprovalError
    h.ingest(1)
    with pytest.raises(ApprovalError) as e:
        h.svc.ingest(idea(1, h.clock, stop_loss="1.09850"), source="event", who=SYSTEM, update=True)
    assert e.value.code == "INCONSISTENT_ANALYSIS_VERSION"
    with pytest.raises(ApprovalError) as e:
        h.svc.ingest(idea(1, h.clock, stop_loss="1.09850"), source="event", who=SYSTEM)
    assert e.value.code == "DUPLICATE_IDEA"


def test_update_supersedes_open_request(h):
    from aurum.approvals.service import SYSTEM
    a1 = h.ingest(1)["approval_id"]
    out = h.svc.ingest(idea(1, h.clock, stop_loss="1.09850", analysis_version="test:1:v2"), source="event", who=SYSTEM,
                       update=True)
    assert out["version"] == 2 and out["approval_id"] != a1
    assert h.view(a1)["status"] == "INVALIDATED"
