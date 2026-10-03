from pathlib import Path

import pytest

from api.config import DEFAULT_DB_PATH, ApiConfig
from config.settings import DEFAULT_DB_PATH as PIPELINE_DEFAULT_DB_PATH


def test_defaults_are_safe():
    c = ApiConfig.from_env({})
    assert c.cors_origins == () and c.enable_docs is False and c.log_level == "INFO"
    assert c.db_path == DEFAULT_DB_PATH == PIPELINE_DEFAULT_DB_PATH      # API and pipeline agree on the default file


def test_env_parsing():
    c = ApiConfig.from_env({"FOOTBALL_INTEL_DB_PATH": "/x/y.duckdb", "FOOTBALL_INTEL_API_CORS_ORIGINS": "https://a.example, https://b.example",
                            "FOOTBALL_INTEL_API_LOG_LEVEL": "debug", "FOOTBALL_INTEL_API_ENABLE_DOCS": "true"})
    assert c.db_path == Path("/x/y.duckdb") and c.cors_origins == ("https://a.example", "https://b.example")
    assert c.log_level == "DEBUG" and c.enable_docs is True


@pytest.mark.parametrize("env", [
    {"FOOTBALL_INTEL_API_LOG_LEVEL": "LOUD"}, {"FOOTBALL_INTEL_API_ENABLE_DOCS": "maybe"},
    {"FOOTBALL_INTEL_API_CORS_ORIGINS": "*"}, {"FOOTBALL_INTEL_API_LOG_LEVEL": "NOTSET"},
])
def test_invalid_config_is_rejected(env):
    with pytest.raises(ValueError):
        ApiConfig.from_env(env)
