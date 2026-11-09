-- =====================================================================
-- AURUM AI - Module 07: Human Approval and Trade Management Engine.
-- Additive only: creates new tables; nothing existing is altered, dropped or deleted.
-- Generated from aurum/auth/db_models.py and aurum/approvals/db_models.py (SQLite/dev uses create_all),
-- then the database-level guarantees below are added by hand:
--   * approval status changes must follow the state machine (trigger);
--   * decisions, audit rows, idea versions, evidence/risk snapshots and execution results are append-only;
--   * one active approval per idea and one active management request per position (partial unique indexes).
-- Safe to run more than once.
-- =====================================================================

CREATE TABLE IF NOT EXISTS approval_audit_logs (
	seq BIGSERIAL NOT NULL, 
	at TIMESTAMP WITH TIME ZONE NOT NULL, 
	actor VARCHAR(80) NOT NULL, 
	actor_kind VARCHAR(8) NOT NULL, 
	action VARCHAR(48) NOT NULL, 
	entity_type VARCHAR(32) NOT NULL, 
	entity_id VARCHAR(64) NOT NULL, 
	approval_id VARCHAR(36), 
	idea_id VARCHAR(64), 
	previous_state VARCHAR(24), 
	new_state VARCHAR(24), 
	reason TEXT, 
	correlation_id VARCHAR(64), 
	evidence_snapshot_id VARCHAR(36), 
	details JSONB, 
	prev_hash VARCHAR(64) NOT NULL, 
	hash VARCHAR(64) NOT NULL, 
	PRIMARY KEY (seq)
);

CREATE INDEX IF NOT EXISTS ix_approval_audit_logs_approval_id ON approval_audit_logs (approval_id);

CREATE INDEX IF NOT EXISTS ix_approval_audit_logs_idea_id ON approval_audit_logs (idea_id);

CREATE TABLE IF NOT EXISTS approval_evidence_snapshots (
	id VARCHAR(36) NOT NULL, 
	approval_id VARCHAR(36), 
	idea_id VARCHAR(64) NOT NULL, 
	idea_version INTEGER NOT NULL, 
	content JSONB NOT NULL, 
	content_hash VARCHAR(64) NOT NULL, 
	completeness JSONB, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_approval_evidence_snapshots_approval_id ON approval_evidence_snapshots (approval_id);

CREATE INDEX IF NOT EXISTS ix_approval_evidence_snapshots_idea_id ON approval_evidence_snapshots (idea_id);

CREATE TABLE IF NOT EXISTS approval_policies (
	version INTEGER NOT NULL, 
	policy JSONB NOT NULL, 
	policy_hash VARCHAR(64) NOT NULL, 
	reason TEXT NOT NULL, 
	created_by VARCHAR(64) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (version)
);

CREATE TABLE IF NOT EXISTS approval_risk_snapshots (
	id VARCHAR(36) NOT NULL, 
	approval_id VARCHAR(36), 
	management_request_id VARCHAR(36), 
	idea_id VARCHAR(64), 
	idea_version INTEGER, 
	purpose VARCHAR(16) NOT NULL, 
	assessment_id VARCHAR(36), 
	decision VARCHAR(8) NOT NULL, 
	final_volume NUMERIC(24, 8), 
	risk_amount NUMERIC(24, 8), 
	risk_pct NUMERIC(24, 8), 
	config_version INTEGER, 
	assessed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE, 
	payload JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_approval_risk_snapshots_approval_id ON approval_risk_snapshots (approval_id);

CREATE INDEX IF NOT EXISTS ix_approval_risk_snapshots_idea_id ON approval_risk_snapshots (idea_id);

CREATE TABLE IF NOT EXISTS idempotency_records (
	id BIGSERIAL NOT NULL, 
	scope VARCHAR(64) NOT NULL, 
	key VARCHAR(128) NOT NULL, 
	request_hash VARCHAR(64) NOT NULL, 
	status_code INTEGER NOT NULL, 
	response JSONB, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_idempotency_records UNIQUE (scope, key)
);

CREATE TABLE IF NOT EXISTS managed_positions (
	position_id VARCHAR(32) NOT NULL, 
	account_login VARCHAR(32), 
	symbol VARCHAR(32) NOT NULL, 
	side VARCHAR(4) NOT NULL, 
	volume NUMERIC(24, 8) NOT NULL, 
	entry_price NUMERIC(24, 8) NOT NULL, 
	current_price NUMERIC(24, 8), 
	stop_loss NUMERIC(24, 8), 
	take_profit NUMERIC(24, 8), 
	unrealized_pnl NUMERIC(24, 8), 
	realized_pnl NUMERIC(24, 8), 
	status VARCHAR(16) NOT NULL, 
	opened_at TIMESTAMP WITH TIME ZONE, 
	source VARCHAR(16) NOT NULL, 
	source_updated_at TIMESTAMP WITH TIME ZONE, 
	approval_id VARCHAR(36), 
	execution_request_id VARCHAR(36), 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (position_id)
);

CREATE TABLE IF NOT EXISTS outbox_events (
	seq BIGSERIAL NOT NULL, 
	event_id VARCHAR(36) NOT NULL, 
	event_type VARCHAR(64) NOT NULL, 
	event_version VARCHAR(8) NOT NULL, 
	occurred_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	source_module VARCHAR(16) NOT NULL, 
	correlation_id VARCHAR(64) NOT NULL, 
	causation_id VARCHAR(64), 
	idempotency_key VARCHAR(160) NOT NULL, 
	payload JSONB NOT NULL, 
	status VARCHAR(12) NOT NULL, 
	attempts INTEGER NOT NULL, 
	next_attempt_at TIMESTAMP WITH TIME ZONE, 
	last_error TEXT, 
	delivered_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (seq), 
	UNIQUE (event_id), 
	UNIQUE (idempotency_key)
);

CREATE INDEX IF NOT EXISTS ix_outbox_events_due ON outbox_events (status, next_attempt_at);

CREATE INDEX IF NOT EXISTS ix_outbox_events_event_type ON outbox_events (event_type);

CREATE TABLE IF NOT EXISTS service_tokens (
	id VARCHAR(36) NOT NULL, 
	name VARCHAR(64) NOT NULL, 
	token_hash VARCHAR(64) NOT NULL, 
	scopes JSONB, 
	created_by VARCHAR(64) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	last_used_at TIMESTAMP WITH TIME ZONE, 
	expires_at TIMESTAMP WITH TIME ZONE, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_service_tokens_name UNIQUE (name), 
	UNIQUE (token_hash)
);

CREATE TABLE IF NOT EXISTS trade_ideas (
	idea_id VARCHAR(64) NOT NULL, 
	source VARCHAR(16) NOT NULL, 
	source_ref VARCHAR(64), 
	symbol VARCHAR(32) NOT NULL, 
	market VARCHAR(16) NOT NULL, 
	timeframe VARCHAR(4) NOT NULL, 
	strategy VARCHAR(64) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	current_version INTEGER NOT NULL, 
	correlation_id VARCHAR(64) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (idea_id)
);

CREATE INDEX IF NOT EXISTS ix_trade_ideas_symbol ON trade_ideas (symbol);

CREATE TABLE IF NOT EXISTS users (
	id VARCHAR(36) NOT NULL, 
	username VARCHAR(64) NOT NULL, 
	display_name VARCHAR(128) NOT NULL, 
	email VARCHAR(254), 
	role VARCHAR(16) NOT NULL, 
	password_hash TEXT NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	account_scope JSONB, 
	failed_logins INTEGER NOT NULL, 
	locked_until TIMESTAMP WITH TIME ZONE, 
	password_changed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	last_login_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (username)
);

CREATE TABLE IF NOT EXISTS approval_requests (
	approval_id VARCHAR(36) NOT NULL, 
	idea_id VARCHAR(64) NOT NULL, 
	idea_version INTEGER NOT NULL, 
	parent_approval_id VARCHAR(36), 
	status VARCHAR(24) NOT NULL, 
	version INTEGER NOT NULL, 
	assigned_user_id VARCHAR(36), 
	review_started_by VARCHAR(36), 
	symbol VARCHAR(32) NOT NULL, 
	direction VARCHAR(4) NOT NULL, 
	timeframe VARCHAR(4) NOT NULL, 
	strategy VARCHAR(64) NOT NULL, 
	entry NUMERIC(24, 8) NOT NULL, 
	stop_loss NUMERIC(24, 8) NOT NULL, 
	take_profit NUMERIC(24, 8) NOT NULL, 
	position_size NUMERIC(24, 8), 
	risk_amount NUMERIC(24, 8), 
	risk_pct NUMERIC(24, 8), 
	risk_reward NUMERIC(24, 8), 
	ai_confidence NUMERIC(24, 8), 
	risk_decision VARCHAR(8) NOT NULL, 
	risk_assessment_id VARCHAR(36), 
	risk_assessed_at TIMESTAMP WITH TIME ZONE, 
	risk_expires_at TIMESTAMP WITH TIME ZONE, 
	risk_config_version INTEGER, 
	risk_warnings JSONB, 
	evidence_snapshot_id VARCHAR(36), 
	evidence_hash VARCHAR(64), 
	analysis_version VARCHAR(64) NOT NULL, 
	policy_version INTEGER NOT NULL, 
	account_login VARCHAR(32), 
	approvals_required INTEGER NOT NULL, 
	idempotency_key VARCHAR(128) NOT NULL, 
	correlation_id VARCHAR(64) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	expiring_notified_at TIMESTAMP WITH TIME ZONE, 
	status_changed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	status_reason TEXT, 
	decided_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (approval_id), 
	FOREIGN KEY(idea_id) REFERENCES trade_ideas (idea_id), 
	FOREIGN KEY(parent_approval_id) REFERENCES approval_requests (approval_id), 
	FOREIGN KEY(assigned_user_id) REFERENCES users (id), 
	FOREIGN KEY(review_started_by) REFERENCES users (id), 
	UNIQUE (idempotency_key)
);

CREATE INDEX IF NOT EXISTS ix_approval_requests_idea_id ON approval_requests (idea_id);

CREATE INDEX IF NOT EXISTS ix_approval_requests_status_expires ON approval_requests (status, expires_at);

CREATE INDEX IF NOT EXISTS ix_approval_requests_symbol ON approval_requests (symbol);

CREATE UNIQUE INDEX IF NOT EXISTS uq_approval_requests_active_idea ON approval_requests (idea_id) WHERE status IN ('PENDING','UNDER_REVIEW','APPROVED','EXECUTION_PENDING','EXECUTION_SUBMITTED');

CREATE TABLE IF NOT EXISTS auth_sessions (
	id VARCHAR(36) NOT NULL, 
	token_hash VARCHAR(64) NOT NULL, 
	csrf_hash VARCHAR(64) NOT NULL, 
	user_id VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	last_seen_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	revoke_reason VARCHAR(64), 
	client_ip VARCHAR(64), 
	user_agent VARCHAR(256), 
	PRIMARY KEY (id), 
	UNIQUE (token_hash), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_auth_sessions_user_id ON auth_sessions (user_id);

CREATE TABLE IF NOT EXISTS notifications (
	id VARCHAR(36) NOT NULL, 
	dedupe_key VARCHAR(160) NOT NULL, 
	event_type VARCHAR(48) NOT NULL, 
	priority VARCHAR(8) NOT NULL, 
	title VARCHAR(160) NOT NULL, 
	message TEXT NOT NULL, 
	approval_id VARCHAR(36), 
	management_request_id VARCHAR(36), 
	position_id VARCHAR(32), 
	recipient_user_id VARCHAR(36), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (dedupe_key), 
	FOREIGN KEY(recipient_user_id) REFERENCES users (id)
);

CREATE INDEX IF NOT EXISTS ix_notifications_approval_id ON notifications (approval_id);

CREATE INDEX IF NOT EXISTS ix_notifications_created_at ON notifications (created_at);

CREATE TABLE IF NOT EXISTS reviewer_assignments (
	id VARCHAR(36) NOT NULL, 
	user_id VARCHAR(36) NOT NULL, 
	symbol VARCHAR(32), 
	strategy VARCHAR(64), 
	timeframe VARCHAR(4), 
	active BOOLEAN NOT NULL, 
	created_by VARCHAR(64) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
);

CREATE INDEX IF NOT EXISTS ix_reviewer_assignments_user_id ON reviewer_assignments (user_id);

CREATE TABLE IF NOT EXISTS trade_idea_versions (
	id BIGSERIAL NOT NULL, 
	idea_id VARCHAR(64) NOT NULL, 
	version INTEGER NOT NULL, 
	direction VARCHAR(4) NOT NULL, 
	entry_low NUMERIC(24, 8) NOT NULL, 
	entry_high NUMERIC(24, 8) NOT NULL, 
	entry NUMERIC(24, 8) NOT NULL, 
	stop_loss NUMERIC(24, 8) NOT NULL, 
	take_profits JSONB NOT NULL, 
	risk_reward NUMERIC(24, 8), 
	requested_volume NUMERIC(24, 8), 
	higher_timeframe_context JSONB, 
	ai_summary TEXT, 
	ai_confidence NUMERIC(24, 8), 
	evidence JSONB, 
	market_conditions JSONB, 
	analysis_version VARCHAR(64) NOT NULL, 
	source_refs JSONB, 
	schema_version VARCHAR(8) NOT NULL, 
	payload JSONB NOT NULL, 
	payload_hash VARCHAR(64) NOT NULL, 
	created_by VARCHAR(80) NOT NULL, 
	reason TEXT, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_trade_idea_versions UNIQUE (idea_id, version), 
	FOREIGN KEY(idea_id) REFERENCES trade_ideas (idea_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS trade_management_requests (
	request_id VARCHAR(36) NOT NULL, 
	position_id VARCHAR(32) NOT NULL, 
	account_login VARCHAR(32), 
	symbol VARCHAR(32) NOT NULL, 
	action VARCHAR(24) NOT NULL, 
	params JSONB NOT NULL, 
	position_snapshot JSONB NOT NULL, 
	reason TEXT NOT NULL, 
	evidence JSONB, 
	risk_implications JSONB, 
	priority VARCHAR(12) NOT NULL, 
	requested_by VARCHAR(36) NOT NULL, 
	status VARCHAR(24) NOT NULL, 
	version INTEGER NOT NULL, 
	idempotency_key VARCHAR(128) NOT NULL, 
	correlation_id VARCHAR(64) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	status_changed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	status_reason TEXT, 
	PRIMARY KEY (request_id), 
	FOREIGN KEY(requested_by) REFERENCES users (id), 
	UNIQUE (idempotency_key)
);

CREATE INDEX IF NOT EXISTS ix_trade_management_requests_position_id ON trade_management_requests (position_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_trade_management_active_position ON trade_management_requests (position_id) WHERE status IN ('PENDING','APPROVED','EXECUTION_PENDING','EXECUTION_SUBMITTED');

CREATE TABLE IF NOT EXISTS approval_decisions (
	id VARCHAR(36) NOT NULL, 
	approval_id VARCHAR(36) NOT NULL, 
	decision VARCHAR(24) NOT NULL, 
	is_final BOOLEAN NOT NULL, 
	user_id VARCHAR(36) NOT NULL, 
	username VARCHAR(64) NOT NULL, 
	role VARCHAR(16) NOT NULL, 
	method VARCHAR(16) NOT NULL, 
	decided_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	evidence_snapshot_id VARCHAR(36), 
	evidence_hash VARCHAR(64), 
	risk_assessment_id VARCHAR(36), 
	risk_decision VARCHAR(8), 
	analysis_version VARCHAR(64) NOT NULL, 
	policy_version INTEGER NOT NULL, 
	client_request_id VARCHAR(128) NOT NULL, 
	reason_code VARCHAR(48), 
	comment TEXT, 
	acknowledged_warnings JSONB, 
	confirmation JSONB, 
	record_hash VARCHAR(64) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_approval_decisions_user UNIQUE (approval_id, user_id), 
	FOREIGN KEY(approval_id) REFERENCES approval_requests (approval_id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
);

CREATE INDEX IF NOT EXISTS ix_approval_decisions_approval_id ON approval_decisions (approval_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_approval_decisions_final ON approval_decisions (approval_id) WHERE is_final;

CREATE TABLE IF NOT EXISTS execution_handoffs (
	execution_request_id VARCHAR(36) NOT NULL, 
	kind VARCHAR(16) NOT NULL, 
	approval_id VARCHAR(36), 
	management_request_id VARCHAR(36), 
	status VARCHAR(16) NOT NULL, 
	payload JSONB NOT NULL, 
	payload_hash VARCHAR(64) NOT NULL, 
	account_login VARCHAR(32), 
	idempotency_key VARCHAR(128) NOT NULL, 
	correlation_id VARCHAR(64) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	picked_up_at TIMESTAMP WITH TIME ZONE, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (execution_request_id), 
	UNIQUE (approval_id), 
	FOREIGN KEY(approval_id) REFERENCES approval_requests (approval_id), 
	UNIQUE (management_request_id), 
	UNIQUE (idempotency_key)
);

CREATE TABLE IF NOT EXISTS modification_requests (
	id VARCHAR(36) NOT NULL, 
	approval_id VARCHAR(36) NOT NULL, 
	requested_by VARCHAR(36) NOT NULL, 
	kinds JSONB NOT NULL, 
	changes JSONB, 
	previous JSONB NOT NULL, 
	comment TEXT, 
	status VARCHAR(24) NOT NULL, 
	new_idea_version INTEGER, 
	new_approval_id VARCHAR(36), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(approval_id) REFERENCES approval_requests (approval_id), 
	FOREIGN KEY(requested_by) REFERENCES users (id)
);

CREATE INDEX IF NOT EXISTS ix_modification_requests_approval_id ON modification_requests (approval_id);

CREATE TABLE IF NOT EXISTS notification_deliveries (
	id BIGSERIAL NOT NULL, 
	notification_id VARCHAR(36) NOT NULL, 
	user_id VARCHAR(36) NOT NULL, 
	channel VARCHAR(16) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	attempts INTEGER NOT NULL, 
	next_attempt_at TIMESTAMP WITH TIME ZONE, 
	last_error TEXT, 
	sent_at TIMESTAMP WITH TIME ZONE, 
	read_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_notification_deliveries UNIQUE (notification_id, user_id, channel), 
	FOREIGN KEY(notification_id) REFERENCES notifications (id) ON DELETE CASCADE, 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_notification_deliveries_due ON notification_deliveries (status, next_attempt_at);

CREATE INDEX IF NOT EXISTS ix_notification_deliveries_user_id ON notification_deliveries (user_id);

CREATE TABLE IF NOT EXISTS trade_management_decisions (
	id VARCHAR(36) NOT NULL, 
	request_id VARCHAR(36) NOT NULL, 
	decision VARCHAR(16) NOT NULL, 
	is_final BOOLEAN NOT NULL, 
	user_id VARCHAR(36) NOT NULL, 
	username VARCHAR(64) NOT NULL, 
	role VARCHAR(16) NOT NULL, 
	decided_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	client_request_id VARCHAR(128) NOT NULL, 
	comment TEXT, 
	confirmation JSONB, 
	record_hash VARCHAR(64) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_trade_management_decisions_user UNIQUE (request_id, user_id), 
	FOREIGN KEY(request_id) REFERENCES trade_management_requests (request_id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
);

CREATE INDEX IF NOT EXISTS ix_trade_management_decisions_request_id ON trade_management_decisions (request_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_trade_management_decisions_final ON trade_management_decisions (request_id) WHERE is_final;

CREATE TABLE IF NOT EXISTS execution_results (
	id BIGSERIAL NOT NULL, 
	event_id VARCHAR(64) NOT NULL, 
	execution_request_id VARCHAR(36) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	broker_ticket VARCHAR(32), 
	position_id VARCHAR(32), 
	filled_volume NUMERIC(24, 8), 
	fill_price NUMERIC(24, 8), 
	message TEXT, 
	payload JSONB NOT NULL, 
	reported_by VARCHAR(80) NOT NULL, 
	reported_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (event_id), 
	FOREIGN KEY(execution_request_id) REFERENCES execution_handoffs (execution_request_id)
);

CREATE INDEX IF NOT EXISTS ix_execution_results_execution_request_id ON execution_results (execution_request_id);


-- ---- state machine: only the transitions in aurum/approvals/states.py ----
CREATE OR REPLACE FUNCTION approval_check_transition() RETURNS trigger AS $$
BEGIN
    IF NEW.status IS DISTINCT FROM OLD.status AND NOT EXISTS (
        SELECT 1 FROM (VALUES
        ('APPROVED','CANCELLED'),
        ('APPROVED','EXECUTION_PENDING'),
        ('APPROVED','INVALIDATED'),
        ('EXECUTION_PENDING','EXECUTION_FAILED'),
        ('EXECUTION_PENDING','EXECUTION_SUBMITTED'),
        ('EXECUTION_SUBMITTED','EXECUTED'),
        ('EXECUTION_SUBMITTED','EXECUTION_FAILED'),
        ('PENDING','APPROVED'),
        ('PENDING','CANCELLED'),
        ('PENDING','EXPIRED'),
        ('PENDING','INVALIDATED'),
        ('PENDING','MODIFICATION_REQUESTED'),
        ('PENDING','REJECTED'),
        ('PENDING','UNDER_REVIEW'),
        ('UNDER_REVIEW','APPROVED'),
        ('UNDER_REVIEW','CANCELLED'),
        ('UNDER_REVIEW','EXPIRED'),
        ('UNDER_REVIEW','INVALIDATED'),
        ('UNDER_REVIEW','MODIFICATION_REQUESTED'),
        ('UNDER_REVIEW','REJECTED')
        ) AS allowed(f, t) WHERE allowed.f = OLD.status AND allowed.t = NEW.status) THEN
        RAISE EXCEPTION 'illegal approval transition % -> %', OLD.status, NEW.status;
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status AND NEW.version <= OLD.version THEN
        RAISE EXCEPTION 'approval status changed without a version increase';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_approval_transition ON approval_requests;
CREATE TRIGGER trg_approval_transition BEFORE UPDATE ON approval_requests
    FOR EACH ROW EXECUTE FUNCTION approval_check_transition();

-- ---- append-only history ----
CREATE OR REPLACE FUNCTION approval_reject_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_approval_audit_logs_append_only ON approval_audit_logs;
CREATE TRIGGER trg_approval_audit_logs_append_only BEFORE UPDATE OR DELETE ON approval_audit_logs
    FOR EACH ROW EXECUTE FUNCTION approval_reject_change();

DROP TRIGGER IF EXISTS trg_approval_decisions_append_only ON approval_decisions;
CREATE TRIGGER trg_approval_decisions_append_only BEFORE UPDATE OR DELETE ON approval_decisions
    FOR EACH ROW EXECUTE FUNCTION approval_reject_change();

DROP TRIGGER IF EXISTS trg_trade_idea_versions_append_only ON trade_idea_versions;
CREATE TRIGGER trg_trade_idea_versions_append_only BEFORE UPDATE OR DELETE ON trade_idea_versions
    FOR EACH ROW EXECUTE FUNCTION approval_reject_change();

DROP TRIGGER IF EXISTS trg_approval_evidence_snapshots_append_only ON approval_evidence_snapshots;
CREATE TRIGGER trg_approval_evidence_snapshots_append_only BEFORE UPDATE OR DELETE ON approval_evidence_snapshots
    FOR EACH ROW EXECUTE FUNCTION approval_reject_change();

DROP TRIGGER IF EXISTS trg_approval_risk_snapshots_append_only ON approval_risk_snapshots;
CREATE TRIGGER trg_approval_risk_snapshots_append_only BEFORE UPDATE OR DELETE ON approval_risk_snapshots
    FOR EACH ROW EXECUTE FUNCTION approval_reject_change();

DROP TRIGGER IF EXISTS trg_execution_results_append_only ON execution_results;
CREATE TRIGGER trg_execution_results_append_only BEFORE UPDATE OR DELETE ON execution_results
    FOR EACH ROW EXECUTE FUNCTION approval_reject_change();

DROP TRIGGER IF EXISTS trg_trade_management_decisions_append_only ON trade_management_decisions;
CREATE TRIGGER trg_trade_management_decisions_append_only BEFORE UPDATE OR DELETE ON trade_management_decisions
    FOR EACH ROW EXECUTE FUNCTION approval_reject_change();

DROP TRIGGER IF EXISTS trg_approval_policies_append_only ON approval_policies;
CREATE TRIGGER trg_approval_policies_append_only BEFORE UPDATE OR DELETE ON approval_policies
    FOR EACH ROW EXECUTE FUNCTION approval_reject_change();
