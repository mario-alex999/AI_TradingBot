-- =====================================================================
-- AURUM AI - Module 02: COT Institutional Intelligence Engine.
-- Additive only: creates cot_* tables; no Module 01 table is altered.
-- Mirrors aurum/cot/models.py (SQLite/dev uses SQLAlchemy create_all).
-- Applied once by `python -m aurum.cli init-db` and recorded in schema_migrations.
-- =====================================================================

CREATE TABLE IF NOT EXISTS cot_datasets (
    id                SERIAL PRIMARY KEY,
    report_type       VARCHAR(16)  NOT NULL UNIQUE,          -- legacy | tff | disaggregated
    dataset_name      VARCHAR(128) NOT NULL,
    dataset_id        VARCHAR(32)  NOT NULL,                 -- CFTC Socrata dataset id
    dataset_id_status VARCHAR(16)  NOT NULL DEFAULT 'unverified',
    endpoint          TEXT         NOT NULL,
    report_scope      VARCHAR(32)  NOT NULL,
    history_start     DATE,
    description       TEXT,
    active            BOOLEAN      NOT NULL DEFAULT TRUE,
    last_verified_at  TIMESTAMPTZ,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cot_contract_mappings (
    id                   SERIAL PRIMARY KEY,
    aurum_symbol         VARCHAR(32)  NOT NULL,
    report_type          VARCHAR(16)  NOT NULL,
    cftc_contract_name   VARCHAR(160) NOT NULL,
    cftc_contract_code   VARCHAR(16)  NOT NULL,
    exchange             VARCHAR(64),
    contract_description TEXT,
    currency             VARCHAR(8),
    mapping_status       VARCHAR(16)  NOT NULL DEFAULT 'unverified',
    verification_source  TEXT,
    verified_at          DATE,
    primary_category     VARCHAR(32)  NOT NULL,
    active               BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_cot_mapping_symbol_type UNIQUE (aurum_symbol, report_type)
);
CREATE INDEX IF NOT EXISTS ix_cot_mapping_code ON cot_contract_mappings (report_type, cftc_contract_code);

CREATE TABLE IF NOT EXISTS cot_reports (
    id                   BIGSERIAL PRIMARY KEY,
    dataset_id           INTEGER      NOT NULL REFERENCES cot_datasets(id),
    contract_mapping_id  INTEGER      REFERENCES cot_contract_mappings(id),
    report_type          VARCHAR(16)  NOT NULL,
    contract_code        VARCHAR(16)  NOT NULL,
    contract_market_name VARCHAR(160),
    report_date          DATE         NOT NULL,              -- positions as of (Tuesday)
    release_date         DATE         NOT NULL,              -- CFTC public release day
    release_timestamp    TIMESTAMPTZ  NOT NULL,              -- first moment the data may be used
    release_method       VARCHAR(16)  NOT NULL,              -- override | rule | rule_holiday
    release_uncertain    BOOLEAN      NOT NULL DEFAULT FALSE,
    retrieval_timestamp  TIMESTAMPTZ  NOT NULL,
    processed_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
    source               VARCHAR(16)  NOT NULL,              -- socrata | files
    source_file          VARCHAR(64),
    source_record_id     VARCHAR(96),
    open_interest        BIGINT,
    source_payload       JSONB        NOT NULL,              -- verbatim CFTC record
    payload_hash         VARCHAR(64)  NOT NULL,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_cot_report_source UNIQUE (dataset_id, contract_code, report_date)
);
CREATE INDEX IF NOT EXISTS ix_cot_reports_mapping_date ON cot_reports (contract_mapping_id, report_date);
CREATE INDEX IF NOT EXISTS ix_cot_reports_release ON cot_reports (release_timestamp);

CREATE TABLE IF NOT EXISTS cot_report_revisions (
    id                           BIGSERIAL PRIMARY KEY,
    cot_report_id                BIGINT      NOT NULL REFERENCES cot_reports(id) ON DELETE CASCADE,
    previous_payload             JSONB       NOT NULL,
    previous_hash                VARCHAR(64) NOT NULL,
    new_hash                     VARCHAR(64) NOT NULL,
    previous_retrieval_timestamp TIMESTAMPTZ,
    detected_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cot_positions (
    id                  BIGSERIAL PRIMARY KEY,
    cot_report_id       BIGINT      NOT NULL REFERENCES cot_reports(id) ON DELETE CASCADE,
    trader_category     VARCHAR(32) NOT NULL,
    long_positions      BIGINT,
    short_positions     BIGINT,
    spreading_positions BIGINT,
    net_positions       BIGINT,
    is_reportable       BOOLEAN     NOT NULL,
    open_interest       BIGINT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_cot_position UNIQUE (cot_report_id, trader_category)
);

CREATE TABLE IF NOT EXISTS cot_metrics (
    id                        BIGSERIAL PRIMARY KEY,
    cot_report_id             BIGINT      NOT NULL REFERENCES cot_reports(id) ON DELETE CASCADE,
    trader_category           VARCHAR(32) NOT NULL,
    report_date               DATE        NOT NULL,
    release_timestamp         TIMESTAMPTZ NOT NULL,
    net_position              BIGINT,
    long_change               BIGINT,
    short_change              BIGINT,
    weekly_net_change         BIGINT,
    net_position_pct_oi       DOUBLE PRECISION,
    historical_percentile_12m DOUBLE PRECISION,
    historical_percentile_24m DOUBLE PRECISION,
    historical_percentile_36m DOUBLE PRECISION,
    historical_percentile_48m DOUBLE PRECISION,
    cot_index                 DOUBLE PRECISION,
    z_score                   DOUBLE PRECISION,
    history_weeks             INTEGER     NOT NULL,
    positioning_direction     VARCHAR(16),
    positioning_strength      VARCHAR(24),
    classification_score      DOUBLE PRECISION,
    change_type               VARCHAR(32),
    change_driver             VARCHAR(24),
    significant_change        BOOLEAN     NOT NULL DEFAULT FALSE,
    unusual_positioning       BOOLEAN     NOT NULL DEFAULT FALSE,
    calculation_version       VARCHAR(16) NOT NULL,
    calculation_timestamp     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_cot_metric UNIQUE (cot_report_id, trader_category)
);
CREATE INDEX IF NOT EXISTS ix_cot_metrics_category_date ON cot_metrics (trader_category, report_date);

CREATE TABLE IF NOT EXISTS cot_ingestion_logs (
    id                BIGSERIAL PRIMARY KEY,
    job_id            VARCHAR(36) NOT NULL,
    report_type       VARCHAR(16),
    job_type          VARCHAR(16) NOT NULL,                  -- backfill | update | calculate
    period_start      DATE,
    period_end        DATE,
    source            VARCHAR(16),
    started_at        TIMESTAMPTZ NOT NULL,
    finished_at       TIMESTAMPTZ,
    duration_seconds  DOUBLE PRECISION,
    records_retrieved INTEGER     NOT NULL DEFAULT 0,
    records_inserted  INTEGER     NOT NULL DEFAULT 0,
    records_updated   INTEGER     NOT NULL DEFAULT 0,
    records_unchanged INTEGER     NOT NULL DEFAULT 0,
    records_rejected  INTEGER     NOT NULL DEFAULT 0,
    status            VARCHAR(16) NOT NULL,                  -- running | success | partial | failed
    error_message     TEXT,
    details           JSONB
);
CREATE INDEX IF NOT EXISTS ix_cot_ingestion_logs_job_id ON cot_ingestion_logs (job_id);
CREATE INDEX IF NOT EXISTS ix_cot_ingestion_started ON cot_ingestion_logs (started_at);

CREATE TABLE IF NOT EXISTS cot_data_quality_issues (
    id            BIGSERIAL PRIMARY KEY,
    fingerprint   VARCHAR(64) NOT NULL UNIQUE,
    report_type   VARCHAR(16),
    contract_code VARCHAR(16),
    aurum_symbol  VARCHAR(32),
    report_date   DATE,
    issue_type    VARCHAR(48) NOT NULL,
    severity      VARCHAR(16) NOT NULL,                      -- info | warning | error | critical
    description   TEXT        NOT NULL,
    details       JSONB,
    detected_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    occurrences   INTEGER     NOT NULL DEFAULT 1,
    resolved      BOOLEAN     NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_cot_quality_lookup ON cot_data_quality_issues (aurum_symbol, report_type, report_date);
CREATE INDEX IF NOT EXISTS ix_cot_quality_severity ON cot_data_quality_issues (severity, resolved);
