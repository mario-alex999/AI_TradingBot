-- AURUM AI - migration 0016: when a signal triggered and the price it closed at.
-- The AI Signals page shows, for every signal, whether price reached its entry, and whether it then hit the target or
-- the stop. These outcomes are hypothetical (AURUM places no trades) and come from stored M5 candles. Additive.
ALTER TABLE candidate_outcomes ADD COLUMN IF NOT EXISTS filled_at TIMESTAMPTZ;
ALTER TABLE candidate_outcomes ADD COLUMN IF NOT EXISTS exit_price DOUBLE PRECISION;
