-- =====================================================================
-- AURUM AI - per-person MT5 account links.
-- 1. account_deals and trade_records gain a `login` column, so several people's linked DEMO accounts never mix
--    (deal tickets and position ids are only unique within one broker account). Existing rows are assigned to the
--    single account found in account_snapshots, or to 0 when that is unknown or ambiguous. Nothing is deleted.
-- 2. mt5_connections records each authorisation (who, which account, which terminal slot, status).
--    Broker passwords are never stored.
-- Safe to run more than once.
-- =====================================================================

-- ---- 1. account mirror: one account per row ----------------------------------------------------------------
ALTER TABLE account_deals ADD COLUMN IF NOT EXISTS login BIGINT;
ALTER TABLE trade_records ADD COLUMN IF NOT EXISTS login BIGINT;

UPDATE account_deals SET login = (SELECT CASE WHEN COUNT(DISTINCT s.login) = 1 THEN MAX(s.login) ELSE 0 END
                                  FROM account_snapshots s)
 WHERE login IS NULL;
UPDATE trade_records SET login = (SELECT CASE WHEN COUNT(DISTINCT s.login) = 1 THEN MAX(s.login) ELSE 0 END
                                  FROM account_snapshots s)
 WHERE login IS NULL;

ALTER TABLE account_deals ALTER COLUMN login SET DEFAULT 0;
ALTER TABLE account_deals ALTER COLUMN login SET NOT NULL;
ALTER TABLE trade_records ALTER COLUMN login SET DEFAULT 0;
ALTER TABLE trade_records ALTER COLUMN login SET NOT NULL;

-- Primary keys become (login, ticket) and (login, id). Nothing references these tables by foreign key.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indrelid
                    WHERE c.relname = 'account_deals' AND i.indisprimary AND array_length(i.indkey::int2[], 1) = 2) THEN
        ALTER TABLE account_deals DROP CONSTRAINT IF EXISTS account_deals_pkey;
        ALTER TABLE account_deals ADD CONSTRAINT account_deals_pkey PRIMARY KEY (login, ticket);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indrelid
                    WHERE c.relname = 'trade_records' AND i.indisprimary AND array_length(i.indkey::int2[], 1) = 2) THEN
        ALTER TABLE trade_records DROP CONSTRAINT IF EXISTS trade_records_pkey;
        ALTER TABLE trade_records ADD CONSTRAINT trade_records_pkey PRIMARY KEY (login, id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_account_deals_login_time ON account_deals (login, ts_utc);
CREATE INDEX IF NOT EXISTS ix_trade_records_login_status ON trade_records (login, status);
CREATE INDEX IF NOT EXISTS ix_account_snapshots_login_time ON account_snapshots (login, captured_at);

-- ---- 2. who linked which account ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mt5_connections (
    id                 VARCHAR(36) NOT NULL PRIMARY KEY,
    user_id            VARCHAR(36) NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    session_id         VARCHAR(36),
    login              BIGINT NOT NULL,
    server             VARCHAR(128) NOT NULL,
    slot               INTEGER NOT NULL,
    status             VARCHAR(16) NOT NULL,
    pid                INTEGER,
    company            VARCHAR(128),
    currency           VARCHAR(16),
    leverage           INTEGER,
    trade_mode         VARCHAR(16),
    error_code         VARCHAR(48),
    error_message      VARCHAR(400),
    last_sync          JSONB,
    created_at         TIMESTAMP WITH TIME ZONE NOT NULL,
    connected_at       TIMESTAMP WITH TIME ZONE,
    heartbeat_at       TIMESTAMP WITH TIME ZONE,
    last_sync_at       TIMESTAMP WITH TIME ZONE,
    disconnected_at    TIMESTAMP WITH TIME ZONE,
    disconnect_reason  VARCHAR(48),
    updated_at         TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT ck_mt5_connections_status
        CHECK (status IN ('CONNECTING', 'CONNECTED', 'DISCONNECTING', 'DISCONNECTED', 'FAILED', 'LOST'))
);
CREATE INDEX IF NOT EXISTS ix_mt5_connections_user_id ON mt5_connections (user_id);
CREATE INDEX IF NOT EXISTS ix_mt5_connections_session_id ON mt5_connections (session_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_mt5_connections_active_slot ON mt5_connections (slot)
    WHERE status IN ('CONNECTING', 'CONNECTED', 'DISCONNECTING');
CREATE UNIQUE INDEX IF NOT EXISTS uq_mt5_connections_active_login ON mt5_connections (login)
    WHERE status IN ('CONNECTING', 'CONNECTED', 'DISCONNECTING');
