from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from pathlib import Path

import pyarrow as pa

from letools.model import DatasetMetadata, Episode


class DatasetSource(ABC):
    root: Path
    metadata: DatasetMetadata
    episodes: tuple[Episode, ...]

    def iter_episodes(self) -> Iterator[Episode]:
        return iter(self.episodes)

    @abstractmethod
    def read_episode(self, episode: Episode) -> pa.Table:
        raise NotImplementedError

    def read_episodes(self, episodes: Sequence[Episode]) -> list[pa.Table]:
        return [self.read_episode(episode) for episode in episodes]
