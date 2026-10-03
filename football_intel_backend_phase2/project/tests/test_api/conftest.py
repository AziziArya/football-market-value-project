"""Shared fixtures for the API foundation tests (Step 4.1). Databases are built by the REAL pipeline."""
import json
import logging
import shutil
from pathlib import Path

import jsonschema
import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.config import ApiConfig
from config.settings import PipelineConfig
from scripts.run_ingestion import run_pipeline

ROOT = Path(__file__).parents[2]
SMALL_EA = ROOT / "tests" / "fixtures" / "small_ea_fc26.csv"
CONTRACT_PATH = ROOT / "api_contract" / "openapi.json"


def build_db(directory: Path, name="db.duckdb", small=True, providers=None) -> Path:
    kwargs = {"db_path": directory / name, "report_dir": directory / "reports"}
    if small:
        kwargs["ea_fc26_csv_path"] = SMALL_EA
    if providers:
        kwargs["enabled_providers"] = providers
    assert run_pipeline(PipelineConfig(**kwargs)).success
    return kwargs["db_path"]


@pytest.fixture(scope="session")
def contract():
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def small_db(tmp_path_factory) -> Path:
    return build_db(tmp_path_factory.mktemp("api_small"))


@pytest.fixture(scope="session")
def full_db(tmp_path_factory) -> Path:
    return build_db(tmp_path_factory.mktemp("api_full"), small=False)


@pytest.fixture
def db_copy(small_db, tmp_path) -> Path:
    """A private writable copy of the small database, for tests that corrupt data on purpose."""
    dst = tmp_path / "copy.duckdb"
    shutil.copy(small_db, dst)
    return dst


@pytest.fixture
def make_client():
    clients = []

    def _make(db_path, **config) -> TestClient:
        logging.getLogger("api").setLevel(logging.CRITICAL)
        client = TestClient(create_app(ApiConfig(db_path=Path(db_path), **config)), raise_server_exceptions=False)
        client.__enter__()
        clients.append(client)
        return client

    yield _make
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture
def validate(contract):
    def _validate(schema_name, payload):
        wrapper = {"$ref": f"#/components/schemas/{schema_name}", "components": contract["components"]}
        jsonschema.Draft202012Validator(wrapper).validate(payload)
    return _validate
