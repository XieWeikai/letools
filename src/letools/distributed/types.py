"""Versioned, scheduler-neutral contracts for distributed conversion.

Every object in this module is JSON serializable. A coordinator may therefore
create a plan on a login node and workers may reconstruct it under Slurm,
Kubernetes, or another batch system without pickling live Python objects.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, cast


PROTOCOL_VERSION = 1
SourceKind = Literal["lerobot", "hdf5", "agilex"]


@dataclass(frozen=True)
class SourceSpec:
    """Portable instructions for reopening one source on a worker node."""

    kind: SourceKind
    root: str
    options: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SourceSpec:
        kind = str(value["kind"])
        if kind not in {"lerobot", "hdf5", "agilex"}:
            raise ValueError(f"Unsupported distributed source kind: {kind}")
        return cls(
            cast(SourceKind, kind),
            str(value["root"]),
            dict(value.get("options", {})),
        )


@dataclass(frozen=True)
class DistributedTask:
    """One deterministic, contiguous episode interval owned by one worker."""

    task_id: int
    episode_start: int
    episode_stop: int
    expected_frames: int

    @property
    def episodes(self) -> int:
        return self.episode_stop - self.episode_start


@dataclass(frozen=True)
class WorkerConfig:
    """Node-local controls used by every task in one distributed job."""

    workers: int
    video_workers: int
    data_file_size_mb: int = 100
    video_file_size_mb: int = 200


@dataclass(frozen=True)
class DistributedPlan:
    """Immutable plan consumed identically by every scheduler adapter."""

    protocol_version: int
    job_id: str
    source: SourceSpec
    destination: str
    target_version: str
    source_version: str
    total_episodes: int
    total_frames: int
    tasks: tuple[DistributedTask, ...]
    worker: WorkerConfig
    overwrite: bool = False
    validate: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> DistributedPlan:
        version = int(value.get("protocol_version", 0))
        if version != PROTOCOL_VERSION:
            raise ValueError(
                f"Unsupported distributed protocol {version}; expected {PROTOCOL_VERSION}"
            )
        return cls(
            protocol_version=version,
            job_id=str(value["job_id"]),
            source=SourceSpec.from_dict(value["source"]),
            destination=str(value["destination"]),
            target_version=str(value["target_version"]),
            source_version=str(value["source_version"]),
            total_episodes=int(value["total_episodes"]),
            total_frames=int(value["total_frames"]),
            tasks=tuple(DistributedTask(**task) for task in value["tasks"]),
            worker=WorkerConfig(**value["worker"]),
            overwrite=bool(value.get("overwrite", False)),
            validate=bool(value.get("validate", True)),
        )


@dataclass(frozen=True)
class TaskResult:
    """Durable completion record written after a part is fully published."""

    protocol_version: int
    job_id: str
    task_id: int
    episode_start: int
    episode_stop: int
    episodes: int
    frames: int
    part: str
    elapsed_seconds: float
    worker_host: str


@dataclass(frozen=True)
class DistributedStatus:
    """Read-only snapshot derived from plan and durable result records."""

    job_id: str
    state: str
    completed_tasks: int
    total_tasks: int
    completed_episodes: int
    total_episodes: int
    destination: Path


@dataclass(frozen=True)
class SubmissionResult:
    """Scheduler submission identity and generated artifact locations."""

    scheduler: str
    scheduler_job_id: str | None
    job_dir: Path
    artifacts: tuple[Path, ...] = ()
