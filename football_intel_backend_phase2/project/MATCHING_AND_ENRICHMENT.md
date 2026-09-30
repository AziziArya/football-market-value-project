# MATCHING AND ENRICHMENT

All matching logic lives in `matching/identity.py` and `matching/schema.py`.
There is exactly ONE scoring engine in the codebase — it is used for both
EA↔transfermarkt matching (Phase 1) and player↔wikidata enrichment
(Phase 2.6), via adapter functions. There is no separate/looser
"enrichment matching" implementation anywhere.

## Blocking strategy (candidate generation, for scale)

Four blocking functions, used as a UNION (a candidate needs to match
only ONE, not all):

```python
block_by_name_prefix(identity)   # first 3 letters of the FIRST name token
block_by_nationality(identity)   # normalized nationality string
block_by_birth_year(identity)    # "year:YYYY" from date_of_birth
block_by_club(identity)          # normalized club string
```

`match_all(ea_identities, other_identities, blocking_fns=DEFAULT_BLOCKING_FNS)`
builds one index per blocking function and unions the candidate sets per
query. This is swappable per-call — a caller can pass a reduced or
custom tuple of blocking functions without touching scoring logic at
all.

**Why first-name-prefix, not last-name**: a real bug was found in Phase
1.5 — EA's "Vini Jr." and a source's "Vinicius Junior" have different
LAST tokens ("jr" vs "junior"), which produced different block keys
under the original last-token strategy, so the true match was never
even compared. First-token prefix survives this
(`"vini"[:3] == "vinicius"[:3] == "vin"`).

**Why nationality/birth-year/club were added (Phase 2.3)**: to increase
recall for cases where name normalization diverges too much (heavy
transliteration, very different nicknames) for name-prefix blocking to
catch at all. Verified with a REAL case found in production data: a
Dutch player born on Mbappé's exact birthdate got surfaced as a
candidate purely via the birth-year block, and was correctly classified
`AMBIGUOUS` (not merged) by the unchanged scoring/classification logic.

## Scoring

```
total_score = name*0.40 + dob*0.35 + nationality*0.10 + club*0.15
```
- name/nationality/club: `difflib.SequenceMatcher` ratio on accent-
  stripped, lowercased, punctuation-stripped text (0..1).
- dob: `1.0` if both present and exactly equal; `0.0` if either is
  missing (no penalty, no bonus — just no signal); a special "conflict"
  flag if both are present and DIFFER (see classification below).
- Weights are chosen so that name+dob+nationality alone (0.85) sits
  BELOW the `MATCHED` floor (0.90) — a club mismatch (e.g. a recent
  transfer) always caps a result at `PROBABLE_MATCH`, never a
  false-confident `MATCHED`. This was deliberately calibrated against a
  real Bellingham (Dortmund→Real Madrid) transfer case.

## Classification / thresholds

All in `MatchThresholds` (a dataclass, not buried in code):

```python
matched: float = 0.90
probable: float = 0.75
ambiguous: float = 0.55
ambiguity_margin: float = 0.05
dob_conflict_review_floor: float = 0.75
weight_name/weight_dob/weight_nationality/weight_club  # sum must == 1.0, enforced
```

```
if dob_conflict (both present, disagree):
    non_dob_score = (name*0.40 + nat*0.10 + club*0.15) rescaled to 0..1
    if non_dob_score >= dob_conflict_review_floor (0.75): AMBIGUOUS
        ("everything else agrees strongly — maybe a DOB data-entry error, ask a human")
    else: UNMATCHED ("different person")
else:
    score >= 0.90 -> MATCHED
    score >= 0.75 -> PROBABLE_MATCH
    score >= 0.55 -> AMBIGUOUS
    else          -> UNMATCHED
```

**Ambiguity-margin rule**: even a `MATCHED`/`PROBABLE_MATCH` top
candidate is downgraded to `AMBIGUOUS` if the runner-up is within
`ambiguity_margin` (0.05) of it — "can't be sure which one is right"
overrides an otherwise-confident score.

`position` is collected in `IdentityInput` but NOT used in scoring —
deliberately deferred per an earlier explicit instruction ("secondary/
tie-break signal, optional"), not a bug.

## Ambiguity handling / review queue

`AMBIGUOUS` results are never linked automatically. Two review-queue
implementations exist:
- `matching/review_queue.py` — in-memory, Phase 1.5, useful for
  interactive/in-process review without a DB.
- `matching/review_persistence.py` — DB-backed, Phase 2.4, for review
  state that must survive across pipeline runs. Three deliberately
  SEPARATE functions: `persist_review_queue()` (pipeline, PENDING only),
  `resolve()` (human, records decision, never touches `players`),
  `promote_review_decision()` (human, separate explicit step, only then
  creates a `players` row — reusing `create_player_records()`'s exact
  conflict-refusal logic).

## Wikidata matching adapter (Phase 2.6)

- `from_wikidata_record(record) -> IdentityInput`: maps a wikidata
  `RawRecord` to the same `IdentityInput` shape used everywhere else.
  `club` and `position` are always `None` (wikidata isn't queried for
  those fields, per the approved priority-field list) — `score_pair()`
  already handles missing club/position gracefully (0 contribution, no
  penalty), so no special-casing was needed.
- `build_identity_input_for_player(con, player_uid) -> IdentityInput`:
  reconstructs an `IdentityInput` for an already-canonical `players` row
  by reading `player_field_values`, preferring `ea_fc26`-sourced values
  and falling back to `transfermarkt_dataset`'s. This is what lets
  enrichment call `match_one()` completely unchanged.
- `enrich_players_with_wikidata()` (in `matching/wikidata_enrichment.py`)
  is the orchestration glue: for every canonical player, call
  `match_one()` against all wikidata candidates using
  `DEFAULT_THRESHOLDS` (verified by test to be the literal same object,
  not a copy or a wikidata-specific instance).

**Real, honest limitation found**: because wikidata carries no club
field, a nickname case that DOES match via transfermarkt (which has
club) can score BELOW the enrichment threshold via wikidata alone. This
happened for real in production data (`Vini Jr.` linked to transfermarkt
at 0.85 confidence, but wikidata enrichment for the same player scored
0.704 — below `PROBABLE_MATCH` — and correctly stored NOTHING rather
than force a low-confidence link). This is working as designed, not a
bug: the system never guesses even when a human could infer the
connection from context.

## Conflict protection (Wikidata linking)

`players.wikidata_id` cannot be `UPDATE`d after row creation in DuckDB
(see `DATABASE_SCHEMA.md`), so linking uses a separate
`player_wikidata_links` table:
- `PRIMARY KEY (player_uid)` — a player can never be linked twice by the
  database's own constraint, not just application logic.
- `UNIQUE (wikidata_id)` — a QID can never belong to two players, same
  enforcement level.
- Application code (`_link_state()` in `wikidata_enrichment.py`) checks
  for conflicts BEFORE attempting an insert (checking both
  `players.wikidata_id` — in case it was set at creation time — and
  the links table), so a conflict is reported cleanly
  (`conflict_skipped`) rather than surfacing as a raw constraint
  exception in normal operation.

## No-fabrication rules (tested explicitly)

- Zero candidates -> nothing stored (no link, no field, no image).
- Multiple equally-good candidates (would be `AMBIGUOUS`) -> nothing
  stored.
- A DOB-conflicting candidate -> nothing stored.
- Missing `image_filename`/`license`/`attribution` -> stay `NULL`,
  never defaulted or guessed.
- Missing `nationality`/`date_of_birth` on the wikidata side -> no
  corresponding `player_field_values` row is created for that field at
  all (not a row with a NULL/empty value).

## Idempotency

Re-running the pipeline against the same data:
- `player_wikidata_links`: `already_linked` counted, no new row (PK
  prevents it anyway).
- `player_field_values` (wikidata rows): deduped per
  `(player_uid, source='wikidata', dataset_version)` — a same-day rerun
  adds nothing.
- `player_images`: deduped per `(player_uid, image_url, source)`.
- Verified at real full production scale (16,107 EA players), not just
  in small unit tests.
