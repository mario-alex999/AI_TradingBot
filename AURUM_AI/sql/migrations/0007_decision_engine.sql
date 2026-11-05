-- =====================================================================
-- AURUM AI - Module 05: decision engine (trade candidates for human review).
-- Additive only: creates new tables; nothing existing is altered, dropped or deleted.
-- Mirrors aurum/decision/db_models.py (SQLite/dev uses SQLAlchemy create_all).
-- =====================================================================

CREATE TABLE IF NOT EXISTS decision_runs (
    id                  BIGSERIAL PRIMARY KEY,
    run_uid             VARCHAR(36) NOT NULL UNIQUE,
    symbol              VARCHAR(32) NOT NULL,
    as_of               TIMESTAMPTZ NOT NULL,
    mode                VARCHAR(16) NOT NULL,
    engine_version      VARCHAR(16) NOT NULL,
    config_fingerprint  VARCHAR(32) NOT NULL,
    input_hash          VARCHAR(64),
    availability        JSONB,
    summary             JSONB,
    duration_ms         INTEGER,
    status              VARCHAR(16) NOT NULL,
    error               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_decision_runs_symbol_asof ON decision_runs (symbol, as_of);

CREATE TABLE IF NOT EXISTS trade_candidates (
    id                   BIGSERIAL PRIMARY KEY,
    candidate_uid        VARCHAR(36) NOT NULL UNIQUE,
    idempotent_key       VARCHAR(64) NOT NULL,
    symbol               VARCHAR(32) NOT NULL,
    timeframe            VARCHAR(4) NOT NULL,
    template             VARCHAR(48) NOT NULL,
    direction            VARCHAR(8) NOT NULL,
    as_of                TIMESTAMPTZ NOT NULL,
    entry_low            DOUBLE PRECISION NOT NULL,
    entry_high           DOUBLE PRECISION NOT NULL,
    stop_loss            DOUBLE PRECISION NOT NULL,
    take_profit          DOUBLE PRECISION NOT NULL,
    take_profit2         DOUBLE PRECISION,
    risk_reward          DOUBLE PRECISION NOT NULL,
    suggested_volume     DOUBLE PRECISION,
    volume_reason        TEXT,
    deterministic_score  DOUBLE PRECISION NOT NULL,
    confidence           DOUBLE PRECISION NOT NULL,
    grade                VARCHAR(1) NOT NULL,
    status               VARCHAR(16) NOT NULL,
    rejected_by          VARCHAR(48),
    expires_at           TIMESTAMPTZ NOT NULL,
    invalidation         TEXT NOT NULL,
    market_condition     VARCHAR(64) NOT NULL,
    first_seen_run       VARCHAR(36) NOT NULL,
    last_seen_run        VARCHAR(36) NOT NULL,
    input_hash           VARCHAR(64) NOT NULL,
    availability         JSONB,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_trade_candidates_key UNIQUE (idempotent_key)
);
CREATE INDEX IF NOT EXISTS ix_trade_candidates_symbol_status_created ON trade_candidates (symbol, status, created_at);

CREATE TABLE IF NOT EXISTS candidate_evidence (
    id             BIGSERIAL PRIMARY KEY,
    candidate_uid  VARCHAR(36) NOT NULL REFERENCES trade_candidates (candidate_uid) ON DELETE CASCADE,
    component      VARCHAR(48) NOT NULL,
    points         DOUBLE PRECISION NOT NULL,
    max_points     DOUBLE PRECISION NOT NULL,
    sentence       TEXT NOT NULL,
    sources        JSONB
);
CREATE INDEX IF NOT EXISTS ix_candidate_evidence_candidate_uid ON candidate_evidence (candidate_uid);

CREATE TABLE IF NOT EXISTS candidate_gate_results (
    id             BIGSERIAL PRIMARY KEY,
    candidate_uid  VARCHAR(36) NOT NULL REFERENCES trade_candidates (candidate_uid) ON DELETE CASCADE,
    gate           VARCHAR(32) NOT NULL,
    result         VARCHAR(8) NOT NULL,
    evidence       JSONB,
    thresholds     JSONB,
    checked_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_candidate_gate_results_candidate_uid ON candidate_gate_results (candidate_uid);

CREATE TABLE IF NOT EXISTS candidate_ai_reviews (
    id              BIGSERIAL PRIMARY KEY,
    candidate_uid   VARCHAR(36) NOT NULL REFERENCES trade_candidates (candidate_uid) ON DELETE CASCADE,
    input_hash      VARCHAR(64) NOT NULL,
    prompt_version  VARCHAR(16) NOT NULL,
    model           VARCHAR(64),
    status          VARCHAR(16) NOT NULL,
    reason          VARCHAR(32),
    verdict         VARCHAR(16),
    adjustment      DOUBLE PRECISION,
    review          JSONB,
    payload_hash    VARCHAR(64),
    budget_month    VARCHAR(7),
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    latency_ms      INTEGER,
    cost_microusd   BIGINT NOT NULL DEFAULT 0,
    api_called      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_candidate_ai_reviews_lookup ON candidate_ai_reviews (candidate_uid, input_hash, prompt_version);
CREATE INDEX IF NOT EXISTS ix_candidate_ai_reviews_created ON candidate_ai_reviews (created_at);

CREATE TABLE IF NOT EXISTS candidate_status_history (
    id             BIGSERIAL PRIMARY KEY,
    candidate_uid  VARCHAR(36) NOT NULL REFERENCES trade_candidates (candidate_uid) ON DELETE CASCADE,
    from_status    VARCHAR(16),
    to_status      VARCHAR(16) NOT NULL,
    actor          VARCHAR(80) NOT NULL,
    note           TEXT,
    at             TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_candidate_status_history_candidate_uid ON candidate_status_history (candidate_uid);

CREATE TABLE IF NOT EXISTS candidate_outcomes (
    candidate_uid  VARCHAR(36) PRIMARY KEY REFERENCES trade_candidates (candidate_uid) ON DELETE CASCADE,
    touched        BOOLEAN NOT NULL DEFAULT FALSE,
    filled         BOOLEAN NOT NULL DEFAULT FALSE,
    result         VARCHAR(16) NOT NULL,
    r_multiple     DOUBLE PRECISION,
    mfe_r          DOUBLE PRECISION,
    mae_r          DOUBLE PRECISION,
    resolved_at    TIMESTAMPTZ,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS backtest_runs (
    id                  BIGSERIAL PRIMARY KEY,
    run_uid             VARCHAR(36) NOT NULL UNIQUE,
    config_fingerprint  VARCHAR(32) NOT NULL,
    symbols             JSONB NOT NULL,
    period_start        TIMESTAMPTZ NOT NULL,
    period_end          TIMESTAMPTZ NOT NULL,
    split_date          TIMESTAMPTZ,
    metrics             JSONB NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS backtest_trades (
    id           BIGSERIAL PRIMARY KEY,
    run_uid      VARCHAR(36) NOT NULL REFERENCES backtest_runs (run_uid) ON DELETE CASCADE,
    symbol       VARCHAR(32) NOT NULL,
    template     VARCHAR(48) NOT NULL,
    timeframe    VARCHAR(4) NOT NULL,
    direction    VARCHAR(8) NOT NULL,
    as_of        TIMESTAMPTZ NOT NULL,
    session      VARCHAR(32),
    confidence   DOUBLE PRECISION NOT NULL,
    result       VARCHAR(16) NOT NULL,
    r_multiple   DOUBLE PRECISION NOT NULL,
    resolved_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_backtest_trades_run_uid ON backtest_trades (run_uid);
