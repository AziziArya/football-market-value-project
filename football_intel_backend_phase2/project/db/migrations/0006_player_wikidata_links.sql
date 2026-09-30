-- ═══════════════════════════════════════════════════════════
-- Migration 0006: player_wikidata_links (+ v_players view)
--
-- Found during Phase 2.6b: `UPDATE players SET wikidata_id = ...` FAILS in
-- DuckDB with a foreign-key ConstraintError. players.wikidata_id is UNIQUE
-- (indexed), DuckDB executes an UPDATE of an indexed column as
-- DELETE+INSERT, and the delete is refused because child rows
-- (player_field_values, market_value_history, ...) still reference the
-- player_uid. So a canonical player's wikidata_id can only be set at
-- INSERT time — never afterwards.
--
-- Fix: record enrichment links in their own INSERT-only table, which also
-- gives the link full lineage (status, confidence, matched_on,
-- dataset_version, linked_at) that a bare column could not carry.
--   * PRIMARY KEY (player_uid)  -> a player can never be silently re-linked
--   * UNIQUE (wikidata_id)      -> a QID can never belong to two players
-- players.wikidata_id is left untouched (never overwritten); consumers
-- should read v_players, which exposes the effective wikidata_id.
-- ═══════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS player_wikidata_links (
    player_uid          VARCHAR PRIMARY KEY REFERENCES players(player_uid),
    wikidata_id         VARCHAR NOT NULL UNIQUE,
    match_status        VARCHAR NOT NULL CHECK (match_status IN ('MATCHED', 'PROBABLE_MATCH')),
    match_confidence    DOUBLE NOT NULL,
    matched_on          VARCHAR,
    dataset_version     VARCHAR,
    linked_at           TIMESTAMP NOT NULL
);

CREATE VIEW IF NOT EXISTS v_players AS
SELECT
    p.player_uid,
    p.ea_fc26_id,
    p.transfermarkt_id,
    COALESCE(p.wikidata_id, w.wikidata_id) AS wikidata_id,
    p.thesportsdb_id,
    p.display_name,
    p.created_at,
    p.updated_at
FROM players p
LEFT JOIN player_wikidata_links w ON w.player_uid = p.player_uid;
