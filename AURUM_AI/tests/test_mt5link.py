"""Per-person MT5 links: sign-in -> authorise own DEMO account -> slot -> connector -> per-account mirror -> sign-out.
MetaTrader is the FakeMT5 from conftest; the connector runs in-process with terminal operations switched off."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from aurum.account.db_models import AccountSnapshot
from aurum.api.app import create_app
from aurum.auth.service import AuthService
from aurum.config import Settings
from aurum.database.connection import engine_from_settings, init_database, session_scope
from aurum.mt5link import connector, terminals
from aurum.mt5link.db_models import Mt5Connection
from aurum.mt5link.service import Mt5LinkService
from conftest import Account, FakeMT5

PASSWORD = "correct-horse-battery-1"
BROKER_PW = "Br0ker!Secret#42"


class FakeLauncher:
    def __init__(self):
        self.started: list[dict] = []
        self.killed: list[int] = []

    def start(self, conn_id, slot, login, server, password):
        self.started.append({"id": conn_id, "slot": slot.number, "login": login, "server": server, "password": password})
        return 4000 + len(self.started)

    def kill(self, pid):
        self.killed.append(pid)


class AccountMT5(FakeMT5):
    """A terminal that logs in to whatever account it is asked for, with an empty deal history."""

    def __init__(self, login: int, **kw):
        super().__init__(**kw)
        self.login = login

    def account_info(self):
        return Account(self.login, self.trade_mode, "Broker-Demo", "Fake Broker Ltd", "USD", 100)

    def history_deals_get(self, a, b):
        return []

    def orders_get(self):
        return ()


@pytest.fixture
def env(tmp_path):
    settings = Settings(_env_file=None, database_url=f"sqlite:///{(tmp_path / 'a.db').as_posix()}", mt5_server_timezone="Etc/UTC",
                        auth_password_iterations=1000, mt5_slots_dir=tmp_path / "slots", mt5_slot_count=2,
                        log_dir=tmp_path / "logs", data_dir=tmp_path / "data", api_cors_origins=["http://localhost:5173"])
    for sl in terminals.slots(settings.mt5_slots_dir, 2):
        sl.folder.mkdir(parents=True)
        sl.exe.write_bytes(b"")
    eng = engine_from_settings(settings)
    init_database(eng)
    auth = AuthService.from_settings(eng, settings)
    auth.create_user("alice", PASSWORD, "trader", account_scope=["111"])
    auth.create_user("bob", PASSWORD, "trader", account_scope=["222"])
    auth.create_user("carol", PASSWORD, "observer", account_scope=["333"])
    launcher = FakeLauncher()
    svc = Mt5LinkService(eng, settings, launcher=launcher, cleanup=lambda slot: None)
    return settings, eng, auth, svc, launcher


def client(env) -> TestClient:
    settings, eng, auth, svc, _ = env
    return TestClient(create_app(settings=settings, engine=eng, auth_service=auth, mt5link_service=svc))


def sign_in(c: TestClient, user: str) -> dict:
    r = c.post("/api/v1/auth/login", json={"username": user, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"X-CSRF-Token": r.json()["csrfToken"]}


def run_connector(env, conn_id, login, *, trade_mode=0, max_syncs=1, mt5=None):
    settings, eng, *_ = env
    row = env[3].get(conn_id)
    return connector.run(conn_id, Path(settings.mt5_slots_dir) / f"slot{row['slot']}", login, "Broker-Demo", BROKER_PW, eng,
                         settings, mt5_module=mt5 or AccountMT5(login, trade_mode=trade_mode), sleep=lambda s: None,
                         max_syncs=max_syncs, terminal_ops=False)


# ---------------------------------------------------------------- API + flow
def test_sign_in_then_authorise_links_and_syncs_the_account(env):
    c = client(env)
    h = sign_in(c, "alice")
    assert c.get("/api/v1/mt5/connection").json() == {"connection": None, "accountScope": ["111"]}
    r = c.post("/api/v1/mt5/connect", json={"login": "111", "password": BROKER_PW, "server": "Broker-Demo"}, headers=h)
    assert r.status_code == 200, r.text
    conn = r.json()["connection"]
    assert conn["status"] == "CONNECTING" and conn["slot"] == 1 and BROKER_PW not in r.text
    assert env[4].started[0]["password"] == BROKER_PW          # handed to the connector only
    assert run_connector(env, conn["id"], 111) == 0
    now = c.get("/api/v1/mt5/connection").json()["connection"]
    assert now["login"] == 111 and now["tradeMode"] == "DEMO" and now["lastSync"]["deals"] == 0
    assert now["status"] == "DISCONNECTED" and now["disconnectReason"] == "test_limit"
    with session_scope(env[1]) as s:
        assert s.execute(select(AccountSnapshot.login)).scalars().all() == [111]


def test_password_is_never_stored_or_logged(env, caplog):
    c = client(env)
    h = sign_in(c, "alice")
    caplog.set_level("DEBUG")
    conn = c.post("/api/v1/mt5/connect", json={"login": 111, "password": BROKER_PW, "server": "Broker-Demo"}, headers=h).json()["connection"]
    run_connector(env, conn["id"], 111)
    assert BROKER_PW not in caplog.text
    with session_scope(env[1]) as s:
        row = s.get(Mt5Connection, conn["id"])
        assert BROKER_PW not in repr({k: getattr(row, k) for k in row.__table__.columns.keys()})
    db = Path(str(env[1].url.database)).read_bytes()
    assert BROKER_PW.encode() not in db


def test_non_demo_account_is_refused_and_nothing_is_mirrored(env):
    c = client(env)
    h = sign_in(c, "alice")
    conn = c.post("/api/v1/mt5/connect", json={"login": 111, "password": BROKER_PW, "server": "Broker-Demo"}, headers=h).json()["connection"]
    assert run_connector(env, conn["id"], 111, trade_mode=2) == 3
    now = c.get("/api/v1/mt5/connection").json()["connection"]
    assert now["status"] == "FAILED" and now["errorCode"] == "NOT_DEMO"
    with session_scope(env[1]) as s:
        assert s.execute(select(AccountSnapshot)).first() is None


def test_wrong_account_after_login_is_refused(env):
    c = client(env)
    h = sign_in(c, "alice")
    conn = c.post("/api/v1/mt5/connect", json={"login": 111, "password": BROKER_PW, "server": "Broker-Demo"}, headers=h).json()["connection"]
    assert run_connector(env, conn["id"], 111, mt5=AccountMT5(999)) == 4
    assert c.get("/api/v1/mt5/connection").json()["connection"]["errorCode"] == "WRONG_ACCOUNT"


def test_bad_terminal_login_reports_failure(env):
    c = client(env)
    h = sign_in(c, "alice")
    conn = c.post("/api/v1/mt5/connect", json={"login": 111, "password": BROKER_PW, "server": "Broker-Demo"}, headers=h).json()["connection"]
    assert run_connector(env, conn["id"], 111, mt5=AccountMT5(111, init_ok=False)) == 2
    now = c.get("/api/v1/mt5/connection").json()["connection"]
    assert now["status"] == "FAILED" and now["errorCode"] == "CONNECT_FAILED" and BROKER_PW not in (now["errorMessage"] or "")


def test_only_accounts_in_the_persons_scope(env):
    c = client(env)
    h = sign_in(c, "alice")
    r = c.post("/api/v1/mt5/connect", json={"login": 222, "password": BROKER_PW, "server": "Broker-Demo"}, headers=h)
    assert r.status_code == 403 and r.json()["error"]["code"] == "ACCOUNT_NOT_IN_SCOPE"
    assert env[4].started == []


@pytest.mark.parametrize("body,code", [({"login": "12a", "password": "x", "server": "Broker-Demo"}, "INVALID_LOGIN"),
                                       ({"login": 111, "password": "x", "server": "bad;server"}, "INVALID_SERVER"),
                                       ({"login": 111, "password": "", "server": "Broker-Demo"}, "INVALID_PASSWORD")])
def test_input_validation(env, body, code):
    c = client(env)
    h = sign_in(c, "alice")
    r = c.post("/api/v1/mt5/connect", json=body, headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == code


def test_csrf_and_sign_in_required(env):
    c = client(env)
    assert c.get("/api/v1/mt5/connection").status_code == 401
    sign_in(c, "alice")
    r = c.post("/api/v1/mt5/connect", json={"login": 111, "password": BROKER_PW, "server": "Broker-Demo"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "CSRF_FAILED"


def test_several_people_get_separate_slots_and_data(env):
    a, b = client(env), client(env)
    ha, hb = sign_in(a, "alice"), sign_in(b, "bob")
    ca = a.post("/api/v1/mt5/connect", json={"login": 111, "password": "pa", "server": "Broker-Demo"}, headers=ha).json()["connection"]
    cb = b.post("/api/v1/mt5/connect", json={"login": 222, "password": "pb", "server": "Broker-Demo"}, headers=hb).json()["connection"]
    assert {ca["slot"], cb["slot"]} == {1, 2}
    run_connector(env, ca["id"], 111)
    run_connector(env, cb["id"], 222)
    assert a.get("/api/v1/mt5/connection").json()["connection"]["login"] == 111   # each sees only their own link
    assert b.get("/api/v1/mt5/connection").json()["connection"]["login"] == 222
    with session_scope(env[1]) as s:
        assert sorted(s.execute(select(AccountSnapshot.login)).scalars().all()) == [111, 222]


def test_all_slots_busy_is_reported_clearly(env):
    users = ["alice", "bob", "carol"]
    clients = [client(env) for _ in users]
    heads = [sign_in(c, u) for c, u in zip(clients, users)]
    for c, h, login in zip(clients[:2], heads[:2], (111, 222)):
        assert c.post("/api/v1/mt5/connect", json={"login": login, "password": "p", "server": "Broker-Demo"}, headers=h).status_code == 200
    r = clients[2].post("/api/v1/mt5/connect", json={"login": 333, "password": "p", "server": "Broker-Demo"}, headers=heads[2])
    assert r.status_code == 503 and r.json()["error"]["code"] == "ALL_SLOTS_BUSY" and "2 MT5 terminal slot" in r.json()["detail"]


def test_same_account_cannot_be_linked_twice_at_once(env):
    _, _, auth, *_ = env
    auth.create_user("alice2", PASSWORD, "trader", account_scope=["111"])
    a, a2 = client(env), client(env)
    ha, ha2 = sign_in(a, "alice"), sign_in(a2, "alice2")
    assert a.post("/api/v1/mt5/connect", json={"login": 111, "password": "p", "server": "Broker-Demo"}, headers=ha).status_code == 200
    r = a2.post("/api/v1/mt5/connect", json={"login": 111, "password": "p", "server": "Broker-Demo"}, headers=ha2)
    assert r.status_code == 409 and r.json()["error"]["code"] == "ACCOUNT_IN_USE"


def test_authorising_again_is_idempotent(env):
    c = client(env)
    h = sign_in(c, "alice")
    first = c.post("/api/v1/mt5/connect", json={"login": 111, "password": "p", "server": "Broker-Demo"}, headers=h).json()["connection"]
    again = c.post("/api/v1/mt5/connect", json={"login": 111, "password": "p", "server": "Broker-Demo"}, headers=h).json()["connection"]
    assert first["id"] == again["id"] and len(env[4].started) == 1


def test_sign_out_disconnects_and_the_connector_stops(env):
    c = client(env)
    h = sign_in(c, "alice")
    conn = c.post("/api/v1/mt5/connect", json={"login": 111, "password": "p", "server": "Broker-Demo"}, headers=h).json()["connection"]
    assert c.post("/api/v1/auth/logout", headers=h).status_code == 200
    assert env[3].get(conn["id"])["status"] == "DISCONNECTING"
    assert run_connector(env, conn["id"], 111, max_syncs=None) == 0          # stops by itself before syncing
    row = env[3].get(conn["id"])
    assert row["status"] == "DISCONNECTED" and row["disconnect_reason"] == "signed_out"


def test_expired_session_stops_the_connector(env):
    settings, eng, auth, svc, _ = env
    c = client(env)
    h = sign_in(c, "alice")
    conn = c.post("/api/v1/mt5/connect", json={"login": 111, "password": "p", "server": "Broker-Demo"}, headers=h).json()["connection"]
    from aurum.auth.db_models import AuthSession
    with session_scope(eng) as s:
        sess = s.get(AuthSession, s.get(Mt5Connection, conn["id"]).session_id)
        sess.last_seen_at = datetime.now(timezone.utc) - timedelta(hours=2)
    assert run_connector(env, conn["id"], 111, max_syncs=None) == 0
    assert svc.get(conn["id"])["disconnect_reason"] == "session_expired"


def test_disconnect_endpoint(env):
    c = client(env)
    h = sign_in(c, "alice")
    c.post("/api/v1/mt5/connect", json={"login": 111, "password": "p", "server": "Broker-Demo"}, headers=h)
    r = c.post("/api/v1/mt5/disconnect", headers=h)
    assert r.status_code == 200 and r.json()["connection"]["status"] == "DISCONNECTING"


def test_lost_connector_frees_its_slot(env):
    settings, eng, auth, svc, launcher = env
    c = client(env)
    h = sign_in(c, "alice")
    conn = c.post("/api/v1/mt5/connect", json={"login": 111, "password": "p", "server": "Broker-Demo"}, headers=h).json()["connection"]
    with session_scope(eng) as s:
        s.get(Mt5Connection, conn["id"]).created_at = datetime.now(timezone.utc) - timedelta(hours=1)
    assert svc.reap() == 1
    assert svc.get(conn["id"])["status"] == "LOST" and launcher.killed == [4001]
    assert svc.status()["free_slots"] == 2


def test_not_configured_when_no_slots(env, tmp_path):
    settings, eng, auth, _, launcher = env
    svc = Mt5LinkService(eng, settings.model_copy(update={"mt5_slot_count": 0}), launcher=launcher, cleanup=lambda s: None)
    c = TestClient(create_app(settings=settings, engine=eng, auth_service=auth, mt5link_service=svc))
    h = sign_in(c, "alice")
    assert c.get("/api/v1/mt5/status").json()["enabled"] is False
    r = c.post("/api/v1/mt5/connect", json={"login": 111, "password": "p", "server": "Broker-Demo"}, headers=h)
    assert r.status_code == 503 and r.json()["error"]["code"] == "MT5_LINK_NOT_CONFIGURED"


def test_service_tokens_cannot_link(env):
    from aurum.auth import roles
    assert roles.MT5_LINK in roles.HUMAN_ONLY
    assert roles.MT5_LINK not in roles.permissions_for(roles.Role.SERVICE)


# ---------------------------------------------------------------- pieces
def test_connector_reads_password_from_stdin_not_argv(monkeypatch):
    seen = {}
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(BROKER_PW + "\n"))
    monkeypatch.setattr(connector, "run", lambda *a, **k: seen.setdefault("args", a) and 0)
    monkeypatch.setattr("aurum.config.get_settings", lambda: Settings(_env_file=None, database_url="sqlite:///:memory:"))
    monkeypatch.setattr("aurum.logging_config.setup_logging", lambda *a, **k: None)
    connector.main(["--connection-id", "x", "--slot-folder", "C:/s", "--login", "5", "--server", "Broker-Demo"])
    assert seen["args"][4] == BROKER_PW


def test_launcher_command_line_has_no_password(monkeypatch, tmp_path):
    from aurum.mt5link import service as svc_mod
    captured = {}

    class P:
        pid = 7

        def __init__(self, cmd, **kw):
            captured["cmd"] = cmd
            self.stdin = __import__("io").BytesIO()
            self.stdin.close = lambda: captured.setdefault("stdin", self.stdin.getvalue())

    monkeypatch.setattr(svc_mod.subprocess, "Popen", P)
    pid = svc_mod.SubprocessLauncher(tmp_path).start("id", terminals.Slot(1, tmp_path / "slot1"), 111, "Broker-Demo", BROKER_PW)
    assert pid == 7 and all(BROKER_PW not in str(a) for a in captured["cmd"])
    assert captured["stdin"] == (BROKER_PW + "\n").encode()


def test_setup_slots_copies_the_terminal_without_saved_logins(tmp_path):
    src = tmp_path / "MetaTrader 5"
    (src / "Config").mkdir(parents=True)
    (src / "terminal64.exe").write_bytes(b"exe")
    (src / "Config" / "accounts.dat").write_bytes(b"secret")
    (src / "logs").mkdir()
    servers = tmp_path / "servers.dat"
    servers.write_bytes(b"srv")
    rows = terminals.setup_slots(src, tmp_path / "slots", 2, servers_dat=servers)
    assert [r["created"] for r in rows] == [True, True] and all(r["serversDat"] for r in rows)
    s1 = tmp_path / "slots" / "slot1"
    assert (s1 / "terminal64.exe").is_file() and not (s1 / "Config" / "accounts.dat").exists() and not (s1 / "logs").exists()
    assert terminals.setup_slots(src, tmp_path / "slots", 2, servers_dat=servers)[0]["created"] is False
    (s1 / "Config" / "accounts.dat").write_bytes(b"x")
    terminals.forget_saved_logins(s1)
    assert not (s1 / "Config" / "accounts.dat").exists()


def test_migration_0011_covers_the_models():
    sql = (Path(__file__).resolve().parents[1] / "sql" / "migrations" / "0011_mt5_accounts.sql").read_text(encoding="utf-8")
    for col in Mt5Connection.__table__.columns:
        assert re.search(rf"\b{col.name}\b", sql), col.name
    assert "PRIMARY KEY (login, ticket)" in sql and "PRIMARY KEY (login, id)" in sql
    assert not re.search(r"\bDROP\s+TABLE\b|\bDELETE\s+FROM\b|\bTRUNCATE\b", sql, re.I)


def test_connector_models_load_in_a_fresh_process():
    """Regression: the connector runs as its own process; its first write must not fail on an unresolved foreign key."""
    import subprocess
    import sys
    code = "; ".join(["import aurum.mt5link.connector",
                      "from aurum.mt5link.db_models import Mt5Connection",
                      "from sqlalchemy.orm import class_mapper",
                      "print([fk.column.table.name for fk in Mt5Connection.__table__.foreign_keys])",
                      "class_mapper(Mt5Connection)._sorted_tables"])
    root = Path(__file__).resolve().parents[1]
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    assert "users" in r.stdout


def test_connector_rides_out_a_database_drop(env, monkeypatch):
    """Regression: a hosted database dropping a connection mid-link must not end the MT5 link."""
    from sqlalchemy.exc import OperationalError
    c = client(env)
    h = sign_in(c, "alice")
    conn = c.post("/api/v1/mt5/connect", json={"login": 111, "password": "p", "server": "Broker-Demo"}, headers=h).json()["connection"]
    real = connector.ConnectionStore._stop_reason_once
    fails = {"n": 2}

    def flaky(self):
        if fails["n"]:
            fails["n"] -= 1
            raise OperationalError("SELECT 1", {}, Exception("server closed the connection unexpectedly"))
        return real(self)
    monkeypatch.setattr(connector.ConnectionStore, "_stop_reason_once", flaky)
    assert run_connector(env, conn["id"], 111, max_syncs=2) == 0
    row = env[3].get(conn["id"])
    assert fails["n"] == 0 and row["status"] == "DISCONNECTED" and row["disconnect_reason"] == "test_limit"
    assert row["error_code"] is None
