"""0010_orchestration.sql matches the models, is additive only, and its database guarantees hold."""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
SQL = (ROOT / "sql" / "migrations" / "0010_orchestration.sql").read_text(encoding="utf-8")


def _tables():
    import aurum.orchestration.db_models as m
    from aurum.database.models import Base
    return {mp.local_table.name for mp in Base.registry.mappers if mp.class_.__module__ == m.__name__}


def test_migration_matches_models():
    from aurum.database.models import Base
    names = _tables()
    assert len(names) == 22 and all(n.startswith("orch_") for n in names)
    for name in names:
        assert f"CREATE TABLE IF NOT EXISTS {name} (" in SQL, name
        block = SQL.split(f"CREATE TABLE IF NOT EXISTS {name} (")[1].split(");")[0]
        for col in Base.metadata.tables[name].columns:
            assert re.search(rf"\b{col.name}\b", block), f"{name}.{col.name} missing from migration"
        for ix in Base.metadata.tables[name].indexes:
            assert ix.name in SQL, ix.name


def test_migration_is_additive_and_touches_only_module08_objects():
    upper = SQL.upper()
    assert "DROP TABLE" not in upper and "TRUNCATE" not in upper and "DELETE FROM" not in upper
    assert "ALTER TABLE" not in upper                                  # existing tables are never changed
    for stmt in re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", SQL):
        assert stmt.startswith("orch_")
    for trg_table in re.findall(r"ON (\w+)\s*(?:FOR EACH|$)", SQL, re.M):
        assert trg_table.startswith("orch_"), trg_table


def test_migration_has_database_guarantees():
    from aurum.orchestration.states import execution_sql_pairs
    for a, b in execution_sql_pairs():
        assert f"('{a}','{b}')" in SQL
    for t in ("orch_audit_logs", "orch_config_versions"):
        assert f"trg_{t}_append_only" in SQL
    assert "trg_orch_workflow_versions_immutable" in SQL
    for uq in ("uq_orch_executions_idem", "uq_orch_schedule_runs", "uq_orch_event_deliveries", "uq_orch_inbox",
               "uq_orch_execution_attempts", "uq_orch_idempotency"):
        assert uq in SQL, uq


def _install_sqlite_transition_trigger(engine):
    """The PostgreSQL trigger's rule, as a SQLite trigger built from the same pair table."""
    from aurum.orchestration.states import execution_sql_pairs
    with engine.begin() as c:
        c.execute(text("CREATE TABLE allowed_pairs (f TEXT, t TEXT)"))
        for a, b in execution_sql_pairs():
            c.execute(text("INSERT INTO allowed_pairs VALUES (:a, :b)"), {"a": a, "b": b})
        c.execute(text("""CREATE TRIGGER trg_check BEFORE UPDATE OF status ON orch_executions
            WHEN NEW.status IS NOT OLD.status AND (NOT EXISTS (SELECT 1 FROM allowed_pairs WHERE f = OLD.status AND t = NEW.status)
                 OR NEW.version <= OLD.version)
            BEGIN SELECT RAISE(ABORT, 'illegal workflow execution transition'); END"""))


def test_every_code_path_issues_one_legal_transition_per_update(h):
    """Each status change is flushed separately with a version increase, so the PostgreSQL trigger accepts all flows."""
    from aurum.orchestration.engines import DispatchResult
    from m08_harness import TOM
    _install_sqlite_transition_trigger(h.engine)
    h.activate_test_workflow()
    h.n8n.script = [DispatchResult("transient", 503, error_category="SERVER_ERROR", error="503"),
                    DispatchResult("permanent", 404, error_category="WORKFLOW_NOT_FOUND", error="404")]
    a, _ = h.trigger("m1")                                             # RECEIVED -> VALIDATING -> QUEUED
    h.svc.dispatch_due()                                               # -> DISPATCHED -> RETRY_PENDING
    h.clock.advance(minutes=5)
    h.svc.dispatch_due()                                               # -> DISPATCHED -> FAILED -> DEAD_LETTERED
    assert h.status(a["execution_id"]) == "DEAD_LETTERED"
    h.svc.retry_execution(TOM, a["execution_id"], {"reason": "r"})     # -> RETRY_PENDING -> DISPATCHED -> WAITING_CALLBACK
    h.callback(a["execution_id"], "started", attempt=3)
    h.callback(a["execution_id"], "failed", attempt=3, error={"category": "RATE_LIMITED", "message": "x"})  # FAILED -> RETRY_PENDING
    b, _ = h.trigger("m2")
    h.svc.dispatch_due()
    h.clock.advance(hours=1)
    h.svc.sweep_timeouts()                                             # WAITING_CALLBACK -> TIMED_OUT
    c_, _ = h.trigger("m3")
    h.svc.cancel_execution(TOM, c_["execution_id"], {"reason": "x"})  # QUEUED -> CANCELLED
    # And a direct illegal update is refused by the trigger.
    with h.engine.begin() as c, pytest.raises(Exception, match="illegal"):
        c.execute(text("UPDATE orch_executions SET status='QUEUED', version=version+1 WHERE execution_id=:e"),
                  {"e": c_["execution_id"]})


@pytest.mark.skipif(not os.environ.get("AURUM_TEST_DATABASE_URL"), reason="set AURUM_TEST_DATABASE_URL to run")
def test_postgres_enforces_state_machine_and_append_only():
    from sqlalchemy import create_engine
    engine = create_engine(os.environ["AURUM_TEST_DATABASE_URL"])
    with engine.begin() as c:
        c.connection.driver_connection.execute(SQL)
        c.connection.driver_connection.execute(SQL)                    # re-runnable
        c.exec_driver_sql("INSERT INTO orch_audit_logs (at, actor, actor_kind, action, entity_type, entity_id, outcome, "
                          "prev_hash, hash) VALUES (now(),'t','system','x','x','x','ok','0','0')")
    with pytest.raises(Exception, match="append-only"):
        with engine.begin() as c:
            c.exec_driver_sql("UPDATE orch_audit_logs SET actor='x'")
    engine.dispose()
