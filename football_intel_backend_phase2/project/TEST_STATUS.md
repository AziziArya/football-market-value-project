# TEST STATUS

**190 tests, 0 failures**, run via `python3 -m pytest tests/`.
Runtime: ~25-35 seconds for the full suite.

This document explains what each category actually exercises — not
just that tests exist.

## By file

| File | Count | What it actually tests |
|---|---|---|
| `test_providers/test_base.py` | 4 | `Provider` ABC can't be instantiated directly; `StaticDatasetProvider` health_check contract |
| `test_providers/test_ea_fc26.py` | 7 | Real 16,107-row fetch, correct row count, known real player (Salah id 209331) present, missing-file guard, raw-field contract (no premature renaming) |
| `test_providers/test_transfermarkt_dataset.py` | 16 | Real fixture parsing, all 4 record types, partial-dump handling, **no-network-call guard** (monkeypatches `socket.socket` to raise), immutability of `RawRecord` |
| `test_providers/test_wikidata.py` | 9 | Fixture parsing, honest `health_check()=False` without a fixture (never fakes success), the one real verified record (Haaland), the 4 illustrative-only records have NULL image fields, no-network-call guard |
| `test_validator/test_validator.py` | 10 | Required/type/range checks per schema, unknown-schema rejection (not silently passed), never mutates the input payload |
| `test_normalizer/test_normalizer.py` | 18 | **The value-field separation guarantee** (EA `value_eur` -> `value_eur_ingame` exclusively, transfermarkt market value never collides with it), conflicting-source fields both preserved (not merged), no injury-fabrication function exists at all, wikidata identity fields never include club/position |
| `test_normalizer/test_loader.py` | 19 | Append-only vs. upsert behavior per table, dedup correctness (including the "two different players, same value+date" case), `backfill_player_uid` linking and honest-NULL-when-unmatched, wikidata image loading (URL encoding, license/attribution NULL handling, primary-image logic) |
| `test_normalizer/test_injury_loader.py` | 4 | Marks every player, idempotent, **never downgrades** an existing `CONFIRMED_NO_INJURIES`/`HAS_RECORDS` row |
| `test_matching/test_identity.py` | 20 | Every match status (exact/probable/ambiguous/unmatched/conflicting-identity), a REAL nickname case (Vini Jr.), configurable thresholds, multi-key blocking recall (including a real cross-signal case), classification-unchanged-by-blocking guard |
| `test_matching/test_loader.py` | 7 | `identity_matches` persistence, `match_group_id`/`is_best` consistency after ambiguity-downgrade, `players` creation only for MATCHED/PROBABLE, non-destructive conflict refusal, idempotent promotion |
| `test_matching/test_review_queue.py` | 5 | In-memory queue never auto-resolves, resolve() rejects an uninvestigated candidate |
| `test_matching/test_review_persistence.py` | 12 | DB-backed queue: persist/list/resolve/promote as 3 separate steps, dedup on rerun, resolve-twice rejected, promote-before-resolve rejected, real conflict refusal |
| `test_matching/test_wikidata_matching.py` | 14 | Adapter correctness (`from_wikidata_record`, `build_identity_input_for_player`), and **explicit reuse verification**: no wikidata-specific thresholds/scoring exist anywhere in `matching/identity.py`, enrichment calls the literal same `DEFAULT_THRESHOLDS` object |
| `test_matching/test_wikidata_enrichment.py` | 17 | Conflict protection (3 distinct conflict shapes), no-fabrication (zero/multiple candidates, DOB conflict, unrelated person), missing-field NULL handling, idempotent rerun, DB constraint-level protection (`ConstraintException` on attempted re-link/QID-sharing) |
| `test_pipeline/test_run_ingestion.py` | 8 | Full real pipeline smoke test, missing-provider abort behavior, `--continue-on-error`, idempotent rerun (append-only tables double, upserted tables don't), **real 16,107-record performance regression guard (<30s ceiling)** |
| `test_pipeline/test_wikidata_orchestration.py` | 11 | Enrichment runs only after matching+players exist (verified by inspecting DB state from inside a monkeypatched spy), non-blocking failure modes (missing fixture, fetch exception, mid-enrichment exception), core-failure-still-aborts-before-enrichment, enrichment errors appear in the saved JSON report |
| `test_reporting/test_coverage_report.py` | 9 | Synthetic completeness/lineage math, injury-gap detection (seeded a real gap, confirmed it's flagged), value-field separation in the report itself, **real-data smoke test** against an actual pipeline run |

## Real-data validation (not mocked)

- **`test_providers/test_ea_fc26.py`**: runs against the actual
  `fc26_merged_clean.csv` (16,107 real rows), not a fixture.
- **`test_pipeline/test_run_ingestion.py::test_performance_regression_full_real_scale`**:
  runs the full real 16,107-row EA dataset through the entire pipeline.
- **`test_reporting/test_coverage_report.py::test_real_data_smoke`**:
  runs the actual `run_pipeline()` then builds a coverage report and
  asserts against known real numbers (4 MATCHED, 1 PROBABLE_MATCH).
- Additionally, **outside the pytest suite** (run manually, documented
  in the implementation history, reproducible on demand):
  - Full production pipeline run: all 16,107 real EA players + 5-player
    transfermarkt sample + 5-player wikidata sample, end to end, one
    command.
  - A deliberately-injected mid-enrichment failure at full 16,107-record
    scale, confirming rollback isolation against a byte-for-byte DB
    snapshot comparison.
  - A full-scale idempotent rerun, confirming correct append-only growth
    vs. correct de-duplication per table.

## Performance benchmark

`scripts/benchmark_loaders.py` — not a pytest test, a standalone
comparison script. Real 16,107-record EA data, OLD (naive per-record
`executemany`) vs. NEW (accumulate + real bulk insert via DataFrame
registration): **104.66s → 0.56s, 185x speedup**. See
`INGESTION_PIPELINE.md` for the root-cause explanation.

## Known test limitations

- **No live network tests.** `transfermarkt_dataset` and `wikidata`
  providers are tested exclusively against local sample fixtures — see
  `KNOWN_ISSUES.md`. The "no network call" guard tests
  (`test_no_network_calls_are_made`) confirm the CURRENT fixture-mode
  code path never attempts a connection; they do not (and cannot, in
  this environment) test the future live-SPARQL/live-scrape code paths,
  because those aren't implemented yet.
- **Small fixture scale for transfermarkt/wikidata.** Tests that exercise
  matching/enrichment logic do so against 5-player samples. The
  matching/blocking logic itself has been separately verified for
  correctness against the real 16,107-player EA side (which is the
  larger, more realistic scale) — but the transfermarkt/wikidata SIDE of
  every test is small by necessity of the available data.
- **No concurrency/multi-process tests.** The pipeline is assumed
  single-process, single-writer. DuckDB's own concurrency limitations
  are not exercised or worked around.
- **No test of the CLI argument parser's `--continue-on-error` /
  `--providers` flags in isolation** — these are exercised via
  `PipelineConfig` directly (unit-testable) rather than via subprocess
  invocation of `scripts/run_ingestion.py`'s `main()`. The manual runs
  documented above DID exercise the real CLI, but not under pytest.
