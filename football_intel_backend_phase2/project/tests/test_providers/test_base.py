from dataclasses import dataclass

import pytest

from ingestion.providers.base import Provider, StaticDatasetProvider


def test_provider_is_abstract_cannot_instantiate_directly():
    with pytest.raises(TypeError):
        Provider()  # type: ignore[abstract]


def test_static_dataset_provider_is_still_abstract_without_fetch():
    # StaticDatasetProvider only fixes health_check(); fetch() is still
    # unimplemented, so it must remain non-instantiable on its own.
    with pytest.raises(TypeError):
        StaticDatasetProvider()  # type: ignore[abstract]


@dataclass
class _DummyStaticProvider(StaticDatasetProvider):
    """Minimal concrete subclass, used only to exercise health_check()."""

    name: str = "dummy_static"

    def fetch(self, **kwargs):
        yield from ()


def test_static_dataset_provider_health_check_false_when_dir_missing(tmp_path):
    p = _DummyStaticProvider(dump_dir=tmp_path / "nope")
    assert p.health_check() is False


def test_static_dataset_provider_health_check_true_when_files_present(tmp_path):
    (tmp_path / "sample.csv").write_text("a,b\n1,2\n")
    p = _DummyStaticProvider(dump_dir=tmp_path)
    assert p.health_check() is True
