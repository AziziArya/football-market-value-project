# NEXT PHASE — RECOMMENDATION (not implemented)

This document describes the recommended next phase based on the actual
completed backend. Nothing in this document has been built — it is a
recommendation for the next work session to plan from.

## Recommendation: Backend API / Service Layer, before frontend or ML

### Why this, and why now

1. **The data layer is stable and tested; nothing above it exists yet.**
   There is a real, working DuckDB database with a verified schema, a
   reproducible ingestion pipeline, and 190 passing tests — but no way
   for anything outside a Python script to query it. A frontend cannot
   be built against a moving target; an API contract gives it one.

2. **The original master plan's own architecture diagram put an API
   layer between the data layer and the UI.** Nothing has changed to
   suggest skipping it — going straight to frontend would mean the UI
   either embeds SQL directly (breaks the source-lineage/value-
   separation discipline built over 6 sub-phases) or gets built twice.

3. **ML integration (Phase 3) will be easier to design correctly once
   an API contract exists**, because the API's response shapes will
   force explicit decisions about how `value_eur_ingame` /
   `market_value_history` / `predicted_value_eur` are exposed
   side-by-side (the exact separation this backend has enforced since
   Phase 1) — designing the ML integration in isolation risks that
   discipline leaking back into a merged "value" concept at the API
   boundary if the API is designed after the fact instead of before.

### What the API layer should cover (based on what actually exists)

Endpoints that map directly to tables/queries already built and tested:

- `GET /players/search?q=` — search `players`/`v_players` by
  `display_name`
- `GET /players/{player_uid}` — full profile: `v_players` joined with
  `ea_fc26_attributes`, latest `market_value_history` row, current
  `player_images`
- `GET /players/{player_uid}/market-value` — `market_value_history`
  rows, clearly labeled as historical/static (per the `dataset_version`
  already stored) — never described as "live"
- `GET /players/{player_uid}/ea-attributes` — `ea_fc26_attributes`, with
  `value_eur_ingame` and `predicted_value_eur` as clearly distinct
  fields (the latter will be `null` until Phase 3)
- `GET /players/{player_uid}/injuries` — reads `injury_data_status`
  FIRST; if `NO_SOURCE_AVAILABLE`, the API should say exactly that,
  not omit the field or imply "no injuries"
- `GET /players/{player_uid}/lineage` — expose
  `player_field_values`/`identity_matches` provenance for a given
  player, useful for debugging and for a future "data sources" UI panel
- `GET /admin/review-queue` (or similar, access-controlled) —
  `list_pending()` from `matching/review_persistence.py`; a `POST` to
  resolve/promote an item, reusing those functions directly rather than
  reimplementing the decision logic
- `GET /admin/coverage-report` — thin wrapper around
  `reporting/coverage_report.py::build_coverage_report()`
- `GET /health`, `GET /data-freshness` — dataset_version/fetched_at
  summary across sources

### Design constraints the API should inherit from this backend

- **Never merge value fields in a response.** If a player endpoint
  returns market value, in-game value, and predicted value, they must
  be distinct, clearly-labeled fields — never combined into one
  "value".
- **Never claim "live" data that isn't.** `market_value_history` and
  wikidata enrichment are both snapshot/dataset_version-tagged, not
  live — the API's responses should surface `dataset_version`/
  `fetched_at` rather than implying real-time freshness.
- **Injury status needs its three-state honesty preserved** — don't
  collapse `NO_SOURCE_AVAILABLE` into an empty list or a boolean.
- **Read from `v_players`, not `players` directly**, wherever
  `wikidata_id` matters (see `DATABASE_SCHEMA.md`'s warning) — this is
  exactly the kind of internal detail an API layer should hide from
  consumers.

### What should NOT happen yet

- **No frontend/UI work** until the API contract above (or the team's
  own revision of it) is designed and at least partially implemented —
  building UI against raw SQL access to this DB would bypass the
  lineage/separation discipline this backend enforces.
- **No ML model integration** until the API's response shape for
  `predicted_value_eur` is settled — this avoids having to redesign the
  API once a model exists.
- **No swapping in the real transfermarkt/wikidata datasets** is
  strictly required before starting API work — the API can and should
  be built against the current schema (which won't change shape when
  real data replaces the fixtures, only row counts will grow) — but
  doing so earlier rather than later is still recommended opportunistically
  if the network/data-access blocker gets resolved.
