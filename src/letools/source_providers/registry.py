"""Deterministic registry for built-in and application-provided source factories."""

from __future__ import annotations

import importlib
import importlib.metadata
import os
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import SourceProvider


ENTRY_POINT_GROUP = "letools.source_providers"


@dataclass(frozen=True)
class ProviderInfo:
    """Stable provenance shown by ``letools providers list``."""

    name: str
    aliases: tuple[str, ...]
    module: str
    distribution: str | None
    version: str | None
    origin: str


class SourceProviderRegistry:
    """Map canonical provider names and aliases to one provider instance."""

    def __init__(self, providers: Iterable[SourceProvider[Any]] = ()) -> None:
        self._providers: dict[str, SourceProvider[Any]] = {}
        self._canonical_names: list[str] = []
        self._info: dict[str, ProviderInfo] = {}
        self._discovered_references: set[str] = set()
        for provider in providers:
            self.register(provider, origin="builtin")

    def register(
        self,
        provider: SourceProvider[Any],
        *,
        origin: str = "application",
        distribution: str | None = None,
        version: str | None = None,
    ) -> None:
        """Register a provider and reject ambiguous names or aliases."""

        names = (provider.name, *provider.aliases)
        if not provider.name or any(not name for name in names):
            raise ValueError("Source provider names cannot be empty")
        conflicts = sorted(name for name in names if name in self._providers)
        if conflicts:
            raise ValueError(f"Source provider names already registered: {conflicts}")
        self._canonical_names.append(provider.name)
        for name in names:
            self._providers[name] = provider
        self._info[provider.name] = ProviderInfo(
            name=provider.name,
            aliases=provider.aliases,
            module=type(provider).__module__,
            distribution=distribution,
            version=version,
            origin=origin,
        )

    def get(self, name: str) -> SourceProvider[Any]:
        """Return a provider by canonical name or alias."""

        try:
            return self._providers[name]
        except KeyError as error:
            raise ValueError(f"Unknown source provider: {name}") from error

    def choices(self) -> tuple[str, ...]:
        """Return stable CLI choices with aliases adjacent to their provider."""

        values: list[str] = []
        for name in self._canonical_names:
            provider = self._providers[name]
            values.extend((provider.name, *provider.aliases))
        return tuple(values)

    def infos(self) -> tuple[ProviderInfo, ...]:
        """Return canonical providers in deterministic registration order."""

        return tuple(self._info[name] for name in self._canonical_names)

    def info(self, name: str) -> ProviderInfo:
        """Return provenance for a canonical name or alias."""

        provider = self.get(name)
        return self._info[provider.name]

    def register_module(
        self,
        reference: str,
        *,
        pythonpath: Iterable[str | Path] = (),
        origin: str = "local",
    ) -> SourceProvider[Any]:
        """Load ``module:object`` from an explicit local module reference."""

        module_name, separator, object_name = reference.partition(":")
        if not separator or not module_name or not object_name:
            raise ValueError(f"Provider reference must be module:object, got {reference!r}")
        paths = [str(Path(path)) for path in pythonpath]
        old_path = list(sys.path)
        try:
            for path in reversed(paths):
                if path not in sys.path:
                    sys.path.insert(0, path)
            value: Any = importlib.import_module(module_name)
            for part in object_name.split("."):
                value = getattr(value, part)
        finally:
            sys.path[:] = old_path
        provider = self._coerce(value, reference)
        self.register(provider, origin=origin)
        return provider

    @staticmethod
    def _coerce(value: Any, reference: str) -> SourceProvider[Any]:
        if isinstance(value, SourceProvider):
            return value
        if isinstance(value, type) and issubclass(value, SourceProvider):
            return value()
        if callable(value):
            candidate = value()
            if isinstance(candidate, SourceProvider):
                return candidate
        raise TypeError(
            f"Provider entry {reference!r} must expose a SourceProvider instance, "
            "class, or zero-argument factory"
        )

    def discover(self, *, local_file: str | Path | None = None) -> tuple[ProviderInfo, ...]:
        """Discover installed entry points and explicitly configured local modules.

        Discovery is idempotent for one registry. Built-ins are registered by
        the caller first, so an external name collision fails instead of
        silently replacing a trusted provider.
        """

        discovered: list[ProviderInfo] = []
        entries = importlib.metadata.entry_points()
        if hasattr(entries, "select"):
            selected = entries.select(group=ENTRY_POINT_GROUP)
        else:  # pragma: no cover - compatibility with Python 3.9 metadata API
            selected = entries.get(ENTRY_POINT_GROUP, ())
        for entry in sorted(selected, key=lambda item: item.name):
            reference = f"entry-point:{entry.name}:{entry.value}"
            if reference in self._discovered_references:
                continue
            value = entry.load()
            provider = self._coerce(value, entry.value)
            distribution_info = getattr(entry, "dist", None)
            distribution = getattr(distribution_info, "name", None)
            version = getattr(distribution_info, "version", None)
            self.register(
                provider,
                origin="entry-point",
                distribution=distribution,
                version=version,
            )
            discovered.append(self._info[provider.name])
            self._discovered_references.add(reference)

        config_path = Path(
            local_file
            or os.environ.get("LETOOLS_PROVIDERS_FILE", "")
            or Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
            / "letools/providers.toml"
        )
        if config_path.is_file():
            import tomllib

            value = tomllib.loads(config_path.read_text(encoding="utf-8"))
            providers = value.get("providers", {})
            if not isinstance(providers, dict):
                raise ValueError(f"Invalid providers table in {config_path}")
            for name in sorted(providers):
                config = providers[name]
                if not isinstance(config, dict) or config.get("enabled", True) is False:
                    continue
                reference = config.get("module")
                if not isinstance(reference, str):
                    raise ValueError(f"Provider {name!r} in {config_path} lacks module")
                pythonpath = config.get("pythonpath", [])
                if isinstance(pythonpath, str):
                    pythonpath = [pythonpath]
                marker = f"config:{config_path}:{reference}"
                if marker in self._discovered_references:
                    continue
                provider = self.register_module(
                    reference,
                    pythonpath=pythonpath,
                    origin=f"config:{config_path}",
                )
                discovered.append(self._info[provider.name])
                self._discovered_references.add(marker)

        module_env = os.environ.get("LETOOLS_PROVIDER_MODULES", "")
        for reference in filter(None, (item.strip() for item in module_env.split(","))):
            marker = f"environment:{reference}"
            if marker in self._discovered_references:
                continue
            provider = self.register_module(reference, origin="environment")
            discovered.append(self._info[provider.name])
            self._discovered_references.add(marker)
        return tuple(discovered)


__all__ = ["ENTRY_POINT_GROUP", "ProviderInfo", "SourceProviderRegistry"]
