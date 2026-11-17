-- AURUM AI - migration 0015: the cloud backup price feed (Twelve Data).
-- When the MT5 PC is offline, the hosted API stores prices from Twelve Data so the site keeps updating. Every tick now
-- says where it came from (mt5 or twelvedata); broker prices always replace backup prices. Additive and safe to re-run.
ALTER TABLE market_ticks ADD COLUMN IF NOT EXISTS source VARCHAR(16) NOT NULL DEFAULT 'mt5';
CREATE INDEX IF NOT EXISTS ix_market_ticks_source_time ON market_ticks (source, ts_utc);

CREATE TABLE IF NOT EXISTS backup_price_status (
    instrument_id     INTEGER PRIMARY KEY REFERENCES instruments (id),
    provider          VARCHAR(16) NOT NULL DEFAULT 'twelvedata',
    provider_symbol   VARCHAR(32),
    last_attempt_at   TIMESTAMPTZ,
    last_success_at   TIMESTAMPTZ,
    last_price_at     TIMESTAMPTZ,
    last_outcome      VARCHAR(24),
    last_error        TEXT,
    unavailable_until TIMESTAMPTZ,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
