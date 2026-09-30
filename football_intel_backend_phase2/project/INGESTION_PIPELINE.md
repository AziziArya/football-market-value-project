# INGESTION PIPELINE

## Entrypoint

```
python3 scripts/run_ingestion.py [options]
```
or programmatically: `run_pipeline(config: PipelineConfig) -> IngestionReport`
in `scripts/run_ingestion.py`.

CLI options:
```
--db-path PATH
--providers "ea_fc26,transfermarkt_dataset,wikidata"   (comma-separated)
--transfermarkt-dataset-version VERSION
--continue-on-error         (sets stop_on_provider_failure=False)
--report-dir PATH
```

## Configuration (`config/settings.py`)

`PipelineConfig` dataclass, overridable via environment variables
(`FOOTBALL_INTEL_DB_PATH`, `FOOTBALL_INTEL_PROVIDERS`,
`FOOTBALL_INTEL_EA_CSV_PATH`, `FOOTBALL_INTEL_TM_DUMP_DIR`,
`FOOTBALL_INTEL_TM_DATASET_VERSION`, `FOOTBALL_INTEL_STOP_ON_FAILURE`,
`FOOTBALL_INTEL_REPORT_DIR`) or CLI flags (CLI wins over env, env wins
over defaults).

Key fields: `db_path`, `enabled_providers` (tuple, validated against
`ALL_PROVIDERS = ("ea_fc26", "transfermarkt_dataset", "wikidata")`),
`ea_fc26_csv_path`, `transfermarkt_dump_dir`,
`transfermarkt_dataset_version`, `wikidata_fixture_path`,
`stop_on_provider_failure` (default `True`), `report_dir`.

`ENRICHMENT_PROVIDERS = ("wikidata",)` — this tuple is what makes
wikidata's failure non-blocking; any provider name in it is routed to
the separate, non-blocking enrichment step instead of the core loop.

## Provider execution model

```
PROVIDER_REGISTRY = {
    "ea_fc26": _build_ea_fc26,
    "transfermarkt_dataset": _build_transfermarkt_dataset,
    "wikidata": _build_wikidata,
}
```
This dict is the ONLY place concrete provider classes are imported/named
in the orchestrator. Core providers (`ea_fc26`, `transfermarkt_dataset`)
run in a loop that calls only `Provider.health_check()`/`.fetch()`.
Wikidata is filtered out of that loop (`core_provider_names` vs
`enrichment_provider_names` split at the top of `run_pipeline()`) and
run separately, after matching.

## Failure handling

| Failure point | Core provider (EA/TM) | Enrichment provider (wikidata) |
|---|---|---|
| `health_check()` returns False | recorded in `provider_errors`; aborts if `stop_on_provider_failure` | recorded in `enrichment_errors`; pipeline continues |
| `health_check()` raises | same as above (caught, never propagates raw) | same (caught) |
| `fetch()`/loader raises mid-stream | `ROLLBACK` that provider's transaction; recorded in `provider_errors`; aborts if `stop_on_provider_failure` | `ROLLBACK` the enrichment's own transaction; recorded in `enrichment_errors`; core data (already committed earlier) is untouched |
| Unknown provider name in config | `ValueError` raised at `PipelineConfig` construction time (fails fast, before any DB work) | n/a |

`report.success = (len(report.provider_errors) == 0)` — enrichment
errors never factor into this.

**Verified with a real, deliberately-injected failure at full 16,107-
record production scale** (not just small test fixtures): a mid-
enrichment exception rolled back exactly the enrichment's writes
(`player_wikidata_links`, wikidata-sourced `player_field_values` rows,
`player_images`) while every core table (`players`,
`ea_fc26_attributes`, `market_value_history`, etc.) remained byte-for-
byte identical to a snapshot taken before the failure was injected.

## Transaction boundaries

- Each CORE provider's ingest gets its own `BEGIN`/`COMMIT`/`ROLLBACK`
  block (fetch is outside the transaction — reading a file/API has
  nothing to roll back; only the DB writes are wrapped).
- Matching + `persist_identity_matches` + `create_player_records` +
  `persist_review_queue` + `backfill_player_uid` currently run OUTSIDE
  an explicit transaction wrapper in `run_pipeline()` (each individual
  function issues its own statements) — this is a known gap, see
  `KNOWN_ISSUES.md`.
- Wikidata enrichment has its OWN separate `BEGIN`/`COMMIT`/`ROLLBACK`,
  entirely independent of the core providers' transactions — this is
  what makes the rollback-isolation guarantee above possible.

## Ingestion report (`IngestionReport` dataclass)

```python
started_at, finished_at, success
fetched_per_source, valid_per_source, invalid_per_source   # per provider
normalized_counts            # identity_fields, ea_attributes, market_value, transfer, appearance
matching_counts               # MATCHED, PROBABLE_MATCH, AMBIGUOUS, UNMATCHED
db_row_counts_after             # one COUNT(*) per table, taken at the end of the run
provider_errors                  # core providers only; non-empty -> success=False
enrichment_counts                 # linked, already_linked, conflict_skipped, no_confident_match
enrichment_errors                  # wikidata only; NEVER affects success
```
`.save(report_dir)` writes a timestamped JSON file to
`data/processed/unified/reports/`. `.print_summary()` logs a
human-readable summary. Both are called by the CLI (`main()`); library
callers using `run_pipeline()` directly get the dataclass back and
decide what to do with it themselves.

## Performance / batching behavior (Phase 2.3)

**Real bug found and fixed**: DuckDB's Python `executemany()` is NOT
vectorized — empirically ~1ms/row regardless of batch size (measured:
96,642 rows took 106 seconds). The fix (`ingestion/loader.py::_bulk_insert`,
mirrored in `matching/loader.py`) registers a `pandas.DataFrame` and
issues a single `INSERT INTO ... SELECT ... FROM registered_df`, which
uses DuckDB's actual vectorized engine.

**Measured improvement** (`scripts/benchmark_loaders.py`, real
16,107-record EA data):
```
OLD (per-record round trips / naive executemany): 104.66s
NEW (accumulate + real bulk insert):                0.56s
speedup: 185x
```
Full pipeline (all 3 sources + matching + enrichment), one command:
before batching ≈90s, after ≈5-6s.

**Pattern to follow for any new bulk writer**: accumulate all records
for a provider/step in memory (they're small Python objects, not a
memory concern at this scale — tens of thousands of dataclass
instances), then call ONE batch loader function that does the existing-
keys lookup (if deduping) and the `_bulk_insert()` call. Never loop
calling a single-row `INSERT` or `executemany()` per application-level
record for anything that will run at real scale.
