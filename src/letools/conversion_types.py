from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from letools.telemetry import StageMetrics


@dataclass(frozen=True)
class VideoEncodingConfig:
    """Encoding policy used only when a source provides image frames."""

    codec: str = "mpeg4"
    pixel_format: str = "yuv420p"
    batch_frames: int = 32
    codec_threads: int = 1


@dataclass(frozen=True)
class ConversionConfig:
    workers: int = max(1, min(8, os.cpu_count() or 1))
    video_workers: int = max(1, min(3, os.cpu_count() or 1))
    data_file_size_mb: int = 100
    video_file_size_mb: int = 200
    chunks_size: int = 1000
    overwrite: bool = False
    validate: bool = True
    video_encoding: VideoEncodingConfig = field(default_factory=VideoEncodingConfig)


@dataclass(frozen=True)
class ConversionResult:
    source: Path
    destination: Path
    source_version: str
    target_version: str
    episodes: int
    frames: int
    elapsed_seconds: float
    stages: dict[str, StageMetrics]
