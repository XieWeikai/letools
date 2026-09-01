"""Source provider for path-detected LeRobot v2.1 and v3.0 datasets."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from letools.plugins import DatasetSource, open_dataset

from .base import SourceProvider, SourceProviderContext


@dataclass(frozen=True)
class LeRobotSourceConfig:
    """Typed marker configuration for LeRobot path auto-detection."""


class LeRobotSourceProvider(SourceProvider[LeRobotSourceConfig]):
    """Open an existing physical LeRobot dataset without format-specific options."""

    name = "lerobot"
    aliases = ("auto",)

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """LeRobot path detection has no additional CLI arguments."""

    def config_from_args(
        self, args: argparse.Namespace, context: SourceProviderContext
    ) -> LeRobotSourceConfig:
        return LeRobotSourceConfig()

    def open(self, source: Path, config: LeRobotSourceConfig) -> DatasetSource:
        return open_dataset(source)

    def distributed_spec(self, source: Path, config: LeRobotSourceConfig):
        """Store only the absolute dataset root; workers auto-detect its version."""

        from letools.distributed.types import SourceSpec

        return SourceSpec("lerobot", str(source.resolve()))


__all__ = ["LeRobotSourceConfig", "LeRobotSourceProvider"]
