"""Abstract write-side boundary between conversion lifecycle and target layout."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from letools.conversion_types import ConversionConfig
from letools.plugins import DatasetSource
from letools.telemetry import StageRecorder


class DatasetBackend(ABC):
    """Write one complete target layout into an unpublished staging path."""

    version: str

    @abstractmethod
    def write(
        self,
        source: DatasetSource,
        destination: Path,
        config: ConversionConfig,
        recorder: StageRecorder,
    ) -> None:
        """Materialize source semantics at destination and record owned stages.

        Implementations may create files only below destination. They own target
        metadata and grouping, but must consume source data through DatasetSource
        rather than parse source-format internals.
        """

        raise NotImplementedError
