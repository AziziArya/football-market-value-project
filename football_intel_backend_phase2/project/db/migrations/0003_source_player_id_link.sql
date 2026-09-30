-- ═══════════════════════════════════════════════════════════
-- Migration 0003: player_id_in_source on time-series tables
--
-- Found while implementing ingestion/loader.py: market_value_history,
-- transfers, and appearances only had `player_uid` to link to a player —
-- but player_uid doesn't exist until AFTER identity matching runs, and
-- ingestion loads happen before/independently of matching. Without a
-- stable source-native id, rows couldn't be correctly attributed to a
-- player, nor safely deduplicated on re-run. This column lets rows be
-- inserted immediately and backfilled with player_uid once matching
-- has run — same pattern already used by ea_fc26_attributes/
-- player_field_values via ea_fc26_id / source_record_id.
-- ═══════════════════════════════════════════════════════════

ALTER TABLE market_value_history ADD COLUMN player_id_in_source VARCHAR;
ALTER TABLE transfers ADD COLUMN player_id_in_source VARCHAR;
ALTER TABLE appearances ADD COLUMN player_id_in_source VARCHAR;
