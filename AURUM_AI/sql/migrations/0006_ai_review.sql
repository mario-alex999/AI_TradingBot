-- =====================================================================
-- AURUM AI - optional AI review: stored reviews and the monthly budget ledger.
-- Additive only: creates new tables; nothing existing is altered, dropped or deleted.
-- Mirrors aurum/ai_review/db_models.py (SQLite/dev uses SQLAlchemy create_all).
-- =====================================================================

CREATE TABLE IF NOT EXISTS ai_review_budget_months (
    month               VARCHAR(7) PRIMARY KEY,
    committed_microusd  BIGINT NOT NULL DEFAULT 0,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ai_reviews (
    id                 BIGSERIAL PRIMARY KEY,
    review_uid         VARCHAR(36) NOT NULL UNIQUE,
    budget_month       VARCHAR(7) NOT NULL,
    symbol             VARCHAR(32) NOT NULL,
    as_of              TIMESTAMPTZ NOT NULL,
    status             VARCHAR(16) NOT NULL,
    model              VARCHAR(64) NOT NULL,
    effort             VARCHAR(16) NOT NULL,
    context_chars      INTEGER NOT NULL,
    verdict            JSONB,
    error_code         VARCHAR(32),
    error_message      TEXT,
    input_tokens       INTEGER,
    output_tokens      INTEGER,
    reserved_microusd  BIGINT NOT NULL,
    cost_microusd      BIGINT NOT NULL,
    cost_is_estimate   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_ai_reviews_symbol_created ON ai_reviews (symbol, created_at);
CREATE INDEX IF NOT EXISTS ix_ai_reviews_month ON ai_reviews (budget_month);
