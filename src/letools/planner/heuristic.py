from __future__ import annotations

import math
from dataclasses import dataclass

from letools.planner.types import (
    DatasetProfile,
    PerformanceOverrides,
    ResourceProfile,
    StorageProfile,
)


DATA_TARGETS_MB = (32, 64, 100, 128, 200, 256, 512)
VIDEO_TARGETS_MB = (64, 100, 200, 256, 400, 800)
WORKER_LATTICE = (1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64, 96)
_MIB = 1024**2
_DATA_TASK_OVERHEAD_BYTES = 16 * _MIB


@dataclass(frozen=True)
class HeuristicChoice:
    workers: int
    video_workers: int
    data_file_size_mb: int | None
    video_file_size_mb: int | None
    estimated_peak_memory_bytes: int
    estimated_data_tasks: int
    estimated_video_tasks: int
    reasons: tuple[str, ...]


def worker_candidates(cpu_limit: int, task_limit: int) -> tuple[int, ...]:
    limit = max(1, min(cpu_limit, max(1, task_limit)))
    values = {value for value in WORKER_LATTICE if value <= limit}
    values.add(limit)
    return tuple(sorted(values))


def _target_for_parallelism(
    candidates: tuple[int, ...],
    total_bytes: int,
    workers: int,
    preferred: int,
    tasks_per_worker: int = 2,
    maximize_tasks_when_insufficient: bool = False,
) -> int:
    if total_bytes <= 0:
        return preferred
    required_tasks = max(1, workers * tasks_per_worker)
    feasible = [
        target
        for target in candidates
        if math.ceil(total_bytes / (target * _MIB)) >= required_tasks
    ]
    pool = feasible or list(candidates)
    if not feasible and maximize_tasks_when_insufficient:
        return min(candidates)
    return min(pool, key=lambda target: (abs(target - preferred), target))


def _target_for_balanced_groups(
    candidates: tuple[int, ...],
    total_bytes: int,
    workers: int,
    preferred: int,
) -> int:
    if total_bytes <= 0:
        return preferred

    def score(target_mb: int) -> tuple[int, int, int]:
        target_bytes = target_mb * _MIB
        full_groups, remainder = divmod(total_bytes, target_bytes)
        groups = [target_bytes] * full_groups
        if remainder:
            groups.append(remainder)
        loads = [0] * max(1, workers)
        for group_bytes in groups:
            worker = min(range(len(loads)), key=loads.__getitem__)
            loads[worker] += group_bytes + _DATA_TASK_OVERHEAD_BYTES
        return max(loads), len(groups), abs(target_mb - preferred)

    return min(candidates, key=score)


def _memory_worker_cap(
    resources: ResourceProfile,
    dataset: DatasetProfile,
    target_mb: int,
) -> tuple[int, int]:
    usable = int(resources.effective_memory_bytes * 0.85)
    largest_input = dataset.data_logical_bytes.p95
    per_worker = max(target_mb * _MIB, largest_input) * 2 + 64 * _MIB
    workers = max(1, usable // max(1, per_worker))
    return workers, per_worker


def choose_heuristic(
    target_version: str,
    resources: ResourceProfile,
    dataset: DatasetProfile,
    source_storage: StorageProfile,
    destination_storage: StorageProfile,
    overrides: PerformanceOverrides,
) -> HeuristicChoice:
    reasons: list[str] = []
    numeric_overrides = {
        "workers": overrides.workers,
        "video_workers": overrides.video_workers,
        "data_file_size_mb": overrides.data_file_size_mb,
        "video_file_size_mb": overrides.video_file_size_mb,
    }
    invalid = [name for name, value in numeric_overrides.items() if value is not None and value < 1]
    if invalid:
        raise ValueError(f"Planner overrides must be positive: {', '.join(invalid)}")
    if overrides.workers and overrides.workers > resources.effective_cpus:
        raise ValueError("Data workers exceed the effective CPU allocation")
    if overrides.video_workers and overrides.video_workers > resources.effective_cpus:
        raise ValueError("Video workers exceed the effective CPU allocation")
    if target_version == "v2.1" and (
        overrides.data_file_size_mb is not None or overrides.video_file_size_mb is not None
    ):
        raise ValueError("V3 file-size targets are not applicable to v2.1 output")
    network_io = (
        source_storage.storage_class == "network"
        or destination_storage.storage_class == "network"
    )
    cpu_limit = resources.effective_cpus

    if target_version == "v3.0":
        preferred_data_target = overrides.data_file_size_mb or 100
        provisional_workers = overrides.workers or min(cpu_limit, dataset.episodes, 8)
        data_target = overrides.data_file_size_mb or _target_for_balanced_groups(
            DATA_TARGETS_MB,
            dataset.data_logical_bytes.total,
            provisional_workers,
            preferred_data_target,
        )
        workers = provisional_workers
        for _ in range(4):
            data_tasks = max(
                1,
                math.ceil(dataset.data_logical_bytes.total / (data_target * _MIB)),
            )
            memory_cap, per_worker_memory = _memory_worker_cap(resources, dataset, data_target)
            if overrides.workers and overrides.workers > memory_cap:
                raise ValueError("Data workers exceed the planner's memory safety limit")
            workers = overrides.workers or min(cpu_limit, data_tasks, memory_cap, 8)
            workers = max(1, workers)
            if overrides.data_file_size_mb is not None:
                break
            adjusted_target = _target_for_balanced_groups(
                DATA_TARGETS_MB,
                dataset.data_logical_bytes.total,
                workers,
                preferred_data_target,
            )
            if adjusted_target == data_target:
                break
            data_target = adjusted_target
        data_tasks = max(
            1,
            math.ceil(dataset.data_logical_bytes.total / (data_target * _MIB)),
        )
        memory_cap, per_worker_memory = _memory_worker_cap(resources, dataset, data_target)
        if overrides.workers and overrides.workers > memory_cap:
            raise ValueError("Data workers exceed the planner's memory safety limit")
        workers = overrides.workers or min(cpu_limit, data_tasks, memory_cap, 8)
        workers = max(1, workers)
        video_target = overrides.video_file_size_mb or 200
        video_tasks = (
            max(
                dataset.cameras,
                math.ceil(dataset.media_input_bytes.total / (video_target * _MIB)),
            )
            if dataset.video_files
            else 0
        )
    else:
        data_target = None
        video_target = None
        data_tasks = max(1, dataset.data_files)
        video_tasks = dataset.video_files
        per_worker_memory = dataset.data_logical_bytes.p95 * 2 + 64 * _MIB
        usable = int(resources.effective_memory_bytes * 0.85)
        memory_cap = max(1, usable // max(1, per_worker_memory))
        if overrides.workers and overrides.workers > memory_cap:
            raise ValueError("Data workers exceed the planner's memory safety limit")
        workers = overrides.workers or min(cpu_limit, data_tasks, memory_cap, 8)
        workers = max(1, workers)

    if overrides.video_workers is not None:
        video_workers = max(1, overrides.video_workers)
    elif not dataset.video_files:
        video_workers = 1
    elif network_io:
        video_workers = min(cpu_limit, max(1, video_tasks), 3)
        reasons.append("network storage starts video calibration at three workers")
    else:
        video_workers = min(cpu_limit, max(1, video_tasks), 8)
    if (
        target_version == "v3.0"
        and dataset.video_files
        and overrides.video_file_size_mb is None
    ):
        video_target = 100
        if network_io:
            video_target = _target_for_balanced_groups(
                VIDEO_TARGETS_MB,
                dataset.media_input_bytes.total,
                video_workers,
                200,
            )
        video_tasks = max(
            dataset.cameras,
            math.ceil(dataset.media_input_bytes.total / (video_target * _MIB)),
        )

    if overrides.workers is not None:
        reasons.append("data workers fixed by explicit override")
    elif workers < min(cpu_limit, data_tasks):
        reasons.append("data concurrency capped by memory or conservative static limit")
    if target_version == "v2.1":
        reasons.append("v3 target-size parameters are not applicable to v2.1 output")
    elif data_tasks < workers * 2:
        reasons.append("dataset has too few size groups to sustain requested data concurrency")
    if source_storage.mount_point == destination_storage.mount_point:
        reasons.append("source and destination share one filesystem mount")

    estimated_peak = min(
        resources.effective_memory_bytes,
        max(256 * _MIB, workers * per_worker_memory),
    )
    return HeuristicChoice(
        workers=workers,
        video_workers=max(1, video_workers),
        data_file_size_mb=data_target,
        video_file_size_mb=video_target,
        estimated_peak_memory_bytes=estimated_peak,
        estimated_data_tasks=data_tasks,
        estimated_video_tasks=video_tasks,
        reasons=tuple(reasons),
    )
