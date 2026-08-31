"""Deterministic registry for built-in and application-provided source factories."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .base import SourceProvider


class SourceProviderRegistry:
    """Map canonical provider names and aliases to one provider instance."""

    def __init__(self, providers: Iterable[SourceProvider[Any]] = ()) -> None:
        self._providers: dict[str, SourceProvider[Any]] = {}
        self._canonical_names: list[str] = []
        for provider in providers:
            self.register(provider)

    def register(self, provider: SourceProvider[Any]) -> None:
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


__all__ = ["SourceProviderRegistry"]
