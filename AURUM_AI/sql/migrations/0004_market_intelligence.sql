-- =====================================================================
-- AURUM AI - Module 04: Trading Sessions and Market Intelligence Engine.
-- Additive only: creates new tables; no Module 01, 02 or 03 table is altered, and nothing is dropped or deleted.
-- Mirrors aurum/market_intelligence/db_models.py (SQLite/dev uses SQLAlchemy create_all).
-- Applied once by `python -m aurum.cli init-db` and recorded in schema_migrations.
-- =====================================================================

CREATE TABLE IF NOT EXISTS market_intelligence_runs (
    id                  BIGSERIAL PRIMARY KEY,
    run_uid             VARCHAR(36) NOT NULL UNIQUE,
    run_type            VARCHAR(24) NOT NULL,                 -- analysis | calendar | macro | news
    symbol              VARCHAR(32),
    as_of               TIMESTAMPTZ NOT NULL,
    generated_at        TIMESTAMPTZ NOT NULL,
    module_version      VARCHAR(16) NOT NULL,
    methodology_version VARCHAR(16) NOT NULL,
    config_fingerprint  VARCHAR(32) NOT NULL,
    status              VARCHAR(24) NOT NULL,
    summary             JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_mi_runs_symbol_asof ON market_intelligence_runs (symbol, as_of);

CREATE TABLE IF NOT EXISTS market_session_definitions (
    id                 SERIAL PRIMARY KEY,
    name               VARCHAR(32) NOT NULL,
    kind               VARCHAR(16) NOT NULL,                  -- session | kill_zone
    timezone           VARCHAR(64) NOT NULL,                  -- IANA zone; local times below follow its DST rules
    local_open         VARCHAR(5)  NOT NULL,
    local_close        VARCHAR(5)  NOT NULL,
    weekdays           VARCHAR(32) NOT NULL,
    holidays           JSONB,
    pre_minutes        INTEGER     NOT NULL DEFAULT 0,
    post_minutes       INTEGER     NOT NULL DEFAULT 0,
    config_fingerprint VARCHAR(32) NOT NULL,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_market_session_definitions UNIQUE (name, kind)
);

CREATE TABLE IF NOT EXISTS market_sessions (
    id          BIGSERIAL PRIMARY KEY,
    name        VARCHAR(32) NOT NULL,
    kind        VARCHAR(16) NOT NULL,                         -- session | kill_zone | overlap | transition
    start_utc   TIMESTAMPTZ NOT NULL,
    end_utc     TIMESTAMPTZ NOT NULL,
    local_date  VARCHAR(10),
    timezone    VARCHAR(64),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_market_sessions UNIQUE (name, kind, start_utc),
    CONSTRAINT ck_market_sessions_order CHECK (end_utc > start_utc)
);
CREATE INDEX IF NOT EXISTS ix_market_sessions_start ON market_sessions (start_utc);

CREATE TABLE IF NOT EXISTS market_session_levels (
    id                 BIGSERIAL PRIMARY KEY,
    symbol             VARCHAR(32) NOT NULL,
    level_type         VARCHAR(40) NOT NULL,                  -- previous_asian_high, previous_day_low, ...
    side               VARCHAR(4)  NOT NULL,
    price              DOUBLE PRECISION NOT NULL,
    period_kind        VARCHAR(16) NOT NULL,
    period_label       VARCHAR(32),
    period_start       TIMESTAMPTZ NOT NULL,
    period_end         TIMESTAMPTZ NOT NULL,
    formed_at          TIMESTAMPTZ NOT NULL,                  -- period end (completed) / last close (provisional)
    source_candle_time TIMESTAMPTZ NOT NULL,
    timeframe          VARCHAR(4)  NOT NULL,
    method             TEXT        NOT NULL,
    provisional        BOOLEAN     NOT NULL,
    state              VARCHAR(16) NOT NULL,                  -- untouched | swept | broken | provisional
    swept_at           TIMESTAMPTZ,
    broken_at          TIMESTAMPTZ,
    candles            INTEGER,
    missing_candles    INTEGER,
    detail             JSONB,
    first_run_uid      VARCHAR(36) NOT NULL REFERENCES market_intelligence_runs (run_uid),
    last_run_uid       VARCHAR(36) NOT NULL REFERENCES market_intelligence_runs (run_uid),
    last_as_of         TIMESTAMPTZ NOT NULL,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_market_session_levels UNIQUE (symbol, level_type, period_start)
);
CREATE INDEX IF NOT EXISTS ix_market_session_levels_lookup ON market_session_levels (symbol, period_end);

CREATE TABLE IF NOT EXISTS market_session_statistics (
    id          BIGSERIAL PRIMARY KEY,
    run_uid     VARCHAR(36) NOT NULL REFERENCES market_intelligence_runs (run_uid),
    symbol      VARCHAR(32) NOT NULL,
    session     VARCHAR(32) NOT NULL,
    "window"    INTEGER     NOT NULL,
    as_of       TIMESTAMPTZ NOT NULL,
    sample_size INTEGER     NOT NULL,
    sufficient  BOOLEAN     NOT NULL,
    metrics     JSONB,
    CONSTRAINT uq_market_session_statistics UNIQUE (symbol, session, "window", as_of)
);

CREATE TABLE IF NOT EXISTS market_session_liquidity_events (
    id                 BIGSERIAL PRIMARY KEY,
    symbol             VARCHAR(32) NOT NULL,
    session            VARCHAR(32),
    level_type         VARCHAR(40) NOT NULL,
    level              DOUBLE PRECISION,
    event_type         VARCHAR(32) NOT NULL,   -- wick_sweep | confirmed_breakout | false_breakout_candidate | unconfirmed | range_expansion
    direction          VARCHAR(16),
    event_time         TIMESTAMPTZ NOT NULL,
    confirmation_time  TIMESTAMPTZ,
    source_candle_time TIMESTAMPTZ NOT NULL,
    timeframe          VARCHAR(4)  NOT NULL,
    closed_beyond      BOOLEAN,
    returned_inside    BOOLEAN,
    returned_at        TIMESTAMPTZ,
    related_structure  JSONB,
    detail             JSONB,
    first_run_uid      VARCHAR(36) NOT NULL REFERENCES market_intelligence_runs (run_uid),
    last_run_uid       VARCHAR(36) NOT NULL REFERENCES market_intelligence_runs (run_uid),
    last_as_of         TIMESTAMPTZ NOT NULL,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_market_session_liquidity_events UNIQUE (symbol, session, level_type, event_time)
);
CREATE INDEX IF NOT EXISTS ix_market_liquidity_lookup ON market_session_liquidity_events (symbol, event_time);

CREATE TABLE IF NOT EXISTS economic_calendar_sources (
    id               SERIAL PRIMARY KEY,
    provider         VARCHAR(32) NOT NULL UNIQUE,
    full_calendar    BOOLEAN     NOT NULL DEFAULT FALSE,
    requires_key     BOOLEAN     NOT NULL DEFAULT FALSE,
    status           VARCHAR(24) NOT NULL,                     -- ok | error | not_configured
    last_attempt_at  TIMESTAMPTZ,
    last_success_at  TIMESTAMPTZ,
    last_error       TEXT,
    last_event_count INTEGER,
    window_start     DATE,
    window_end       DATE,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS economic_calendar_events (
    id                    BIGSERIAL PRIMARY KEY,
    provider              VARCHAR(32) NOT NULL,
    provider_event_id     VARCHAR(96) NOT NULL,
    name                  TEXT        NOT NULL,               -- original provider event name, never rewritten
    country               VARCHAR(64),
    currency              VARCHAR(8),
    category              VARCHAR(32) NOT NULL,
    subcategory           VARCHAR(48),
    importance            VARCHAR(8),                         -- low | medium | high | NULL (unrated)
    importance_source     VARCHAR(48),
    scheduled_at          TIMESTAMPTZ NOT NULL,               -- current scheduled release time (UTC)
    original_scheduled_at TIMESTAMPTZ NOT NULL,               -- first time seen; changes kept in the revisions table
    source_timezone       VARCHAR(64),
    time_precision        VARCHAR(16) NOT NULL,               -- exact | typical | date_only
    "values"              JSONB,                              -- actual / consensus / previous / revised_previous (NULL = missing, never 0)
    unit                  VARCHAR(32),
    status                VARCHAR(16) NOT NULL,
    source_url            TEXT,
    provider_updated_at   TIMESTAMPTZ,
    first_retrieved_at    TIMESTAMPTZ NOT NULL,
    retrieved_at          TIMESTAMPTZ NOT NULL,
    actual_first_seen_at  TIMESTAMPTZ,
    affected_instruments  JSONB,
    quality_status        VARCHAR(16) NOT NULL DEFAULT 'valid',
    raw                   JSONB,                              -- original provider payload (audit)
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_economic_calendar_events UNIQUE (provider, provider_event_id)
);
CREATE INDEX IF NOT EXISTS ix_calendar_events_time ON economic_calendar_events (scheduled_at);
CREATE INDEX IF NOT EXISTS ix_calendar_events_currency ON economic_calendar_events (currency, importance);

CREATE TABLE IF NOT EXISTS economic_calendar_event_revisions (
    id                BIGSERIAL PRIMARY KEY,
    event_id          BIGINT      NOT NULL REFERENCES economic_calendar_events (id) ON DELETE CASCADE,
    observed_at       TIMESTAMPTZ NOT NULL,                   -- when AURUM saw this version
    change_type       VARCHAR(24) NOT NULL,                   -- created | rescheduled | values | status
    changes           JSONB,
    snapshot          JSONB       NOT NULL,
    ingestion_run_uid VARCHAR(36)
);
CREATE INDEX IF NOT EXISTS ix_calendar_revisions_event ON economic_calendar_event_revisions (event_id, observed_at);

CREATE TABLE IF NOT EXISTS economic_calendar_ingestion_logs (
    id           BIGSERIAL PRIMARY KEY,
    run_uid      VARCHAR(36) NOT NULL,
    provider     VARCHAR(32) NOT NULL,
    started_at   TIMESTAMPTZ NOT NULL,
    finished_at  TIMESTAMPTZ,
    status       VARCHAR(16) NOT NULL,
    window_start DATE,
    window_end   DATE,
    received     INTEGER,
    inserted     INTEGER,
    updated      INTEGER,
    unchanged    INTEGER,
    invalid      INTEGER,
    error        TEXT,
    details      JSONB
);
CREATE INDEX IF NOT EXISTS ix_economic_calendar_ingestion_logs_run_uid ON economic_calendar_ingestion_logs (run_uid);

CREATE TABLE IF NOT EXISTS macro_indicators (
    id           BIGSERIAL PRIMARY KEY,
    series_id    VARCHAR(48) NOT NULL,
    source       VARCHAR(32) NOT NULL,
    name         TEXT        NOT NULL,
    category     VARCHAR(32),
    currency     VARCHAR(8),
    unit         VARCHAR(48),
    period       VARCHAR(16) NOT NULL,
    value        DOUBLE PRECISION NOT NULL,
    vintage      VARCHAR(32) NOT NULL,          -- FRED realtime_start, or first-retrieval time for sources without vintages
    known_from   TIMESTAMPTZ NOT NULL,          -- earliest time an analysis may use this value
    retrieved_at TIMESTAMPTZ NOT NULL,
    source_url   TEXT,
    CONSTRAINT uq_macro_indicators UNIQUE (series_id, period, vintage)
);
CREATE INDEX IF NOT EXISTS ix_macro_indicators_known ON macro_indicators (series_id, known_from);

CREATE TABLE IF NOT EXISTS central_bank_rates (
    id             BIGSERIAL PRIMARY KEY,
    bank           VARCHAR(16) NOT NULL,
    effective_date DATE        NOT NULL,
    rate           DOUBLE PRECISION NOT NULL,                 -- policy rate (upper bound for a target range)
    rate_lower     DOUBLE PRECISION,
    source         VARCHAR(32) NOT NULL,
    source_url     TEXT,
    known_from     TIMESTAMPTZ NOT NULL,
    retrieved_at   TIMESTAMPTZ NOT NULL,
    detail         JSONB,
    CONSTRAINT uq_central_bank_rates UNIQUE (bank, effective_date)
);

CREATE TABLE IF NOT EXISTS central_bank_decisions (
    id              BIGSERIAL PRIMARY KEY,
    bank            VARCHAR(16) NOT NULL,
    decision_date   DATE        NOT NULL,
    effective_date  DATE,
    status          VARCHAR(16) NOT NULL,                     -- changed | held
    previous_rate   DOUBLE PRECISION,
    new_rate        DOUBLE PRECISION,
    change_bps      INTEGER,
    known_from      TIMESTAMPTZ NOT NULL,
    matched_meeting BOOLEAN     NOT NULL DEFAULT FALSE,
    source          VARCHAR(32) NOT NULL,
    source_url      TEXT,
    retrieved_at    TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_central_bank_decisions UNIQUE (bank, decision_date)
);

CREATE TABLE IF NOT EXISTS market_news (
    id           BIGSERIAL PRIMARY KEY,
    item_uid     VARCHAR(32) NOT NULL UNIQUE,
    provider     VARCHAR(32) NOT NULL,
    source       VARCHAR(64) NOT NULL,
    headline     TEXT        NOT NULL,                        -- exactly as published
    url          TEXT        NOT NULL,
    published_at TIMESTAMPTZ,
    retrieved_at TIMESTAMPTZ NOT NULL,
    category     VARCHAR(32),
    source_type  VARCHAR(32) NOT NULL,                        -- official_announcement | reported_news | commentary | unverified
    currencies   JSONB,
    reliability  TEXT,
    description  TEXT
);
CREATE INDEX IF NOT EXISTS ix_market_news_published ON market_news (published_at);

CREATE TABLE IF NOT EXISTS market_volatility_metrics (
    id               BIGSERIAL PRIMARY KEY,
    run_uid          VARCHAR(36) NOT NULL REFERENCES market_intelligence_runs (run_uid),
    symbol           VARCHAR(32) NOT NULL,
    timeframe        VARCHAR(4)  NOT NULL,
    as_of            TIMESTAMPTZ NOT NULL,
    regime           VARCHAR(16) NOT NULL,
    atr              DOUBLE PRECISION,
    atr_percentile   DOUBLE PRECISION,
    realised_vol_pct DOUBLE PRECISION,
    metrics          JSONB,
    CONSTRAINT uq_market_volatility_metrics UNIQUE (symbol, timeframe, as_of)
);

CREATE TABLE IF NOT EXISTS market_condition_snapshots (
    id         BIGSERIAL PRIMARY KEY,
    run_uid    VARCHAR(36) NOT NULL REFERENCES market_intelligence_runs (run_uid),
    symbol     VARCHAR(32) NOT NULL,
    as_of      TIMESTAMPTZ NOT NULL,
    tags       JSONB,
    conditions JSONB,
    summary    JSONB,
    CONSTRAINT uq_market_condition_snapshots UNIQUE (symbol, as_of)
);

CREATE TABLE IF NOT EXISTS market_intelligence_scores (
    id                  BIGSERIAL PRIMARY KEY,
    run_uid             VARCHAR(36) NOT NULL REFERENCES market_intelligence_runs (run_uid),
    symbol              VARCHAR(32) NOT NULL,
    as_of               TIMESTAMPTZ NOT NULL,
    score_type          VARCHAR(32) NOT NULL,                 -- event_risk | session_activity | volatility
    score               DOUBLE PRECISION,                     -- NULL = could not be scored (e.g. UNKNOWN coverage)
    methodology_version VARCHAR(16) NOT NULL,
    components          JSONB,
    CONSTRAINT uq_market_intelligence_scores UNIQUE (symbol, as_of, score_type)
);

CREATE TABLE IF NOT EXISTS market_intelligence_quality_issues (
    id            BIGSERIAL PRIMARY KEY,
    fingerprint   VARCHAR(64) NOT NULL UNIQUE,
    run_uid       VARCHAR(36),
    symbol        VARCHAR(32),
    component     VARCHAR(32) NOT NULL,
    issue_type    VARCHAR(48) NOT NULL,
    severity      VARCHAR(16) NOT NULL,
    message       TEXT,
    details       JSONB,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    occurrences   INTEGER     NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_mi_quality_component ON market_intelligence_quality_issues (component, severity);
