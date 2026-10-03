# ARCHITECTURE_API - API service architecture (Step 3.3 design, Step 4.1 foundation built)

Status: **Step 4.1 implemented the foundation** (package `api/`, `getHealth`, `getDataFreshness`, RM1/RM2 + startup invariants), **Step 4.2 implemented `searchPlayers`** (RM3 aliases, search index, batch enrichment) and **Step 4.3 implemented `getPlayer`**. Everything else below is still design: the four sub-resource endpoints (market-value, EA attributes, injuries, lineage), ML and G1 are NOT built.
Inputs: the real repository, `api_contract/openapi.json` (Steps 3.1/3.2, 8 GET endpoints) and measurements taken on the real
16,107-player database (section 13). If this document and `openapi.json` disagree, **the contract wins**.

## 0. Scope and non-goals
In scope: framework choice, layers, dependency rules, read models (logical), search design, errors, config, test strategy, G1/ML decisions, frontend boundary.
Out of scope (deferred, not designed here): `/admin/*` and any auth, ML integration, G1 implementation, frontend/UI, live or new external data, scraping,
database migrations, caching/rate-limiting/deployment tooling.

## 1. Principles
1. Contract-first: `openapi.json` is the product; code conforms to it, never the reverse.
2. The API is **read-only** with respect to the database and to ingestion.
3. Honesty over polish: every empty value has an `Availability`; every value carries provenance and `data_origin`; nothing is live (`is_live` = false).
4. Three value domains never mix: `ea_ingame_value`, `source_market_value`, `model_estimate` (target `EA_INGAME_VALUE`).
5. No inference: no identity, value, injury, image or attribute is derived for a player that the database does not hold.

## 2. Framework and dependencies (installed in Step 4.1)
| Package | Role | Scope | Version seen on PyPI (2026-10-01) |
|---|---|---|---|
| fastapi | HTTP layer, request validation | production | pinned 0.142.2 |
| uvicorn | ASGI server | production | pinned 0.54.0 |
| pydantic (v2) | response/request models | production | pinned 2.13.5 |
| httpx2 | test client (starlette 1.x deprecates plain `httpx` for `TestClient`) | dev | pinned 2.13.1 (replaces the earlier httpx recommendation) |
| jsonschema | validate responses/examples against `openapi.json` | dev (**added now**, 4.26.0, `requirements-dev.txt`) | 4.26.0 |
| openapi-typescript (frontend, later) | generate TS types from `openapi.json` | frontend | not checked |
Pinned exactly (`==`) in `requirements.txt` / `requirements-dev.txt` in Step 4.1; `duckdb==1.5.5` unchanged (G10).
Why FastAPI: Python (same runtime as the data layer), Pydantic v2 models, built-in OpenAPI to diff against the contract, async not required but harmless.
Rejected: Flask (no schema layer), Django (ORM/admin we do not need), a hand-rolled server (no gain).
Rules: no ORM (DuckDB + plain parameterised SQL in repositories); no pandas in the request path; **no ML libraries (TensorFlow/CatBoost/scikit-learn) in the API runtime**.
Contract-first mechanics: Pydantic models are written by hand to mirror `openapi.json`; a test compares the app's generated OpenAPI (paths, operationIds, required fields, enums) with the contract, and another validates real responses with `jsonschema`.

## 3. Layers and dependency boundaries (proposed layout, NOT created)
```
api/
  app.py            application factory, startup invariants, exception handlers
  config.py         ApiConfig (env only)
  errors.py         exception types -> Problem codes
  routers/          HTTP only: parse path/query, call a service, return a schema
  schemas/          Pydantic models mirroring openapi.json (no logic)
  services/         use-cases: assemble responses, apply Availability rules, no SQL
  repositories/     the ONLY place with SQL; read-only DuckDB; returns plain typed rows
  readmodels/       logical read-model definitions + startup loaders (section 5)
  domain/           pure functions: normalization, match quality, source registry, availability
```
| Layer | May import | Must NOT import |
|---|---|---|
| routers | services, schemas, errors | repositories, duckdb, domain internals |
| services | repositories, domain, schemas, errors | duckdb, fastapi, routers |
| repositories | duckdb, readmodels, domain (types only) | fastapi, schemas, services, routers |
| domain | stdlib only | everything else (no I/O) |
| whole `api/` | | `ingestion`, `matching`, `scripts`, `db.migrate`, `reporting`, pandas, ML libraries |
Enforced by an architecture-fitness test (`tests/test_contract/test_architecture_doc.py`) that scans `api/` as soon as it exists.
The API reads the database; it never calls the pipeline. It shares only the env var `FOOTBALL_INTEL_DB_PATH`.

## 4. Read-only operation, concurrency and rebuilds
Measured (DuckDB 1.5.5/1.5.6): several read-only processes can open one file; a read-write open fails with a lock error while a reader holds it.
Decisions:
- AD-1 The API opens the database with `read_only=True`, never runs migrations, never writes. A write attempt in a test must fail.
- AD-2 **Build-then-swap**: ingestion always writes a NEW database file; the API serves a different, finished file; promotion = atomic rename/symlink + API restart/reload. Ingestion never touches the served file.
- AD-3 One read-only connection per worker process; per-request `cursor()`; no connection per request.
- AD-4 At startup the API verifies: schema/migrations present, read-model invariants (section 5). On failure it **fails closed**: data endpoints answer 503 `DATA_UNAVAILABLE`, `/health` reports `unavailable`.
Ingestion is **idempotent** (G9, fixed in Step 3.4a): re-running the same dataset into the same database adds no rows to any table (full real scale: identical row counts after runs 2 and 3). Source tables stay append-only: a CHANGED value (same or new `dataset_version`) is appended, and `identity_matches` appends a new group only when the match outcome changed. Nothing is deleted or edited.
Build-then-swap remains the deployment rule (clean reproducible files, no writer/reader lock conflicts), but it is no longer needed for correctness.
Selection rule used by every read model (also for legacy databases built before the fix, which may still hold duplicates and are never auto-cleaned): the **latest row per logical key** - `field values`: key (`source`, `source_record_id`, `field_name`), order `fetched_at DESC, id DESC`; `identity_matches`: latest `is_best` row per `ea_fc26_id`, order `matched_at DESC, id DESC`. `is_current` must NOT be trusted as a selection signal (several rows can be current at once). Executable spec: `tests/test_idempotency/test_g9_rerun_invariants.py`.
Consequence for freshness: `fetched_at`/`last_fetched_at` is when the currently served data was **first loaded**; a no-op rerun does not advance it.

## 5. SQL read models (logical definitions; no SQL, no views, no migration)
Each read model is a parameterless SELECT executed by a repository; none is persisted. They produce the contract's `x-source` fields.
| Read model | Grain | Built from | Rules | Load |
|---|---|---|---|---|
| RM1 player_index | 1 row per EA player (16,107) | `ea_fc26_attributes` + latest EA `player_field_values` (display_name, position, club, nationality, date_of_birth, preferred_foot; join `source_record_id` = `ea_fc26_id`) + `v_players` | invariant: exactly one row per `ea_fc26_id`; `entity_kind` = CANONICAL iff a `v_players` row exists; **read `v_players`, never `players`** | in memory at startup |
| RM2 link_state | 1 row per EA id x {transfermarkt, wikidata} | latest `is_best` row of `identity_matches`, `review_queue` (PENDING), `player_wikidata_links` | confidence/matched_on only for MATCHED/PROBABLE_MATCH; candidate ids never loaded into the API model; wikidata for EA-only = `NOT_EVALUATED`; `reference_data_origin` from the registry | in memory |
| RM3 alias_index | (ea id, alias, source) | non-EA `display_name` values of CANONICAL players | search only | in memory |
| RM4 market_value_points | per canonical player | `market_value_history` | order by `valuation_date`, `dataset_version`; `latest` = max date, newest version | on demand (ms) |
| RM5 injury_state | per canonical player | `injury_data_status`, `injury_records` | absence of a status row = `NOT_EVALUATED` (EA-only); records only when `HAS_RECORDS` | on demand |
| RM6 image | per canonical player | `player_images` primary | only `source = 'wikidata'`; any other source is dropped and logged (SoFIFA `player_face_url` is never read) | on demand |
| RM7 freshness | per source | `player_field_values.fetched_at/dataset_version`, `market_value_history.imported_at`, registry | `last_fetched_at` = when the served dataset version was FIRST loaded by us | on demand |
| lineage | per player | direct query of `player_field_values` | all sources listed, never merged | on demand |
Indexing: **none needed now.** Measured on the real DB (12.3 MB, 96,679 field rows, 0 indexes): full RM1 pivot 33 ms (once at startup), substring scan 5 ms, per-player lookup 1-3 ms. No migration is proposed in Phase 3; revisit only if data grows by orders of magnitude.
Search-page payloads come from RM1/RM2 in memory; canonical extras (market value, injury, image) are fetched for the whole page in ONE batched query (`IN (...)`), never N+1.

## 6. Search design (`searchPlayers`)
Normalization (one pure function used for both the index and the query):
1. Unicode NFKD, `casefold`. 2. Drop combining marks and format characters (U+00AD soft hyphen is deleted, not turned into a space).
3. Fold letters NFKD cannot decompose: `ø->o ł->l ı->i æ->ae œ->oe ð->d þ->th đ->d ħ->h` (casefold already gives `ß->ss`).
4. Replace punctuation/symbol/separator/control characters (categories P, S, Z, C) with a space; keep letters/digits of any script. 5. Collapse and trim whitespace.
Measured on the real data: NFKD + stripping marks alone leaves **314** of 16,107 names non-ASCII (e.g. "Martin Ødegaard", 3 names with soft hyphens); with steps 1-5 **0**, and none becomes empty.
Match quality (on normalized strings; the best of EA name and, for CANONICAL players, aliases): `EXACT` (equal) < `PREFIX` (name starts with q) < `TOKEN_PREFIX` (every q token is a prefix of a different name token, any order) < `SUBSTRING` (q occurs in the name). No quality -> not a result.
`match.matched_alias` is set only when an alias gives a strictly better quality than the EA name (or the EA name does not match at all).
Duplicate names: measured **124** duplicated exact names, **128** after folding. Results are never merged or deduplicated; each EA player is its own result with a stable `ea:<id>`; the item carries club, nationality, position, date of birth.
Filters (AND): `entity_kind`; `position`, `nationality`, `club` = casefold equality with the EA value (as in the contract); `min_overall`. Positions in the data: CAM CB CDM CM GK LB LM LW RB RM RW ST.
Ordering is total: `relevance` = (quality, overall DESC, ea id ASC); `overall_desc` = (overall DESC, ea id ASC); `name_asc` = (normalized name, ea id ASC).
Pagination: compute the full matching list in memory (measured ~1 ms per scan of 16,107 names in Python), slice `[offset, offset+limit)`; `total` = list length; `next_offset` null on the last page; an offset past the end is `200` with empty `items`. Stable because data only changes on a swap (AD-2).
EA-only vs canonical: both are searchable; aliases exist only for canonical players; an EA-only player never gains Transfermarkt/Wikidata fields through search.

### 6.1 As implemented in Step 4.2 (decisions taken while building)
- `TOKEN_PREFIX` uses real bipartite matching (each query token needs a DIFFERENT name token), not greedy matching: greedy wrongly rejects e.g. query `a ab` vs name `Ab A`. The executable oracle (`tests/test_contract/test_search_spec.py`) is brute force over permutations; the implementation is differential-tested against it (random token sets) and end-to-end on the real data.
- Validation: `q` limits (2-80) apply to the raw value and to the trimmed value; a `q` with no letter/digit after normalization is rejected (`INVALID_PARAMETER`); **unknown query parameters are rejected**, never ignored (a typo such as `min_pace` must not return an unfiltered list); a blank filter value counts as absent. `errors[].reason` wording is stable and framework-independent.
- Alias reporting: the best alias is chosen deterministically (aliases pre-sorted by source, normalized value); it is reported only if STRICTLY better than the EA name; aliases equal to the EA name are dropped at index time.
- Enrichment: canonical items need market value, injury state and image; they are loaded for the whole page with at most THREE queries (`list_contains(?, player_uid)`), none when the page holds only EA-only players (served entirely from memory, even if the database is lost after startup). Verified by a query-counting test (same count for 1 and 50 canonical items).
- `meta.contains_sample_data` = something SHOWN comes from a sample source: a source market value, an image, or a MATCHED/PROBABLE link (its ids). AMBIGUOUS/UNMATCHED/NOT_EVALUATED links show no sample data and do not count.
- `source_ids.transfermarkt_id` / `wikidata_id` are exposed only while the corresponding link is MATCHED/PROBABLE_MATCH (G12). `canonical_player_uid` is exposed but is random per database build (G13): clients use `id`.
- Measured on the real database (16,107 players): 4-30 ms per search including a 50-item page; startup (RM1 + RM2 + RM3 + index) well under 1 s.

### 6.2 `getPlayer` as implemented in Step 4.3
- **The only public id is `ea:<ea_fc26_id>`** (G13 decision): canonical form `^ea:(0|[1-9][0-9]{0,8})$` (no leading zeros, ASCII digits only, at most 9 digits; validated with `fullmatch` because `$` would accept a trailing newline). `p:<uuid>` is never accepted and never returned as an id: the uuid is random per database build.
- Malformed id -> `400 INVALID_PLAYER_ID` (the rejected value is never reflected: `detail` is fixed and `instance` is null). Well-formed but unknown id -> `404 PLAYER_NOT_FOUND`, the only 404. When the service is unavailable the answer is 503 (checked first).
- The route `/players/search` is registered BEFORE `/players/{player_id}`, so "search" is never read as an id (tested).
- The response is the SAME `PlayerSummary` as search (built by the same function) plus `provenance` and `meta`. `provenance` has one entry per source that contributed any shown block, in registry order: `ea_fc26` always; `transfermarkt_dataset` when a market value is shown (that value's provenance) or, failing that, when the Transfermarkt link is MATCHED/PROBABLE (its ids come from that source: the source's latest load is used); `wikidata` when its link is MATCHED/PROBABLE or an image is shown (the player's link row, else the source's latest load).
- EA-only players are served entirely from memory (0 queries, works even if the database is lost after startup); canonical players cost 3 batch queries + at most 1 (wikidata link) + at most 2 fallbacks. `canonical_player_uid` is still present in `identity` as a documented INTERNAL field (G13).

## 7. Service rules: provenance, freshness, value separation
- `domain/source_registry`: `ea_fc26 -> REAL_FULL`; `transfermarkt_dataset -> SAMPLE_FIXTURE`; `wikidata -> SAMPLE_FIXTURE`; unknown source -> `UNKNOWN` (never REAL). It is changed deliberately when real data arrives; `dataset_version` text is never parsed for origin.
- `is_live` is the constant false until a provider with a real endpoint exists. `meta.generated_at` is response time; data age comes only from provenance.
- `meta.contains_sample_data` = any block in the response has `data_origin = SAMPLE_FIXTURE`.
- Availability: `NOT_MATCHED` (no link), `NO_SOURCE_DATA` (linked, no value), `NOT_YET_INTEGRATED` (capability missing), else `AVAILABLE`. 404 only for an unknown id.
- Value separation is structural: three distinct Pydantic classes, no shared `value` field, **no arithmetic between them** (no difference/trend: not in the contract).

## 8. Error handling
Problem details (RFC 9457) per contract. Mapping: parse/validation failures -> 400 `INVALID_PARAMETER`/`INVALID_PLAYER_ID`; unknown well-formed id -> 404 `PLAYER_NOT_FOUND`;
DuckDB/IO/schema or startup-invariant failures -> 503 `DATA_UNAVAILABLE`; anything else -> 500 `INTERNAL_ERROR`. `detail` never contains SQL, file paths or stack traces; the full error is logged with a request id.

## 9. Configuration
Environment variables only, no secrets in this phase: `FOOTBALL_INTEL_DB_PATH` (shared with the pipeline), `FOOTBALL_INTEL_API_CORS_ORIGINS` (default: none), `FOOTBALL_INTEL_API_LOG_LEVEL`, `FOOTBALL_INTEL_API_ENABLE_DOCS` (default false).
Contract limits (`limit` <= 50, `offset` <= 10000, `q` 2-80) are constants, not configuration, so they cannot drift from the contract. `ApiConfig` is separate from `PipelineConfig`.

## 10. Test strategy
1. Domain unit tests: normalization (the 314-name table), match quality, ordering. The executable spec is `tests/test_contract/test_search_spec.py`.
2. Repository tests on the small fixture DB and a full-scale session fixture (16,107 players).
3. Service tests with in-memory repositories (availability and honesty rules).
4. Contract tests: every 200/4xx response validated with `jsonschema` against `openapi.json`; golden replays of the 14 contract examples for ea:239085, ea:209331, ea:233097, ea:238794 (timestamps masked); generated OpenAPI vs contract diff.
5. Honesty invariants at HTTP level (no `live:`, EA-only never gets TM/Wikidata/image, `model_estimate` always NOT_YET_INTEGRATED/`EA_INGAME_VALUE`).
6. Architecture-fitness tests (import boundaries, no SQL outside repositories, read-only connection), a rerun-robustness test (a DB ingested twice yields identical responses), and a non-gating latency budget.

## 11. Decisions on G1 and ML
**AD-G1 (decision, not implementation).** `ea_fc26_attributes.raw_json` stays NULL; detailed attributes stay `NOT_YET_INTEGRATED`/`null`.
- The service depends on a storage-agnostic port (e.g. "EA detail provider") that returns detailed attributes or "none". Today's adapter always returns none; it does not read `raw_json`, any CSV, or the SoFIFA file, and never derives attributes from `overall_rating`.
- A later, separate step chooses raw_json vs typed columns vs both. The API contract must not change when that happens except for freezing the allowed attribute keys of `detailed_attributes.attributes` (an additive contract change with a version bump).
- Criteria for that step: typed columns = validated, queryable, needs migration + loader/normalizer/validator + tests; raw_json = full traceability, untyped, larger file; both = double maintenance. Provisional, non-binding lean: typed columns for what the API serves (the 48 numeric features of `feature_list.json` are a candidate list); decide with measurements in that step.
**AD-ML (decision, not implementation).** Scoring is an offline batch step that runs during the build (before the swap) and fills the existing `predicted_value_eur` / `model_version` columns; the API only reads them.
- Target is always `EA_INGAME_VALUE` (notebook target `log1p(value_eur)`, inverse `expm1`). Until then: `NOT_YET_INTEGRATED`, null amount, null version.
- The API never loads a model, never scores on request, never mixes the estimate with EA value or market value, never shows a difference between them.
- Open prerequisites for that step: the saved pipelines were not loaded in the audit (library versions unverified); reported R2 about 0.998 is on EA's own value; no uncertainty is stored. Explanation/uncertainty endpoints need a contract bump and separate approval.

## 12. API and frontend boundary
1. The frontend never opens DuckDB or runs SQL; no database file is mounted in a frontend container.
2. The only shared artifact is `api_contract/openapi.json`; frontend types are generated from it, not hand-copied.
3. The API has no UI knowledge: no HTML, no UI copy, no display formatting, no layout-driven fields. UI wording for `Availability`, `MatchStatus`, `data_origin` lives in the frontend.
4. The frontend calls the API from server-side code where possible; CORS is deny-by-default.
5. Within `/api/v1` only additive changes; a rename, removal or semantic change needs a new contract version and approval. `meta.contract_version` is the handshake.
6. Frontend must tolerate unknown enum values and must show "LIVE" only if `is_live` is true (never today).

## 13. Evidence (measured 2026-10-01 on this repo, throwaway scripts, not committed)
DuckDB `1.5.5` (the pinned version; 1.5.6 also verified compatible, see G10). Full DB: 12.3 MB, 96,679 `player_field_values` rows, 16,107 EA players, no indexes.
Query costs: full pivot 33 ms, substring scan 5 ms, per-player lookup 1.1-2.8 ms. Python scan of 16,107 normalized names ~1 ms.
Names: 314 non-ASCII after NFKD only, 0 after the spec; 124 duplicate names (128 after folding). Rerun behaviour: section 4 (fixed).

## 14. Known gaps register
| ID | Status | Gap | Architectural note |
|---|---|---|---|
| G1 | OPEN (separate step) | `raw_json` NULL; no detailed EA attributes | AD-G1: port + PLANNED/null; never fabricate |
| G2 | FIXED | fixture rows were stamped `live:` | now `fixture:<content-hash>` (was `fixture:<date>`, then made content-based by G9); old DBs keep old labels until rebuilt |
| G3 | KNOWN | EA `dataset_version` hard-coded `2025-09-19`, SoFIFA file dated 2025-09-21 | freshness shows the stored value; fix belongs to the data-integrity step |
| G4 | KNOWN | duplicated names (124/128) | ids are identity; search never merges |
| G5 | KNOWN | only 5 players have market value/injury state/image | UI/API must treat EA_ONLY as normal |
| G6 | KNOWN (by design) | no `players` row for 16,102 EA-only players | `ea:<id>` is the public id; `canonical_player_uid` null |
| G7 | KNOWN | Transfermarkt/Wikidata/Commons are samples | registry marks SAMPLE_FIXTURE; `contains_sample_data` |
| G8 | KNOWN | ML slot exists, nothing writes it | AD-ML |
| G9 | FIXED | re-running ingestion into one DB duplicated EA/TM field rows and `is_best` matches | idempotent loaders (no migration); read models still use latest-per-key; legacy DBs are not auto-cleaned |
| G10 | RESOLVED | `requirements.txt` pinned duckdb 1.5.5, environment had 1.5.6 | keep `duckdb==1.5.5` (no change): both versions pass the full suite and produce identical, cross-readable databases |
| G11 | FIXED | `HealthResponse.status` allowed `unavailable` although `/health` 503 is a Problem | contract aligned (enum is `ok` only); service raises DATA_UNAVAILABLE |
| G12 | NEW | a canonical player whose Transfermarkt link is not MATCHED/PROBABLE (e.g. resolved by human review) hides its transfermarkt id (contract rule); none exists in today's data | tested with a corrupted copy; revisit with the review-resolution flow |
| G13 | DECIDED | `canonical_player_uid` is a random uuid per database build, so it changes on every rebuild | decision (Step 4.3): the only public id is `ea:<id>`; `p:<uuid>` is never accepted nor returned. The raw field `identity.canonical_player_uid` is still exposed as a documented internal value; removing it (or making it deterministic) is an open contract decision |
| G14 | FIXED | `overall_rating`/`potential` are nullable in `ea_fc26_attributes` but the contract (and the response models) require them: a NULL would have become a 500 | startup invariant (fail closed) + `PlayerCore.required` aligned in the contract (found by the conformance tests during the Step 4.3 verification) |

## 15. Implementation status (Steps 4.1-4.2) and what remains
Built: `api/` (config, errors, domain: enums/models/normalize/source registry/link rules/player index/**search**; readmodels: SQL definitions incl. RM3 and batch enrichment; repositories: read-only DuckDB, catalog, RM1/RM2/RM3, freshness, **player_detail**; services: integrity, health, freshness, **player_summary, search**; schemas incl. **player/search**; routers; `create_app`). Run: `uvicorn api.app:create_app --factory`.
Implemented endpoints: `getHealth`, `getDataFreshness`, `searchPlayers`, `getPlayer`. Not built (framework-default 404): `getPlayerMarketValue`, `getPlayerEaAttributes`, `getPlayerInjuries`, `getPlayerLineage`.
Decisions taken so far:
- `/health` when unhealthy answers **503 with a Problem** and `HealthResponse.status` is the constant `ok` (G11 fixed in Step 4.2: the contract was aligned with the behaviour; contract version 0.3.0-draft).
- `last_fetched_at` per source = latest load of that source's rows (field-based sources: `player_field_values.fetched_at`; market value: `market_value_history.imported_at`).
- Startup also fails closed if a MATCHED/PROBABLE link exists for an EA_ONLY player, if an alias points to an unknown player, if a player has no `overall_rating`/`potential` (G14), or if ML columns are non-NULL while no model is integrated.
- `PlayerSummary` (schemas/player.py + services/player_summary.py) is the shared building block for the later `getPlayer`.
