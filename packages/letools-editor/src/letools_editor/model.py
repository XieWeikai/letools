"""Small value contracts shared by planning, execution, and the CLI."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SYSTEM_FEATURES = frozenset(
    {"timestamp", "frame_index", "episode_index", "index", "task_index"}
)


@dataclass(frozen=True)
class VideoEdit:
    """Explicit video transform; omitting it preserves compressed payloads.

    Width/height mean exact resize (aspect ratio may change). The MVP supports
    portable CPU libx264 and MJPEG encoders. It intentionally does not promise
    hardware acceleration, FPS changes, audio preservation, or depth conversion.
    """

    codec: str = "libx264"
    size: tuple[int, int] | None = None
    pixel_format: str | None = None
    crf: int = 23
    preset: str = "veryfast"
    quality: int = 2
    keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.codec not in {"libx264", "mjpeg"}:
            raise ValueError("The editor MVP supports libx264 and mjpeg encoders")
        if self.size is not None and (len(self.size) != 2 or min(self.size) <= 0):
            raise ValueError("Video size must contain positive width and height")
        if not 0 <= self.crf <= 51 or not 1 <= self.quality <= 31:
            raise ValueError("CRF must be 0..51; MJPEG quality must be 1..31")
        if self.preset not in {
            "ultrafast",
            "superfast",
            "veryfast",
            "faster",
            "fast",
            "medium",
            "slow",
            "slower",
            "veryslow",
        }:
            raise ValueError("Unknown libx264 preset")


@dataclass(frozen=True)
class EditConfig:
    """One fused same-version edit, addressed by original episode indices.

    An overridden task replaces every frame's task within that episode. Other
    per-frame task assignments and existing task IDs remain unchanged. Reusing
    task IDs avoids rewriting unaffected shards just to compact a dictionary.
    """

    task_by_episode: dict[int, str] = field(default_factory=dict)
    delete_episodes: frozenset[int] = frozenset()
    remove_features: frozenset[str] = frozenset()
    video: VideoEdit | None = None
    workers: int | None = None
    codec_threads: int = 1
    batch_rows: int = 65536
    ffmpeg: str | None = None
    overwrite: bool = False

    def __post_init__(self) -> None:
        if self.delete_episodes & self.task_by_episode.keys():
            raise ValueError("Cannot change the task of a deleted episode")
        if self.remove_features & SYSTEM_FEATURES:
            raise ValueError("Cannot remove required LeRobot system features")
        if any(
            not isinstance(key, int) or key < 0
            for key in (*self.task_by_episode, *self.delete_episodes)
        ):
            raise ValueError("Episode indices must be non-negative integers")
        if any(
            not isinstance(task, str) or not task.strip()
            for task in self.task_by_episode.values()
        ):
            raise ValueError("Task descriptions must be nonempty strings")
        if (
            (self.workers is not None and self.workers <= 0)
            or self.codec_threads <= 0
            or self.batch_rows <= 0
        ):
            raise ValueError(
                "Worker, codec-thread, and batch-row limits must be positive"
            )


@dataclass(frozen=True)
class EditPlan:
    """Read-only summary; execute rebuilds it to avoid stale-file TOCTOU plans."""

    source: Path
    destination: Path
    version: str
    input_episodes: int
    episodes: int
    frames: int
    data_files_rewrite: int
    files_reuse: int
    videos_remux: int
    videos_transcode: int
    workers: int
    codec_threads: int
    effective_cpus: int
    memory_budget_bytes: int
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class EditResult:
    """End-to-end timing includes planning, validation, and publication."""

    plan: EditPlan
    elapsed_seconds: float
    episodes_per_second: float
    frames_per_second: float
    cloned_files: int
    copied_files: int
    reused_bytes: int
    stages: dict[str, float]
    episode_index_map: dict[int, int]


@dataclass
class EpisodeEdit:
    """Private execution identity; stats are updated before metadata publication."""

    original: Any
    index: int
    frame_start: int
    tasks: tuple[str, ...]
    task_override: int | None
    stats: dict[str, Any]
    data_location: tuple[int, int] = (0, 0)
    video_locations: dict[str, tuple[int, int, float, float]] = field(
        default_factory=dict
    )


@dataclass(frozen=True)
class MediaJob:
    """One physical file, not one episode: decode/remux shared v3 media once."""

    key: str
    source: Path
    relative_output: Path
    episodes: tuple[EpisodeEdit, ...]
    ranges: tuple[tuple[int, int], ...]
    mode: str
    transform: VideoEdit | None
    width: int
    height: int
    fps: int
