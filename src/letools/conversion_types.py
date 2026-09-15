"""Immutable execution inputs and observable conversion result contracts."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from letools.telemetry import StageMetrics


@dataclass(frozen=True)
class VideoEncodingConfig:
    """Encoding policy used only when a source provides image frames."""

    codec: str = "mjpeg"
    # None preserves JPEG payloads as-is; encoders otherwise use a compatible
    # default. An explicit value is a conversion requirement, never a label.
    pixel_format: str | None = None
    batch_frames: int = 48
    codec_threads: int = 1

    def __post_init__(self) -> None:
        if not self.codec or (self.pixel_format is not None and not self.pixel_format):
            raise ValueError("Video codec and pixel format cannot be empty")
        if self.batch_frames < 1 or self.codec_threads < 1:
            raise ValueError("Video batch size and codec thread count must be positive")

    @property
    def encoder_pixel_format(self) -> str:
        """Resolve a pixel format only for the decode/encode path."""
        return self.pixel_format or ("yuvj420p" if self.codec == "mjpeg" else "yuv420p")


@dataclass(frozen=True)
class ConversionConfig:
    """Explicit executor controls; no field performs resource discovery."""

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
    """Published conversion identity, totals, wall time, and phase metrics."""

    source: Path
    destination: Path
    source_version: str
    target_version: str
    episodes: int
    frames: int
    elapsed_seconds: float
    stages: dict[str, StageMetrics]
