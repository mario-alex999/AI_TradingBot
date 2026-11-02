-- =====================================================================
-- AURUM AI - Module 01 schema (v1). PostgreSQL 14+ / TimescaleDB 2.x.
-- Idempotent: safe to run repeatedly (python -m aurum.cli init-db).
-- If TimescaleDB is available, market_candles and market_ticks become hypertables.
-- Without TimescaleDB the same tables work as ordinary PostgreSQL tables (see README).
-- All timestamps are TIMESTAMPTZ and written in UTC.
-- =====================================================================

DO $$
BEGIN
    BEGIN
        CREATE EXTENSION IF NOT EXISTS timescaledb;
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'TimescaleDB not available (%). Using plain PostgreSQL tables.', SQLERRM;
    END;
END $$;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS instruments (
    id            SERIAL PRIMARY KEY,
    symbol        VARCHAR(32)  NOT NULL UNIQUE,          -- AURUM canonical symbol
    broker_symbol VARCHAR(64)  NOT NULL,                 -- exact broker MT5 name
    asset_class   VARCHAR(32)  NOT NULL DEFAULT 'unknown',
    description   TEXT,
    is_available  BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS instrument_specs (
    id                BIGSERIAL PRIMARY KEY,
    instrument_id     INTEGER     NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    captured_at       TIMESTAMPTZ NOT NULL,
    collection_run_id VARCHAR(36),
    broker_symbol     VARCHAR(64) NOT NULL,
    digits            INTEGER,
    point             DOUBLE PRECISION,
    contract_size     DOUBLE PRECISION,
    tick_size         DOUBLE PRECISION,
    tick_value        DOUBLE PRECISION,
    volume_min        DOUBLE PRECISION,
    volume_max        DOUBLE PRECISION,
    volume_step       DOUBLE PRECISION,
    spread_points     DOUBLE PRECISION,
    bid               DOUBLE PRECISION,
    ask               DOUBLE PRECISION,
    trade_mode        VARCHAR(16),
    trading_available BOOLEAN,
    currency_base     VARCHAR(16),
    currency_profit   VARCHAR(16),
    raw               JSONB,
    CONSTRAINT uq_instrument_specs_snapshot UNIQUE (instrument_id, captured_at)
);
CREATE INDEX IF NOT EXISTS ix_instrument_specs_instrument_time ON instrument_specs (instrument_id, captured_at);

CREATE TABLE IF NOT EXISTS market_candles (
    instrument_id     INTEGER     NOT NULL REFERENCES instruments(id),
    timeframe         VARCHAR(4)  NOT NULL,
    ts_utc            TIMESTAMPTZ NOT NULL,              -- candle open time, UTC
    open              DOUBLE PRECISION,                  -- nullable: invalid rows are retained, not dropped
    high              DOUBLE PRECISION,
    low               DOUBLE PRECISION,
    close             DOUBLE PRECISION,
    tick_volume       BIGINT,
    real_volume       BIGINT,
    spread            INTEGER,
    is_complete       BOOLEAN     NOT NULL DEFAULT TRUE,
    quality_status    VARCHAR(16) NOT NULL DEFAULT 'valid',   -- valid | suspect | invalid
    quality_flags     TEXT,
    broker_symbol     VARCHAR(64) NOT NULL,
    mt5_time          BIGINT      NOT NULL,              -- original MT5 server timestamp (traceability)
    source            VARCHAR(16) NOT NULL DEFAULT 'mt5',
    collection_run_id VARCHAR(36),
    ingested_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT market_candles_pkey PRIMARY KEY (instrument_id, timeframe, ts_utc)   -- prevents duplicates
);
CREATE INDEX IF NOT EXISTS ix_market_candles_tf_time ON market_candles (timeframe, ts_utc);
CREATE INDEX IF NOT EXISTS ix_market_candles_inst_tf_time_desc ON market_candles (instrument_id, timeframe, ts_utc DESC);

CREATE TABLE IF NOT EXISTS market_ticks (
    instrument_id     INTEGER     NOT NULL REFERENCES instruments(id),
    ts_utc            TIMESTAMPTZ NOT NULL,
    bid               DOUBLE PRECISION,
    ask               DOUBLE PRECISION,
    last              DOUBLE PRECISION,
    volume            DOUBLE PRECISION,
    spread_points     DOUBLE PRECISION,
    broker_symbol     VARCHAR(64) NOT NULL,
    mt5_time_msc      BIGINT      NOT NULL,
    collection_run_id VARCHAR(36),
    ingested_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT market_ticks_pkey PRIMARY KEY (instrument_id, ts_utc)
);
CREATE INDEX IF NOT EXISTS ix_market_ticks_time ON market_ticks (ts_utc DESC);

CREATE TABLE IF NOT EXISTS data_collection_logs (
    id            BIGSERIAL PRIMARY KEY,
    run_id        VARCHAR(36) NOT NULL,
    event         VARCHAR(32) NOT NULL,                  -- run | specs | ticks | candles
    status        VARCHAR(16) NOT NULL,                  -- success | partial | failed | skipped
    instrument_id INTEGER REFERENCES instruments(id),
    symbol        VARCHAR(32),
    timeframe     VARCHAR(4),
    started_at    TIMESTAMPTZ NOT NULL,
    finished_at   TIMESTAMPTZ,
    rows_received INTEGER,
    rows_stored   INTEGER,
    issues_found  INTEGER,
    message       TEXT,
    details       JSONB
);
CREATE INDEX IF NOT EXISTS ix_data_collection_logs_run_id ON data_collection_logs (run_id);
CREATE INDEX IF NOT EXISTS ix_collection_logs_started ON data_collection_logs (started_at);

CREATE TABLE IF NOT EXISTS data_quality_issues (
    id            BIGSERIAL PRIMARY KEY,
    fingerprint   VARCHAR(64) NOT NULL UNIQUE,           -- dedupes repeated detections
    instrument_id INTEGER REFERENCES instruments(id),
    symbol        VARCHAR(32),
    timeframe     VARCHAR(4),
    ts_utc        TIMESTAMPTZ,
    issue_type    VARCHAR(48) NOT NULL,
    severity      VARCHAR(16) NOT NULL,                  -- error | warning | info
    details       JSONB,
    first_run_id  VARCHAR(36),
    last_run_id   VARCHAR(36),
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    occurrences   INTEGER     NOT NULL DEFAULT 1,
    resolved      BOOLEAN     NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_quality_issues_lookup ON data_quality_issues (instrument_id, timeframe, ts_utc);
CREATE INDEX IF NOT EXISTS ix_quality_issues_type ON data_quality_issues (issue_type, severity);

-- Convenience view: candles fit for analysis (complete and not invalid).
CREATE OR REPLACE VIEW validated_candles AS
SELECT i.symbol, c.*
FROM market_candles c JOIN instruments i ON i.id = c.instrument_id
WHERE c.is_complete AND c.quality_status <> 'invalid';

-- TimescaleDB hypertables (only when the extension is installed).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
        PERFORM create_hypertable('market_candles', 'ts_utc', chunk_time_interval => INTERVAL '30 days',
                                  if_not_exists => TRUE, migrate_data => TRUE);
        PERFORM create_hypertable('market_ticks', 'ts_utc', chunk_time_interval => INTERVAL '7 days',
                                  if_not_exists => TRUE, migrate_data => TRUE);
    END IF;
END $$;

INSERT INTO schema_migrations (version) VALUES ('0001_initial') ON CONFLICT (version) DO NOTHING;
