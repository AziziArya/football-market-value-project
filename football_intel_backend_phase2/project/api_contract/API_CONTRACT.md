# API contract (Steps 3.1 + 3.2, v0.4.0-draft) - DRAFT (implemented: `getHealth`, `getDataFreshness`, `searchPlayers`, `getPlayer`)

Machine-readable: `openapi.json` (OpenAPI 3.1, 8 GET endpoints, 40 schemas, 14 examples).
Checked by `tests/test_contract/` (schema drift, endpoint list, examples validate, honesty invariants).
**Implemented: `getHealth`, `getDataFreshness` (Step 4.1), `searchPlayers` (Step 4.2) and `getPlayer` (Step 4.3); the other four endpoints are contract-only.** `x-status`: `IMPLEMENTABLE_TODAY` (producible from the current DB), `SCHEMA_ONLY` (table exists, empty), `PLANNED` (not produced; always nullable / availability-tagged).

## Decisions
D1 all 16,107 EA players exposed; 5 `CANONICAL`, 16,102 `EA_ONLY`; nothing inferred for EA-only.
D2 public id `ea:<ea_fc26_id>` (stable, never changes on later match) is the ONLY API identifier. `p:<uuid>` is never accepted (400 `INVALID_PLAYER_ID`) and never returned (G13: the uuid is random per database build).
D3 display fields for everyone come from `player_field_values` (source `ea_fc26`, `source_record_id` = EA id).
D4 match status (`identity.links.*`) and source data blocks are separate. D5 every empty block has an `Availability`.
D6 three value blocks: `ea_ingame_value`, `source_market_value`, `model_estimate` (`target: EA_INGAME_VALUE`, PLANNED).
D7 images: Wikidata/Commons only. D8 `data_origin` from a server-side source registry.
D9 (3.2) an existing player with no data is `200` + Availability, never 404. D10 (3.2) no derived analytics (trend, prediction difference, injury burden) are in the contract: the backend produces none.

## Exact endpoint list (base path `/api/v1`, all GET)
| operationId | Path | x-status |
|---|---|---|
| getHealth | `/health` | IMPLEMENTABLE_TODAY |
| getDataFreshness | `/data-freshness` | IMPLEMENTABLE_TODAY |
| searchPlayers | `/players/search` | IMPLEMENTABLE_TODAY |
| getPlayer | `/players/{player_id}` | IMPLEMENTABLE_TODAY (model block PLANNED) |
| getPlayerMarketValue | `/players/{player_id}/market-value` | IMPLEMENTABLE_TODAY (sample data only) |
| getPlayerEaAttributes | `/players/{player_id}/ea-attributes` | IMPLEMENTABLE_TODAY (detailed attributes PLANNED) |
| getPlayerInjuries | `/players/{player_id}/injuries` | SCHEMA_ONLY |
| getPlayerLineage | `/players/{player_id}/lineage` | IMPLEMENTABLE_TODAY |

Deferred, NOT in the contract: `/admin/review-queue`, `/admin/coverage-report` (need an auth design), transfers/appearances endpoints, model-explanation endpoint, real-time anything.

## Search semantics (`/players/search`)
`q` required, 2-80 chars, normalised (NFKD accent-strip, casefold, collapsed whitespace). Matches EA `display_name` for all players, plus other-source `display_name` for CANONICAL players (reported as `match.matched_alias`). Match quality: `EXACT` < `PREFIX` < `TOKEN_PREFIX` < `SUBSTRING`.
Filters (AND): `entity_kind`, `position`, `nationality`, `club` (exact, case-insensitive, EA spelling), `min_overall`. Only fields that exist in the DB.
Sort: `relevance` (quality, overall DESC), `overall_desc`, `name_asc`; every sort ends with `ea_fc26_id ASC` -> total order.
Paging: `limit` 1-50 (default 20), `offset` 0-10000; response has `total`, `next_offset`. Assumption: data changes only when an ingestion run completes.
Names are not identifiers (124 duplicated EA names): every item carries club, nationality, position, date of birth, id.

## Search behaviour decided in Step 4.2
`q` limits apply to the raw and the trimmed value; a `q` with no letter/digit after normalization, and any unknown query parameter, is a 400 `INVALID_PARAMETER`; a blank filter counts as absent; offset past the end is 200 with empty `items`; `TOKEN_PREFIX` = every query token is a prefix of a DIFFERENT name token. `contains_sample_data` is true when a market value, an image or a MATCHED/PROBABLE link (ids) is shown. `canonical_player_uid` is internal and not stable across rebuilds (G13).

## getPlayer behaviour decided in Step 4.3
Id: `^ea:(0|[1-9][0-9]{0,8})$` only. Malformed (incl. `p:<uuid>`, wrong case, leading zeros, non-ASCII digits, trailing characters) -> 400 `INVALID_PLAYER_ID` with `instance` null (the rejected value is not reflected); unknown well-formed id -> 404. `provenance` = one entry per contributing source in registry order (see `ARCHITECTURE_API.md` 6.2).

## Error model
`application/problem+json` (RFC 9457): `type,title,status,code,detail,instance,errors[]`.
Codes: `INVALID_PARAMETER`(400) `INVALID_PLAYER_ID`(400) `PLAYER_NOT_FOUND`(404, unknown id only) `DATA_UNAVAILABLE`(503) `INTERNAL_ERROR`(500). No SQL, paths or stack traces in `detail`.

## What each kind of player returns today
| | EA_ONLY | CANONICAL (Haaland) |
|---|---|---|
| links.transfermarkt | UNMATCHED or AMBIGUOUS(+review_pending); no candidate ids, no confidence | MATCHED / PROBABLE_MATCH + confidence + matched_on |
| links.wikidata | NOT_EVALUATED | PROBABLE_MATCH (0.85, fixture) |
| source_market_value | NOT_MATCHED | AVAILABLE, SAMPLE_FIXTURE |
| model_estimate | NOT_YET_INTEGRATED | NOT_YET_INTEGRATED |
| injury | NOT_EVALUATED | NO_SOURCE_AVAILABLE |
| image | null | Wikidata/Commons if any |
`UNMATCHED` only means "not found in the reference set", hence `reference_data_origin` (today a 5-player sample).
Freshness: nothing is live (`is_live` const false); `ea_fc26` REAL_FULL `2025-09-19`; transfermarkt `sample_2026-07-06` and wikidata `fixture:<content-hash>` are SAMPLE_FIXTURE; injury has no source; model not integrated.

## Gaps
- **G1 (OPEN, separate step)** `ea_fc26_attributes.raw_json` is NULL for all rows -> detailed EA attributes are not in the DB. Contract exposes them only as PLANNED/null.
- **G2 (FIXED)** fixture rows are now `fixture:<content-hash>`; `live:` reserved for a real endpoint path. Old DBs keep old labels until rebuilt.
- **G3** EA `dataset_version` hard-coded `2025-09-19` vs SoFIFA file dated 2025-09-21.
- **G4** duplicated names. **G5** only 5 players have market-value/injury-state/image data.
- **G6 (new)** no `players` row exists for EA-only players, so `p:` ids and `canonical_player_uid` are null for 16,102 players by design.
- **G7 (new)** Transfermarkt/Wikidata/Commons data is sample-only, so examples show fixture values (marked SAMPLE_FIXTURE).
- **G8 (new)** `ea_fc26_attributes.model_version`/`predicted_value_eur` are the ML slot; nothing writes them.

## Planned separate step (NOT started): "Backend Data Integrity - EA detailed attributes / raw_json"
Decide between (a) storing the real `raw_json` (full traceability, larger DB, untyped), (b) typed columns (queryable/validated, schema migration + loader/normalizer/validator changes, column list must be frozen), (c) both (typed for what the API serves, raw_json for lineage). To evaluate: DB size at 16,107 rows, query speed for search/profile, single source of truth, migration path via a numbered migration (0007), effect on the 190+ tests, and whether `value_eur_ingame`-style naming rules must also cover attributes.
