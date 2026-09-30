# SAMPLE FIXTURE — NOT A LIVE WIKIDATA QUERY RESULT

This folder exists for the same reason as
`data/raw/transfermarkt_dataset/sample_2026-07-06/`: `query.wikidata.org`
and `commons.wikimedia.org` are not network-reachable from this sandbox
(see Phase 1/2.6 notes). This fixture lets the wikidata provider,
normalizer, loader, and non-destructive matching/merge logic be built
and tested end-to-end against realistically-shaped data.

## What is REAL / verified (via web search against Wikidata directly)
Row 1 only — **Erling Haaland**:
- `qid` = `Q28967995` — real Wikidata item ID, confirmed
- `date_of_birth` = 2000-07-21, `country_of_citizenship` = Norway — confirmed on the
  live Wikidata item page
- `image_filename` — a real Wikimedia Commons filename that was the item's P18
  (image) value at the time of the search
- `image_attribution` = "Bryan Berlin" — the photographer name as shown on the
  Commons file page at that time

## What is ILLUSTRATIVE / NOT verified against live Wikidata
Rows 2–5 (Mbappé, Vinicius Junior, Saka, Bellingham):
- `qid` values (`Q00000001`–`Q00000004`) are **deliberately obvious placeholders**,
  not real Wikidata IDs — chosen specifically so they can never be mistaken for
  genuine identifiers
- `date_of_birth`/`country_of_citizenship` for these four are the same real-world
  facts already used elsewhere in this project (EA FC26 data, transfermarkt
  sample), NOT independently re-verified against a live Wikidata query
- `image_filename`/`image_license`/`image_attribution` are left blank
  (NULL) for these four — no image data is fabricated for them

## Why row 1 is enough to prove the pipeline
One fully-real row is sufficient to test: real QID storage, real image
metadata with real attribution, and the full identity-matching-reuse
path (see `matching/identity.py::from_wikidata_record` +
`build_identity_input_for_player`). The other four exercise volume,
the multi-candidate matching path, and the "no image data" case
(license/attribution correctly staying NULL, never guessed).

## Getting real data for all rows
Point `WikidataProvider(sparql_endpoint=...)` at the live endpoint once
network access is available; the provider, validator, normalizer, and
loader all already work against the real field names Wikidata returns —
no code changes needed, same pattern as the transfermarkt_dataset provider.
