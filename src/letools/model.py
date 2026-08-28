from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow as pa


@dataclass(frozen=True)
class VideoSlice:
    path: Path
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class Episode:
    index: int
    length: int
    tasks: tuple[str, ...]
    stats: dict[str, dict[str, Any]]
    data_path: Path
    data_start: int = 0
    data_end: int | None = None
    videos: dict[str, VideoSlice] = field(default_factory=dict)


@dataclass(frozen=True)
class DatasetMetadata:
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
        return tuple(
            sorted(key for key, value in self.features.items() if value["dtype"] == "video")
        )


PointBatch = pa.RecordBatch
