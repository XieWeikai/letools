from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ConversionConfig:
    workers: int = max(1, min(8, os.cpu_count() or 1))
    video_workers: int = 1
    data_file_size_mb: int = 100
    video_file_size_mb: int = 200
    chunks_size: int = 1000
    overwrite: bool = False
    validate: bool = True


@dataclass(frozen=True)
class ConversionResult:
    source: Path
    destination: Path
    source_version: str
    target_version: str
    episodes: int
    frames: int
    elapsed_seconds: float
