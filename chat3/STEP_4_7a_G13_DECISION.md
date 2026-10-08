# Step 4.7a - G13 decision memo (`canonical_player_uid`)

Status: **analysis only. Nothing was changed, no migration, no code.** Base: repository `14751b0` + patches 4.4 -> 4.5 -> 4.6 (677 tests passing).
Decision owner: the project owner. This memo ends with a recommendation, not a decision.

## 1. Where `canonical_player_uid` is today

**Origin.** `players.player_uid` is `uuid.uuid4()`, created in exactly one place (`matching/loader.py:197`, the only `insert into players`). It is the primary key of `players` and the join key of the other tables (field values, market values, injury tables, Wikidata links...). Within one database it is stable (an in-place rerun finds the existing row and keeps it); every fresh build (build-then-swap) generates new values. 5 canonical players have one; the 16,102 EA-only players have none (G6).

**Internal use (must stay in every option).** `PlayerIndexRow.canonical_player_uid` (read model RM1) decides `entity_kind` and is the key of every repository query (`player_service.py`, `search_service.py`, `player_summary.py`, `player_index_repository.py`). This is implementation, not API.

**Public exposure (the only thing G13 is about).** One schema property: `Identity.canonical_player_uid` (`schemas/player.py:47`; contract `components.schemas.Identity.properties`, `x-source v_players.player_uid`, optional, not in `required`). `Identity` is returned in:
- every `searchPlayers` item,
- `getPlayer`,
- `getPlayerLineage` (contract only; not built yet).
Not returned by `getPlayerMarketValue`, `getPlayerInjuries`, `getPlayerEaAttributes` (tests scan for it). The string occurs 9 times in `openapi.json`: 1 schema property + 8 example locations (`profile_canonical_haaland`, `profile_ea_only_salah`, `profile_ea_only_ambiguous`, `lineage_vini`, `search_vinicius_canonical`, `search_duplicate_names` x2, `search_ambiguous_ea_only`); the three canonical examples carry a uuid from a sandbox run.

## 2. What the earlier decisions say

- Contract D2 / `API_CONTRACT.md`: `ea:<ea_fc26_id>` is the ONLY API identifier; `p:<uuid>` is never accepted (400) and never returned.
- Contract text of the field itself: "INTERNAL and NOT stable ... never an identifier for API calls ... clients must identify players only by `id`".
- Chat 2 `HANDOFF_CHAT_2` / `KNOWN_ISSUES #14`: G13 is **DECIDED (part open)**. Open sub-question, explicitly left to the owner: remove the field from the contract, or make it deterministic. Chat 2 asked that it be settled before `getPlayerLineage`, because lineage exposes `identity` too.
- Chat 3 brief: "the internal canonical player UID remains an internal implementation detail"; do not introduce a new public id format.
- Original master prompt section 14: "Do not expose unnecessary internal implementation details through the API."
- No code, test or document found uses the exposed value for anything: it cannot be passed back to any endpoint. Lineage's real cross-source identity is `source_ids` + `links`; `FieldValue` carries no uid.

## 3. Options

- **A - remove it from the public contract.** The field disappears from `Identity`; the uid stays internal.
- **B - keep it, documented unstable** (status quo).
- **C - make it deterministic.** Two sub-variants: **C-lite** `uuid5(namespace, "ea:<id>")` generated in `matching/loader.py`, no schema change; **C-full** an independent identity that survives dataset changes, which needs a persistent registry (new table / state kept outside the build-then-swap DB) = migration + new ingestion behaviour.

| Criterion | A remove | B keep, unstable | C-lite (uuid5 of ea id) | C-full (registry) |
|---|---|---|---|---|
| API contract | field removed, contract 0.4.0 -> 0.5.0-draft (breaking only for a reader of an optional field) | none | none (field stays) | none, plus a new meaning/guarantee to document |
| Backward compat | no known consumer: frontend not started, API not deployed, field never accepted as input; draft contract | none | none | none |
| Rebuild stability | n/a: the unstable thing leaves the public surface | stays unstable, only documented | stable, but only because it is derived from the ea id | stable by construction, if the registry is persisted |
| DB / migration | none | none | no migration, but changes core matching (G9 idempotency, tests); existing DBs keep old uuids until rebuilt | migration + persistence design (explicitly out of scope until approved) |
| Lineage semantics | identity = `ea:<id>` + `source_ids` + `links`: complete | lineage example shows an arbitrary, unusable uuid | uuid adds no information beyond `ea:<id>` | adds a handle nobody can use yet |
| Future frontend | no unstable value in TS types, URLs, caches | an optional field that invites misuse (bookmarking, cache keys, joins) | looks like a second id, conflicts with "one public id" | same, plus a possible id-format debate |
| Tests | ~8 assertion sites in 3 files change; new tripwires | none; examples keep needing masking (`VOLATILE`) | idempotency/uuid-format tests touched | large |
| Risk | low, mechanical | low, but standing API smell | medium (core matching) | high (new state) |

Notes on C: C-lite re-encodes the key the API already exposes (`ea:<id>`), so it buys no capability and weakens "one public id". `players.ea_fc26_id` is nullable, so C-lite also needs a fallback rule for a canonical player without an EA id (none exists today). C-full is the right tool only if real id instability is observed (see assumption below); that is a separate, later decision.

## 4. If option A is chosen: exact change list

**Contract (`api_contract/openapi.json`, `API_CONTRACT.md`).** Remove `Identity.properties.canonical_player_uid` (incl. its `x-source` and description). Remove `identity.canonical_player_uid` from the 8 example locations listed above. Bump `info.version` to `0.5.0-draft` (and the changelog/status text). Keep: `entity_kind`, `source_ids`, `links`; the 400 `INVALID_PLAYER_ID` rule for `p:<uuid>`; D2.

**Code.** `api/__init__.py` `CONTRACT_VERSION` -> `0.5.0-draft` (a test requires it to equal `info.version`). `api/schemas/player.py`: delete `Identity.canonical_player_uid`. `api/services/player_summary.py:66`: stop passing `canonical_player_uid=uid` (the local `uid` is still used right above it for the DB joins and for `entity_kind`). **Unchanged:** `PlayerIndexRow`, `player_index_repository`, `player_service`, `search_service`, all repositories, SQL/read models, DB, migrations, ingestion, matching.

**Tests (public-field assertions only).**
- `test_search_endpoint.py`: remove `canonical_player_uid` from `VOLATILE` (line 21) and the three assertions at lines ~251, 278, 425 (replace by `entity_kind`-based ones).
- `test_player_endpoint.py`: lines ~132 (obtain the uid from the in-memory index instead of the response), 173, 204.
- `test_contract/test_endpoint_contract.py:150`.
- `test_contract/test_architecture_doc.py:50`: G13 pin `DECIDED` -> the new status (e.g. `RESOLVED`).
- Examples-replay tests need no change (the examples simply lose the key).
- Internal uses stay as they are (tests that read `r.canonical_player_uid` from the index rows, `test_repositories`): they test internal state.
- **New tripwires:** (1) the generated OpenAPI and the contract have no property whose name contains `uid`; (2) no response of any of the 8 endpoints (real DB, sample + EA-only + canonical players, error bodies included) contains a key `canonical_player_uid` or any uuid-shaped string (today only the `p:`-prefixed form is scanned); (3) `Identity` serializes exactly `{entity_kind, source_ids, links}`.

**Docs.** `ARCHITECTURE_API.md` (lines ~104, 112, 194, gap row G13), `KNOWN_ISSUES.md #14` (+ a closing note), `API_CONTRACT.md` (lines ~9, 73), `CURRENT_STATUS`/`NEXT_PHASE` status lines. `HANDOFF_CHAT_2.md` and `chat2/` stay as historical snapshots.

**Not touched by A.** Database, migrations, ingestion, matching, `ea:<id>` format, `p:` rejection, endpoints, G1, G15, G16, ML.

Size estimate: about 12 files, a few dozen lines of code/tests; no behaviour change for any endpoint except the removed key.

## 5. Why A is still the recommendation (from the real architecture)

1. **The uid has no public job.** It cannot be sent back to any endpoint (`p:` is rejected by design), identifies nothing across rebuilds, and lineage's identity is already complete without it (`ea:<id>`, `source_ids`, `links`).
2. **It contradicts the project's own rules.** Master prompt s.14 (no unnecessary internal details in the API), the Chat 3 brief (the uid is internal), and D2 (one public id). B keeps an exception to D2 alive in the contract only to document that the value is unusable.
3. **The contract already admits the problem.** The field is described as INTERNAL and NOT stable; removing it makes the contract say less, not more. Examples carry sandbox uuids that nobody can reproduce.
4. **Cost is lowest now.** The contract is a draft, no consumer exists (frontend not started), the field is optional, and the internal use is untouched, so there is no DB change at all. After the frontend and lineage exist the same change gets more expensive, and lineage is the next endpoint to expose it.
5. **C solves a different problem.** Stability across rebuilds only matters for an identifier that is exposed or persisted outside the DB. C-lite re-encodes `ea:<id>`; C-full needs a persistent registry (migration + new state) for a need that has not been shown.

## 6. Counter-arguments and residual risks (stated honestly)

- B is "consistent with the decision already taken" (the field is already labelled internal). True, but Chat 2 left exactly this sub-question open.
- A is a (draft) contract version bump. Anyone who already generated a client from `0.4.0-draft` would see a removed optional property; none is known.
- Operators lose the uid as a correlation hint in responses. `players.ea_fc26_id` is UNIQUE, so the same lookup is possible with `ea:<id>`.
- Future internal/admin needs (review queue, `match_group_id` are also uuid4) are outside the public API and not blocked by A.
- **Assumption, not verified:** that `ea:<ea_fc26_id>` itself stays stable across future EA dataset versions (FC26 -> FC27). A does not depend on it, but if it proves false the right answer is a persistent identity registry (C-full) as its own approved step, not a deterministic uid now. Tracked as an assumption, not a new gap.

## 7. Recommendation and proposed sequence (nothing started)

Recommend **A**. Proposed order, each step ending with a report and a stop:
- **4.7b - apply A:** contract 0.5.0-draft, schema/summary, tests + tripwires, docs; full suite; mutation checks; real-server smoke; DB byte-identical.
- **4.8 - `getPlayerLineage`:** built on the already-clean `Identity`.

If you prefer to approve A and lineage together, say so; I would still report between the two parts.
Decision needed from the owner: **A / B / C-lite / C-full**.

## 8. Out of scope here (unchanged)

G1, G15, G16, ML audit, frontend, real Transfermarkt/Wikidata data, any GitHub action.
