"""Immutable plans and results produced by the specialized LeRobot merger."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from letools.telemetry import StageMetrics


@dataclass(frozen=True)
class MergePlan:
    """Static resource and layout choices for one same-version merge."""

    schema_version: int
    sources: tuple[Path, ...]
    destination: Path
    version: str
    episodes: int
    frames: int
    tasks: int
    data_resources: int
    file_resources: int
    data_bytes: int
    file_bytes: int
    data_workers: int
    file_workers: int
    parquet_batch_rows: int
    max_inflight_memory_bytes: int
    chunks_size: int
    copy_strategy: str
    fingerprint: str
    confidence: str
    planning_seconds: float
    cache_hit: bool = False
    reasons: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class MergeContribution:
    """Episode and frame contribution made by one input dataset."""

    source: Path
    episodes: int
    frames: int


@dataclass(frozen=True)
class MergeResult:
    """Published merge identity, resource plan, totals, and stage evidence."""

    sources: tuple[Path, ...]
    destination: Path
    version: str
    episodes: int
    frames: int
    tasks: int
    elapsed_seconds: float
    cloned_files: int
    copied_files: int
    copied_bytes: int
    contributions: tuple[MergeContribution, ...]
    plan: MergePlan
    stages: dict[str, StageMetrics]


__all__ = ["MergeContribution", "MergePlan", "MergeResult"]
