from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from letools.conversion_types import ConversionConfig
from letools.plugins import DatasetSource


class DatasetBackend(ABC):
    version: str

    @abstractmethod
    def write(self, source: DatasetSource, destination: Path, config: ConversionConfig) -> None:
        raise NotImplementedError
