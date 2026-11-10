-- =====================================================================
-- AURUM AI - Module 08: Workflow Orchestration and Integration Core.
-- Additive only: creates new orch_* tables; nothing existing is altered, dropped or deleted.
-- Generated from aurum/orchestration/db_models.py (SQLite/dev uses create_all), then the database-level
-- guarantees below are added by hand:
--   * execution status changes must follow the state machine and bump the row version (trigger);
--   * the orchestration audit log and configuration versions are append-only;
--   * workflow version schemas and n8n references never change after creation (trigger).
-- Safe to run more than once.
-- =====================================================================

CREATE TABLE IF NOT EXISTS orch_audit_logs (
	seq BIGSERIAL NOT NULL, 
	at TIMESTAMP WITH TIME ZONE NOT NULL, 
	actor VARCHAR(80) NOT NULL, 
	actor_kind VARCHAR(8) NOT NULL, 
	action VARCHAR(48) NOT NULL, 
	entity_type VARCHAR(24) NOT NULL, 
	entity_id VARCHAR(64) NOT NULL, 
	outcome VARCHAR(12) NOT NULL, 
	correlation_id VARCHAR(64), 
	details JSONB, 
	prev_hash VARCHAR(64) NOT NULL, 
	hash VARCHAR(64) NOT NULL, 
	PRIMARY KEY (seq)
);

CREATE INDEX IF NOT EXISTS ix_orch_audit_logs_action ON orch_audit_logs (action);

CREATE INDEX IF NOT EXISTS ix_orch_audit_logs_at ON orch_audit_logs (at);

CREATE TABLE IF NOT EXISTS orch_config_versions (
	version INTEGER NOT NULL, 
	config JSONB NOT NULL, 
	config_hash VARCHAR(64) NOT NULL, 
	reason TEXT NOT NULL, 
	created_by VARCHAR(80) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (version)
);

CREATE TABLE IF NOT EXISTS orch_credentials (
	name VARCHAR(48) NOT NULL, 
	source VARCHAR(64) NOT NULL, 
	purpose TEXT NOT NULL, 
	configured BOOLEAN NOT NULL, 
	fingerprint VARCHAR(16), 
	first_seen_at TIMESTAMP WITH TIME ZONE, 
	rotated_at TIMESTAMP WITH TIME ZONE, 
	checked_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (name)
);

CREATE TABLE IF NOT EXISTS orch_dead_letters (
	id VARCHAR(36) NOT NULL, 
	kind VARCHAR(12) NOT NULL, 
	event_id VARCHAR(64), 
	execution_id VARCHAR(36), 
	delivery_id INTEGER, 
	workflow_id VARCHAR(64), 
	error_category VARCHAR(32) NOT NULL, 
	reason TEXT NOT NULL, 
	delivery_count INTEGER NOT NULL, 
	last_attempt_at TIMESTAMP WITH TIME ZONE, 
	failed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	correlation_id VARCHAR(64), 
	recovery_status VARCHAR(12) NOT NULL, 
	resolved_by VARCHAR(80), 
	resolved_at TIMESTAMP WITH TIME ZONE, 
	resolution_note TEXT, 
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_orch_dead_letters_event_id ON orch_dead_letters (event_id);

CREATE INDEX IF NOT EXISTS ix_orch_dead_letters_execution_id ON orch_dead_letters (execution_id);

CREATE INDEX IF NOT EXISTS ix_orch_dead_letters_status ON orch_dead_letters (recovery_status);

CREATE TABLE IF NOT EXISTS orch_event_deliveries (
	id BIGSERIAL NOT NULL, 
	event_id VARCHAR(64) NOT NULL, 
	subscription_id VARCHAR(36) NOT NULL, 
	replay INTEGER NOT NULL, 
	status VARCHAR(12) NOT NULL, 
	attempts INTEGER NOT NULL, 
	next_attempt_at TIMESTAMP WITH TIME ZONE, 
	last_error TEXT, 
	error_category VARCHAR(32), 
	execution_id VARCHAR(36), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	delivered_at TIMESTAMP WITH TIME ZONE, 
	latency_ms FLOAT, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_orch_event_deliveries UNIQUE (event_id, subscription_id, replay)
);

CREATE INDEX IF NOT EXISTS ix_orch_event_deliveries_due ON orch_event_deliveries (status, next_attempt_at);

CREATE INDEX IF NOT EXISTS ix_orch_event_deliveries_event_id ON orch_event_deliveries (event_id);

CREATE TABLE IF NOT EXISTS orch_events (
	seq BIGSERIAL NOT NULL, 
	event_id VARCHAR(64) NOT NULL, 
	event_type VARCHAR(64) NOT NULL, 
	event_version VARCHAR(8) NOT NULL, 
	schema_version VARCHAR(8) NOT NULL, 
	occurred_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	published_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	source_module VARCHAR(16) NOT NULL, 
	destination VARCHAR(32), 
	correlation_id VARCHAR(64) NOT NULL, 
	causation_id VARCHAR(64), 
	idempotency_key VARCHAR(160) NOT NULL, 
	environment VARCHAR(16) NOT NULL, 
	payload JSONB NOT NULL, 
	metadata JSONB, 
	ingress VARCHAR(12) NOT NULL, 
	status VARCHAR(12) NOT NULL, 
	deliveries_total INTEGER NOT NULL, 
	PRIMARY KEY (seq), 
	UNIQUE (event_id), 
	UNIQUE (idempotency_key)
);

CREATE INDEX IF NOT EXISTS ix_orch_events_correlation_id ON orch_events (correlation_id);

CREATE INDEX IF NOT EXISTS ix_orch_events_event_type ON orch_events (event_type);

CREATE INDEX IF NOT EXISTS ix_orch_events_published_at ON orch_events (published_at);

CREATE TABLE IF NOT EXISTS orch_execution_callbacks (
	callback_id VARCHAR(64) NOT NULL, 
	execution_id VARCHAR(36) NOT NULL, 
	workflow_id VARCHAR(64) NOT NULL, 
	kind VARCHAR(16) NOT NULL, 
	attempt INTEGER, 
	received_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	sent_at TIMESTAMP WITH TIME ZONE, 
	outcome VARCHAR(16) NOT NULL, 
	detail TEXT, 
	previous_status VARCHAR(16), 
	new_status VARCHAR(16), 
	payload JSONB, 
	PRIMARY KEY (callback_id)
);

CREATE INDEX IF NOT EXISTS ix_orch_execution_callbacks_execution_id ON orch_execution_callbacks (execution_id);

CREATE TABLE IF NOT EXISTS orch_health_checks (
	id BIGSERIAL NOT NULL, 
	integration_id VARCHAR(32) NOT NULL, 
	checked_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	status VARCHAR(12) NOT NULL, 
	latency_ms FLOAT, 
	http_status INTEGER, 
	detail TEXT, 
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_orch_health_checks_at ON orch_health_checks (integration_id, checked_at);

CREATE TABLE IF NOT EXISTS orch_idempotency (
	id BIGSERIAL NOT NULL, 
	scope VARCHAR(64) NOT NULL, 
	key VARCHAR(160) NOT NULL, 
	request_hash VARCHAR(64) NOT NULL, 
	status_code INTEGER NOT NULL, 
	response JSONB, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_orch_idempotency UNIQUE (scope, key)
);

CREATE TABLE IF NOT EXISTS orch_inbox (
	id BIGSERIAL NOT NULL, 
	channel VARCHAR(16) NOT NULL, 
	message_id VARCHAR(64) NOT NULL, 
	event_type VARCHAR(64), 
	received_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	status VARCHAR(12) NOT NULL, 
	caller VARCHAR(80), 
	error_code VARCHAR(48), 
	duplicates INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_orch_inbox UNIQUE (channel, message_id)
);

CREATE TABLE IF NOT EXISTS orch_instance (
	key VARCHAR(16) NOT NULL, 
	environment VARCHAR(16) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (key)
);

CREATE TABLE IF NOT EXISTS orch_integrations (
	integration_id VARCHAR(32) NOT NULL, 
	name VARCHAR(80) NOT NULL, 
	kind VARCHAR(16) NOT NULL, 
	base_url VARCHAR(300), 
	health_path VARCHAR(160), 
	environment VARCHAR(16) NOT NULL, 
	enabled BOOLEAN NOT NULL, 
	status VARCHAR(12) NOT NULL, 
	detail TEXT, 
	last_checked_at TIMESTAMP WITH TIME ZONE, 
	last_healthy_at TIMESTAMP WITH TIME ZONE, 
	latency_ms FLOAT, 
	consecutive_failures INTEGER NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (integration_id)
);

CREATE TABLE IF NOT EXISTS orch_locks (
	lock_key VARCHAR(160) NOT NULL, 
	scope VARCHAR(24) NOT NULL, 
	owner VARCHAR(80) NOT NULL, 
	token VARCHAR(36) NOT NULL, 
	fencing INTEGER NOT NULL, 
	acquired_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	renewed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	released_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (lock_key)
);

CREATE TABLE IF NOT EXISTS orch_source_cursors (
	source VARCHAR(32) NOT NULL, 
	position INTEGER NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (source)
);

CREATE TABLE IF NOT EXISTS orch_workflows (
	workflow_id VARCHAR(64) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	description TEXT, 
	purpose TEXT, 
	category VARCHAR(32) NOT NULL, 
	owner VARCHAR(80) NOT NULL, 
	environment VARCHAR(16) NOT NULL, 
	engine VARCHAR(16) NOT NULL, 
	n8n_workflow_id VARCHAR(64), 
	webhook_path VARCHAR(160), 
	state VARCHAR(16) NOT NULL, 
	trigger_types JSONB NOT NULL, 
	active_version_id VARCHAR(36), 
	timeout_seconds INTEGER NOT NULL, 
	retry_policy JSONB NOT NULL, 
	idempotent_dispatch BOOLEAN NOT NULL, 
	placeholder BOOLEAN NOT NULL, 
	health_status VARCHAR(16) NOT NULL, 
	health_detail TEXT, 
	version INTEGER NOT NULL, 
	created_by VARCHAR(80) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	last_execution_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (workflow_id)
);

CREATE TABLE IF NOT EXISTS orch_event_delivery_attempts (
	id BIGSERIAL NOT NULL, 
	delivery_id BIGINT NOT NULL, 
	attempt INTEGER NOT NULL, 
	at TIMESTAMP WITH TIME ZONE NOT NULL, 
	outcome VARCHAR(12) NOT NULL, 
	http_status INTEGER, 
	latency_ms FLOAT, 
	error TEXT, 
	PRIMARY KEY (id), 
	FOREIGN KEY(delivery_id) REFERENCES orch_event_deliveries (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_orch_event_delivery_attempts_delivery_id ON orch_event_delivery_attempts (delivery_id);

CREATE TABLE IF NOT EXISTS orch_event_subscriptions (
	subscription_id VARCHAR(36) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	event_pattern VARCHAR(64) NOT NULL, 
	target_kind VARCHAR(12) NOT NULL, 
	workflow_id VARCHAR(64), 
	webhook_url VARCHAR(400), 
	environment VARCHAR(16) NOT NULL, 
	filter JSONB, 
	active BOOLEAN NOT NULL, 
	max_attempts INTEGER NOT NULL, 
	created_by VARCHAR(80) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (subscription_id), 
	FOREIGN KEY(workflow_id) REFERENCES orch_workflows (workflow_id)
);

CREATE TABLE IF NOT EXISTS orch_executions (
	execution_id VARCHAR(36) NOT NULL, 
	workflow_id VARCHAR(64) NOT NULL, 
	version_id VARCHAR(36) NOT NULL, 
	version_label VARCHAR(32) NOT NULL, 
	environment VARCHAR(16) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	version INTEGER NOT NULL, 
	trigger_type VARCHAR(12) NOT NULL, 
	trigger_source VARCHAR(80) NOT NULL, 
	requesting_module VARCHAR(16), 
	idempotency_key VARCHAR(160) NOT NULL, 
	correlation_id VARCHAR(64) NOT NULL, 
	causation_id VARCHAR(64), 
	input JSONB NOT NULL, 
	input_hash VARCHAR(64) NOT NULL, 
	output JSONB, 
	symbol VARCHAR(32), 
	trade_idea_id VARCHAR(64), 
	approval_id VARCHAR(36), 
	n8n_execution_id VARCHAR(64), 
	attempt INTEGER NOT NULL, 
	retry_count INTEGER NOT NULL, 
	next_attempt_at TIMESTAMP WITH TIME ZONE, 
	deadline_at TIMESTAMP WITH TIME ZONE, 
	error_category VARCHAR(32), 
	error_message TEXT, 
	warnings JSONB, 
	timeline JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	status_changed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (execution_id), 
	CONSTRAINT uq_orch_executions_idem UNIQUE (workflow_id, idempotency_key), 
	FOREIGN KEY(workflow_id) REFERENCES orch_workflows (workflow_id)
);

CREATE INDEX IF NOT EXISTS ix_orch_executions_approval_id ON orch_executions (approval_id);

CREATE INDEX IF NOT EXISTS ix_orch_executions_correlation_id ON orch_executions (correlation_id);

CREATE INDEX IF NOT EXISTS ix_orch_executions_created ON orch_executions (created_at);

CREATE INDEX IF NOT EXISTS ix_orch_executions_status_due ON orch_executions (status, next_attempt_at);

CREATE INDEX IF NOT EXISTS ix_orch_executions_symbol ON orch_executions (symbol);

CREATE INDEX IF NOT EXISTS ix_orch_executions_trade_idea_id ON orch_executions (trade_idea_id);

CREATE INDEX IF NOT EXISTS ix_orch_executions_workflow_id ON orch_executions (workflow_id);

CREATE TABLE IF NOT EXISTS orch_schedules (
	schedule_id VARCHAR(36) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	workflow_id VARCHAR(64) NOT NULL, 
	kind VARCHAR(10) NOT NULL, 
	expression VARCHAR(64) NOT NULL, 
	timezone VARCHAR(40) NOT NULL, 
	input JSONB NOT NULL, 
	enabled BOOLEAN NOT NULL, 
	next_run_at TIMESTAMP WITH TIME ZONE, 
	last_run_at TIMESTAMP WITH TIME ZONE, 
	last_status VARCHAR(16), 
	last_execution_id VARCHAR(36), 
	failure_count INTEGER NOT NULL, 
	created_by VARCHAR(80) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (schedule_id), 
	UNIQUE (name), 
	FOREIGN KEY(workflow_id) REFERENCES orch_workflows (workflow_id)
);

CREATE INDEX IF NOT EXISTS ix_orch_schedules_next_run_at ON orch_schedules (next_run_at);

CREATE INDEX IF NOT EXISTS ix_orch_schedules_workflow_id ON orch_schedules (workflow_id);

CREATE TABLE IF NOT EXISTS orch_workflow_versions (
	version_id VARCHAR(36) NOT NULL, 
	workflow_id VARCHAR(64) NOT NULL, 
	version_label VARCHAR(32) NOT NULL, 
	description TEXT, 
	status VARCHAR(12) NOT NULL, 
	input_schema JSONB NOT NULL, 
	output_schema JSONB NOT NULL, 
	n8n_version_id VARCHAR(64), 
	contract_version VARCHAR(8) NOT NULL, 
	compatibility JSONB NOT NULL, 
	definition_hash VARCHAR(64) NOT NULL, 
	validation JSONB, 
	created_by VARCHAR(80) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	validated_at TIMESTAMP WITH TIME ZONE, 
	activated_at TIMESTAMP WITH TIME ZONE, 
	deactivated_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (version_id), 
	CONSTRAINT uq_orch_workflow_versions UNIQUE (workflow_id, version_label), 
	FOREIGN KEY(workflow_id) REFERENCES orch_workflows (workflow_id)
);

CREATE INDEX IF NOT EXISTS ix_orch_workflow_versions_workflow_id ON orch_workflow_versions (workflow_id);

CREATE TABLE IF NOT EXISTS orch_execution_attempts (
	id BIGSERIAL NOT NULL, 
	execution_id VARCHAR(36) NOT NULL, 
	attempt INTEGER NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	outcome VARCHAR(16) NOT NULL, 
	http_status INTEGER, 
	latency_ms FLOAT, 
	error_category VARCHAR(32), 
	error_message TEXT, 
	response_meta JSONB, 
	manual BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_orch_execution_attempts UNIQUE (execution_id, attempt), 
	FOREIGN KEY(execution_id) REFERENCES orch_executions (execution_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS orch_schedule_runs (
	id BIGSERIAL NOT NULL, 
	schedule_id VARCHAR(36) NOT NULL, 
	slot_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	fired_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	execution_id VARCHAR(36), 
	detail TEXT, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_orch_schedule_runs UNIQUE (schedule_id, slot_at), 
	FOREIGN KEY(schedule_id) REFERENCES orch_schedules (schedule_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_orch_schedule_runs_schedule_id ON orch_schedule_runs (schedule_id);


-- ---- execution state machine (pairs generated from aurum/orchestration/states.py) ----
CREATE OR REPLACE FUNCTION orch_check_execution_transition() RETURNS trigger AS $$
BEGIN
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        IF NOT EXISTS (
            SELECT 1 FROM (VALUES
            ('DEAD_LETTERED','RETRY_PENDING'),
            ('DISPATCHED','CANCELLED'),
            ('DISPATCHED','FAILED'),
            ('DISPATCHED','RETRY_PENDING'),
            ('DISPATCHED','RUNNING'),
            ('DISPATCHED','SUCCEEDED'),
            ('DISPATCHED','TIMED_OUT'),
            ('DISPATCHED','WAITING_CALLBACK'),
            ('FAILED','DEAD_LETTERED'),
            ('FAILED','RETRY_PENDING'),
            ('QUEUED','CANCELLED'),
            ('QUEUED','DISPATCHED'),
            ('QUEUED','FAILED'),
            ('RECEIVED','CANCELLED'),
            ('RECEIVED','FAILED'),
            ('RECEIVED','VALIDATING'),
            ('RETRY_PENDING','CANCELLED'),
            ('RETRY_PENDING','DEAD_LETTERED'),
            ('RETRY_PENDING','DISPATCHED'),
            ('RETRY_PENDING','FAILED'),
            ('RUNNING','CANCELLED'),
            ('RUNNING','FAILED'),
            ('RUNNING','SUCCEEDED'),
            ('RUNNING','TIMED_OUT'),
            ('RUNNING','WAITING_CALLBACK'),
            ('TIMED_OUT','DEAD_LETTERED'),
            ('TIMED_OUT','FAILED'),
            ('TIMED_OUT','RETRY_PENDING'),
            ('TIMED_OUT','SUCCEEDED'),
            ('VALIDATING','CANCELLED'),
            ('VALIDATING','FAILED'),
            ('VALIDATING','QUEUED'),
            ('WAITING_CALLBACK','CANCELLED'),
            ('WAITING_CALLBACK','FAILED'),
            ('WAITING_CALLBACK','RUNNING'),
            ('WAITING_CALLBACK','SUCCEEDED'),
            ('WAITING_CALLBACK','TIMED_OUT')
            ) AS allowed(f, t) WHERE allowed.f = OLD.status AND allowed.t = NEW.status) THEN
            RAISE EXCEPTION 'illegal workflow execution transition % -> %', OLD.status, NEW.status;
        END IF;
        IF NEW.version <= OLD.version THEN
            RAISE EXCEPTION 'workflow execution status changed without a version increase';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_orch_execution_transition ON orch_executions;
CREATE TRIGGER trg_orch_execution_transition BEFORE UPDATE ON orch_executions
    FOR EACH ROW EXECUTE FUNCTION orch_check_execution_transition();

-- ---- append-only tables ----
CREATE OR REPLACE FUNCTION orch_reject_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_orch_audit_logs_append_only ON orch_audit_logs;
CREATE TRIGGER trg_orch_audit_logs_append_only BEFORE UPDATE OR DELETE ON orch_audit_logs
    FOR EACH ROW EXECUTE FUNCTION orch_reject_change();
DROP TRIGGER IF EXISTS trg_orch_config_versions_append_only ON orch_config_versions;
CREATE TRIGGER trg_orch_config_versions_append_only BEFORE UPDATE OR DELETE ON orch_config_versions
    FOR EACH ROW EXECUTE FUNCTION orch_reject_change();

-- ---- workflow versions: content is immutable, only status and timestamps move ----
CREATE OR REPLACE FUNCTION orch_version_immutable() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'workflow versions are never deleted (retire them instead)';
    END IF;
    IF NEW.input_schema IS DISTINCT FROM OLD.input_schema OR NEW.output_schema IS DISTINCT FROM OLD.output_schema
       OR NEW.n8n_version_id IS DISTINCT FROM OLD.n8n_version_id OR NEW.definition_hash IS DISTINCT FROM OLD.definition_hash
       OR NEW.compatibility IS DISTINCT FROM OLD.compatibility OR NEW.version_label IS DISTINCT FROM OLD.version_label
       OR NEW.workflow_id IS DISTINCT FROM OLD.workflow_id OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'workflow version content is immutable; register a new version';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_orch_workflow_versions_immutable ON orch_workflow_versions;
CREATE TRIGGER trg_orch_workflow_versions_immutable BEFORE UPDATE OR DELETE ON orch_workflow_versions
    FOR EACH ROW EXECUTE FUNCTION orch_version_immutable();
