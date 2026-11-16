-- AURUM AI - migration 0014: the MT5 account each Module 06 assessment covers.
-- With several people linked, each risk assessment is made for one account (its balance, positions and loss history),
-- and the risk an assessment reserves while it waits for approval counts only against that account.
-- Older rows keep NULL and count against every account, as before. Additive and safe to run more than once.
ALTER TABLE risk_assessments ADD COLUMN IF NOT EXISTS account_login BIGINT;
CREATE INDEX IF NOT EXISTS ix_risk_assessments_account_live
    ON risk_assessments (account_login, state, expires_at);

-- Each account keeps its own day / week / month starting values (the reference for its loss limits). Existing rows
-- belong to an unknown account (0); the next assessment of each account stores its own.
ALTER TABLE risk_period_records ADD COLUMN IF NOT EXISTS account_login BIGINT NOT NULL DEFAULT 0;
ALTER TABLE risk_period_records DROP CONSTRAINT IF EXISTS uq_risk_period_records;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_risk_period_records_account') THEN
        ALTER TABLE risk_period_records
            ADD CONSTRAINT uq_risk_period_records_account UNIQUE (kind, label, basis, account_login);
    END IF;
END $$;
