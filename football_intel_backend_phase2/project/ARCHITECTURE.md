# ARCHITECTURE

## High-level shape

```
Source A (EA FC26)         ─┐
Source B (transfermarkt)    ├─▶ Provider ──▶ Validator ──▶ Normalizer ──▶ Loader(s) ──▶ DuckDB
Source C (wikidata)         ─┘   (raw)       (schema)      (canonical      (persistence)
                                              per source,    fields,
                                              per type)      per-type
                                                              records)
                                                                    │
                                                                    ▼
                                            Identity Matching (EA ↔ transfermarkt)
                                                    │
                                    ┌───────────────┼────────────────┐
                                    ▼               ▼                ▼
                              players table   review_queue    identity_matches
                              (canonical)     (AMBIGUOUS,      (full audit trail,
                                               human review)    every candidate)
                                    │
                                    ▼
                         Wikidata Enrichment (non-blocking, runs AFTER matching)
                                    │
                                    ▼
                         player_wikidata_links, player_field_values(wikidata),
                         player_images
                                    │
                                    ▼
                         Coverage/Data-Quality Reporting (read-only)
```

## Directory layout (of the actual project, not this handoff package)

```
(this repository's root)/
├─ data/
│  ├─ raw/                    # immutable, as-received data
│  │  ├─ ea_fc26/              # real fc26_merged_clean.csv (16,107 players)
│  │  ├─ transfermarkt_dataset/sample_2026-07-06/   # SAMPLE FIXTURE, labeled
│  │  └─ wikidata/sample/                            # SAMPLE FIXTURE, labeled
│  ├─ processed/unified/       # the DuckDB file + generated reports
│  └─ mappings/                 # (reserved, currently unused — identity_matches
│                                  table absorbed this role)
├─ db/
│  ├─ migrations/*.sql          # 6 migrations, applied in filename order
│  └─ migrate.py                # idempotent migration runner
├─ ingestion/
│  ├─ providers/                # base.py (ABC) + one file per source
│  ├─ validator.py               # schema/range checks, per (source, record_type)
│  ├─ normalizer.py               # raw fields -> canonical dataclasses
│  ├─ loader.py                    # DB writes for normalized output (batch + per-record variants)
│  └─ injury_loader.py              # the one honest "NO_SOURCE_AVAILABLE" writer
├─ matching/
│  ├─ schema.py                    # IdentityInput, MatchThresholds, MatchCandidate, MatchResult
│  ├─ identity.py                   # scoring, classification, blocking, match_one/match_all,
│  │                                  from_*_record adapters, build_identity_input_for_player
│  ├─ loader.py                      # identity_matches + players table persistence
│  ├─ review_queue.py                 # in-memory review queue (Phase 1.5)
│  ├─ review_persistence.py            # DB-backed review queue (Phase 2.4)
│  └─ wikidata_enrichment.py            # enrich_players_with_wikidata() (Phase 2.6)
├─ reporting/
│  └─ coverage_report.py                # read-only data-quality report
├─ scripts/
│  ├─ run_ingestion.py                   # THE pipeline entrypoint
│  ├─ generate_coverage_report.py         # coverage report CLI
│  └─ benchmark_loaders.py                 # old-vs-new loader performance comparison
├─ config/
│  └─ settings.py                          # PipelineConfig, env var overrides
└─ tests/                                   # 190 tests, mirrors the structure above
```

## Important architectural boundaries (do not violate these)

1. **Providers are isolated.** `scripts/run_ingestion.py`'s
   `PROVIDER_REGISTRY` is the ONLY place concrete provider classes are
   named. All orchestration code calls only the `Provider` ABC
   (`health_check()`, `fetch()`). Adding a 4th source = one new provider
   file + one registry entry, zero changes to validator/normalizer/
   matcher/orchestrator control flow.

2. **Validation and normalization are pure functions.** Neither touches
   the database. `validate(record) -> ValidationResult`,
   `normalize(record) -> NormalizationResult`. Both dispatch on
   `(source, record_type)` and raise/reject rather than guess for any
   combination they don't recognize.

3. **Value fields are never merged.** EA's in-game value
   (`ea_fc26_attributes.value_eur_ingame`), transfermarkt's real market
   value (`market_value_history.value_eur`), and the future ML
   prediction (`ea_fc26_attributes.predicted_value_eur`) are three
   separate columns in separate contexts. `normalizer.py` hard-asserts
   against a `FORBIDDEN_GENERIC_FIELD_NAMES` set to make it structurally
   hard to reintroduce a generic "value" field by accident.

4. **Identity matching is one engine, reused everywhere.** The same
   `score_pair()` / `match_one()` / `MatchThresholds` used for EA↔
   transfermarkt matching (Phase 1) is reused unchanged for player↔
   wikidata matching (Phase 2.6) via adapter functions
   (`from_wikidata_record`, `build_identity_input_for_player`). There is
   no second scoring implementation anywhere in the codebase.

5. **Matching never destructively merges.** A match produces a
   `MatchCandidate` with a status (`MATCHED` / `PROBABLE_MATCH` /
   `AMBIGUOUS` / `UNMATCHED`). Only the first two are ever used to link
   records. `AMBIGUOUS` always goes to a review queue. Original source
   IDs are never deleted or overwritten — see `DATABASE_SCHEMA.md` for
   how this is enforced at the DB constraint level, not just in
   application code.

6. **Ingestion happens before matching; enrichment happens after.** EA
   and transfermarkt data is loaded with `player_uid = NULL` (matching
   hasn't run yet) and backfilled once `players` exists. Wikidata
   enrichment runs the opposite way — it iterates the already-canonical
   `players` table, so it always has `player_uid` at write time. This is
   why wikidata needed no `NOT NULL` relaxation the way EA/transfermarkt
   tables did (see `DATABASE_SCHEMA.md` migration 0004 vs 0006).

7. **Enrichment (wikidata) is non-blocking; core ingestion (EA,
   transfermarkt) is not.** A core provider failing aborts the pipeline
   before matching/persistence (`stop_on_provider_failure`, default
   True). A wikidata failure is caught, logged, recorded in
   `report.enrichment_errors`, and never affects `report.success` or
   the already-committed core data. See `INGESTION_PIPELINE.md`.

8. **No fabrication, anywhere.** Missing values stay `NULL` through the
   whole pipeline — validator, normalizer, loader, matcher, and reporter
   all treat "no data" as a distinct, honest state, never inferred or
   defaulted. The clearest example: `injury_data_status` exists
   specifically to distinguish "we don't know" from "confirmed healthy",
   because no injury data source has been approved.

## Reporting is strictly read-only

`reporting/coverage_report.py` issues only `SELECT`/`COUNT`/`GROUP BY`
queries. It cannot affect matching behavior or write data by
construction — there is no write path in the module at all.
