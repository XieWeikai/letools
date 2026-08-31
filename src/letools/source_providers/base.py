"""CLI-facing factories that turn typed source options into DatasetSource objects."""

from __future__ import annotations

import argparse
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, TypeVar

from letools.plugins import DatasetSource


@dataclass(frozen=True)
class SourceProviderContext:
    """Frontend capabilities available while resolving source configuration."""

    interactive: bool


_ConfigT = TypeVar("_ConfigT")


class SourceProvider(ABC, Generic[_ConfigT]):
    """Construct one source family without exposing its options to the core CLI."""

    name: str
    aliases: tuple[str, ...] = ()

    @abstractmethod
    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Register only the CLI options owned by this source family."""

        raise NotImplementedError

    @abstractmethod
    def config_from_args(
        self, args: argparse.Namespace, context: SourceProviderContext
    ) -> _ConfigT:
        """Validate parsed options and return an immutable, typed configuration."""

        raise NotImplementedError

    @abstractmethod
    def open(self, source: Path, config: _ConfigT) -> DatasetSource:
        """Construct the DatasetSource represented by a resolved configuration."""

        raise NotImplementedError

    def create(
        self,
        source: Path,
        args: argparse.Namespace,
        context: SourceProviderContext,
    ) -> DatasetSource:
        """Resolve frontend options and construct one source object."""

        return self.open(source, self.config_from_args(args, context))


__all__ = ["SourceProvider", "SourceProviderContext"]
