-- AURUM AI - migration 0013: widen orch_events.status.
-- The column was VARCHAR(12) but the event router writes 'no_subscribers' (14 characters), so on PostgreSQL the first
-- event without a subscriber failed with "value too long". SQLite does not enforce lengths, which hid it in tests.
-- Idempotent: setting the same type again is a no-op.
ALTER TABLE orch_events ALTER COLUMN status TYPE VARCHAR(20);
