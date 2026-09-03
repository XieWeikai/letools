from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import pyarrow as pa

from letools.distributed.source import open_source_spec
from letools.distributed.types import SourceSpec
from letools.cli import parse_cli_args
from letools.plugins import DatasetSource
from letools.source_providers import (
    SourceProvider,
    SourceProviderContext,
    SourceProviderRegistry,
)


class _ExternalSource(DatasetSource):
    def __init__(self, root: Path, token: str) -> None:
        self.root = root
        self.token = token

    def read_episode(self, episode):  # pragma: no cover - construction test only
        return pa.table({"index": []})


@dataclass(frozen=True)
class _ExternalConfig:
    token: str


class _ExternalProvider(SourceProvider[_ExternalConfig]):
    name = "example"
    aliases = ("example-alias",)
    config_type = _ExternalConfig

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--token", required=True)

    def config_from_args(
        self, args: argparse.Namespace, context: SourceProviderContext
    ) -> _ExternalConfig:
        return _ExternalConfig(args.token)

    def open(self, source: Path, config: _ExternalConfig) -> DatasetSource:
        return _ExternalSource(source, config.token)


def test_default_external_manifest_round_trip() -> None:
    assert SourceSpec("lerobot", "/shared/source").to_dict() == {
        "kind": "lerobot",
        "root": "/shared/source",
        "options": {},
    }
    provider = _ExternalProvider()
    spec = provider.distributed_spec(Path("relative/source"), _ExternalConfig("abc"))
    assert spec.kind == "provider"
    assert spec.provider == "example"
    assert spec.options == {"token": "abc"}

    registry = SourceProviderRegistry([provider])
    # Worker reconstruction uses the process-local registry populated by the
    # package import, so install this provider for the duration of the test.
    import importlib

    package = importlib.import_module("letools.source_providers")

    package.source_providers.register(_ExternalProvider())
    try:
        source = open_source_spec(SourceSpec.from_dict(spec.to_dict()))
        assert isinstance(source, _ExternalSource)
        assert source.token == "abc"
    finally:
        # Keep the global registry deterministic for subsequent tests.
        package.source_providers._providers.pop("example", None)
        package.source_providers._providers.pop("example-alias", None)
        package.source_providers._canonical_names.remove("example")
        package.source_providers._info.pop("example", None)
    assert registry.get("example-alias").name == "example"


def test_entry_point_discovery_is_sorted_and_records_provenance(monkeypatch) -> None:
    class Entry:
        def __init__(self, name: str, provider):
            self.name = name
            self.value = f"{provider.__module__}:{provider.__qualname__}"
            self.dist = None

        def load(self):
            return _ExternalProvider

    class EntryPoints(list):
        def select(self, *, group):
            assert group == "letools.source_providers"
            return self

    import importlib

    registry_module = importlib.import_module("letools.source_providers.registry")

    monkeypatch.setattr(
        registry_module.importlib.metadata,
        "entry_points",
        lambda: EntryPoints([Entry("z-example", _ExternalProvider)]),
    )
    registry = SourceProviderRegistry()
    infos = registry.discover()
    assert [info.name for info in infos] == ["example"]
    assert infos[0].origin == "entry-point"
    assert registry.choices() == ("example", "example-alias")


def test_external_manifest_rejects_provider_api_mismatch() -> None:
    import importlib

    package = importlib.import_module("letools.source_providers")
    package.source_providers.register(_ExternalProvider())
    try:
        spec = SourceSpec(
            "provider",
            "/tmp/source",
            {"token": "abc"},
            provider="example",
            provider_api_version=999,
        )
        import pytest

        with pytest.raises(ValueError, match="API mismatch"):
            open_source_spec(spec)
    finally:
        package.source_providers._providers.pop("example", None)
        package.source_providers._providers.pop("example-alias", None)
        package.source_providers._canonical_names.remove("example")
        package.source_providers._info.pop("example", None)


def test_external_provider_options_are_added_only_after_selection() -> None:
    import importlib

    package = importlib.import_module("letools.source_providers")
    package.source_providers.register(_ExternalProvider())
    try:
        args = parse_cli_args(
            [
                "convert",
                "/data/raw",
                "/data/output",
                "--source-format",
                "example",
                "--token",
                "from-cli",
                "--to",
                "v3.0",
            ]
        )
        assert args._source_provider.name == "example"
        assert args._source_provider.config_from_args(
            args, SourceProviderContext(interactive=False)
        ) == _ExternalConfig("from-cli")
    finally:
        package.source_providers._providers.pop("example", None)
        package.source_providers._providers.pop("example-alias", None)
        package.source_providers._canonical_names.remove("example")
        package.source_providers._info.pop("example", None)
