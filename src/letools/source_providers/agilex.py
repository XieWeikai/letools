"""Source provider for timestamp-aligned AgileX recording directories."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from letools.plugins import AgileXSource, DatasetSource

from .base import SourceProvider, SourceProviderContext


@dataclass(frozen=True)
class AgileXSourceConfig:
    """Validated task and metadata settings required by AgileXSource."""

    instruction: str
    fps: int = 30
    robot_type: str = "cobot_magic"

    def __post_init__(self) -> None:
        instruction = self.instruction.strip()
        robot_type = self.robot_type.strip()
        if not instruction:
            raise ValueError("AgileX instruction cannot be empty")
        if self.fps <= 0:
            raise ValueError("AgileX FPS must be positive")
        if not robot_type:
            raise ValueError("AgileX robot type cannot be empty")
        object.__setattr__(self, "instruction", instruction)
        object.__setattr__(self, "robot_type", robot_type)


class AgileXSourceProvider(SourceProvider[AgileXSourceConfig]):
    """Parse explicit AgileX semantics before opening the physical recording."""

    name = "agilex"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--instruction",
            required=True,
            help="fixed task instruction assigned to every AgileX episode",
        )
        parser.add_argument(
            "--fps",
            type=int,
            default=30,
            help="AgileX output sampling rate (default: 30)",
        )
        parser.add_argument(
            "--robot-type",
            default="cobot_magic",
            help="AgileX robot_type metadata (default: cobot_magic)",
        )

    def config_from_args(
        self, args: argparse.Namespace, context: SourceProviderContext
    ) -> AgileXSourceConfig:
        return AgileXSourceConfig(args.instruction, args.fps, args.robot_type)

    def open(self, source: Path, config: AgileXSourceConfig) -> DatasetSource:
        return AgileXSource(
            source,
            config.instruction,
            fps=config.fps,
            robot_type=config.robot_type,
        )

    def distributed_spec(self, source: Path, config: AgileXSourceConfig):
        """Embed every semantic input required to reconstruct AgileXSource."""

        from letools.distributed.source import agilex_source_spec

        return agilex_source_spec(
            source,
            config.instruction,
            config.fps,
            config.robot_type,
        )


__all__ = ["AgileXSourceConfig", "AgileXSourceProvider"]
