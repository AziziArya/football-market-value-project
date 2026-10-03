"""
Wikidata provider — source 2 in the approved multi-source architecture.

Priority fields ONLY (per approved plan): QID, P569 (date of birth),
P27 (country of citizenship), P18 (image).

CURRENT MODE: fixture-based. `query.wikidata.org` is not network-reachable
from this sandbox (same constraint documented for the transfermarkt_dataset
provider in Phase 1). `health_check()` honestly returns False in live mode
rather than pretending to succeed — this is enrichment, not a core source,
so the orchestrator treats its absence as non-blocking (see
scripts/run_ingestion.py's ENRICHMENT_PROVIDERS handling).

LIVE MODE (future, once network access exists): a single batched SPARQL
query using a VALUES clause for all players at once — never one request
per player — respecting the public query service's fair-use policy. The
method stub below documents the exact query shape so implementing it
later is a small, contained change.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import pandas as pd

from ingestion.providers.base import Provider, RawRecord

DEFAULT_FIXTURE_PATH = Path(__file__).parents[2] / "data" / "raw" / "wikidata" / "sample" / "players.csv"

# Documents the exact live query shape for when network access is available.
# Batched via VALUES — one request for all players, not N requests.
SPARQL_TEMPLATE = """
SELECT ?item ?itemLabel ?dob ?citizenshipLabel ?image WHERE {
  VALUES ?nameLabel { %(names)s }
  ?item rdfs:label ?nameLabel.
  OPTIONAL { ?item wdt:P569 ?dob. }
  OPTIONAL { ?item wdt:P27 ?citizenship. }
  OPTIONAL { ?item wdt:P18 ?image. }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
"""


@dataclass
class WikidataProvider(Provider):
    """Source 2: Wikidata identity/image enrichment.

    fixture_path: local sample CSV (see data/raw/wikidata/sample/README.md
                  for exactly which fields in it are real vs illustrative).
    sparql_endpoint: for future live mode; unused while fixture_path is set.
    """

    name: str = "wikidata"
    fixture_path: Path | None = DEFAULT_FIXTURE_PATH
    sparql_endpoint: str = "https://query.wikidata.org/sparql"

    def health_check(self) -> bool:
        if self.fixture_path is not None:
            return self.fixture_path.exists()
        # Live mode: not implemented in this environment. Honest False,
        # never a fabricated True — the orchestrator's enrichment-provider
        # handling means this doesn't block the core pipeline.
        return False

    def fetch(self, **kwargs) -> Iterator[RawRecord]:
        if self.fixture_path is None or not self.fixture_path.exists():
            raise FileNotFoundError(
                "WikidataProvider has no usable fixture and live SPARQL querying is "
                "not available in this environment (query.wikidata.org is not network-"
                "reachable here). Pass fixture_path= pointing at a local sample, or run "
                "this provider in an environment where that domain is whitelisted."
            )

        fetched_at = datetime.now(timezone.utc)
        # The only reachable code path above reads a LOCAL FIXTURE, so rows are
        # tagged 'fixture:<date>' - never 'live:'. A 'live:<date>' tag is reserved
        # for a future code path that actually queries the external endpoint.
        dataset_version = f"fixture:{fetched_at.date().isoformat()}"

        df = pd.read_csv(self.fixture_path)
        for _, row in df.iterrows():
            payload = {k: (None if pd.isna(v) else v) for k, v in row.items()}
            yield RawRecord(
                source=self.name,
                source_record_id=str(payload["qid"]),
                record_type="player",
                payload=payload,
                fetched_at=fetched_at,
                dataset_version=dataset_version,
            )
