-- ═══════════════════════════════════════════════════════════
-- Migration 0001: core schema
-- Football Player Intelligence Platform
-- ═══════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS players (
    player_uid          VARCHAR PRIMARY KEY,     -- uuid4 hex string
    ea_fc26_id           INTEGER UNIQUE,
    transfermarkt_id      INTEGER UNIQUE,
    wikidata_id           VARCHAR UNIQUE,
    thesportsdb_id        VARCHAR UNIQUE,
    display_name          VARCHAR NOT NULL,
    created_at             TIMESTAMP NOT NULL,
    updated_at             TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS player_field_values (
    id                   BIGINT PRIMARY KEY,
    player_uid            VARCHAR NOT NULL REFERENCES players(player_uid),
    field_name             VARCHAR NOT NULL,
    field_value             VARCHAR NOT NULL,
    source                 VARCHAR NOT NULL,
    source_record_id        VARCHAR,
    fetched_at              TIMESTAMP NOT NULL,
    dataset_version          VARCHAR,
    is_current               BOOLEAN DEFAULT TRUE,
    confidence               DOUBLE
);

CREATE TABLE IF NOT EXISTS market_value_history (
    id                    BIGINT PRIMARY KEY,
    player_uid             VARCHAR NOT NULL REFERENCES players(player_uid),
    value_eur                BIGINT NOT NULL,
    valuation_date            DATE NOT NULL,
    source                  VARCHAR DEFAULT 'transfermarkt_dataset',
    dataset_version           VARCHAR NOT NULL,
    imported_at               TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS transfers (
    id                    BIGINT PRIMARY KEY,
    player_uid             VARCHAR NOT NULL REFERENCES players(player_uid),
    transfer_date             DATE,
    from_club                 VARCHAR,
    to_club                   VARCHAR,
    fee_eur                   BIGINT,
    is_loan                   BOOLEAN,
    source                   VARCHAR DEFAULT 'transfermarkt_dataset',
    dataset_version            VARCHAR
);

CREATE TABLE IF NOT EXISTS appearances (
    id                    BIGINT PRIMARY KEY,
    player_uid             VARCHAR NOT NULL REFERENCES players(player_uid),
    game_date                 DATE,
    competition                VARCHAR,
    minutes_played              INTEGER,
    goals                      INTEGER,
    assists                    INTEGER,
    source                    VARCHAR DEFAULT 'transfermarkt_dataset',
    dataset_version             VARCHAR
);

CREATE TABLE IF NOT EXISTS injury_records (
    id                    BIGINT PRIMARY KEY,
    player_uid             VARCHAR NOT NULL REFERENCES players(player_uid),
    injury_type                VARCHAR,
    start_date                 DATE,
    expected_return             DATE,
    actual_return               DATE,
    days_out                   INTEGER,
    source                    VARCHAR,
    imported_at                 TIMESTAMP
);

CREATE TABLE IF NOT EXISTS injury_data_status (
    player_uid            VARCHAR PRIMARY KEY REFERENCES players(player_uid),
    status                  VARCHAR NOT NULL CHECK (
        status IN ('NO_SOURCE_AVAILABLE', 'CONFIRMED_NO_INJURIES', 'HAS_RECORDS')
    ),
    checked_at                TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS player_images (
    id                    BIGINT PRIMARY KEY,
    player_uid             VARCHAR NOT NULL REFERENCES players(player_uid),
    image_url                 VARCHAR NOT NULL,
    source                   VARCHAR NOT NULL,
    license                   VARCHAR,
    attribution                VARCHAR,
    is_primary                 BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS ea_fc26_attributes (
    ea_fc26_id             INTEGER PRIMARY KEY,
    player_uid               VARCHAR REFERENCES players(player_uid),
    overall_rating              INTEGER,
    potential                   INTEGER,
    value_eur_ingame              BIGINT,   -- EA in-game value, NEVER shown as real market value
    predicted_value_eur            BIGINT,   -- our ML model output, separate column
    model_version                 VARCHAR,
    raw_json                      VARCHAR    -- full row from fc26_merged_clean.csv, for traceability
);

-- ═══════════════════════════════════════════════════════════
-- identity matching results — every candidate pair, no silent merges
-- ═══════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS identity_matches (
    id                    BIGINT PRIMARY KEY,
    ea_fc26_id              INTEGER,
    transfermarkt_id          INTEGER,
    wikidata_id                VARCHAR,
    match_status               VARCHAR NOT NULL CHECK (
        match_status IN ('MATCHED', 'PROBABLE_MATCH', 'AMBIGUOUS', 'UNMATCHED')
    ),
    match_confidence            DOUBLE NOT NULL,
    matched_on                   VARCHAR,   -- comma-joined list, e.g. 'name,dob,nationality'
    matched_at                    TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version                VARCHAR PRIMARY KEY,
    applied_at                TIMESTAMP NOT NULL
);
