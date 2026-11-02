-- =====================================================================
-- AURUM AI - Module 03: Technical Analysis and SMC Engine.
-- Additive only: creates ta_* tables; no Module 01 or Module 02 table is altered.
-- Mirrors aurum/analysis/services/db_models.py (SQLite/dev uses SQLAlchemy create_all).
-- Applied once by `python -m aurum.cli init-db` and recorded in schema_migrations.
-- =====================================================================

CREATE TABLE IF NOT EXISTS ta_analysis_runs (
    id                 BIGSERIAL PRIMARY KEY,
    run_uid            VARCHAR(36) NOT NULL UNIQUE,
    run_type           VARCHAR(16) NOT NULL,                 -- timeframe | confluence
    symbol             VARCHAR(32) NOT NULL,
    timeframe          VARCHAR(32),                          -- "D1,H4,..." for confluence runs
    as_of              TIMESTAMPTZ NOT NULL,                 -- close time of the last candle used
    generated_at       TIMESTAMPTZ NOT NULL,
    engine_version     VARCHAR(16) NOT NULL,
    config_fingerprint VARCHAR(32) NOT NULL,
    provisional        BOOLEAN     NOT NULL DEFAULT FALSE,
    candles_used       INTEGER,
    status             VARCHAR(24) NOT NULL,
    bias               VARCHAR(16),
    score              DOUBLE PRECISION,
    grade              VARCHAR(8),
    summary            JSONB,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_ta_runs_symbol_tf_asof ON ta_analysis_runs (symbol, timeframe, as_of);

CREATE TABLE IF NOT EXISTS ta_structure_events (
    id                    BIGSERIAL PRIMARY KEY,
    symbol                VARCHAR(32) NOT NULL,
    timeframe             VARCHAR(4)  NOT NULL,
    scope                 VARCHAR(16) NOT NULL,              -- internal | external
    event_type            VARCHAR(8)  NOT NULL,              -- BOS | CHOCH
    direction             VARCHAR(8)  NOT NULL,
    broken_level          DOUBLE PRECISION NOT NULL,
    broken_swing_time     TIMESTAMPTZ NOT NULL,
    break_candle_time     TIMESTAMPTZ NOT NULL,
    confirmed_at          TIMESTAMPTZ NOT NULL,
    confirmed             BOOLEAN     NOT NULL,
    displacement_strength DOUBLE PRECISION NOT NULL DEFAULT 0,
    detail                JSONB,
    first_run_uid         VARCHAR(36) NOT NULL,
    last_run_uid          VARCHAR(36) NOT NULL,
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_ta_structure_events UNIQUE (symbol, timeframe, scope, event_type, direction, break_candle_time)
);
CREATE INDEX IF NOT EXISTS ix_ta_events_lookup ON ta_structure_events (symbol, timeframe, confirmed_at);

CREATE TABLE IF NOT EXISTS ta_zones (
    id            BIGSERIAL PRIMARY KEY,
    zone_uid      VARCHAR(96) NOT NULL,
    symbol        VARCHAR(32) NOT NULL,
    timeframe     VARCHAR(4)  NOT NULL,
    kind          VARCHAR(32) NOT NULL,  -- order_block | fair_value_gap | balanced_price_range | liquidity_pool | support_resistance
    direction     VARCHAR(16),
    top           DOUBLE PRECISION NOT NULL,
    bottom        DOUBLE PRECISION NOT NULL,
    state         VARCHAR(24) NOT NULL,
    detected_at   TIMESTAMPTZ NOT NULL,
    detail        JSONB,
    first_run_uid VARCHAR(36) NOT NULL,
    last_run_uid  VARCHAR(36) NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_ta_zones UNIQUE (symbol, timeframe, kind, zone_uid)
);
CREATE INDEX IF NOT EXISTS ix_ta_zones_lookup ON ta_zones (symbol, timeframe, kind, state);
