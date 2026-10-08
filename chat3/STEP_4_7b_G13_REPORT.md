# Step 4.7b - G13 resolved: `canonical_player_uid` removed from the public API

Owner decision: option A of `STEP_4_7a_G13_DECISION.md`. Base: repository `14751b0` + patches 4.4 -> 4.5 -> 4.6 (677 tests).
No commit, no push, no GitHub action: everything was done in a scratch clone and is delivered as a patch.

**Result: 694 passed, 0 failed. Contract `0.4.0-draft` -> `0.5.0-draft`. No migration, no database change.**

## 1. Files changed (18)

Code (3)
- `api/__init__.py`: `CONTRACT_VERSION = "0.5.0-draft"`.
- `api/schemas/player.py`: `Identity.canonical_player_uid` deleted (the model forbids extras, so building an `Identity` with a uid now fails).
- `api/services/player_summary.py`: `build_summary` no longer passes `canonical_player_uid=uid`. The local `uid` stays: it is still used for the internal market-value / injury / image lookups.

Contract (2)
- `api_contract/openapi.json`: `Identity.properties.canonical_player_uid` removed (with its `x-source` and description); the key removed from the 8 example locations (`profile_canonical_haaland`, `profile_ea_only_salah`, `profile_ea_only_ambiguous`, `lineage_vini`, `search_vinicius_canonical`, `search_duplicate_names` x2, `search_ambiguous_ea_only`); `info.version` and the 14 example `meta.contract_version` values bumped. The diff contains nothing else (checked line by line).
- `api_contract/API_CONTRACT.md`: version in the title, D2 sentence, G6 sentence, new "0.5.0-draft - G13 resolved" changelog section; earlier "contract unchanged (0.4.0-draft)" remarks made precise ("at the time").

Tests (5)
- NEW `tests/test_api/test_no_public_uid.py` (17 tests, the tripwires).
- `test_player_endpoint.py` (3 assertions), `test_search_endpoint.py` (3 assertions + the `VOLATILE` mask), `test_contract/test_endpoint_contract.py` (1 assertion): the 7 assertions that read the removed key were rewritten (e.g. `set(identity) == {entity_kind, source_ids, links}`); the `p:<uuid>` test now reads the real uuid from the internal in-memory index and checks it appears nowhere in a response.
- `test_contract/test_architecture_doc.py`: G13 pin `DECIDED` -> `RESOLVED`, plus the Step 4.7b sentence.

Docs (8): `ARCHITECTURE_API.md` (new section 6.6, G13 row RESOLVED, three sentences, status line), `KNOWN_ISSUES.md` (#14 RESOLVED, a superseded note in #17, new #21), `NEXT_PHASE.md`, `README.md`, `CURRENT_STATUS.md`, `HANDOFF.md`, `PROJECT_MANIFEST.md`, `TEST_STATUS.md` (counts and status lines).

## 2. Deliberately NOT changed (verified against the pre-4.7b snapshot)

Database and the 6 migrations (identical), `db/`, `ingestion/`, `matching/`, `config/`, `scripts/`, `reporting/`, `api/readmodels/` (all SQL), `api/repositories/`, `api/domain/` (incl. `PlayerIndexRow.canonical_player_uid`), `api/routers/`, `api/errors.py`, `api/app.py`, `api/config.py`, `api/services/` except `player_summary.py` (container, integrity, search, player service), the schemas of market value / injuries / EA attributes / common, all endpoints, `ea:<id>` (still the only public id), the `p:<uuid>` rejection and the `PlayerId` description (still correct: it explains why the form is rejected). `HANDOFF_CHAT_2.md` and `chat2/` stay historical. G1, G15, G16, ML, frontend, real Transfermarkt/Wikidata data: untouched. The internal `players.player_uid` and every uid-keyed join are exactly as before.

## 3. Contract version

`0.5.0-draft` everywhere: `info.version`, `api.CONTRACT_VERSION` (a test requires them to be equal), and `meta.contract_version` in every example and every response. Breaking only for a reader of an optional property that was never usable as an input; no consumer exists.

## 4. Tests

- First run after the removal: 7 failures, all in assertions that read the removed key (as predicted by the memo); no other test, endpoint, SQL or repository was affected.
- New tripwires (17), all on the real 16,107-player database:
  1. contract and generated OpenAPI have no property whose name contains `uid`, no `canonical_player_uid` anywhere, no uuid-shaped string in any example, `Identity` = `{entity_kind, source_ids, links}` (required too), version `0.5.0-draft` everywhere;
  2. `Identity(...canonical_player_uid=...)` raises `ValidationError`;
  3. 44 real responses (health, freshness, 7 players x 4 routes, 9 searches, 2 canonical listings, 3 error bodies): no key, no uuid-shaped string, and none of the 5 REAL internal uuids (read from the in-memory index), with or without dashes; whole-universe serialization sample;
  4. anti over-removal: `players.player_uid` (5 rows, equal to the index), `v_players`, uid-keyed `market_value_history` / `injury_data_status`, the read model, `entity_kind`, and all uid-joined data (market value, injury, Haaland's Wikidata image) still work;
  5. `p:<uuid>`, `p%3A<uuid>` and the bare uuid are `400 INVALID_PLAYER_ID` on all four player routes, `instance` null, value never reflected; `ea:<id>` is still the only public id.
- `canonical_player_uid` was removed from the search tests' `VOLATILE` mask, so it can no longer be silently masked.

## 5. Mutation tests: 16 of 16 caught, 0 survived

Re-exposure (schema field only -> serialized null; schema + summary; under another name `uid`; leaked in the market-value response with dashes and as hex), contract drift (property restored; a sandbox uuid left in an example; `internal_uid` added; `required` weakened; `info.version` or one example's `meta.contract_version` or `CONTRACT_VERSION` not bumped), over-removal (internal `uid` lost in the summary; `entity_kind` always EA_ONLY), and `p:` ids accepted / the 400 echoing the rejected value in `instance` (the tripwire catches the last one on its own).

## 6. Smoke test (real uvicorn, freshly built real DB)

42 responses scanned (7 players x 4 routes, search listings, a CANONICAL listing, health, freshness, error bodies) against the 5 real `players.player_uid` values read from the DB: 0 leaks (key, uuid-shaped string, raw or dash-less uid). Profiles: `identity` keys are exactly `entity_kind`, `links`, `source_ids` (Haaland CANONICAL, Salah / Van Drongelen EA_ONLY). `p:<uid>`, `p%3A<uid>` and the bare uid on all four player routes: `400 application/problem+json INVALID_PLAYER_ID`, `instance` null, uid not echoed. `ea:1` 404, `ea:01` 400, POST/PUT/DELETE/PATCH 405. Served OpenAPI: version `0.5.0-draft`, `Identity` = 3 properties. Uid-keyed joins alive (Haaland: 3 market-value points, Wikidata image, injury `NO_SOURCE_AVAILABLE`). DB byte-identical after serving, 0 tracebacks, 6 migrations, 5 `players` rows, `player_uid` column present.

## 7. Discrepancies found

No product bug and no contract discrepancy. Honest list of what came up:
- My smoke check "is `uid` anywhere in the served OpenAPI" returned true. Investigated, benign: it was only the word `uuid` in the parameter description "`p:<uuid>` is not accepted" (8 times), which documents the rule that must stay. No property, key or value is a uid. The tripwire test (property names + key + value scans) is the real check; my smoke check was a loose substring match.
- Test-construction errors of mine, found and fixed before delivery: a wrong expected response count (44, not "> 60"), a list + tuple concatenation, an assertion that could never fail (`... or ea_id in CANONICAL`; replaced by the real fact that only Haaland has a uid-joined Wikidata image), and one badly designed mutation (the first M16 only changed a message text and was caught for an unrelated reason; replaced by one that echoes the rejected id in `instance`).
- A heredoc slip of mine wrote a literal backslash-n into `KNOWN_ISSUES.md`; checked with a full diff against the snapshot (only the intended changes), cleaned, and the patch is applied with `--whitespace=error` in the verification.
- Still true from before: `HANDOFF_CHAT_2.md` / `chat2/` are outdated snapshots; the suite writes the untracked `data/processed/unified/football_intel.duckdb`, which `.gitignore` does not cover.
- Assumption, not verified and not a new gap: `ea:<ea_fc26_id>` is assumed stable across future EA dataset versions.

## 8. Final test count and how to reproduce

694 passed (677 + 17), 0 failed; `test_api` 443 tests.
Reproduce on a clean clone of `14751b0`, from the repository root:

    git apply --whitespace=error step_4_4_getPlayerMarketValue.patch
    git apply --whitespace=error step_4_5_getPlayerInjuries.patch
    git apply --whitespace=error step_4_6_getPlayerEaAttributes.patch
    git apply --whitespace=error step_4_7b_G13_remove_public_uid.patch
    cd football_intel_backend_phase2/project
    pip install -r requirements-dev.txt
    python -m pytest tests -q          # 694 passed

The resulting tree was diffed against the working tree (identical) and the full suite was run on it.

## 9. Status of G13 after this step

**RESOLVED.** `canonical_player_uid` is not part of the public contract or of any response; the internal uid (`players.player_uid`, read model, joins) is unchanged; `ea:<id>` remains the only public id and `p:<uuid>` stays invalid; tripwires guard against regression. Not touched: G1 (open), G15, G16 (new, open), G12 note, ML, frontend, real source data. The next planned step is `getPlayerLineage` (Step 4.8), not started.
