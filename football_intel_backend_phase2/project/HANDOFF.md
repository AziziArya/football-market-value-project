# HANDOFF.md — Read This First If You Are a New Chat/Agent

> **GitHub repository is the source of truth. Do not rely on previous
> chat context when repository contents are available.** Everything you
> need to continue this project is either in this file, one of the
> other root-level `.md` files, or the source code itself. If a previous
> conversation's summary disagrees with what's actually in this
> repository, **trust the repository.**

## Current phase: Phase 2.6 complete

All of Phase 0 (audit), Phase 1 (architecture + ingestion foundation),
and Phase 2 sub-phases 2.1 through 2.6 (persistence, orchestration,
performance/blocking, review queue persistence, coverage reporting,
Wikidata enrichment) are implemented, tested, and validated against
real data. Nothing beyond this has been started.

## Exact test count

**527 tests, 0 failures.** Run `python3 -m pytest tests/` to verify —
this should be your first action in a new session, before reading
further or writing any code. If it doesn't say 527 passed, something in
the repository or environment has changed since this handoff was
written; investigate that discrepancy before proceeding.

## Last end-to-end result (real data, not a fixture-only run)

Full pipeline (`python3 scripts/run_ingestion.py`) against the real
16,107-player EA FC26 dataset + the 5-player transfermarkt sample + the
5-player wikidata sample:

```
ea_fc26:               fetched=16107  valid=16107  invalid=0
transfermarkt_dataset: fetched=29     valid=29     invalid=0
wikidata:               fetched=5      valid=5      invalid=0
matching:    MATCHED=4  PROBABLE_MATCH=1  AMBIGUOUS=1  UNMATCHED=16101
enrichment:  linked=4  already_linked=0  conflict_skipped=0  no_confident_match=1
db rows:     players=5  player_field_values=96679  ea_fc26_attributes=16107
             market_value_history=15  transfers=3  appearances=6
             injury_data_status=5  identity_matches=19436  review_queue=1
             player_images=1  player_wikidata_links=4
success: True
```
Also verified (not part of the automated test suite, but reproducible):
a real idempotent rerun (no duplication of non-append-only data) and a
real deliberately-injected mid-enrichment failure at this same full
scale (enrichment rolled back completely; all core EA/transfermarkt
data remained byte-for-byte unchanged).

## Database migrations, current head

```
0001_init.sql
0002_review_queue.sql
0003_source_player_id_link.sql
0004_allow_null_player_uid_pre_match.sql
0005_ea_attributes_dataset_version.sql
0006_player_wikidata_links.sql   <- current head
```
Three of these six exist because of real bugs found while implementing
against real data (0003, 0004, 0005), and one (0006) exists because of a
fundamental DuckDB behavior discovered in Phase 2.6b (see below and
`DATABASE_SCHEMA.md`). Full detail in `KNOWN_ISSUES.md` and
`DATABASE_SCHEMA.md`.

## Architecture, in one paragraph

Providers (`ingestion/providers/*.py`, one per source, all implementing
a common `Provider` ABC) fetch raw records. `ingestion/validator.py` and
`ingestion/normalizer.py` are pure functions dispatching on
`(source, record_type)` — they validate/canonicalize but never touch the
database. `ingestion/loader.py` persists normalized output to DuckDB,
batched (not per-record — see performance note below). Once EA and
transfermarkt data are loaded, `matching/identity.py` scores and
classifies candidate pairs (`MATCHED`/`PROBABLE_MATCH`/`AMBIGUOUS`/
`UNMATCHED`); only the first two create rows in the canonical `players`
table (`matching/loader.py`), `AMBIGUOUS` goes to a durable review queue
(`matching/review_persistence.py`). Wikidata enrichment
(`matching/wikidata_enrichment.py`) then runs, reusing the identical
matching engine, to link `players` rows to Wikidata QIDs — non-
destructively and non-blockingly. `reporting/coverage_report.py` is a
read-only data-quality report over all of the above.
`scripts/run_ingestion.py` orchestrates the whole thing behind one
command. Full detail in `ARCHITECTURE.md` and `DATA_FLOW.md`.

## Most important invariants — do not violate these

1. **Value fields are never merged.** EA in-game value
   (`ea_fc26_attributes.value_eur_ingame`), real market value
   (`market_value_history.value_eur`), and the future ML prediction
   (`ea_fc26_attributes.predicted_value_eur`) are three separate
   columns, always. `normalizer.py` hard-asserts against this.
2. **No fabrication, ever.** Missing data stays `NULL` at every layer.
   `injury_data_status` exists specifically to distinguish "we don't
   know" (`NO_SOURCE_AVAILABLE`) from "confirmed healthy"
   (`CONFIRMED_NO_INJURIES`) — never collapse these.
3. **Matching never destructively merges.** Only `MATCHED`/
   `PROBABLE_MATCH` create/link canonical records. `AMBIGUOUS` always
   goes to human review. Original source IDs are never deleted.
4. **`players.wikidata_id` cannot be `UPDATE`d after row creation** —
   DuckDB refuses it (foreign-key/constraint error) once any child row
   references that `player_uid`, which is always true by enrichment
   time. Wikidata links live in the separate `player_wikidata_links`
   table (INSERT-only, `PRIMARY KEY(player_uid)`, `UNIQUE(wikidata_id)`)
   instead. **Read `v_players`, not `players`, when you need the
   effective `wikidata_id`.** If you're tempted to write
   `UPDATE players SET wikidata_id = ...` anywhere, don't — you will
   hit the same constraint error this handoff is warning you about.
5. **Providers are isolated.** Only `scripts/run_ingestion.py`'s
   `PROVIDER_REGISTRY` names concrete provider classes. Everything else
   talks to the `Provider` ABC only.
6. **One matching engine, reused everywhere.** `matching/identity.py`'s
   `score_pair()`/`match_one()`/`MatchThresholds` are used for BOTH
   EA↔transfermarkt matching and player↔wikidata enrichment, via adapter
   functions. Do not write a second scoring implementation.

## Matching rules and thresholds — exact values

```python
# matching/schema.py — MatchThresholds defaults
matched: float = 0.90
probable: float = 0.75
ambiguous: float = 0.55
ambiguity_margin: float = 0.05
dob_conflict_review_floor: float = 0.75
weight_name: float = 0.40
weight_dob: float = 0.35
weight_nationality: float = 0.10
weight_club: float = 0.15
```
Blocking (candidate generation) unions 4 keys: name-prefix (first 3
letters of first name token), nationality, birth-year, club — see
`MATCHING_AND_ENRICHMENT.md` for why each exists and what real bug/case
justified it. `position` is collected but NOT used in scoring
(deliberate, not a bug).

## Wikidata non-blocking behavior — exact mechanism

`config/settings.py::ENRICHMENT_PROVIDERS = ("wikidata",)`.
`scripts/run_ingestion.py::run_pipeline()` splits `enabled_providers`
into core vs. enrichment BEFORE the main loop. Core provider failure
aborts the pipeline (if `stop_on_provider_failure`, default `True`).
Enrichment provider failure is caught, logged as a WARNING, recorded in
`report.enrichment_errors` (a SEPARATE dict from `report.provider_errors`),
and `report.success` is computed ONLY from `provider_errors` — enrichment
failing can never flip a run to `success=False`. Enrichment runs in its
own `BEGIN`/`COMMIT`/`ROLLBACK` block, independent of the core providers'
transactions, so a mid-enrichment failure cannot corrupt or roll back
already-committed core data. This has been verified with a real
injected failure at full 16,107-record scale, not just in unit tests.

## Known bugs found and fixed during Phase 2.6 (context for future debugging)

1. **pandas 3.0's `future.infer_string` mode** broke the
   `Series.where(pd.notna(...), None)` NaN-to-`None` idiom used in all
   three providers, under certain row dtype shapes (triggered for the
   wikidata fixture's mostly-empty image columns; happened NOT to
   trigger for EA/transfermarkt's data shapes — this was luck, not
   correctness). Fixed everywhere with per-value `pd.isna()` checks
   instead of the vectorized `.where()`. **If you add a new
   pandas-based provider, use the `{k: (None if pd.isna(v) else v) for
   k, v in row.items()}` pattern, not `.where()`.**
2. **DuckDB `executemany()` is not vectorized** (~1ms/row regardless of
   batch size — 96,642 rows took 106 seconds). Fixed with
   `_bulk_insert()` (registers a `pandas.DataFrame`, does a single
   `INSERT ... SELECT ... FROM registered_df`). **Never write a new bulk
   loader using `executemany()` for anything that will run at real
   scale — use the `_bulk_insert()` pattern in `ingestion/loader.py` /
   `matching/loader.py`.**
3. **`UPDATE players SET wikidata_id = ...` fails in DuckDB** — see
   invariant #4 above. This is the reason migration 0006 and
   `player_wikidata_links`/`v_players` exist.
4. A malformed Wikimedia Commons image URL (unencoded spaces) was found
   and fixed — `load_player_image()` now percent-encodes the filename.

Full detail on all of these, plus non-bug limitations, in
`KNOWN_ISSUES.md`.

## Known limitations (not bugs — see `KNOWN_ISSUES.md` for full list)

- `transfermarkt_dataset` and `wikidata` are both small, clearly-labeled
  sample fixtures (5 players each), not the real full datasets — sandbox
  network restrictions prevented fetching the real ~50k-player CC0 dump
  or querying live Wikidata. Provider architecture is ready for real
  data with no interface changes.
- No injury data source approved (researched, none found that's free
  and legally clean).
- No ML model integration.
- Core matching/persistence steps (`persist_identity_matches` →
  `create_player_records` → `persist_review_queue` →
  `backfill_player_uid`) are not wrapped in one outer transaction the
  way provider ingestion and wikidata enrichment are — a low-severity
  gap, never observed to cause a problem, but real.

## Recommended next phase

**Backend API / service layer** — designed against the schema and data
shapes already stable here, before any frontend/UI or ML work. Full
reasoning and a concrete endpoint list in `NEXT_PHASE.md`.

## Explicit DO NOT START list

Do not start any of the following until the API/service layer phase
above has been explicitly scoped and approved by the project owner:

- ❌ A REST/GraphQL API or any service layer beyond what's documented
  here as "recommended, not built."
- ❌ ML model integration (`predicted_value_eur` wiring).
- ❌ Any frontend, UI, or website code.
- ❌ Swapping in the real transfermarkt/wikidata datasets AS PART OF a
  larger phase change — doing so on its own (data-only, no schema/code
  change) is fine opportunistically if network access becomes available,
  but is not a prerequisite for anything above and should not be bundled
  with unrelated new feature work.
- ❌ Adding a new data source without first reading `ARCHITECTURE.md`'s
  "important architectural boundaries" section — the provider isolation
  and non-destructive-matching rules apply to any new source exactly as
  they did to wikidata.

## Reading order for a new session

1. This file (`HANDOFF.md`)
2. `README.md` — setup and how to run things
3. `CURRENT_STATUS.md` — the fuller status breakdown
4. `PROJECT_MANIFEST.md` — what every file/folder is
5. `ARCHITECTURE.md`, `DATA_FLOW.md`, `DATABASE_SCHEMA.md`,
   `MATCHING_AND_ENRICHMENT.md`, `INGESTION_PIPELINE.md` — as needed for
   the specific work being done
6. `KNOWN_ISSUES.md` before assuming anything is broken
7. `NEXT_PHASE.md` before starting new work
