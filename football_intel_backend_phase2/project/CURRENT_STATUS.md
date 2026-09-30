# CURRENT STATUS

## Completed phases

| Phase | Content | Status |
|---|---|---|
| 0 | Full audit of pre-existing university ML project (notebook, datasets, artifacts) | ✅ complete |
| 1 | Architecture design + multi-source data strategy research | ✅ complete |
| 1 (impl.) | DB schema, provider abstraction, EA FC26 + transfermarkt_dataset providers, validator, normalizer, identity matching engine, review queue (in-memory) | ✅ complete |
| 2.1 | DB loaders for all normalized types (player_field_values, ea_fc26_attributes, market_value_history, transfers, appearances) | ✅ complete |
| 2.2 | Pipeline orchestration (`scripts/run_ingestion.py`), config, ingestion report | ✅ complete |
| 2.3 | Performance batching (real bulk insert fix) + multi-key identity-matching blocking | ✅ complete |
| 2.4 | Persistent (DB-backed) review queue, non-destructive resolve/promote flow | ✅ complete |
| 2.5 | Coverage/data-quality reporting (`reporting/coverage_report.py`) | ✅ complete |
| 2.6 (a/b/c) | Wikidata enrichment: provider, normalizer, loader, matching reuse, orchestrator integration, conflict protection, final validation | ✅ complete |

**Not started**: Phase 3 (ML integration), backend API/service layer,
frontend, UI. See `NEXT_PHASE.md`.

## Test count

**190 tests, all passing**, across 17 test files. See `TEST_STATUS.md`
for the full breakdown and what each category actually exercises.

## Real-data validation status

This is not a design-only or mocked-only codebase. The following has
been run against **real data**, not synthetic fixtures alone:

- The full, real 16,107-player EA FC26 dataset (`fc26_merged_clean.csv`,
  itself verified in Phase 0 to be byte-for-byte reproducible from the
  4 original raw EA/FC26 source files).
- A full pipeline run: `python3 scripts/run_ingestion.py` — fetches,
  validates, normalizes, matches, persists, backfills, enriches, and
  reports in one command, against all 16,107 real EA players + the
  transfermarkt sample + the wikidata sample.
- A real, deliberately-injected mid-enrichment failure at full 16,107-
  record scale, confirming rollback isolation (enrichment rolls back;
  already-committed core EA/transfermarkt data is untouched).
- A real idempotency rerun at full scale, confirming no duplication of
  non-append-only data and correct append-only growth where intended.

## Providers implemented

| Provider | Mode | Status |
|---|---|---|
| `ea_fc26` | reads real local CSV | ✅ real data, 16,107 players |
| `transfermarkt_dataset` | reads local CSV (static CC0 dump format) | ⚠️ sample fixture only (5 players) — see `KNOWN_ISSUES.md` |
| `wikidata` | reads local CSV fixture; live-SPARQL-ready architecture | ⚠️ sample fixture only (5 players, 1 fully real+verified) — see `KNOWN_ISSUES.md` |

## Migrations (all applied, in order)

```
0001_init.sql                              — core schema (11 tables)
0002_review_queue.sql                      — review_queue table + identity_matches grouping columns
0003_source_player_id_link.sql             — player_id_in_source on time-series tables (bug fix)
0004_allow_null_player_uid_pre_match.sql   — relax NOT NULL (bug fix)
0005_ea_attributes_dataset_version.sql     — separate dataset_version from model_version (bug fix)
0006_player_wikidata_links.sql             — player_wikidata_links table + v_players view (bug fix — see below)
```
Three of these six migrations exist specifically because a **real bug
was found while implementing against real data**, not because the
schema was planned wrong from the start and never tested. See
`KNOWN_ISSUES.md` and `DATABASE_SCHEMA.md` for details on each.

## Known limitations (see `KNOWN_ISSUES.md` for full detail)

- transfermarkt_dataset and wikidata are both sample fixtures (5 players
  each), not the real full datasets — sandbox network restrictions
  prevented fetching the real ~50k-player CC0 dump or querying live
  Wikidata.
- No injury data source has been approved/found (documented since Phase
  0/1) — `injury_data_status` is honestly `NO_SOURCE_AVAILABLE` for
  every player, by design, not a gap to fix blindly.
- No ML model integration yet — `predicted_value_eur` is always NULL.
- `position` is collected but not used in identity-matching scoring
  (deliberate deferral, not a bug).

## Exact recommended next step

Build a backend API/service layer (see `NEXT_PHASE.md` for full
reasoning) **before** any frontend/UI work, and **before** the real
transfermarkt/wikidata data sources are swapped in — the API's contract
should be designed against the schema and data shapes documented here,
which are already stable and tested.
