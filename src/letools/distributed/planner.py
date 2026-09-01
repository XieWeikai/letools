"""Static episode partitioning for scheduler-neutral distributed jobs."""

from __future__ import annotations

import uuid
from pathlib import Path

from letools.conversion import _normalize_version

from .source import open_source_spec
from .state import JobStore
from .types import (
    PROTOCOL_VERSION,
    DistributedPlan,
    DistributedTask,
    SourceSpec,
    WorkerConfig,
)


def plan_distributed_conversion(
    source: SourceSpec,
    destination: str | Path,
    target_version: str,
    job_dir: str | Path,
    *,
    task_count: int | None = None,
    episodes_per_task: int | None = None,
    worker: WorkerConfig,
    overwrite: bool = False,
    validate: bool = True,
) -> DistributedPlan:
    """Scan metadata once, partition contiguous episodes, and persist the plan."""

    if task_count is not None and episodes_per_task is not None:
        raise ValueError("Set only one of task_count and episodes_per_task")
    if task_count is not None and task_count <= 0:
        raise ValueError("task_count must be positive")
    if episodes_per_task is not None and episodes_per_task <= 0:
        raise ValueError("episodes_per_task must be positive")
    if worker.workers <= 0 or worker.video_workers <= 0:
        raise ValueError("Worker counts must be positive")
    dataset = open_source_spec(source)
    target = _normalize_version(target_version)
    if dataset.metadata.version == target:
        raise ValueError(f"Source is already {target}")
    destination_path = Path(destination).resolve(strict=False)
    if destination_path.exists() and not overwrite:
        raise FileExistsError(f"Destination already exists: {destination_path}")
    total = len(dataset.episodes)
    if total == 0:
        raise ValueError("Distributed conversion requires at least one episode")
    intervals: list[tuple[int, int]] = []
    if episodes_per_task is not None:
        intervals = [
            (start, min(total, start + episodes_per_task))
            for start in range(0, total, episodes_per_task)
        ]
    else:
        count = min(total, task_count or min(total, 32))
        quotient, remainder = divmod(total, count)
        start = 0
        for task_id in range(count):
            stop = start + quotient + (1 if task_id < remainder else 0)
            intervals.append((start, stop))
            start = stop
    tasks = []
    for task_id, (start, stop) in enumerate(intervals):
        tasks.append(
            DistributedTask(
                task_id=task_id,
                episode_start=start,
                episode_stop=stop,
                expected_frames=sum(item.length for item in dataset.episodes[start:stop]),
            )
        )
    plan = DistributedPlan(
        protocol_version=PROTOCOL_VERSION,
        job_id=uuid.uuid4().hex,
        source=source,
        destination=str(destination_path),
        target_version=target,
        source_version=dataset.metadata.version,
        total_episodes=dataset.metadata.total_episodes,
        total_frames=dataset.metadata.total_frames,
        tasks=tuple(tasks),
        worker=worker,
        overwrite=overwrite,
        validate=validate,
    )
    JobStore(job_dir).create(plan)
    return plan


__all__ = ["plan_distributed_conversion"]
