from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SQL = (ROOT / "sql" / "migrations" / "0009_human_approval.sql").read_text(encoding="utf-8")


def test_migration_matches_models():
    import aurum.approvals.db_models as ap
    import aurum.auth.db_models as auth
    from aurum.database.models import Base
    mods = {ap.__name__, auth.__name__}
    names = {m.local_table.name for m in Base.registry.mappers if m.class_.__module__ in mods}
    assert len(names) == 22
    for name in names:
        assert f"CREATE TABLE IF NOT EXISTS {name} (" in SQL, name
        block = SQL.split(f"CREATE TABLE IF NOT EXISTS {name} (")[1].split(");")[0]
        for col in Base.metadata.tables[name].columns:
            assert re.search(rf"\b{col.name}\b", block), f"{name}.{col.name} missing from migration"


def test_migration_has_database_guarantees():
    from aurum.approvals.states import transition_sql_pairs
    assert "uq_approval_requests_active_idea" in SQL and "uq_trade_management_active_position" in SQL
    assert "uq_approval_decisions_final" in SQL
    for a, b in transition_sql_pairs():
        assert f"('{a}','{b}')" in SQL
    for t in ("approval_audit_logs", "approval_decisions", "trade_idea_versions", "execution_results"):
        assert f"trg_{t}_append_only" in SQL
    assert "DROP TABLE" not in SQL.upper().replace("DROP TRIGGER", "")


@pytest.mark.skipif(not os.environ.get("AURUM_TEST_DATABASE_URL"), reason="set AURUM_TEST_DATABASE_URL to run")
def test_postgres_enforces_state_machine_and_append_only():
    from sqlalchemy import create_engine
    engine = create_engine(os.environ["AURUM_TEST_DATABASE_URL"])
    with engine.begin() as c:
        # Raw driver call (as init_database does): psycopg must not parse the '%' in RAISE messages as placeholders.
        c.connection.driver_connection.execute(SQL)
        c.connection.driver_connection.execute(SQL)
        c.exec_driver_sql("INSERT INTO approval_audit_logs (at, actor, actor_kind, action, entity_type, entity_id, prev_hash, hash)"
                          " VALUES (now(),'t','system','x','x','x','0','0')")
    with pytest.raises(Exception, match="append-only"):
        with engine.begin() as c:
            c.exec_driver_sql("UPDATE approval_audit_logs SET actor='x'")
