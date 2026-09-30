# SAMPLE FIXTURE — NOT THE FULL DATASET

This folder contains a **small, hand-authored sample** (5 players) used only to
develop and test `ingestion/providers/transfermarkt_dataset.py` in an
environment where the real CC0 dump (dcaribou/transfermarkt-datasets,
hosted on Cloudflare R2) is not network-reachable.

What is real:
- player_id values are the real transfermarkt_id for these 5 players
- names, nationality, position, club are real/public facts
- column names/schema match the real dataset exactly (verified against
  the project's dbt models: base_players.sql, curated/players.sql,
  curated/player_valuations.sql)

What is illustrative, NOT verified against a live source:
- exact market_value_in_eur figures and dates
- transfer fees
- appearance stats

Do not treat this fixture as authoritative market-value data. It exists
solely to prove the ingestion → validation → normalization → matching
pipeline works end-to-end on realistically-shaped data.

## Getting the real, full dataset

1. Download from the source project: https://github.com/dcaribou/transfermarkt-datasets
   (dataset itself is CC0-1.0 licensed, hosted externally — see repo README
   for the current download link, a DuckDB file + parquet exports)
2. Place the real `players.csv` / `player_valuations.csv` / `transfers.csv` /
   `appearances.csv` (or equivalent parquet files) in a new folder here,
   named by the dataset's actual snapshot date, e.g. `2026-07-06/`
3. Point `TransfermarktDatasetProvider(dump_dir=...)` at that folder instead
   of `sample_2026-07-06/`
4. No code changes needed — same column contract, same provider.
