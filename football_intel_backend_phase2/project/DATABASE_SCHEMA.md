# DATABASE SCHEMA

Engine: **DuckDB**, single embedded file at
`data/processed/unified/football_intel.duckdb`. Schema is managed by
plain numbered SQL files in `db/migrations/`, applied by `db/migrate.py`
(tracks applied versions in `schema_migrations`; safe to run repeatedly).

## Migration history (why each one exists)

| # | File | Reason |
|---|---|---|
| 0001 | `init.sql` | Initial schema — 11 core tables |
| 0002 | `review_queue.sql` | Adds `review_queue` table + `match_group_id`/`is_best` columns on `identity_matches`, needed to group all candidates scored for one query |
| 0003 | `source_player_id_link.sql` | **Bug fix.** `market_value_history`/`transfers`/`appearances` had no column to hold the source's own player id before `player_uid` exists — impossible to attribute or dedupe rows without it. Added `player_id_in_source`. |
| 0004 | `allow_null_player_uid_pre_match.sql` | **Bug fix.** Those same tables had `player_uid NOT NULL`, contradicting the real pipeline order (ingestion happens before matching resolves `player_uid`). Relaxed to nullable. |
| 0005 | `ea_attributes_dataset_version.sql` | **Bug fix.** `ea_fc26_attributes.model_version` was being reused for two different meanings (which EA snapshot vs. which ML model). Added a dedicated `dataset_version` column. |
| 0006 | `player_wikidata_links.sql` | **Bug fix, more fundamental.** See below — `players.wikidata_id` cannot be `UPDATE`d in DuckDB once child rows exist referencing that `player_uid`. New table + view added instead. |

## Table-by-table reference

### `players`
**Purpose**: the canonical entity table — one row per real-world player,
once identity matching has resolved one.
**Key columns**: `player_uid` (PK, UUID string), `ea_fc26_id` (UNIQUE),
`transfermarkt_id` (UNIQUE), `wikidata_id` (UNIQUE, see warning below),
`thesportsdb_id` (UNIQUE, reserved/unused), `display_name`.
**Behavior**: rows are created ONLY by `matching/loader.py::create_player_records()`
for `MATCHED`/`PROBABLE_MATCH` results, or by
`matching/review_persistence.py::promote_review_decision()` after an
explicit human decision. Never created directly by ingestion.
**⚠️ IMPORTANT CONSTRAINT-BEHAVIOR WARNING**: `wikidata_id` is UNIQUE
(indexed). DuckDB executes an `UPDATE` of an indexed column as an
internal DELETE+INSERT; the DELETE is refused with a `ConstraintError`
if any other table still has a row referencing that `player_uid` via
foreign key (which is always true by the time enrichment runs). **This
means `players.wikidata_id` can only ever be set once at row-creation
time, never updated afterward.** This is why Wikidata linking uses a
separate table (`player_wikidata_links`) instead of updating this
column — see that table's entry below. If you ever need to set
`ea_fc26_id`/`transfermarkt_id` after row creation, expect the exact
same failure mode.

### `player_field_values`
**Purpose**: append-only, source-tagged identity field values (name,
nationality, club, date_of_birth, position). The mechanism that lets EA,
transfermarkt, and wikidata disagree on a field without one overwriting
another.
**Key columns**: `id` (PK), `player_uid` (nullable — see note),
`field_name`, `field_value`, `source`, `source_record_id`, `fetched_at`,
`dataset_version`.
**Behavior**: append-only, always. Two sources disagreeing on
`nationality` produces two rows, not one. `player_uid` is `NULL` at
insert time for EA/transfermarkt rows (matching hasn't run yet) and
filled in by `backfill_player_uid()`; wikidata rows get `player_uid` set
directly at insert time (enrichment runs after matching).

### `ea_fc26_attributes`
**Purpose**: EA FC26's own attributes per player — ratings, potential,
**in-game value** (explicitly `value_eur_ingame`, never a generic
"value"), and a slot for the future ML `predicted_value_eur`.
**Key columns**: `ea_fc26_id` (PK), `player_uid`, `overall_rating`,
`potential`, `value_eur_ingame`, `predicted_value_eur`, `model_version`
(for the future ML model), `dataset_version` (which EA snapshot — added
in migration 0005, was previously conflated with `model_version`).
**Behavior**: upsert by `ea_fc26_id`. Refuses to overwrite with an
older-or-equal `dataset_version` (protects against a stale re-run
clobbering newer data).

### `market_value_history`
**Purpose**: real-world market value over time, from
transfermarkt_dataset ONLY. Never confused with EA's in-game value or
an ML prediction.
**Key columns**: `id` (PK), `player_uid` (nullable), `player_id_in_source`
(added in 0003), `value_eur`, `valuation_date`, `source`,
`dataset_version`, `imported_at`.
**Behavior**: append-only. Deduped on `(player_id_in_source,
valuation_date, source, dataset_version)` so a re-run of the same dump
doesn't duplicate rows, while two different players coincidentally
sharing a value+date are never confused with each other.

### `transfers`, `appearances`
Same pattern as `market_value_history`: append-only, `player_id_in_source`
for pre-match attribution, deduped on natural keys
(`player_id_in_source, transfer_date/game_date, source`).

### `identity_matches`
**Purpose**: full audit trail of every candidate pair ever scored during
matching — not just the winner.
**Key columns**: `id` (PK), `ea_fc26_id`, `transfermarkt_id`,
`wikidata_id`, `match_status`, `match_confidence`, `matched_on`,
`matched_at`, `match_group_id` (groups all candidates from one
`match_one()` call), `is_best` (flags the chosen candidate).
**Behavior**: append-only, deliberately. Every pipeline run appends a
fresh full set — this table grows on every rerun by design (it's an
audit log, not current state). `is_best` + `match_group_id` were added
in migration 0002 specifically so `review_queue` and reporting could
find "the one that mattered" without re-deriving it.

### `review_queue`
**Purpose**: durable queue of `AMBIGUOUS` matches awaiting human
decision.
**Key columns**: `id` (PK), `match_group_id` (join back to
`identity_matches`), `query_source`, `query_source_record_id`,
`candidates_snapshot` (JSON, denormalized convenience copy — NOT the
source of truth), `confidence`, `status` (`PENDING`/`RESOLVED`),
`reviewer_decision` (JSON, filled only by `resolve()`), `created_at`,
`resolved_at`.
**Behavior**: `persist_review_queue()` only ever INSERTs `PENDING` rows,
deduped by `(query_source, query_source_record_id)` so a rerun doesn't
reopen or duplicate a case a human already has open or closed.
`resolve()` UPDATEs status/decision but never touches `players`.
`promote_review_decision()` is a separate, explicit step that reuses
`create_player_records()`'s exact conflict-refusal logic.

### `injury_records`, `injury_data_status`
**Purpose**: honestly represent "we have no injury data source" as a
distinct state from "confirmed no injuries". No injury provider exists.
**Key columns** (`injury_data_status`): `player_uid` (PK),
`status` (CHECK constraint: `NO_SOURCE_AVAILABLE` |
`CONFIRMED_NO_INJURIES` | `HAS_RECORDS`), `checked_at`.
**Behavior**: `mark_no_injury_source_available()` is the only writer
today, and it only ever inserts `NO_SOURCE_AVAILABLE` for a player with
no existing row — it never downgrades an existing
`CONFIRMED_NO_INJURIES`/`HAS_RECORDS` row (tested explicitly).
`injury_records` exists in the schema but has no writer yet (no source).

### `player_images`
**Purpose**: player photos, with license/attribution metadata that is
`NULL`, never guessed, when a source doesn't supply it.
**Key columns**: `id` (PK), `player_uid`, `image_url`, `source`,
`license`, `attribution`, `is_primary`.
**Behavior**: `load_player_image()` (in `ingestion/loader.py`) resolves a
Commons filename into a `Special:FilePath` URL (percent-encoded — a real
bug where spaces weren't encoded was found and fixed in Phase 2.6b).
Deduped on `(player_uid, image_url, source)` — a re-run doesn't insert
the same image twice. `is_primary=True` only for a player's first image;
never reassigned afterward.

### `player_wikidata_links` (added in migration 0006)
**Purpose**: the actual mechanism for linking a `players` row to a
Wikidata QID, because `players.wikidata_id` cannot be updated after
creation (see the warning under `players` above).
**Key columns**: `player_uid` (**PRIMARY KEY** — a player can be linked
at most once, enforced by the database, not just application code),
`wikidata_id` (**UNIQUE** — a QID can belong to at most one player,
same enforcement), `match_status` (CHECK: `MATCHED`|`PROBABLE_MATCH`
only — `AMBIGUOUS`/`UNMATCHED` links are structurally impossible),
`match_confidence`, `matched_on`, `dataset_version`, `linked_at`.
**Behavior**: INSERT-only. There is no UPDATE path in the code — a
conflicting link attempt is detected in application code (`_link_state()`
in `matching/wikidata_enrichment.py`) BEFORE the insert is attempted, and
the database's own PK/UNIQUE constraints are a second, structural line
of defense if application logic were ever bypassed.

### `v_players` (VIEW, added in migration 0006)
**Purpose**: read-side convenience. Exposes
`COALESCE(players.wikidata_id, player_wikidata_links.wikidata_id)` as a
single `wikidata_id` column, so consumers don't need to know about the
two-table split. **Read from this view, not `players`, if you need the
current Wikidata link.**

### `schema_migrations`
Tracks applied migration filenames. Not touched by application code
directly, only by `db/migrate.py`.

## Cross-cutting rules that apply to every table above

- **Source lineage**: every row that can plausibly come from more than
  one source carries a `source` column (and usually `dataset_version`).
- **Append-only vs. upsert** is a deliberate per-table decision, not a
  blanket policy — see each table's "Behavior" note above.
- **No table is ever silently overwritten across sources.** Conflicting
  values from two sources are two rows, not a merge.
