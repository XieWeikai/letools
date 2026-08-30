"""Format-neutral semantic and resource contracts shared across letools."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow as pa


@dataclass(frozen=True)
class EpisodeDataProfile:
    """Size and locality information used without interpreting source storage.

    episode_logical_bytes estimates the Arrow payload contributed by one
    episode to an output group. Resource sizes describe a shared source object
    once, so multiple episodes may return the same locality_key.
    """

    locality_key: str
    episode_logical_bytes: int
    resource_logical_bytes: int
    resource_physical_bytes: int
    resource_rows: int


@dataclass(frozen=True)
class MediaProfile:
    """Planner-facing description of one media input."""

    locality_key: str
    input_bytes: int
    kind: str
    requires_encoding: bool


@dataclass(frozen=True)
class VideoSlice:
    """A timestamp range in an already encoded video resource."""

    path: Path
    start: float
    end: float

    @property
    def duration(self) -> float:
        """Return the represented encoded-stream interval in seconds."""

        return self.end - self.start


class FrameSequence(ABC):
    """Batch-oriented source of encoded image frames.

    Batches keep the plugin boundary outside the per-frame encoding loop and
    allow an implementation to amortize source open costs.
    """

    frame_count: int
    width: int
    height: int
    encoded_format: str
    estimated_size_bytes: int

    @abstractmethod
    def read_batch(self, start: int, stop: int) -> tuple[bytes, ...]:
        """Return encoded frames in the half-open interval [start, stop)."""

        raise NotImplementedError

    def iter_batches(self, batch_frames: int) -> Iterator[tuple[bytes, ...]]:
        """Yield bounded frame batches while preserving source-specific locality.

        The default implementation delegates to the random-access contract.
        Sources with an expensive open operation may override this method and
        retain their resource for the lifetime of the iterator.
        """

        if batch_frames <= 0:
            raise ValueError("Frame batch size must be positive")
        for start in range(0, self.frame_count, batch_frames):
            yield self.read_batch(start, min(self.frame_count, start + batch_frames))


MediaInput = VideoSlice | FrameSequence


@dataclass(frozen=True)
class Episode:
    """Format-neutral semantic unit consumed by LeRobot backends.

    data_path and videos remain for source compatibility. Consumers use
    DatasetSource methods so non-Parquet plugins can provide other storage.
    """

    index: int
    length: int
    tasks: tuple[str, ...]
    stats: dict[str, dict[str, Any]]
    data_path: Path
    data_start: int = 0
    data_end: int | None = None
    videos: dict[str, MediaInput] = field(default_factory=dict)


@dataclass(frozen=True)
class DatasetMetadata:
    """Normalized dataset-wide semantics plus source metadata for adaptation."""

    version: str
    fps: int
    features: dict[str, dict[str, Any]]
    robot_type: str | None
    splits: dict[str, str]
    total_frames: int
    total_episodes: int
    tasks: dict[int, str]
    info: dict[str, Any]

    @property
    def video_keys(self) -> tuple[str, ...]:
        """Return video features in deterministic key order."""

        return tuple(
            sorted(key for key, value in self.features.items() if value["dtype"] == "video")
        )


PointBatch = pa.RecordBatch
