# Step 4.8 - `getPlayerLineage`

Base: repository `14751b0` + patches 4.4 -> 4.5 -> 4.6 -> 4.7b (694 tests). Contract `0.5.0-draft` (unchanged by this step).
No commit, no push, no GitHub action: everything was done in a scratch clone and is delivered as a patch.

**Result: 757 passed, 0 failed. Every endpoint of the contract is now served. No migration, no database change, `openapi.json` untouched.**

## 1. Files changed (22, compared with the pre-4.8 snapshot)

Code (7)
- `api/readmodels/definitions.py`: the ONLY new SQL, `LINEAGE_FIELD_VALUES` (reads `player_field_values` only).
- `api/domain/models.py`: `FieldValueRow` (a stored row as lineage shows it; the source record id is deliberately not carried).
- `api/repositories/player_detail_repository.py`: `lineage_field_values(cur, player_uid, ea_fc26_id)`.
- `api/schemas/lineage.py` (new): `FieldValue`, `LineageField`, `LineageResponse` (extra fields forbidden; every key always serialized).
- `api/services/player_summary.py`: two behaviour-identical extractions, `build_identity(row, links)` (the identity block, now shared by `getPlayer`, search and lineage, so the AMBIGUOUS rules cannot diverge) and `identity_shows_sample_data(identity)` (the link part of `contains_sample_data`).
- `api/services/player_service.py`: `PlayerService.lineage()`.
- `api/routers/player.py`: `GET /api/v1/players/{player_id}/lineage`.

Tests (6)
- NEW `tests/test_api/test_lineage_endpoint.py` (58 tests).
- `test_conformance.py` (lineage added, three schemas added, new test `test_every_contract_path_is_implemented`), `test_endpoints.py` (inventory = the whole contract), `test_player_endpoint.py` (the "not built yet" test became a writes-are-rejected test), `test_no_public_uid.py` (the `/lineage` route added to its sub-resources, so the uid tripwires and the `p:<uuid>` 400 checks cover it too), `test_contract/test_architecture_doc.py` (G17, Step 4.8 sentence).

Docs (9): `ARCHITECTURE_API.md` (new section 6.7, status line, section 15, G12 note, new row G17), `api_contract/API_CONTRACT.md` (behaviour section, status text), `KNOWN_ISSUES.md` (#22, #23), `NEXT_PHASE.md`, `README.md` (also fixes a statement that was stale since Step 4.4), `CURRENT_STATUS.md`, `HANDOFF.md`, `PROJECT_MANIFEST.md`, `TEST_STATUS.md` (counts: 757 total, `test_api` 506).

## 2. Deliberately NOT changed (verified against the pre-4.8 snapshot)

`api_contract/openapi.json` (byte-identical), the database and the 6 migrations (identical), `db/`, `ingestion/`, `matching/`, `config/`, `scripts/`, `reporting/`, `api/__init__.py`, `api/app.py`, `api/config.py`, `api/errors.py`, `api/domain/` (except the one new dataclass), `api/schemas/player.py` / `market_value.py` / `injuries.py` / `ea_attributes.py` / `common.py`, `api/repositories/` (except the one new function), `api/services/` container, integrity, search, runtime, all other endpoints, `getPlayer`'s behaviour, `ea:<id>` as the only public id, `p:<uuid>` -> 400. No tracked file outside `football_intel_backend_phase2/project/` was touched. G1, G15, G16, ML, real Transfermarkt/Wikidata data, frontend: untouched.

## 3. Exact behaviour of `GET /api/v1/players/{player_id}/lineage`

Response: `player_id`, `identity`, `fields`, `meta`.

- **`identity`** is built by the same function as in `getPlayer` and search: `entity_kind`, `source_ids` (a source id only while its link is MATCHED/PROBABLE_MATCH), `links` (confidence / matched_on only for MATCHED/PROBABLE_MATCH; never a candidate). No `canonical_player_uid`, no uuid, no source record id anywhere.
- **`fields`**: every stored field value, one entry per source and field, sorted by `field_name` then source (= registry order today). Disagreeing sources are both listed; agreeing sources are not collapsed; nothing is merged, ranked or preferred. Each value: `field_value` (the stored string, byte for byte, empty string included), `source`, `dataset_version`, `fetched_at` (whole seconds, UTC), `is_current` (all three passed through, null included, key always present), `data_origin` (from the source registry, never parsed from `dataset_version`, G2).
- **Selection rule** (the documented rule of every read model): the latest row per `(source, source_record_id, field_name)`, `fetched_at DESC, id DESC`. `is_current` is reported but never used to select; history is not listed.
- **One query for every player** (canonical: by `player_uid`; EA-only: the unlinked rows `source = 'ea_fc26'` and `source_record_id` = EA id). The second branch is restricted to `ea_fc26`, so an unlinked row of any other source (= a candidate) can never be selected.
- **Link filter** (service, on top of the SQL): a source's values are shown only when it is `ea_fc26` or its link is MATCHED/PROBABLE_MATCH; unknown sources are never shown.
- **`meta.contains_sample_data`**: true when a shown value comes from a sample source OR a shown id comes from a MATCHED/PROBABLE link against a sample (same rule as `getPlayer`).

| State | Result |
|---|---|
| Canonical player (5 in the data) | 200; EA fields + Transfermarkt fields (5), Haaland/Mbappe/Saka/Bellingham also 3 Wikidata fields; `preferred_foot` EA only; `contains_sample_data` true |
| EA-only player (16,102) | 200; identity `EA_ONLY` with its real link statuses; the six EA fields, one `ea_fc26` value each, nothing from Transfermarkt/Wikidata; `contains_sample_data` false |
| AMBIGUOUS EA-only (Van Drongelen) | see section 4 |
| Well-formed unknown id (`ea:1`) | real 404 `application/problem+json`, `PLAYER_NOT_FOUND`, `instance` = the path |
| Malformed id (`ea:01`, `p:abc`, `p:<uuid>`, `search`, ...) | 400 `INVALID_PLAYER_ID`, `instance` null, value never reflected |
| Service unavailable (startup) | 503 `DATA_UNAVAILABLE`, checked before id validation |
| Database lost after startup | 503 for EVERY player (the stored strings come from the database, not from memory); 404/400 still answer |
| Non-GET | 405 |

## 4. AMBIGUOUS status

Van Drongelen (`ea:233097`) is EA-only; his Transfermarkt link is AMBIGUOUS (candidate TM id 342229 = Mbappe's record, matched on `dob` only). Lineage returns the AMBIGUOUS link with `confidence` and `matched_on` null, `review_pending` true, `transfermarkt_id` null and only his six `ea_fc26` values. None of the candidate's real values (`342229`, `Kylian Mbappé`, `Centre-Forward`) and no `transfermarkt_dataset` source appears (the candidate's 5 rows DO exist in the database, linked to Mbappe; verified). His date of birth `1998-12-20` is also Mbappe's: it is Van Drongelen's own EA value, which is exactly why the match is AMBIGUOUS, so it is not a leak.
Altered copies (throwaway): (a) unlinked Transfermarkt / Wikidata / unknown-source rows keyed by his EA id or the candidate's TM id are never selected, tested both through HTTP and on the SQL layer alone; (b) a canonical player (Haaland) whose TM link is turned AMBIGUOUS shows the AMBIGUOUS link with null signals, no TM id and none of the Transfermarkt rows (EA and still-linked Wikidata rows stay).

## 5. EA-only status

Served exactly like any player: identity with the real link states (`UNMATCHED` against the sample for Salah, `NOT_EVALUATED` for Wikidata) and the six EA fields from the database. The contract has no "unavailable" state for lineage, and the EA rows do exist for every EA player, so no honest-empty state is needed. 400 sampled EA-only players plus all canonical players equal the stored rows exactly.

## 6. G13 after lineage

**Still RESOLVED.** Lineage exposes the `identity` block of 4.7b (`entity_kind`, `source_ids`, `links`): no uid, no uuid, no record id. The tripwires of `test_no_public_uid.py` now include this route (7 players x 5 routes: 51 real responses scanned instead of 44, the real internal uuids with and without dashes never appear, `p:<uuid>` / `p%3A<uuid>` / the bare uuid are 400 on `/lineage` too). The smoke test additionally scanned for `source_record_id`.

## 7. Tests and mutation tests

- Full suite: **757 passed, 0 failed** (694 + 63: 58 lineage, 1 "every contract path is implemented", 3 conformance schemas, 1 uid-tripwire parameter). Also run on a clean clone built from the five patches in order: 757 passed.
- New file (58): contract-example replay (the response equals `lineage_vini` plus the one field the example omits), shape for 7 players, exact comparison with the stored rows (all canonical + 400 sampled EA-only), registry origin (G2), identity equal to `getPlayer`/search, AMBIGUOUS / unlinked / unknown-source / non-MATCHED-link handling, no internal id or derived key, disagreeing sources both listed, byte-exact values and NULLs, deterministic `fetched_at`, latest-row selection (older duplicate, newer version, id tie-break, `is_current` not a selector), a premise test (one row per key, all current), 404/400/405/503/500, database lost, repository failures without leaks, 1-query count and SQL spy (one statement, only `player_field_values`, parameters `[uid, "<ea id>"]` / `[None, "<ea id>"]`), generated OpenAPI vs contract, read-only/determinism (DB hash).
- **Mutation tests: 38 of 38 caught, 0 survived** (after the two gaps below were closed). They cover the SQL (unlinked branch not restricted to EA, missing branch, no latest-per-key, merged sources, oldest wins, no id tie-break, `is_current` as selector, reversed orders), the service (link filter removed / inverted / leaking unknown sources, hard-coded or parsed origin, `contains_sample_data` fixed or ignoring the identity, TM id always shown, merged values, fabricated or dropped `fetched_at`, forced `is_current`, normalised values, unknown id -> 200, malformed -> 404, no 503 gate, unmapped database error, EA-only without database, a second query, wrong EA-id parameter, sorting), the schema (derived field, removed key, extra field) and the route. The deterministic `fetched_at` test alone catches a "now()" mutation (3 of 3 runs).

## 8. Smoke test (real uvicorn, freshly built real database)

7 players: all 200 `application/json`; canonical players 6 EA + 5 Transfermarkt (+3 Wikidata for four of them) values, EA-only 6 EA values. Errors: `ea:1` 404, `ea:01` / `p:abc` / `search` / `ea:239085x` 400 (all `application/problem+json`), POST/PUT/DELETE/PATCH 405. Leak scan on the 7 responses against the 5 real `players.player_uid` values from the database (with and without dashes), uuid-shaped strings, the key `canonical_player_uid` and `source_record_id`: 0 leaks. AMBIGUOUS: none of the candidate's id/values/source present, link AMBIGUOUS with null signals. 305 players (5 canonical + 300 random EA-only) compared with the database rows: 0 mismatches. Identity equal to `getPlayer`'s for the 7 players; two consecutive calls identical apart from `generated_at`. Served OpenAPI: `0.5.0-draft`, `getPlayerLineage`, 8 paths, `FieldValue` with 6 properties. Database byte-identical after serving, 0 tracebacks, 6 migrations applied, 5 `players` rows, 96,679 `player_field_values` rows.

## 9. Discrepancies and test mistakes found (all reported, none changed the product)

1. **Contract example vs data (new gap G17, not changed):** `lineage_vini` lists 5 of the 6 EA fields (`preferred_foot` is stored for every player and is served) and its summary says the sources disagree on `display_name/club`, although the club is identical (they disagree on `display_name` and `position`). I followed the documented behaviour (all stored fields, "all sources listed, never merged"), not the example. The contract was not touched. Owner decision: correct the example in the next contract version, or define a field allow-list.
2. **Lineage is stricter than `getPlayer` in one state that does not exist in the data (G12 note):** for a canonical player whose Transfermarkt link is no longer MATCHED/PROBABLE, lineage hides every value of that source (the contract forbids exposing candidate values), whereas `getPlayer` still shows the market value. Not aligned in this step; to be decided with G12.
3. **Database dependence:** lineage reads the stored strings from the database for every player, so an EA-only player gets 503 when the database is lost (the other EA-only answers are served from memory). Deliberate (exact strings and per-row provenance cannot be rebuilt from the typed index) and tested.
4. **Stale README (pre-existing):** it still said only health, freshness, search and profile existed (true until Step 4.3); corrected.
5. **My test mistakes, found and fixed before delivery:** (a) the AMBIGUOUS negative test first ran on the small database, which does not contain Van Drongelen: it got a 404 and its "not in text" assertions passed vacuously (now: a copy of the full database and the 200 asserted first); (b) two false-positive leak checks (the candidate's date of birth equals his own EA value; the bare word `transfermarkt` is a legitimate identity key); (c) the first mutation run left two mutants alive: the SQL restriction to `ea_fc26` was masked by the service-level link filter (defence in depth; the SQL layer is now tested on its own and a canonical-player case was added) and a collapse across sources never triggered because real data has no shared record ids (a key-collision test was added); (d) I again left a trailing blank line in `KNOWN_ISSUES.md`: the strict-whitespace patch check caught it, it was fixed and the whole 5-patch chain re-verified (a "694 passed" printed by that failed run belonged to the 4-patch tree and was not used as evidence).

## 10. Final test count and how to reproduce

757 passed (694 + 63), 0 failed; `test_api` 506 tests.
On a clean clone of `14751b0`, from the repository root:

    git apply --whitespace=error step_4_4_getPlayerMarketValue.patch
    git apply --whitespace=error step_4_5_getPlayerInjuries.patch
    git apply --whitespace=error step_4_6_getPlayerEaAttributes.patch
    git apply --whitespace=error step_4_7b_G13_remove_public_uid.patch
    git apply --whitespace=error step_4_8_getPlayerLineage.patch
    cd football_intel_backend_phase2/project
    pip install -r requirements-dev.txt
    python -m pytest tests -q          # 757 passed

The resulting tree was diffed against the working tree (identical) and the full suite was run on it.

## 11. Remaining gaps

- **G1** open (`raw_json` NULL; `detailed_attributes` PLANNED; the tripwire test will fail the day it is populated).
- **G15** open (`dataset_version` ordering assumption). **G16** open (injury contract not ready for a real source).
- **G17 new** (the `lineage_vini` example).
- **G12** open (non-MATCHED link on a canonical player; now also affects how `getPlayer` and lineage differ).
- Not started and not claimed: ML audit and integration (the model predicts the EA in-game value, not a market value), real Transfermarkt/Wikidata data, admin/auth, the frontend.
- Assumption, not verified: `ea:<ea_fc26_id>` stays stable across future EA dataset versions.
- Optional cleanup, deliberately not done: the ~7-line id/health guard is repeated in five service methods; `HANDOFF_CHAT_2.md` / `chat2/` are historical snapshots; the suite writes the untracked `data/processed/unified/football_intel.duckdb`.

## 12. Suggested next step

The API contract is complete. Before any new feature, a short **Step 4.9 - backend integration and contract-consistency check** (no new behaviour): decide G17 (fix the lineage example in a contract `0.5.1-draft`, or add an allow-list), align or consciously keep the G12 difference between `getPlayer` and lineage, re-verify the whole API against the contract with one cross-endpoint consistency suite (the same player through all 8 endpoints), and finish the handoff artifacts. After that the owner chooses among: G1 (store the detailed EA attributes: a migration, its own step), the ML audit (no retraining), real Transfermarkt/Wikidata data, or the frontend design system (UI/UX Pro Max). None of them is started.

## 13. Artifacts to keep in `chat3/`

`step_4_4_getPlayerMarketValue.patch`, `step_4_5_getPlayerInjuries.patch`, `step_4_6_getPlayerEaAttributes.patch`, `step_4_7b_G13_remove_public_uid.patch`, `step_4_8_getPlayerLineage.patch` (apply in this order), `STEP_4_7a_G13_DECISION.md`, `STEP_4_7b_G13_REPORT.md`, `STEP_4_8_REPORT.md`. Not yet written as files: the 4.4, 4.5 and 4.6 reports (they exist only as chat text) and the mutation scripts (`mutate.py` 26, `mutate5.py` 30, `mutate6.py` 29, `mutate7.py` 16, `mutate8.py` 38 mutations; they live only in the temporary environment).

## 14. Final handoff verification (re-done at the end, nothing assumed)

- **Base commit used for all the work:** `14751b09a278830c0803993f0b4fafde6bff34e9` ("Complete Chat 2 Phase 2 checkpoint"), branch `main`.
- **Compatibility with the project baseline:** a read-only `git fetch` showed `origin/main` is at that very commit (0 commits ahead). A fresh clone made directly from `https://github.com/AziziArya/football-market-value-project.git` is at the same hash and the five patches applied to it cleanly (`git apply --whitespace=error`, in order). If you have committed anything to GitHub since this check, or are working from a different local baseline, re-run the apply step: that cannot be known from here.
- **Result on that fresh GitHub clone with a fresh virtual environment (`pip install -r requirements-dev.txt`): `757 passed, 0 failed`**; 6 migrations; the resulting tree is identical to the scratch working tree.
- **Patch chain present** (apply in this order): `step_4_4_getPlayerMarketValue.patch` (19 files), `step_4_5_getPlayerInjuries.patch` (20), `step_4_6_getPlayerEaAttributes.patch` (18), `step_4_7b_G13_remove_public_uid.patch` (18), `step_4_8_getPlayerLineage.patch` (22). Each patch is incremental; none contains a `.duckdb`, `__pycache__`, `.pyc` or `.pytest_cache` entry.
- **Cumulative changed files vs the base: 33 paths = 9 new + 24 modified.**
  New (9): `api/schemas/market_value.py`, `api/schemas/injuries.py`, `api/schemas/ea_attributes.py`, `api/schemas/lineage.py`, `tests/test_api/test_market_value_endpoint.py`, `tests/test_api/test_injuries_endpoint.py`, `tests/test_api/test_ea_attributes_endpoint.py`, `tests/test_api/test_lineage_endpoint.py`, `tests/test_api/test_no_public_uid.py`.
  Modified (24): code (8) `api/__init__.py`, `api/domain/models.py`, `api/readmodels/definitions.py`, `api/repositories/player_detail_repository.py`, `api/routers/player.py`, `api/schemas/player.py`, `api/services/player_service.py`, `api/services/player_summary.py`; contract (2) `api_contract/openapi.json`, `api_contract/API_CONTRACT.md`; tests (6) `tests/test_api/test_conformance.py`, `test_endpoints.py`, `test_player_endpoint.py`, `test_search_endpoint.py`, `tests/test_contract/test_architecture_doc.py`, `test_endpoint_contract.py`; docs (8) `ARCHITECTURE_API.md`, `CURRENT_STATUS.md`, `HANDOFF.md`, `KNOWN_ISSUES.md`, `NEXT_PHASE.md`, `PROJECT_MANIFEST.md`, `README.md`, `TEST_STATUS.md`.
- **Contract:** `info.version` `0.5.0-draft` (changed by 4.7b); Step 4.8 itself did not touch `openapi.json`.
- **Verification evidence for Step 4.8:** full suite 757 passed (also on the reconstructed tree and on the fresh GitHub clone); 38/38 mutations caught; real-uvicorn smoke test: 7 players 200, errors 404/400/405 correct with `application/problem+json`, 0 leaks over 7 responses, 305 players equal to the database rows, database byte-identical, 0 tracebacks, 6 migrations.
- **Uncommitted / unfinished (honest list):**
  1. Nothing is committed or pushed anywhere: the scratch clone has 33 uncommitted paths (exactly the five patches) plus one untracked test artifact, `data/processed/unified/football_intel.duckdb`, which `.gitignore` does not cover and which is NOT in any patch.
  2. `STEP_4_4_REPORT.md`, `STEP_4_5_REPORT.md`, `STEP_4_6_REPORT.md` do not exist as files (their content exists only in the chat); `STEP_4_7a_G13_DECISION.md`, `STEP_4_7b_G13_REPORT.md` and this report exist.
  3. The mutation scripts (`mutate.py` 26, `mutate5.py` 30, `mutate6.py` 29, `mutate7.py` 16, `mutate8.py` 38 mutations) exist only in the temporary environment and are not delivered as files.
  4. Open items that are not Step 4.8: G1, G12, G15, G16, G17; ML audit/integration, real source data, admin/auth, frontend: none started.
