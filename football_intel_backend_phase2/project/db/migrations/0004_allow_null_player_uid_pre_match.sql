-- ═══════════════════════════════════════════════════════════
-- Migration 0004: allow NULL player_uid on pre-match tables
--
-- Migration 0001 declared player_uid NOT NULL on player_field_values,
-- market_value_history, transfers, and appearances. That's wrong given
-- the actual pipeline order: ingestion loads these tables BEFORE
-- identity matching has run (matching needs the normalized data to
-- score against), so player_uid is legitimately unknown at insert time
-- and filled in later by ingestion/loader.py::backfill_player_uid().
-- Found while writing and testing the Phase 2.1 loaders.
-- ═══════════════════════════════════════════════════════════

ALTER TABLE player_field_values ALTER COLUMN player_uid DROP NOT NULL;
ALTER TABLE market_value_history ALTER COLUMN player_uid DROP NOT NULL;
ALTER TABLE transfers ALTER COLUMN player_uid DROP NOT NULL;
ALTER TABLE appearances ALTER COLUMN player_uid DROP NOT NULL;
