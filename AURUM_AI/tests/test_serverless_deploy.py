"""Vercel deployment support: hosted-database URLs, the serverless engine and the pending-migration check."""
from __future__ import annotations

from sqlalchemy.pool import NullPool

from aurum.config import Settings
from aurum.database.connection import init_database, make_engine, pending_migrations, serverless


def test_hosted_postgres_urls_use_psycopg_driver():
    for given in ("postgres://u:p@h:6543/db", "postgresql://u:p@h:6543/db?sslmode=require"):
        url = Settings(_env_file=None, database_url=given).sqlalchemy_url
        assert url.startswith("postgresql+psycopg://u:p@h:6543/db")
    explicit = "postgresql+psycopg://u:p@h/db"
    assert Settings(_env_file=None, database_url=explicit).sqlalchemy_url == explicit


def test_serverless_detection(monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("AURUM_SERVERLESS", raising=False)
    assert not serverless()
    monkeypatch.setenv("VERCEL", "1")
    assert serverless()
    monkeypatch.delenv("VERCEL")
    monkeypatch.setenv("AURUM_SERVERLESS", "true")
    assert serverless()


def test_serverless_engine_keeps_no_pool_and_no_prepared_statements(monkeypatch):
    monkeypatch.setenv("AURUM_SERVERLESS", "1")
    engine = make_engine("postgresql+psycopg://u:p@localhost:1/db")   # nothing connects until first use
    assert isinstance(engine.pool, NullPool)


def test_pending_migrations_is_empty_for_sqlite(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'a.db'}")
    init_database(engine)
    assert pending_migrations(engine) == []
