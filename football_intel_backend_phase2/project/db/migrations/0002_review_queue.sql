-- ═══════════════════════════════════════════════════════════
-- Migration 0002: review queue persistence + match grouping
-- ═══════════════════════════════════════════════════════════

ALTER TABLE identity_matches ADD COLUMN match_group_id VARCHAR;
ALTER TABLE identity_matches ADD COLUMN is_best BOOLEAN DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS review_queue (
    id                      BIGINT PRIMARY KEY,
    match_group_id            VARCHAR NOT NULL,
    query_source                VARCHAR NOT NULL,
    query_source_record_id       VARCHAR NOT NULL,
    candidates_snapshot            VARCHAR NOT NULL,
    confidence                       DOUBLE NOT NULL,
    status                           VARCHAR NOT NULL CHECK (status IN ('PENDING', 'RESOLVED')),
    reviewer_decision                  VARCHAR,
    created_at                          TIMESTAMP NOT NULL,
    resolved_at                          TIMESTAMP
);
