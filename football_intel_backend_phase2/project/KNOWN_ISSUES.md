# KNOWN ISSUES

Only real, verified limitations are listed here. Nothing hypothetical.

## 1. transfermarkt_dataset and wikidata are sample fixtures, not real data

**What**: Both providers currently read small local CSV fixtures (5
players each), not the real datasets.
**Why**: The sandbox this backend was built in has a restricted network
whitelist that does not include the CC0 dataset's host
(`r2.dev`/GitHub release assets for `dcaribou/transfermarkt-datasets`)
or `query.wikidata.org`/`commons.wikimedia.org`.
**What's real vs. illustrative in each fixture**:
- transfermarkt sample (`data/raw/transfermarkt_dataset/sample_2026-07-06/`):
  player identifiers (real transfermarkt_id, name, nationality, position,
  club) are real public facts; exact market values/transfer fees/
  appearance stats are illustrative (documented in that folder's own
  README.md).
- wikidata sample (`data/raw/wikidata/sample/players.csv`): ONE row
  (Erling Haaland, QID Q28967995) is fully real and verified via web
  search at build time, including a real image filename and
  photographer attribution. The other 4 rows use obviously-fake QID
  placeholders (`Q00000001`-`Q00000004`) specifically so they can never
  be mistaken for genuine identifiers; their name/DOB/nationality are
  real-world facts already established elsewhere in this project, not
  independently re-verified against live Wikidata.
**Impact**: Matching/enrichment logic is fully implemented and tested,
but has only ever been exercised at real scale on the EA side. The
transfermarkt/wikidata side of every match is small.
**Fix needed**: Either (a) whitelist the required domains and implement
the live-fetch code paths (the provider architecture is already
designed for this — see the `SPARQL_TEMPLATE` docstring in
`ingestion/providers/wikidata.py` and the sample folder READMEs for
exact swap-in instructions), or (b) have a human download the real
dumps and place them in the expected directory structure — no code
changes needed either way.

## 2. No injury data source approved

**What**: `injury_data_status` is `NO_SOURCE_AVAILABLE` for every
player, always.
**Why**: Researched in Phase 0/1 — no free, legally-clean, structured
injury data source was found. Transfermarkt's own injury pages were
excluded from scope (scraping that specific site was ruled out on ToS
grounds, separate from the CC0 dataset which is a different, explicitly
permissively-licensed derivative product).
**Impact**: The player intelligence platform currently cannot show any
injury information. This is a deliberate, honest gap — not a bug to
silently work around.
**Fix needed**: Product/legal decision on an injury data source is a
prerequisite; the schema (`injury_records` table) and the
distinguishing-state pattern (`NO_SOURCE_AVAILABLE` /
`CONFIRMED_NO_INJURIES` / `HAS_RECORDS`) are already in place and ready
to receive real data whenever a source is approved.

## 3. No ML model integration

**What**: `ea_fc26_attributes.predicted_value_eur` is always `NULL`.
**Why**: Out of scope for Phase 2 (data/ingestion layer only). The
original university project's CatBoost model exists and was fully
audited in Phase 0 (100% reproducibility-verified from raw data), but
has not been wired into this pipeline.
**Fix needed**: See `NEXT_PHASE.md` — this is explicitly deferred, not
forgotten.

## 4. Core matching/persistence steps run without an explicit outer transaction

**What**: In `run_pipeline()`, the sequence `persist_identity_matches()`
→ `create_player_records()` → `persist_review_queue()` →
`backfill_player_uid()` is NOT wrapped in one `BEGIN`/`COMMIT`/
`ROLLBACK` block the way each provider's ingestion step is.
**Impact**: If one of these calls raised partway through (none has been
observed to, in any real or test run), the DB could be left in a
partially-updated state (e.g. `identity_matches` written but
`create_player_records` not yet run) rather than cleanly rolling back.
**Severity**: Low in practice — every real and test run has completed
this sequence without error — but it's a real gap relative to the
transactional discipline used everywhere else in the pipeline (each
provider's ingest, and wikidata enrichment, both use explicit
transactions).
**Fix needed**: Wrap this sequence in its own `BEGIN`/`COMMIT`/
`ROLLBACK`, matching the pattern already used for providers and for
wikidata enrichment.

## 5. `position` field collected but unused in matching

**What**: `IdentityInput.position` exists and is populated by every
`from_*_record()` adapter, but `score_pair()` never reads it.
**Why**: Deliberately deferred per an earlier explicit instruction
("position — secondary signal", optional). Not a bug or oversight.
**Fix needed**: None required; wire it in as a tie-breaker signal if
future matching precision work calls for it.

## 6. Single enrichment-provider dispatch is not fully generic yet

**What**: `run_pipeline()`'s enrichment loop calls
`_run_wikidata_enrichment()` directly for every name in
`enrichment_provider_names`, rather than dispatching through a registry
of enrichment-specific handler functions the way core providers do
through `INGEST_FUNCTIONS`.
**Impact**: None today (wikidata is the only enrichment provider). Adding
a second enrichment provider (e.g. `thesportsdb`) would require adding
an `if provider_name == "thesportsdb": ...` branch rather than a clean
registry lookup.
**Fix needed**: Generalize to an `ENRICHMENT_FUNCTIONS` registry dict,
mirroring `INGEST_FUNCTIONS`'s pattern, when/if a second enrichment
provider is added. Small, contained change.

## 7. Coverage report has not been run against a full-scale (16,107+ real
   transfermarkt/wikidata) dataset

**What**: `reporting/coverage_report.py` is real-data-smoke-tested only
against the small fixtures.
**Impact**: The report's SQL is straightforward (`COUNT`/`GROUP BY`) and
has no reason to behave differently at scale, but this has not been
empirically confirmed the way the ingestion pipeline's performance was
(Phase 2.3's benchmark).
**Fix needed**: Re-run once real transfermarkt/wikidata data is
available; expected to be a non-issue but not yet proven at scale.

## 8. (FIXED) Wikidata fixture rows were labelled `live:<date>`

**What was wrong**: `WikidataProvider.fetch()` only ever reads a local fixture, but stamped
`dataset_version = "live:<today>"`. **Fix**: stamped `fixture:<today>` (later made `fixture:<content-hash>` by G9); `live:` is reserved for a future
path that really queries the external endpoint (not implemented).
**Residual**: a database built BEFORE this fix keeps the old `live:` labels on already-written rows
(append-only tables). Rebuild the DB (`db/migrate.py` + `scripts/run_ingestion.py` on a fresh file) to clear them.

## 9. (OPEN - planned separate step) `ea_fc26_attributes.raw_json` is never populated

`ingestion/loader.py` inserts the `raw_json` column but the normalizer does not supply it: NULL for all 16,107 rows.
Detailed EA attributes (pace, finishing, dribbling, ...) are therefore not in the DB; only `overall_rating`, `potential`,
`value_eur_ingame`. Scheduled as "Backend Data Integrity - EA detailed attributes / raw_json" (raw_json vs typed columns
vs both). NOT started. The API contract exposes these attributes only as PLANNED/null.

## 10. (FIXED) G9 - re-running ingestion into the same DB created duplicate rows

**What was wrong**: `load_identity_fields` appended the EA and transfermarkt `player_field_values` rows on every run
(2x, 3x ...), `identity_matches` appended a full new group per EA player on every run, and the Wikidata fixture's
`dataset_version` contained the run date, so a rerun on another day also duplicated its rows. All duplicated rows were
`is_current = TRUE`.
**Fix (no migration)**: idempotency guards in `ingestion/loader.py` (`_drop_already_loaded`) and `matching/loader.py`
(`drop_unchanged_match_results`, wired in `scripts/run_ingestion.py`); the Wikidata fixture version is now a content hash.
Tables stay append-only for CHANGED data; nothing is deleted. Invariants: `tests/test_idempotency/`.
**Residual**: a database built before the fix keeps its old duplicates (never auto-deleted); a rerun does not grow them
and readers collapse them with the latest-per-key rule (`ARCHITECTURE_API.md` section 4). Rebuild to get a clean file.

## 12. (FIXED) G11 - HealthResponse `unavailable` was unreachable

The contract allowed `HealthResponse.status = "unavailable"`, but `/health` answers 503 with a Problem when unhealthy.
Fixed in Step 4.2: the contract enum is `["ok"]` (contract 0.3.0-draft); the service raises DATA_UNAVAILABLE.

## 13. (NEW) G12 - transfermarkt id hidden for canonical players whose link is not MATCHED/PROBABLE

The contract exposes `source_ids.transfermarkt_id` only while the link is MATCHED/PROBABLE_MATCH. A player promoted to
canonical through human review can keep an AMBIGUOUS best match; its id is then hidden. No such player exists in today's data.

## 15. (FIXED) G14 - overall_rating / potential nullable in the database, required by the contract

`ea_fc26_attributes.overall_rating` and `potential` are nullable columns; the contract and the response models require both,
so a NULL would have produced a 500. Fixed (Step 4.3 verification): startup invariant (fail closed) and `PlayerCore.required`
aligned in the contract. Today all 16,107 rows have both.

## 14. (DECIDED) G13 - `canonical_player_uid` is not stable

It is a random uuid4 generated per database build, so every rebuild (build-then-swap) changes it. Decision (Step 4.3): the only
public API identifier is `ea:<ea_fc26_id>`; `p:<uuid>` is never accepted and never returned. The raw field
`identity.canonical_player_uid` is still exposed as a documented INTERNAL value; whether to remove it from the contract, or to make
the uid deterministic (data-integrity step), is still open.

## 11. (RESOLVED) G10 - duckdb pin

`requirements.txt` pins `duckdb==1.5.5`. Verified in two clean virtualenvs (1.5.5 and 1.5.6): full suite passes in both,
a full 16,107-player build gives identical row counts and content hashes, and each version reads the other's file.
Decision: keep 1.5.5, no change.
