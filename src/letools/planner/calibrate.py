from __future__ import annotations

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
from letools._video import concatenate_videos, split_video
from letools.planner.heuristic import HeuristicChoice, worker_candidates
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
        return (
            time.perf_counter() - self.started < self.options.max_seconds
            and self.read_bytes + input_bytes <= self.options.max_read_bytes
            and self.write_bytes + input_bytes <= self.options.max_write_bytes
        )

    def consume(self, input_bytes: int) -> None:
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


def _parquet_uncompressed(path: Path) -> int:
    metadata = pq.read_metadata(path)
    return sum(
        metadata.row_group(row_group).column(column).total_uncompressed_size
        for row_group in range(metadata.num_row_groups)
        for column in range(metadata.row_group(row_group).num_columns)
    )


def _v30_data_jobs(
    source: DatasetSource,
    target_mb: int,
) -> list[_Job]:
    episodes = list(source.episodes)
    sizes = [_parquet_uncompressed(episode.data_path) for episode in episodes]
    groups = _group_by_limit(episodes, sizes, target_mb * _MIB)
    schema = canonical_data_schema(source)
    jobs: list[_Job] = []
    for group in groups:
        group_tuple = tuple(group)
        input_bytes = sum(episode.data_path.stat().st_size for episode in group_tuple)

        def run(root: Path, index: int, selected=group_tuple) -> None:
            tables = [cast_data_table(table, schema) for table in source.read_episodes(selected)]
            path = root / f"data-{index:04d}.parquet"
            pq.write_table(pa.concat_tables(tables), path)

        jobs.append((input_bytes, run))
    return jobs


def _v21_data_jobs(source: DatasetSource) -> list[_Job]:
    groups: dict[Path, list] = defaultdict(list)
    for episode in source.episodes:
        groups[episode.data_path].append(episode)
    jobs: list[_Job] = []
    for path, episodes in groups.items():
        selected = tuple(episodes)

        def run(root: Path, index: int, group=selected) -> None:
            directory = root / f"data-{index:04d}"
            directory.mkdir(parents=True, exist_ok=True)
            for episode in group:
                pq.write_table(source.read_episode(episode), directory / f"{episode.index}.parquet")

        jobs.append((path.stat().st_size, run))
    return jobs


def _v30_video_jobs(source: DatasetSource) -> list[_Job]:
    if not source.metadata.video_keys:
        return []
    key = source.metadata.video_keys[0]
    episodes = list(source.episodes)
    sizes = [episode.videos[key].path.stat().st_size for episode in episodes]
    groups = _group_by_limit(episodes, sizes, 32 * _MIB)
    jobs: list[_Job] = []
    for group in groups:
        paths = tuple(episode.videos[key].path for episode in group)
        input_bytes = sum(path.stat().st_size for path in paths)

        def run(root: Path, index: int, inputs=paths) -> None:
            concatenate_videos(inputs, root / f"video-{index:04d}.mp4")

        jobs.append((input_bytes, run))
    return jobs


def _v21_video_jobs(source: DatasetSource) -> list[_Job]:
    if not source.metadata.video_keys:
        return []
    key = source.metadata.video_keys[0]
    groups: dict[Path, list] = defaultdict(list)
    for episode in source.episodes:
        groups[episode.videos[key].path].append(episode)
    jobs: list[_Job] = []
    for path, episodes in groups.items():
        selected = tuple((episode.videos[key], episode.index) for episode in episodes)

        def run(root: Path, index: int, slices=selected, source_path=path) -> None:
            directory = root / f"video-{index:04d}"
            split_video(
                source_path,
                [(video_slice, directory / f"{episode_index}.mp4") for video_slice, episode_index in slices],
            )

        jobs.append((path.stat().st_size, run))
    return jobs


def _sample_job_batches(
    jobs: list[_Job],
    per_run_bytes: int,
    max_tasks: int,
    count: int,
) -> list[list[_Job]]:
    if not jobs:
        return []
    batches: list[list[_Job]] = []
    cursor = 0
    for _ in range(count):
        selected: list[_Job] = []
        total = 0
        while cursor < len(jobs):
            job = jobs[cursor]
            if selected and (total + job[0] > per_run_bytes or len(selected) >= max_tasks):
                break
            selected.append(job)
            total += job[0]
            cursor += 1
        if not selected:
            break
        batches.append(selected)
    return batches


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


def calibrate_workers(
    source: DatasetSource,
    target_version: str,
    destination_parent: Path,
    choice: HeuristicChoice,
    cpu_limit: int,
    options: CalibrationOptions,
    fixed_data_workers: bool = False,
    fixed_video_workers: bool = False,
) -> tuple[HeuristicChoice, tuple[CalibrationMeasurement, ...]]:
    if not options.enabled:
        return choice, ()
    data_paths = {
        episode.data_path: episode.data_path.stat().st_size for episode in source.episodes
    }
    video_paths = {
        video.path: video.path.stat().st_size
        for episode in source.episodes
        for video in episode.videos.values()
    }
    data_bytes = sum(data_paths.values())
    video_bytes = sum(video_paths.values())
    total_bytes = data_bytes + video_bytes
    if total_bytes < 64 * _MIB:
        return choice, ()

    root = Path(tempfile.mkdtemp(prefix=".letools-calibration-", dir=destination_parent))
    budget = _Budget(options=options, started=time.perf_counter())
    measurements: list[CalibrationMeasurement] = []
    try:
        selected_data = None
        selected_video = None
        calibrate_data = not video_bytes or data_bytes >= total_bytes * 0.10
        if calibrate_data:
            data_jobs = (
                _v30_data_jobs(source, choice.data_file_size_mb or 100)
                if target_version == "v3.0"
                else _v21_data_jobs(source)
            )
            data_values = (
                (choice.workers,)
                if fixed_data_workers
                else worker_candidates(cpu_limit, len(data_jobs))
            )
            data_budget = options.max_read_bytes if not video_bytes else options.max_read_bytes // 2
            data_batches = _sample_job_batches(
                data_jobs,
                max(16 * _MIB, data_budget // max(1, len(data_values))),
                min(cpu_limit * 2, 32),
                len(data_values),
            )
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
            else worker_candidates(cpu_limit, min(len(video_jobs), cpu_limit * 2))
        )
        remaining_bytes = max(0, options.max_read_bytes - budget.read_bytes)
        video_batches = _sample_job_batches(
            video_jobs,
            max(32 * _MIB, remaining_bytes // max(1, len(video_values))),
            min(cpu_limit * 2, 32),
            len(video_values),
        )
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
        measurements.extend(values)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    if not measurements:
        return choice, ()
    updated = HeuristicChoice(
        workers=selected_data or choice.workers,
        video_workers=selected_video or choice.video_workers,
        data_file_size_mb=choice.data_file_size_mb,
        video_file_size_mb=choice.video_file_size_mb,
        estimated_peak_memory_bytes=choice.estimated_peak_memory_bytes,
        estimated_data_tasks=choice.estimated_data_tasks,
        estimated_video_tasks=choice.estimated_video_tasks,
        reasons=(*choice.reasons, "worker counts selected by bounded workload calibration"),
    )
    return updated, tuple(measurements)
