-- =====================================================================
-- AURUM AI - Account mirror (read-only copy of the MT5 DEMO account).
-- Additive only: creates new tables; nothing existing is altered, dropped or deleted.
-- Mirrors aurum/account/db_models.py (SQLite/dev uses SQLAlchemy create_all).
-- Filled by `python -m aurum.cli sync-account` and `stream-ticks` (account_info + history_deals_get only).
-- =====================================================================

CREATE TABLE IF NOT EXISTS account_snapshots (
    id            BIGSERIAL PRIMARY KEY,
    captured_at   TIMESTAMPTZ NOT NULL,
    login         BIGINT NOT NULL,
    server        VARCHAR(128),
    currency      VARCHAR(16) NOT NULL,
    trade_mode    VARCHAR(16) NOT NULL,
    leverage      INTEGER,
    balance       DOUBLE PRECISION NOT NULL,
    equity        DOUBLE PRECISION NOT NULL,
    margin        DOUBLE PRECISION,
    margin_free   DOUBLE PRECISION,
    margin_level  DOUBLE PRECISION,
    profit        DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS ix_account_snapshots_time ON account_snapshots (captured_at);

CREATE TABLE IF NOT EXISTS trade_records (
    position_id    BIGINT PRIMARY KEY,
    symbol         VARCHAR(32) NOT NULL,
    broker_symbol  VARCHAR(64) NOT NULL,
    side           VARCHAR(4)  NOT NULL CHECK (side IN ('buy', 'sell')),
    status         VARCHAR(8)  NOT NULL CHECK (status IN ('open', 'closed')),
    volume         DOUBLE PRECISION NOT NULL,
    open_time      TIMESTAMPTZ NOT NULL,
    open_price     DOUBLE PRECISION NOT NULL,
    close_time     TIMESTAMPTZ,
    close_price    DOUBLE PRECISION,
    stop_loss      DOUBLE PRECISION,
    take_profit    DOUBLE PRECISION,
    pnl            DOUBLE PRECISION NOT NULL DEFAULT 0,
    strategy       VARCHAR(128),
    magic          BIGINT,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_trade_records_status_close ON trade_records (status, close_time);

CREATE TABLE IF NOT EXISTS account_cash_flows (
    deal_ticket  BIGINT PRIMARY KEY,
    ts_utc       TIMESTAMPTZ NOT NULL,
    amount       DOUBLE PRECISION NOT NULL,
    kind         VARCHAR(16) NOT NULL,
    comment      TEXT
);
