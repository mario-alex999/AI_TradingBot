-- =====================================================================
-- AURUM AI - Module 06: Advanced Risk Management Engine.
-- Additive only: creates new tables; nothing existing is altered, dropped or deleted.
-- Mirrors aurum/risk/db_models.py (SQLite/dev uses SQLAlchemy create_all).
-- Money and volumes are NUMERIC(24,8).
-- =====================================================================

CREATE TABLE IF NOT EXISTS risk_config_versions (
    id           BIGSERIAL PRIMARY KEY,
    version      INTEGER NOT NULL UNIQUE,
    profile      VARCHAR(16) NOT NULL,
    config       JSONB NOT NULL,
    fingerprint  VARCHAR(32) NOT NULL,
    created_by   VARCHAR(80) NOT NULL,
    reason       TEXT,
    weakened     JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS risk_assessments (
    id                    BIGSERIAL PRIMARY KEY,
    assessment_id         VARCHAR(36) NOT NULL UNIQUE,
    idempotency_key       VARCHAR(128) NOT NULL,
    attempt               INTEGER NOT NULL DEFAULT 1,
    trade_idea_id         VARCHAR(64),
    symbol                VARCHAR(32) NOT NULL,
    asset_class           VARCHAR(32) NOT NULL,
    direction             VARCHAR(4) NOT NULL,
    strategy              VARCHAR(64),
    decision              VARCHAR(8) NOT NULL CHECK (decision IN ('PASS', 'REVIEW', 'BLOCK', 'ERROR')),
    state                 VARCHAR(12) NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'consumed', 'cancelled')),
    requested_volume      NUMERIC(24, 8),
    recommended_volume    NUMERIC(24, 8),
    final_volume          NUMERIC(24, 8),
    risk_pct              NUMERIC(24, 8),
    monetary_risk         NUMERIC(24, 8),
    potential_reward      NUMERIC(24, 8),
    risk_reward           NUMERIC(24, 8),
    adjusted_risk_reward  NUMERIC(24, 8),
    reserved_risk         NUMERIC(24, 8),
    account_currency      VARCHAR(16),
    account_status        VARCHAR(16) NOT NULL,
    portfolio_status      VARCHAR(16) NOT NULL,
    correlation_status    VARCHAR(16) NOT NULL,
    market_status         VARCHAR(16) NOT NULL,
    config_version        INTEGER NOT NULL,
    engine_version        VARCHAR(16) NOT NULL,
    correlation_id        VARCHAR(64) NOT NULL,
    input_hash            VARCHAR(64) NOT NULL,
    input_snapshot        JSONB NOT NULL,
    result                JSONB NOT NULL,
    source                VARCHAR(32) NOT NULL,
    created_by            VARCHAR(80) NOT NULL,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at            TIMESTAMPTZ NOT NULL,
    state_changed_at      TIMESTAMPTZ,
    CONSTRAINT uq_risk_assessments_key_attempt UNIQUE (idempotency_key, attempt),
    CONSTRAINT ck_risk_assessments_volumes CHECK (final_volume IS NULL OR final_volume > 0)
);
CREATE INDEX IF NOT EXISTS ix_risk_assessments_created ON risk_assessments (created_at);
CREATE INDEX IF NOT EXISTS ix_risk_assessments_idea ON risk_assessments (trade_idea_id);
CREATE INDEX IF NOT EXISTS ix_risk_assessments_live ON risk_assessments (decision, state, expires_at);
CREATE INDEX IF NOT EXISTS ix_risk_assessments_symbol_created ON risk_assessments (symbol, created_at);

CREATE TABLE IF NOT EXISTS risk_rule_evaluations (
    id             BIGSERIAL PRIMARY KEY,
    assessment_id  VARCHAR(36) NOT NULL REFERENCES risk_assessments (assessment_id) ON DELETE CASCADE,
    rule_id        VARCHAR(8) NOT NULL,
    name           VARCHAR(80) NOT NULL,
    category       VARCHAR(16) NOT NULL,
    severity       VARCHAR(16) NOT NULL,
    outcome        VARCHAR(8) NOT NULL,
    reason_code    VARCHAR(48) NOT NULL,
    message        TEXT NOT NULL,
    evidence       JSONB,
    thresholds     JSONB
);
CREATE INDEX IF NOT EXISTS ix_risk_rule_evals_assessment ON risk_rule_evaluations (assessment_id);
CREATE INDEX IF NOT EXISTS ix_risk_rule_evals_rule_outcome ON risk_rule_evaluations (rule_id, outcome);

CREATE TABLE IF NOT EXISTS risk_account_snapshots (
    id                      BIGSERIAL PRIMARY KEY,
    assessment_id           VARCHAR(36),
    computed_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_captured_at      TIMESTAMPTZ,
    status                  VARCHAR(16) NOT NULL,
    currency                VARCHAR(16),
    balance                 NUMERIC(24, 8),
    equity                  NUMERIC(24, 8),
    drawdown_pct            NUMERIC(24, 8),
    daily_loss_pct          NUMERIC(24, 8),
    weekly_loss_pct         NUMERIC(24, 8),
    monthly_loss_pct        NUMERIC(24, 8),
    margin_utilization_pct  NUMERIC(24, 8),
    consecutive_losses      INTEGER,
    payload                 JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_risk_account_snapshots_time ON risk_account_snapshots (computed_at);

CREATE TABLE IF NOT EXISTS risk_portfolio_snapshots (
    id                   BIGSERIAL PRIMARY KEY,
    assessment_id        VARCHAR(36),
    computed_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    total_open_risk      NUMERIC(24, 8),
    total_open_risk_pct  NUMERIC(24, 8),
    positions            INTEGER NOT NULL,
    reservations         INTEGER NOT NULL,
    bounded              BOOLEAN NOT NULL,
    payload              JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_risk_portfolio_snapshots_time ON risk_portfolio_snapshots (computed_at);

CREATE TABLE IF NOT EXISTS risk_correlation_snapshots (
    id               BIGSERIAL PRIMARY KEY,
    computed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    symbol_a         VARCHAR(32) NOT NULL,
    symbol_b         VARCHAR(32) NOT NULL,
    timeframe        VARCHAR(4) NOT NULL,
    lookback_bars    INTEGER NOT NULL,
    samples          INTEGER NOT NULL,
    coefficient      NUMERIC(24, 8),
    last_common_bar  TIMESTAMPTZ,
    reliable         BOOLEAN NOT NULL,
    reason           TEXT
);
CREATE INDEX IF NOT EXISTS ix_risk_corr_pair_time ON risk_correlation_snapshots (symbol_a, symbol_b, computed_at);

CREATE TABLE IF NOT EXISTS risk_period_records (
    id                BIGSERIAL PRIMARY KEY,
    kind              VARCHAR(8) NOT NULL CHECK (kind IN ('day', 'week', 'month')),
    label             VARCHAR(16) NOT NULL,
    basis             VARCHAR(8) NOT NULL,
    start_at          TIMESTAMPTZ NOT NULL,
    end_at            TIMESTAMPTZ NOT NULL,
    start_value       NUMERIC(24, 8),
    start_source      TEXT NOT NULL,
    start_uncertain   BOOLEAN NOT NULL,
    last_loss_pct     NUMERIC(24, 8),
    limit_reached_at  TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_risk_period_records UNIQUE (kind, label, basis)
);

CREATE TABLE IF NOT EXISTS risk_locks (
    id              BIGSERIAL PRIMARY KEY,
    lock_id         VARCHAR(36) NOT NULL UNIQUE,
    dedupe_key      VARCHAR(160) UNIQUE,
    scope           VARCHAR(16) NOT NULL CHECK (scope IN ('global', 'account', 'instrument', 'strategy')),
    scope_value     VARCHAR(64),
    reason          TEXT NOT NULL,
    trigger_source  VARCHAR(48) NOT NULL,
    rule_id         VARCHAR(8),
    status          VARCHAR(10) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'released', 'expired')),
    activated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    activated_by    VARCHAR(80) NOT NULL,
    expires_at      TIMESTAMPTZ,
    released_at     TIMESTAMPTZ,
    released_by     VARCHAR(80),
    release_reason  TEXT,
    details         JSONB
);
CREATE INDEX IF NOT EXISTS ix_risk_locks_status ON risk_locks (status, activated_at);

CREATE TABLE IF NOT EXISTS risk_lock_history (
    id       BIGSERIAL PRIMARY KEY,
    lock_id  VARCHAR(36) NOT NULL REFERENCES risk_locks (lock_id) ON DELETE CASCADE,
    action   VARCHAR(16) NOT NULL,
    at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor    VARCHAR(80) NOT NULL,
    reason   TEXT,
    details  JSONB
);
CREATE INDEX IF NOT EXISTS ix_risk_lock_history_lock ON risk_lock_history (lock_id, at);

CREATE TABLE IF NOT EXISTS risk_loss_streaks (
    id           BIGSERIAL PRIMARY KEY,
    detected_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    streak       INTEGER NOT NULL,
    trade_ids    JSONB NOT NULL,
    lock_id      VARCHAR(36),
    fingerprint  VARCHAR(64) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS risk_audit_log (
    id              BIGSERIAL PRIMARY KEY,
    at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor           VARCHAR(80) NOT NULL,
    action          VARCHAR(48) NOT NULL,
    entity_type     VARCHAR(32) NOT NULL,
    entity_id       VARCHAR(64),
    correlation_id  VARCHAR(64),
    details         JSONB,
    prev_hash       VARCHAR(64) NOT NULL,
    hash            VARCHAR(64) NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_risk_audit_at ON risk_audit_log (at);
CREATE INDEX IF NOT EXISTS ix_risk_audit_entity ON risk_audit_log (entity_type, entity_id);

-- The audit log and the rule evaluations are append-only in the database itself, not only in application code.
CREATE OR REPLACE FUNCTION risk_reject_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_risk_audit_append_only ON risk_audit_log;
CREATE TRIGGER trg_risk_audit_append_only BEFORE UPDATE OR DELETE ON risk_audit_log
    FOR EACH ROW EXECUTE FUNCTION risk_reject_change();
DROP TRIGGER IF EXISTS trg_risk_rule_evals_append_only ON risk_rule_evaluations;
CREATE TRIGGER trg_risk_rule_evals_append_only BEFORE UPDATE ON risk_rule_evaluations
    FOR EACH ROW EXECUTE FUNCTION risk_reject_change();
DROP TRIGGER IF EXISTS trg_risk_config_append_only ON risk_config_versions;
CREATE TRIGGER trg_risk_config_append_only BEFORE UPDATE OR DELETE ON risk_config_versions
    FOR EACH ROW EXECUTE FUNCTION risk_reject_change();

CREATE TABLE IF NOT EXISTS risk_events (
    id               BIGSERIAL PRIMARY KEY,
    event_id         VARCHAR(36) NOT NULL UNIQUE,
    event_type       VARCHAR(48) NOT NULL,
    schema_version   INTEGER NOT NULL DEFAULT 1,
    occurred_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    correlation_id   VARCHAR(64),
    idempotency_key  VARCHAR(160) NOT NULL UNIQUE,
    entity_id        VARCHAR(64),
    source           VARCHAR(32) NOT NULL,
    payload          JSONB NOT NULL,
    status           VARCHAR(10) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'delivered', 'dead')),
    attempts         INTEGER NOT NULL DEFAULT 0,
    next_attempt_at  TIMESTAMPTZ,
    last_error       TEXT,
    delivered_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_risk_events_status ON risk_events (status, next_attempt_at);

CREATE TABLE IF NOT EXISTS risk_inbox (
    id           BIGSERIAL PRIMARY KEY,
    event_id     VARCHAR(64) NOT NULL UNIQUE,
    event_type   VARCHAR(48) NOT NULL,
    received_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    status       VARCHAR(10) NOT NULL CHECK (status IN ('processed', 'ignored', 'failed', 'dead')),
    attempts     INTEGER NOT NULL DEFAULT 1,
    last_error   TEXT,
    payload      JSONB NOT NULL,
    result       JSONB
);
