"""Football Player Intelligence API (read-only). Step 4.1: foundation (health + data-freshness).

Contract: api_contract/openapi.json. Architecture: ARCHITECTURE_API.md. The API never writes to the database
and never imports the ingestion pipeline."""

CONTRACT_VERSION = "0.4.0-draft"   # must equal info.version of api_contract/openapi.json (checked by a test)
API_BASE_PATH = "/api/v1"
