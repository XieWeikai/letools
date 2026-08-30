"""Strictly bounded calibration using real backend data and media primitives.

Calibration samples disjoint jobs where possible, writes only below a temporary
destination directory, and removes outputs after every measurement. It chooses
static worker counts; it is not a runtime controller.
"""

from __future__ import annotations

import math
import shutil
import tempfile
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pyarrow as pa
import pyarrow.parquet as pq

from letools._arrow import canonical_data_schema, cast_data_table
from letools._video import write_episode_media, write_media_group
from letools.conversion_types import VideoEncodingConfig
from letools.planner.heuristic import (
    VIDEO_TARGETS_MB,
    HeuristicChoice,
    _target_for_balanced_groups,
)
from letools.planner.types import CalibrationMeasurement, CalibrationOptions
from letools.plugins import DatasetSource


_MIB = 1024**2
_Job = tuple[int, Callable[[Path, int], None]]


@dataclass
class _Budget:
    options: CalibrationOptions
    started: float
    read_bytes: int = 0
    write_bytes: int = 0

    def allows(self, input_bytes: int) -> bool:
        """Check time/read/write limits before admitting another job batch."""

        return (
            time.perf_counter() - self.started < self.options.max_seconds
            and self.read_bytes + input_bytes <= self.options.max_read_bytes
            and self.write_bytes + input_bytes <= self.options.max_write_bytes
        )

    def consume(self, input_bytes: int) -> None:
        """Charge one completed symmetric read/write sample to the budget."""

        self.read_bytes += input_bytes
        self.write_bytes += input_bytes


def _group_by_limit(items: list, sizes: list[int], limit_bytes: int) -> list[list]:
    groups: list[list] = []
    current: list = []
    current_size = 0
    for item, size in zip(items, sizes, strict=True):
        if current and current_size + size >= limit_bytes:
            groups.append(current)
            current = []
            current_size = 0
        current.append(item)
        current_size += size
    if current:
        groups.append(current)
    return groups


def _v30_data_jobs(
    source: DatasetSource,
    target_mb: int,
) -> list[_Job]:
    episodes = list(source.episodes)
    sizes = [source.data_profile(episode).episode_logical_bytes for episode in episodes]
    groups = _group_by_limit(episodes, sizes, target_mb * _MIB)
    schema = canonical_data_schema(source)
    jobs: list[_Job] = []
    for group in groups:
        group_tuple = tuple(group)
        input_bytes = sum(
            source.data_profile(episode).resource_physical_bytes
            for episode in group_tuple
        )

        def run(root: Path, index: int, selected=group_tuple) -> None:
            tables = [cast_data_table(table, schema) for table in source.read_episodes(selected)]
            path = root / f"data-{index:04d}.parquet"
            pq.write_table(pa.concat_tables(tables), path)

        jobs.append((input_bytes, run))
    return jobs


def _v21_data_jobs(source: DatasetSource) -> list[_Job]:
    groups: dict[str, list] = defaultdict(list)
    for episode in source.episodes:
        groups[source.data_profile(episode).locality_key].append(episode)
    jobs: list[_Job] = []
    for episodes in groups.values():
        selected = tuple(episodes)
        input_bytes = source.data_profile(selected[0]).resource_physical_bytes

        def run(root: Path, index: int, group=selected) -> None:
            directory = root / f"data-{index:04d}"
            directory.mkdir(parents=True, exist_ok=True)
            for episode in group:
                pq.write_table(source.read_episode(episode), directory / f"{episode.index}.parquet")

        jobs.append((input_bytes, run))
    return jobs


def _v30_video_jobs(source: DatasetSource, target_mb: int = 32) -> list[_Job]:
    if not source.metadata.video_keys:
        return []
    episodes = list(source.episodes)
    jobs: list[_Job] = []
    for key in source.metadata.video_keys:
        sizes = [source.media_profile(episode, key).input_bytes for episode in episodes]
        groups = _group_by_limit(episodes, sizes, target_mb * _MIB)
        for group in groups:
            media = tuple(source.media_input(episode, key) for episode in group)
            input_bytes = sum(
                source.media_profile(episode, key).input_bytes for episode in group
            )

            def run(root: Path, index: int, inputs=media) -> None:
                write_media_group(
                    inputs,
                    root / f"video-{index:04d}.mp4",
                    source.metadata.fps,
                    VideoEncodingConfig(),
                )

            jobs.append((input_bytes, run))
    return jobs


def _v21_video_jobs(source: DatasetSource) -> list[_Job]:
    if not source.metadata.video_keys:
        return []
    jobs: list[_Job] = []
    for key in source.metadata.video_keys:
        groups: dict[str, list] = defaultdict(list)
        for episode in source.episodes:
            locality = source.media_profile(episode, key).locality_key
            groups[locality].append(episode)
        for episodes in groups.values():
            selected = tuple(
                (source.media_input(episode, key), episode.index)
                for episode in episodes
            )
            input_bytes = source.media_profile(episodes[0], key).input_bytes

            def run(root: Path, index: int, media=selected) -> None:
                directory = root / f"video-{index:04d}"
                write_episode_media(
                    [
                        (item, directory / f"{episode_index}.mp4")
                        for item, episode_index in media
                    ],
                    source.metadata.fps,
                    VideoEncodingConfig(),
                )

            jobs.append((input_bytes, run))
    return jobs


def _sample_worker_batches(
    jobs: list[_Job],
    worker_values: tuple[int, ...],
    max_bytes: int,
) -> list[list[_Job]]:
    batches: list[list[_Job]] = []
    cursor = 0
    consumed = 0
    for workers in worker_values:
        if cursor + workers > len(jobs):
            break
        selected = jobs[cursor : cursor + workers]
        input_bytes = sum(job[0] for job in selected)
        if consumed + input_bytes > max_bytes:
            break
        batches.append(selected)
        cursor += workers
        consumed += input_bytes
    return batches


def _sample_comparison_batches(
    jobs: list[_Job],
    worker_values: tuple[int, ...],
    max_bytes: int,
) -> list[list[_Job]]:
    disjoint = _sample_worker_batches(jobs, worker_values, max_bytes)
    if len(disjoint) == len(worker_values):
        return disjoint

    prefixes: list[list[_Job]] = []
    consumed = 0
    for workers in worker_values:
        selected = jobs[:workers]
        if len(selected) < workers:
            return disjoint
        consumed += sum(job[0] for job in selected)
        if consumed > max_bytes:
            return disjoint
        prefixes.append(selected)
    return prefixes


def _run_jobs(jobs: list[_Job], workers: int, output: Path) -> float:
    output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=min(workers, len(jobs))) as pool:
        futures = [pool.submit(job, output, index) for index, (_, job) in enumerate(jobs)]
        for future in futures:
            future.result()
    return time.perf_counter() - started


def _measure_stage(
    stage: str,
    job_batches: list[list[_Job]],
    worker_values: tuple[int, ...],
    root: Path,
    budget: _Budget,
) -> tuple[int | None, list[CalibrationMeasurement]]:
    if not job_batches:
        return None, []
    measurements: list[CalibrationMeasurement] = []
    best_workers: int | None = None
    best_throughput = 0.0
    for workers, jobs in zip(worker_values, job_batches, strict=False):
        input_bytes = sum(item[0] for item in jobs)
        if not budget.allows(input_bytes):
            break
        output = root / f"{stage}-{workers}"
        try:
            elapsed = _run_jobs(jobs, workers, output)
        finally:
            shutil.rmtree(output, ignore_errors=True)
        budget.consume(input_bytes)
        measurement = CalibrationMeasurement(
            stage=stage,
            workers=workers,
            tasks=len(jobs),
            input_bytes=input_bytes,
            elapsed_seconds=elapsed,
        )
        measurements.append(measurement)
        throughput = measurement.throughput_bytes_per_second
        if throughput > best_throughput * 1.03:
            best_throughput = throughput
            best_workers = workers
        if len(measurements) >= 3:
            recent = measurements[-3:]
            if recent[-1].throughput_bytes_per_second <= max(
                item.throughput_bytes_per_second for item in recent[:-1]
            ) * 1.03:
                break
    return best_workers, measurements


def _extrapolate_worker_ceiling(
    measurements: list[CalibrationMeasurement],
    requested_workers: int,
) -> int | None:
    if len(measurements) < 2:
        return None
    previous, latest = measurements[-2:]
    if latest.workers >= requested_workers or previous.workers >= latest.workers:
        return None
    observed_speedup = (
        latest.throughput_bytes_per_second
        / max(previous.throughput_bytes_per_second, 1e-9)
    )
    ideal_speedup = latest.workers / previous.workers
    if observed_speedup >= ideal_speedup * 0.80:
        return requested_workers
    return None


def calibrate_workers(
    source: DatasetSource,
    target_version: str,
    destination_parent: Path,
    choice: HeuristicChoice,
    cpu_limit: int,
    options: CalibrationOptions,
    fixed_data_workers: bool = False,
    fixed_video_workers: bool = False,
    network_io: bool = False,
) -> tuple[HeuristicChoice, tuple[CalibrationMeasurement, ...]]:
    """Measure representative worker points and return an updated static choice.

    Explicit overrides remain fixed. Small workloads skip calibration when its
    cost is unlikely to be recovered by the subsequent conversion.
    """

    if not options.enabled:
        return choice, ()
    data_resources = {
        source.data_profile(episode).locality_key:
            source.data_profile(episode).resource_physical_bytes
        for episode in source.episodes
    }
    media_resources = {
        source.media_profile(episode, key).locality_key:
            source.media_profile(episode, key).input_bytes
        for episode in source.episodes
        for key in source.metadata.video_keys
    }
    data_bytes = sum(data_resources.values())
    video_bytes = sum(media_resources.values())
    total_bytes = data_bytes + video_bytes
    if total_bytes < 64 * _MIB or (not video_bytes and data_bytes < 512 * _MIB):
        return choice, ()

    root = Path(tempfile.mkdtemp(prefix=".letools-calibration-", dir=destination_parent))
    budget = _Budget(options=options, started=time.perf_counter())
    measurements: list[CalibrationMeasurement] = []
    try:
        selected_data = None
        selected_video = None
        calibrate_data = not video_bytes or data_bytes >= total_bytes * 0.10
        extrapolated_video = False
        if calibrate_data:
            data_jobs = (
                _v30_data_jobs(source, choice.data_file_size_mb or 100)
                if target_version == "v3.0"
                else _v21_data_jobs(source)
            )
            data_values = (
                (choice.workers,)
                if fixed_data_workers
                else tuple(
                    sorted({1, choice.workers, min(cpu_limit, len(data_jobs))})
                )
            )
            data_budget = options.max_read_bytes if not video_bytes else options.max_read_bytes // 2
            data_batches = _sample_comparison_batches(data_jobs, data_values, data_budget)
            data_pairs = [
                (workers, jobs)
                for workers, jobs in zip(data_values, data_batches, strict=False)
                if workers <= len(jobs) or fixed_data_workers
            ]
            selected_data, values = _measure_stage(
                "data",
                [jobs for _, jobs in data_pairs],
                tuple(workers for workers, _ in data_pairs),
                root,
                budget,
            )
            measurements.extend(values)

        video_jobs = (
            _v30_video_jobs(source) if target_version == "v3.0" else _v21_video_jobs(source)
        )
        video_values = (
            (choice.video_workers,)
            if fixed_video_workers
            else tuple(
                sorted(
                    value
                    for value in {1, choice.video_workers, min(cpu_limit, len(video_jobs))}
                    if value > 0
                )
            )
        )
        remaining_bytes = max(0, options.max_read_bytes - budget.read_bytes)
        video_batches = _sample_worker_batches(video_jobs, video_values, remaining_bytes)
        video_pairs = [
            (workers, jobs)
            for workers, jobs in zip(video_values, video_batches, strict=False)
            if workers <= len(jobs) or fixed_video_workers
        ]
        selected_video, values = _measure_stage(
            "video",
            [jobs for _, jobs in video_pairs],
            tuple(workers for workers, _ in video_pairs),
            root,
            budget,
        )
        requested_video_workers = (
            min(max(video_values), 16) if network_io else max(video_values)
        )
        extrapolated = _extrapolate_worker_ceiling(values, requested_video_workers)
        if extrapolated is not None and not fixed_video_workers:
            selected_video = extrapolated
            extrapolated_video = True
        measurements.extend(values)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    if not measurements:
        return choice, ()
    video_workers = selected_video or choice.video_workers
    video_target = choice.video_file_size_mb
    if target_version == "v3.0" and selected_video and not fixed_video_workers:
        video_target = 100
        if network_io:
            video_target = _target_for_balanced_groups(
                VIDEO_TARGETS_MB,
                video_bytes,
                video_workers,
                choice.video_file_size_mb or 200,
            )
    estimated_video_tasks = choice.estimated_video_tasks
    if target_version == "v3.0" and video_target and video_bytes:
        estimated_video_tasks = max(
            len(source.metadata.video_keys),
            math.ceil(video_bytes / (video_target * _MIB)),
        )
    updated = HeuristicChoice(
        workers=selected_data or choice.workers,
        video_workers=video_workers,
        data_file_size_mb=choice.data_file_size_mb,
        video_file_size_mb=video_target,
        estimated_peak_memory_bytes=choice.estimated_peak_memory_bytes,
        estimated_data_tasks=choice.estimated_data_tasks,
        estimated_video_tasks=estimated_video_tasks,
        reasons=(
            *choice.reasons,
            "worker counts selected by bounded workload calibration",
            *(
                ("video concurrency extrapolated from unsaturated calibration throughput",)
                if extrapolated_video
                else ()
            ),
        ),
    )
    return updated, tuple(measurements)
