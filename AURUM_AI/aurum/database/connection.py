"""Engine / session factory. Credentials come from Settings (environment), never from source code."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from aurum.config import PROJECT_ROOT, Settings

log = logging.getLogger(__name__)
SCHEMA_SQL = PROJECT_ROOT / "sql" / "schema.sql"
MIGRATIONS_DIR = PROJECT_ROOT / "sql" / "migrations"


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        if ":memory:" not in url:
            Path(url.split("sqlite:///", 1)[1]).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, future=True)

        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _):  # pragma: no cover - trivial
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
        return engine
    return create_engine(url, pool_pre_ping=True, future=True, **_pool_options(url))


def _pool_options(url: str) -> dict:
    """Connection pool sizing. A hosted pooler (e.g. Supabase, ~15 client connections shared by every AURUM process
    and the hosted API) needs small pools, or connections are refused and dropped. DB_POOL_SIZE / DB_MAX_OVERFLOW
    override; defaults stay SQLAlchemy's (5 + 10) for a local database."""
    import os
    from sqlalchemy.engine import make_url
    host = (make_url(url).host or "").lower()
    hosted = "pooler.supabase.com" in host or host.endswith(".supabase.co") or host.endswith(".supabase.com")
    size = int(os.environ.get("DB_POOL_SIZE") or (2 if hosted else 5))
    overflow = int(os.environ.get("DB_MAX_OVERFLOW") or (3 if hosted else 10))
    args: dict = {"connect_timeout": 10}
    if hosted:   # keep idle connections alive through the pooler; recycle before it drops them
        args.update(keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3)
    opts: dict = {"pool_size": size, "max_overflow": overflow, "pool_timeout": 30, "connect_args": args}
    if hosted:
        opts["pool_recycle"] = 300
    return opts


def engine_from_settings(settings: Settings) -> Engine:
    return make_engine(settings.sqlalchemy_url)


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_database(engine: Engine) -> dict[str, object]:
    """Connectivity + TimescaleDB status. Raises on connection failure."""
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
        info: dict[str, object] = {"dialect": engine.dialect.name, "timescaledb": False, "schema_ready": False}
        if engine.dialect.name == "postgresql":
            info["server_version"] = conn.execute(text("SHOW server_version")).scalar()
            info["timescaledb"] = conn.execute(text(
                "SELECT extversion FROM pg_extension WHERE extname='timescaledb'")).scalar() or False
            info["schema_ready"] = bool(conn.execute(text("SELECT to_regclass('public.market_candles')")).scalar())
        else:
            info["schema_ready"] = bool(conn.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='market_candles'")).scalar())
    return info


def init_database(engine: Engine) -> list[str]:
    """Create/upgrade the schema idempotently. PostgreSQL: run sql/schema.sql then pending migrations
    (TimescaleDB hypertables when the extension is available, plain PostgreSQL otherwise).
    SQLite: SQLAlchemy create_all. Returns the list of applied steps."""
    steps: list[str] = []
    if engine.dialect.name == "postgresql":
        raw = engine.raw_connection()
        try:
            cur = raw.cursor()
            cur.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
            steps.append("schema.sql")
            cur.execute("SELECT version FROM schema_migrations")
            applied = {r[0] for r in cur.fetchall()}
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if path.stem in applied:
                    continue
                cur.execute(path.read_text(encoding="utf-8"))
                cur.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.stem,))
                steps.append(path.name)
            raw.commit()
        except Exception:
            raw.rollback()
            raise
        finally:
            raw.close()
    else:
        import aurum.cot.models  # noqa: F401 - registers Module 02 tables on the shared Base
        import aurum.analysis.services.db_models  # noqa: F401 - registers Module 03 tables on the shared Base
        import aurum.market_intelligence.db_models  # noqa: F401 - registers Module 04 tables on the shared Base
        import aurum.account.db_models  # noqa: F401 - registers account mirror tables on the shared Base
        import aurum.ai_review.db_models  # noqa: F401 - registers AI review tables on the shared Base
        import aurum.decision.db_models  # noqa: F401 - registers Module 05 tables on the shared Base
        import aurum.risk.db_models  # noqa: F401 - registers Module 06 tables on the shared Base
        import aurum.auth.db_models  # noqa: F401 - registers users, sessions and service tokens
        import aurum.approvals.db_models  # noqa: F401 - registers Module 07 tables on the shared Base
        import aurum.orchestration.db_models  # noqa: F401 - registers Module 08 tables on the shared Base
        import aurum.mt5link.db_models  # noqa: F401 - registers per-person MT5 account links
        from aurum.database.models import Base
        Base.metadata.create_all(engine)
        steps.append("sqlalchemy create_all")
    log.info("Database initialised", extra={"steps": steps, "dialect": engine.dialect.name})
    return steps
