"""Source provider that resolves an HDF5 preset into an explicit mapping."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from letools.plugins import DatasetSource, HDF5Mapping, HDF5Source
from letools.tools.hdf5_preset import load_preset
from letools.tools.hdf5_tui import require_interactive_terminal, select_hdf5_preset

from .base import SourceProvider, SourceProviderContext


@dataclass(frozen=True)
class HDF5SourceConfig:
    """Resolved HDF5 mapping and optional preset reference used to obtain it."""

    mapping: HDF5Mapping
    preset: str | Path | None = None


class HDF5SourceProvider(SourceProvider[HDF5SourceConfig]):
    """Load or interactively select the mapping required by HDF5Source."""

    name = "hdf5"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--preset",
            help="HDF5 preset name from the user store or an explicit JSON path",
        )

    def config_from_args(
        self, args: argparse.Namespace, context: SourceProviderContext
    ) -> HDF5SourceConfig:
        reference = getattr(args, "preset", None)
        if reference is not None:
            preset = load_preset(reference)
            return HDF5SourceConfig(preset.mapping, reference)
        if not context.interactive:
            raise ValueError(
                "Interactive preset selection requires a TTY; pass --preset NAME_OR_PATH"
            )
        require_interactive_terminal()
        preset = select_hdf5_preset()
        return HDF5SourceConfig(preset.mapping)

    def open(self, source: Path, config: HDF5SourceConfig) -> DatasetSource:
        return HDF5Source(source, config.mapping)


__all__ = ["HDF5SourceConfig", "HDF5SourceProvider"]
