# DATA FLOW

This describes the ACTUAL current flow, as implemented and tested — not
an aspirational design. Entry point: `python3 scripts/run_ingestion.py`
→ `run_pipeline(config)` in `scripts/run_ingestion.py`.

## Core ingestion (blocking — must succeed for the run to be `success=True`)

```
1. run_migrations(db_path)
   Applies any pending db/migrations/*.sql in filename order.

2. For each CORE provider (ea_fc26, transfermarkt_dataset), IN ORDER:
   a. provider.health_check()
      - False (or raises) -> recorded in report.provider_errors
      - if config.stop_on_provider_failure (default True): ABORT HERE,
        before touching validation/normalization/persistence at all
   b. provider.fetch() -> list[RawRecord]      (wrapped in BEGIN)
   c. for each record: validate(record)
      - invalid -> counted, skipped, never reaches normalize()
   d. for each valid record: normalize(record) -> NormalizationResult
      - accumulated in memory across the WHOLE provider's records
        (this is the Phase 2.3 batching fix — NOT done per-record)
   e. ONE call per normalized type to its batch loader:
      - load_identity_fields()          (player_field_values, player_uid=NULL)
      - batch_load_ea_attributes()       (ea_fc26 only)
      - batch_load_market_values()        (transfermarkt only)
      - batch_load_transfers()             (transfermarkt only)
      - batch_load_appearances()            (transfermarkt only)
   f. COMMIT (or ROLLBACK + abort if step (e) raised)

3. IF both ea_fc26 AND transfermarkt_dataset records were loaded this run:
   a. Build IdentityInput lists via from_ea_record()/from_transfermarkt_record()
   b. match_all(ea_identities, tm_identities) -> list[MatchResult]
      (multi-key blocking: name-prefix OR nationality OR birth-year OR club)
   c. drop_unchanged_match_results() + persist_identity_matches() -> identity_matches (append-only, ALL candidates, only for new/changed outcomes)
   d. create_player_records()       -> players (only MATCHED/PROBABLE_MATCH promoted)
   e. persist_review_queue()         -> review_queue (only AMBIGUOUS, deduped)
   f. backfill_player_uid()           -> fills player_uid on the rows from step 2e
      now that `players` exists (ea_fc26_attributes, player_field_values,
      market_value_history, transfers, appearances)
   ELSE: matching is skipped entirely (logged as a warning) — no
   "16,107 unmatched" false-signal run against an empty other side.
```

## Enrichment (non-blocking — never affects the ABOVE succeeding or failing)

```
4. IF wikidata is in config.enabled_providers:
   _run_wikidata_enrichment(config, con, report):
     a. provider.health_check() -> False raises RuntimeError, caught below
     b. provider.fetch() -> validate() each record
     c. BEGIN (enrichment's OWN transaction, separate from step 2/3's)
     d. enrich_players_with_wikidata(con, valid_records):
          for each row in `players`:
            - build_identity_input_for_player(con, player_uid)
              (reads player_field_values, preferring ea_fc26 source,
              falling back to transfermarkt_dataset)
            - match_one(player_identity, wikidata_identities, DEFAULT_THRESHOLDS)
              -- THE SAME scoring engine as step 3b, not a new one
            - if best.status in (MATCHED, PROBABLE_MATCH):
                check for conflict (existing different QID, either on
                `players.wikidata_id` or in `player_wikidata_links`,
                or the QID already owned by another player)
                - conflict -> skip, counted, nothing written
                - free -> INSERT into player_wikidata_links (never UPDATE)
                          + load_identity_fields_for_player() (wikidata-
                            sourced fields, player_uid known immediately)
                          + load_player_image() if an image was supplied
            - else: no_confident_match, nothing written
     e. COMMIT (or ROLLBACK on any exception in (d) — core data from
        steps 1-3 is already committed and completely unaffected)
   ANY exception anywhere in this block -> caught by run_pipeline(),
   recorded in report.enrichment_errors, logged as WARNING, and
   EXECUTION CONTINUES to step 5. report.success is NOT affected.

5. mark_no_injury_source_available(con)
   Inserts NO_SOURCE_AVAILABLE for every player in `players` that
   doesn't already have an injury_data_status row. Never downgrades an
   existing CONFIRMED_NO_INJURIES/HAS_RECORDS row (there are none yet —
   no injury provider exists — but the guard is in place for when one
   does).

6. report.db_row_counts_after populated (one COUNT(*) per table)
   report.success = (len(report.provider_errors) == 0)
                     -- note: enrichment_errors does NOT factor into this

7. report.save(path) -> JSON file in data/processed/unified/reports/
   report.print_summary() -> human-readable log output
```

## Coverage reporting (separate, read-only, run on demand)

```
python3 scripts/generate_coverage_report.py
  -> build_coverage_report(con):
       matching_stats            <- identity_matches WHERE is_best=true GROUP BY status
       source_coverage             <- COUNT per source table
       field_completeness           <- player_field_values GROUP BY source, field_name
       lineage_coverage               <- COUNT(*) vs COUNT(player_uid IS NOT NULL) per table
       injury_status                    <- injury_data_status GROUP BY status
       market_value_coverage              <- 3 INDEPENDENT counts (transfermarkt / ea_ingame / ml_predicted)
       missing_data_summary                 <- generated strings from the numbers above only
  -> CoverageReport.save() / .print_summary()
```
This step never writes to the database and never runs as part of
`run_ingestion.py` — it's a separate, on-demand diagnostic.

## Key distinction: core vs. enrichment

| | EA / transfermarkt (core) | Wikidata (enrichment) |
|---|---|---|
| Runs | before matching | after matching, iterating `players` |
| Failure behavior | aborts pipeline (configurable) | logged, pipeline continues |
| Affects `report.success` | yes | no |
| Transaction | its own, per provider | its own, separate from core |
| `player_uid` at write time | NULL, backfilled later | known immediately |
