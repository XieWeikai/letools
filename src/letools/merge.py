"""Purpose-built, same-version LeRobot dataset merge engine.

Merge is intentionally independent of the extensible DatasetSource/backend
conversion path. Its input contract is fixed to physical LeRobot v2.1 or v3.0
datasets, which permits whole-file media cloning and one-pass Parquet index
rewrites without video remuxing or intermediate layouts.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import shutil
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from letools._io import write_json, write_jsonl
from letools._native import clone_or_copy_files
from letools._stats import aggregate_episode_stats, flatten_stats
from letools.merge_types import MergeContribution, MergePlan, MergeResult
from letools.model import Episode, VideoSlice
from letools.planner.inspect import inspect_resources, inspect_storage
from letools.plugins import DatasetSource, open_dataset
from letools.telemetry import StageRecorder


_SYSTEM_COLUMNS = ("episode_index", "index", "task_index")
_MIB = 1024**2
_PLAN_SCHEMA = 1
_ALGORITHM_VERSION = 1


@dataclass(frozen=True)
class _MappedEpisode:
    """One source episode plus its deterministic output identity."""

    output_index: int
    output_frame_start: int
    source_ordinal: int
    source: DatasetSource
    episode: Episode


@dataclass(frozen=True)
class _DataResource:
    """One physical Parquet input rewritten exactly once."""

    source_path: Path
    episodes: tuple[_MappedEpisode, ...]
    physical_bytes: int
    logical_bytes: int


@dataclass(frozen=True)
class _MediaResource:
    """One complete encoded media file copied without FFmpeg processing."""

    key: str
    source_path: Path
    physical_bytes: int


@dataclass(frozen=True)
class _Manifest:
    """Validated fixed-format inputs and every global remapping table."""

    roots: tuple[Path, ...]
    sources: tuple[DatasetSource, ...]
    version: Literal["v2.1", "v3.0"]
    split: str
    info: dict[str, Any]
    tasks: dict[int, str]
    task_maps: tuple[dict[int, int], ...]
    episode_maps: tuple[tuple[int, ...], ...]
    episodes: tuple[_MappedEpisode, ...]
    data_resources: tuple[_DataResource, ...]
    media_resources: tuple[_MediaResource, ...]
    total_frames: int


def _normalized_features(features: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Remove only layout-specific metadata before strict feature comparison."""

    normalized = copy.deepcopy(features)
    for feature in normalized.values():
        if feature.get("dtype") != "video":
            feature.pop("fps", None)
    return normalized


def _full_split(source: DatasetSource) -> str:
    """Accept one named split covering the complete physical input."""

    splits = source.metadata.splits
    if len(splits) != 1:
        raise ValueError(f"Merge requires one full-dataset split, got {splits!r}")
    name, value = next(iter(splits.items()))
    if value != f"0:{source.metadata.total_episodes}":
        raise ValueError(
            f"Merge requires split {name!r} to cover every episode; got {value!r}"
        )
    return name


def _parquet_logical_bytes(path: Path) -> int:
    metadata = pq.read_metadata(path)
    return sum(
        metadata.row_group(group).column(column).total_uncompressed_size
        for group in range(metadata.num_row_groups)
        for column in range(metadata.row_group(group).num_columns)
    )


def _build_manifest(paths: list[str | Path]) -> _Manifest:
    """Open, validate, and globally index fixed LeRobot inputs in CLI order."""

    if len(paths) < 2:
        raise ValueError("Merge requires at least two input datasets")
    roots = tuple(Path(path).resolve() for path in paths)
    if len(set(roots)) != len(roots):
        raise ValueError("The same input dataset cannot be merged more than once")
    sources = tuple(open_dataset(root) for root in roots)
    first = sources[0]
    if first.metadata.version not in {"v2.1", "v3.0"}:
        raise ValueError(f"Unsupported merge version: {first.metadata.version}")
    version: Literal["v2.1", "v3.0"] = first.metadata.version  # type: ignore[assignment]
    expected_features = _normalized_features(first.metadata.features)
    split = _full_split(first)

    for root, source in zip(roots, sources, strict=True):
        metadata = source.metadata
        if metadata.version != version:
            raise ValueError("All merge inputs must use the same LeRobot version")
        if metadata.fps != first.metadata.fps:
            raise ValueError(f"FPS differs in {root}: {metadata.fps} != {first.metadata.fps}")
        if metadata.robot_type != first.metadata.robot_type:
            raise ValueError(
                f"robot_type differs in {root}: {metadata.robot_type!r} "
                f"!= {first.metadata.robot_type!r}"
            )
        if _normalized_features(metadata.features) != expected_features:
            raise ValueError(f"Feature schema differs in {root}")
        if _full_split(source) != split:
            raise ValueError(f"Split name differs in {root}")
        indices = tuple(episode.index for episode in source.episodes)
        if indices != tuple(range(metadata.total_episodes)):
            raise ValueError(f"Episode indices are not contiguous in {root}")
        if sum(episode.length for episode in source.episodes) != metadata.total_frames:
            raise ValueError(f"Episode frame total differs from info.json in {root}")

    tasks: dict[int, str] = {}
    task_by_text: dict[str, int] = {}
    task_maps: list[dict[int, int]] = []
    for source in sources:
        mapping: dict[int, int] = {}
        for local_index, task in sorted(source.metadata.tasks.items()):
            output_index = task_by_text.setdefault(task, len(task_by_text))
            tasks.setdefault(output_index, task)
            mapping[local_index] = output_index
        task_maps.append(mapping)

    mapped: list[_MappedEpisode] = []
    episode_maps: list[tuple[int, ...]] = []
    frame_start = 0
    episode_start = 0
    for source_ordinal, source in enumerate(sources):
        local_map = tuple(episode_start + index for index in range(len(source.episodes)))
        episode_maps.append(local_map)
        for episode in source.episodes:
            mapped.append(
                _MappedEpisode(
                    output_index=local_map[episode.index],
                    output_frame_start=frame_start,
                    source_ordinal=source_ordinal,
                    source=source,
                    episode=episode,
                )
            )
            frame_start += episode.length
        episode_start += len(source.episodes)

    data_groups: dict[Path, list[_MappedEpisode]] = {}
    media_paths: dict[tuple[str, Path], None] = {}
    for episode in mapped:
        data_groups.setdefault(episode.episode.data_path, []).append(episode)
        for key in first.metadata.video_keys:
            media = episode.source.media_input(episode.episode, key)
            if not isinstance(media, VideoSlice):
                raise TypeError("Same-version merge supports only encoded LeRobot video files")
            media_paths.setdefault((key, media.path), None)
    data_resources = tuple(
        _DataResource(
            path,
            tuple(episodes),
            path.stat().st_size,
            _parquet_logical_bytes(path),
        )
        for path, episodes in data_groups.items()
    )
    if version == "v2.1" and any(len(resource.episodes) != 1 for resource in data_resources):
        raise ValueError("LeRobot v2.1 merge requires one Parquet file per episode")
    media_resources = tuple(
        _MediaResource(key, path, path.stat().st_size)
        for key, path in media_paths
    )
    return _Manifest(
        roots=roots,
        sources=sources,
        version=version,
        split=split,
        info=copy.deepcopy(first.metadata.info),
        tasks=tasks,
        task_maps=tuple(task_maps),
        episode_maps=tuple(episode_maps),
        episodes=tuple(mapped),
        data_resources=data_resources,
        media_resources=media_resources,
        total_frames=frame_start,
    )


def _fingerprint(manifest: _Manifest, destination: Path, resources: Any) -> str:
    source_storage = [inspect_storage(root) for root in manifest.roots]
    destination_storage = inspect_storage(destination)
    payload = {
        "schema": _PLAN_SCHEMA,
        "algorithm": _ALGORITHM_VERSION,
        "version": manifest.version,
        "cpus": resources.effective_cpus,
        "memory_gib": resources.effective_memory_bytes // 1024**3,
        "sources": [
            {
                "filesystem": item.filesystem,
                "device": item.device,
                "class": item.storage_class,
            }
            for item in source_storage
        ],
        "destination": {
            "filesystem": destination_storage.filesystem,
            "device": destination_storage.device,
            "class": destination_storage.storage_class,
        },
        "shape": {
            "episodes": len(manifest.episodes),
            "frames": manifest.total_frames,
            "data": sorted(resource.physical_bytes for resource in manifest.data_resources),
            "media": sorted(resource.physical_bytes for resource in manifest.media_resources),
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _cache_path(fingerprint: str) -> Path:
    return Path.home() / ".cache/letools/merge-plans" / f"{fingerprint}.json"


def _load_cached_plan(path: Path) -> tuple[int, int, int] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return int(value["data_workers"]), int(value["file_workers"]), int(value["batch_rows"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _save_cached_plan(path: Path, plan: MergePlan) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}-{uuid.uuid4().hex}")
        temporary.write_text(
            json.dumps(
                {
                    "data_workers": plan.data_workers,
                    "file_workers": plan.file_workers,
                    "batch_rows": plan.parquet_batch_rows,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(path)
    except OSError:
        pass


def _heuristic_plan(
    manifest: _Manifest,
    destination: Path,
    *,
    data_workers: int | None,
    file_workers: int | None,
) -> MergePlan:
    """Bound worker counts by actual allocation, task count, and peak memory."""

    started = time.perf_counter()
    resources = inspect_resources()
    source_storage = [inspect_storage(root) for root in manifest.roots]
    destination_storage = inspect_storage(destination)
    network = destination_storage.storage_class == "network" or any(
        storage.storage_class == "network" for storage in source_storage
    )
    data_tasks = len(manifest.data_resources)
    file_tasks = len(manifest.media_resources)
    largest_logical = max(
        (resource.logical_bytes for resource in manifest.data_resources), default=_MIB
    )
    memory_budget = max(256 * _MIB, resources.effective_memory_bytes // 2)
    memory_cap = max(1, memory_budget // max(2 * largest_logical, 64 * _MIB))
    default_data = min(resources.effective_cpus, data_tasks or 1, memory_cap, 8 if network else 16)
    selected_data = data_workers or max(1, default_data)
    if selected_data > resources.effective_cpus or selected_data > memory_cap:
        raise ValueError("Requested data workers exceed the CPU or merge memory budget")
    remaining = max(1, resources.effective_cpus - selected_data)
    default_files = min(remaining, file_tasks or 1, 8 if network else 16)
    selected_files = file_workers or max(1, default_files)
    if selected_files > resources.effective_cpus:
        raise ValueError("Requested file workers exceed the CPU allocation")
    bytes_per_row = max(
        1,
        sum(resource.logical_bytes for resource in manifest.data_resources)
        // manifest.total_frames,
    )
    per_worker = max(16 * _MIB, memory_budget // selected_data)
    batch_rows = max(1024, min(131072, per_worker // max(bytes_per_row * 3, 1)))
    fingerprint = _fingerprint(manifest, destination, resources)
    reasons = (
        f"effective allocation is {resources.effective_cpus} CPUs and "
        f"{resources.effective_memory_bytes / 1024**3:.1f} GiB",
        f"source/destination storage is {'network-sensitive' if network else 'local'}",
        f"data concurrency is capped at {memory_cap} by the half-allocation memory budget",
        "media files are cloned when supported and copied otherwise; FFmpeg is not used",
    )
    return MergePlan(
        schema_version=_PLAN_SCHEMA,
        sources=manifest.roots,
        destination=destination,
        version=manifest.version,
        episodes=len(manifest.episodes),
        frames=manifest.total_frames,
        tasks=len(manifest.tasks),
        data_resources=data_tasks,
        file_resources=file_tasks,
        data_bytes=sum(resource.physical_bytes for resource in manifest.data_resources),
        file_bytes=sum(resource.physical_bytes for resource in manifest.media_resources),
        data_workers=selected_data,
        file_workers=selected_files,
        parquet_batch_rows=int(batch_rows),
        max_inflight_memory_bytes=memory_budget,
        chunks_size=int(manifest.info.get("chunks_size", 1000)),
        copy_strategy="reflink-or-copy",
        fingerprint=fingerprint,
        confidence="heuristic",
        planning_seconds=time.perf_counter() - started,
        reasons=reasons,
    )


def _validate_destination(manifest: _Manifest, destination: Path) -> None:
    """Reject destructive path relationships and obviously insufficient storage."""

    for root in manifest.roots:
        if destination == root or destination in root.parents or root in destination.parents:
            raise ValueError(
                f"Merge destination and source directories must not contain each other: "
                f"{destination}, {root}"
            )
    required = sum(item.physical_bytes for item in manifest.data_resources) + sum(
        item.physical_bytes for item in manifest.media_resources
    )
    storage = inspect_storage(destination)
    if storage.free_bytes < required:
        raise OSError(
            f"Merge may require {required} bytes, but {storage.existing_path} reports only "
            f"{storage.free_bytes} bytes free"
        )


def plan_merge(
    sources: list[str | Path],
    destination: str | Path,
    *,
    data_workers: int | None = None,
    file_workers: int | None = None,
    calibrate: bool = False,
    calibration_seconds: float = 10.0,
    calibration_bytes: int = 1024**3,
    use_cache: bool = True,
) -> MergePlan:
    """Inspect same-version inputs and return a bounded static merge plan."""

    started = time.perf_counter()
    manifest = _build_manifest(sources)
    destination_path = Path(destination).resolve(strict=False)
    _validate_destination(manifest, destination_path)
    plan = _heuristic_plan(
        manifest,
        destination_path,
        data_workers=data_workers,
        file_workers=file_workers,
    )
    cached = _load_cached_plan(_cache_path(plan.fingerprint)) if use_cache else None
    if cached and data_workers is None and file_workers is None:
        plan = replace(
            plan,
            data_workers=cached[0],
            file_workers=cached[1],
            parquet_batch_rows=cached[2],
            confidence="cached",
            cache_hit=True,
            reasons=(*plan.reasons, "loaded a matching merge plan from the environment cache"),
        )
    elif calibrate and data_workers is None and file_workers is None:
        plan = _calibrate_plan(
            manifest,
            plan,
            max_seconds=calibration_seconds,
            max_bytes=calibration_bytes,
        )
        if use_cache:
            _save_cached_plan(_cache_path(plan.fingerprint), plan)
    return replace(plan, planning_seconds=time.perf_counter() - started)


def _replace_column(table: pa.Table, name: str, values: pa.Array) -> pa.Table:
    index = table.schema.get_field_index(name)
    if index < 0:
        raise ValueError(f"Required LeRobot system column {name!r} is missing")
    field = table.schema.field(index)
    return table.set_column(index, field, pc.cast(values, field.type))


def _rewrite_table(table: pa.Table, manifest: _Manifest, source_ordinal: int) -> pa.Table:
    """Replace three integer system columns without touching feature arrays."""

    local_episode = table["episode_index"].combine_chunks()
    output_episode = pc.take(pa.array(manifest.episode_maps[source_ordinal]), local_episode)
    output_start = pc.take(
        pa.array(
            [
                mapped.output_frame_start
                for mapped in manifest.episodes
                if mapped.source_ordinal == source_ordinal
            ]
        ),
        local_episode,
    )
    output_index = pc.add(output_start, table["frame_index"].combine_chunks())
    local_task = table["task_index"].combine_chunks()
    task_map = manifest.task_maps[source_ordinal]
    if set(task_map) != set(range(len(task_map))):
        raise ValueError("Task indices must be contiguous from zero")
    output_task = pc.take(
        pa.array([task_map[index] for index in range(len(task_map))]), local_task
    )
    table = _replace_column(table, "episode_index", output_episode)
    table = _replace_column(table, "index", output_index)
    return _replace_column(table, "task_index", output_task)


def _system_stats(values: np.ndarray, template: dict[str, Any]) -> dict[str, Any]:
    """Recalculate index statistics while preserving the source statistic keys."""

    values = np.asarray(values)
    output: dict[str, Any] = {}
    for key in template:
        if key == "min":
            value = values.min()
        elif key == "max":
            value = values.max()
        elif key == "mean":
            value = values.mean()
        elif key == "std":
            value = values.std()
        elif key == "count":
            value = len(values)
        elif key.startswith("q") and key[1:].isdigit():
            value = np.quantile(values, int(key[1:]) / 100)
        else:
            continue
        output[key] = np.atleast_1d(value).tolist()
    return output


def _episode_stats(
    mapped: _MappedEpisode, values: dict[str, list[np.ndarray]]
) -> dict[str, dict[str, Any]]:
    stats = copy.deepcopy(mapped.episode.stats)
    for column in _SYSTEM_COLUMNS:
        arrays = values.get(column, [])
        if arrays and column in stats:
            stats[column] = _system_stats(np.concatenate(arrays), stats[column])
    return stats


def _write_parquet(
    resource: _DataResource,
    output: Path,
    manifest: _Manifest,
    batch_rows: int,
) -> dict[int, dict[str, dict[str, Any]]]:
    """Stream one resource and retain only tiny system arrays for exact stats."""

    output.parent.mkdir(parents=True, exist_ok=True)
    writer: pq.ParquetWriter | None = None
    system_values: dict[int, dict[str, list[np.ndarray]]] = {
        mapped.output_index: {column: [] for column in _SYSTEM_COLUMNS}
        for mapped in resource.episodes
    }
    rows = 0
    try:
        parquet = pq.ParquetFile(resource.source_path)
        for batch in parquet.iter_batches(batch_size=batch_rows):
            table = _rewrite_table(
                pa.Table.from_batches([batch]), manifest, resource.episodes[0].source_ordinal
            )
            if writer is None:
                writer = pq.ParquetWriter(output, table.schema)
            writer.write_table(table)
            output_episode = table["episode_index"].combine_chunks().to_numpy()
            for output_index in np.unique(output_episode):
                mask = output_episode == output_index
                values = system_values[int(output_index)]
                for column in _SYSTEM_COLUMNS:
                    values[column].append(
                        table[column].combine_chunks().to_numpy(zero_copy_only=False)[mask]
                    )
            rows += table.num_rows
    finally:
        if writer is not None:
            writer.close()
    expected_rows = sum(mapped.episode.length for mapped in resource.episodes)
    if rows != expected_rows:
        raise ValueError(
            f"Parquet row count differs from episode metadata in {resource.source_path}"
        )
    return {
        mapped.output_index: _episode_stats(mapped, system_values[mapped.output_index])
        for mapped in resource.episodes
    }


def _worker_candidates(limit: int) -> tuple[int, ...]:
    candidates = [1]
    while candidates[-1] * 2 <= limit:
        candidates.append(candidates[-1] * 2)
    if candidates[-1] != limit:
        candidates.append(limit)
    return tuple(candidates)


def _choose_measurement(measurements: list[tuple[int, float]]) -> int:
    """Choose the least concurrency within 3% of measured peak throughput."""

    if not measurements:
        raise ValueError("Autotune produced no measurements")
    peak = max(throughput for _, throughput in measurements)
    return min(workers for workers, throughput in measurements if throughput >= peak * 0.97)


def _calibrate_plan(
    manifest: _Manifest,
    plan: MergePlan,
    *,
    max_seconds: float,
    max_bytes: int,
) -> MergePlan:
    """Measure real bounded rewrites/copies and retain the smallest near-peak plan."""

    if max_seconds <= 0 or max_bytes <= 0:
        raise ValueError("Merge calibration budgets must be positive")
    started = time.perf_counter()
    resources = inspect_resources()
    largest_logical = max((item.logical_bytes for item in manifest.data_resources), default=_MIB)
    memory_cap = max(1, plan.max_inflight_memory_bytes // max(2 * largest_logical, 64 * _MIB))
    data_limit = min(resources.effective_cpus, len(manifest.data_resources) or 1, memory_cap, 16)
    file_limit = min(resources.effective_cpus, len(manifest.media_resources) or 1, 16)
    data_candidates = _worker_candidates(max(1, data_limit))
    file_candidates = _worker_candidates(max(1, file_limit))
    operation_count = len(data_candidates) + (
        len(file_candidates) if manifest.media_resources else 0
    )
    bytes_per_operation = max(1, max_bytes // max(1, operation_count))

    data_sample: list[_DataResource] = []
    data_bytes = 0
    for resource in sorted(manifest.data_resources, key=lambda item: item.physical_bytes):
        if resource.physical_bytes > bytes_per_operation:
            continue
        if data_sample and data_bytes + resource.physical_bytes > bytes_per_operation:
            break
        data_sample.append(resource)
        data_bytes += resource.physical_bytes
        if len(data_sample) >= data_limit:
            break
    data_measurements: list[tuple[int, float]] = []
    file_measurements: list[tuple[int, float]] = []
    calibration_root = inspect_storage(plan.destination).existing_path
    with tempfile.TemporaryDirectory(
        prefix=".letools-merge-calibration-", dir=calibration_root
    ) as root:
        root_path = Path(root)
        for workers in data_candidates if data_sample else ():
            if time.perf_counter() - started >= max_seconds:
                break
            output_root = root_path / f"data-{workers}"
            measured = time.perf_counter()
            with ThreadPoolExecutor(max_workers=min(workers, len(data_sample) or 1)) as pool:
                list(
                    pool.map(
                        lambda item: _write_parquet(
                            item[1],
                            output_root / f"{item[0]:06d}.parquet",
                            manifest,
                            plan.parquet_batch_rows,
                        ),
                        enumerate(data_sample),
                    )
                )
            elapsed = time.perf_counter() - measured
            data_measurements.append((workers, data_bytes / max(elapsed, 1e-9)))
            shutil.rmtree(output_root)

        if manifest.media_resources and time.perf_counter() - started < max_seconds:
            file_sample: list[_MediaResource] = []
            file_bytes = 0
            for resource in sorted(manifest.media_resources, key=lambda item: item.physical_bytes):
                if resource.physical_bytes > bytes_per_operation:
                    continue
                if file_sample and file_bytes + resource.physical_bytes > bytes_per_operation:
                    break
                file_sample.append(resource)
                file_bytes += resource.physical_bytes
                if len(file_sample) >= file_limit:
                    break
            for workers in file_candidates if file_sample else ():
                if time.perf_counter() - started >= max_seconds:
                    break
                output_root = root_path / f"files-{workers}"
                measured = time.perf_counter()
                clone_or_copy_files(
                    [
                        (resource.source_path, output_root / f"{index:06d}.mp4")
                        for index, resource in enumerate(file_sample)
                    ],
                    workers,
                )
                elapsed = time.perf_counter() - measured
                file_measurements.append((workers, file_bytes / max(elapsed, 1e-9)))
                shutil.rmtree(output_root)

    selected_data = (
        _choose_measurement(data_measurements)
        if data_measurements
        else plan.data_workers
    )
    selected_files = (
        _choose_measurement(file_measurements) if file_measurements else plan.file_workers
    )
    bytes_per_row = max(
        1,
        sum(resource.logical_bytes for resource in manifest.data_resources)
        // manifest.total_frames,
    )
    per_worker = max(16 * _MIB, plan.max_inflight_memory_bytes // selected_data)
    batch_rows = max(
        1024,
        min(plan.parquet_batch_rows, per_worker // max(bytes_per_row * 3, 1)),
    )
    evidence = (
        "data calibration: "
        + ", ".join(
            f"{workers}w={value / _MIB:.1f} MiB/s"
            for workers, value in data_measurements
        ),
        "file calibration: "
        + (
            ", ".join(
                f"{workers}w={value / _MIB:.1f} MiB/s" for workers, value in file_measurements
            )
            if file_measurements
            else "no media resources"
        ),
        f"selected the least worker counts within 3% of each measured peak; "
        f"calibration used {time.perf_counter() - started:.2f}s",
    )
    return replace(
        plan,
        data_workers=selected_data,
        file_workers=selected_files,
        parquet_batch_rows=int(batch_rows),
        confidence="calibrated",
        reasons=(*plan.reasons, *evidence),
    )


def _v21_data_outputs(manifest: _Manifest, destination: Path, chunks_size: int) -> list[Path]:
    return [
        destination
        / f"data/chunk-{resource.episodes[0].output_index // chunks_size:03d}"
        / f"episode_{resource.episodes[0].output_index:06d}.parquet"
        for resource in manifest.data_resources
    ]


def _v30_data_outputs(manifest: _Manifest, destination: Path, chunks_size: int) -> list[Path]:
    return [
        destination
        / f"data/chunk-{number // chunks_size:03d}/file-{number % chunks_size:03d}.parquet"
        for number, _ in enumerate(manifest.data_resources)
    ]


def _media_outputs(
    manifest: _Manifest, destination: Path, chunks_size: int
) -> tuple[list[Path], dict[tuple[str, Path], tuple[int, int]]]:
    outputs: list[Path] = []
    locations: dict[tuple[str, Path], tuple[int, int]] = {}
    if manifest.version == "v2.1":
        by_path = {
            (key, mapped.source.media_input(mapped.episode, key).path): mapped
            for mapped in manifest.episodes
            for key in manifest.sources[0].metadata.video_keys
        }
        for resource in manifest.media_resources:
            mapped = by_path[(resource.key, resource.source_path)]
            output = (
                destination
                / f"videos/chunk-{mapped.output_index // chunks_size:03d}"
                / resource.key
                / f"episode_{mapped.output_index:06d}.mp4"
            )
            outputs.append(output)
            locations[(resource.key, resource.source_path)] = (
                mapped.output_index // chunks_size,
                mapped.output_index % chunks_size,
            )
        return outputs, locations

    next_number: dict[str, int] = {}
    for resource in manifest.media_resources:
        number = next_number.get(resource.key, 0)
        next_number[resource.key] = number + 1
        chunk, file = divmod(number, chunks_size)
        outputs.append(
            destination
            / f"videos/{resource.key}/chunk-{chunk:03d}/file-{file:03d}.mp4"
        )
        locations[(resource.key, resource.source_path)] = (chunk, file)
    return outputs, locations


def _write_v21_metadata(
    manifest: _Manifest,
    destination: Path,
    plan: MergePlan,
    stats: dict[int, dict[str, dict[str, Any]]],
) -> None:
    info = copy.deepcopy(manifest.info)
    info.update(
        {
            "codebase_version": "v2.1",
            "total_episodes": len(manifest.episodes),
            "total_frames": manifest.total_frames,
            "total_tasks": len(manifest.tasks),
            "total_videos": len(manifest.media_resources),
            "total_chunks": math.ceil(len(manifest.episodes) / plan.chunks_size),
            "chunks_size": plan.chunks_size,
            "splits": {manifest.split: f"0:{len(manifest.episodes)}"},
            "data_path": "data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet",
            "video_path": (
                "videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4"
                if manifest.media_resources
                else None
            ),
        }
    )
    info.pop("data_files_size_in_mb", None)
    info.pop("video_files_size_in_mb", None)
    write_json(destination / "meta/info.json", info)
    write_jsonl(
        destination / "meta/tasks.jsonl",
        [{"task_index": index, "task": task} for index, task in manifest.tasks.items()],
    )
    write_jsonl(
        destination / "meta/episodes.jsonl",
        [
            {
                "episode_index": mapped.output_index,
                "tasks": list(mapped.episode.tasks),
                "length": mapped.episode.length,
            }
            for mapped in manifest.episodes
        ],
    )
    write_jsonl(
        destination / "meta/episodes_stats.jsonl",
        [
            {"episode_index": mapped.output_index, "stats": stats[mapped.output_index]}
            for mapped in manifest.episodes
        ],
    )


def _write_v30_metadata(
    manifest: _Manifest,
    destination: Path,
    plan: MergePlan,
    stats: dict[int, dict[str, dict[str, Any]]],
    media_locations: dict[tuple[str, Path], tuple[int, int]],
) -> None:
    info = copy.deepcopy(manifest.info)
    info.update(
        {
            "codebase_version": "v3.0",
            "total_episodes": len(manifest.episodes),
            "total_frames": manifest.total_frames,
            "total_tasks": len(manifest.tasks),
            "splits": {manifest.split: f"0:{len(manifest.episodes)}"},
            "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
            "video_path": (
                "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4"
                if manifest.media_resources
                else None
            ),
        }
    )
    info.pop("total_chunks", None)
    info.pop("total_videos", None)
    write_json(destination / "meta/info.json", info)
    task_table = pa.Table.from_pylist(
        [{"task_index": index, "task": task} for index, task in manifest.tasks.items()],
        schema=pa.schema([("task_index", pa.int64()), ("task", pa.string())]),
    )
    task_path = destination / "meta/tasks.parquet"
    task_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(task_table, task_path)

    data_locations = {
        resource.source_path: divmod(number, plan.chunks_size)
        for number, resource in enumerate(manifest.data_resources)
    }
    rows = []
    for mapped in manifest.episodes:
        data_chunk, data_file = data_locations[mapped.episode.data_path]
        row: dict[str, Any] = {
            "episode_index": mapped.output_index,
            "data/chunk_index": data_chunk,
            "data/file_index": data_file,
            "dataset_from_index": mapped.output_frame_start,
            "dataset_to_index": mapped.output_frame_start + mapped.episode.length,
            "tasks": list(mapped.episode.tasks),
            "length": mapped.episode.length,
            **flatten_stats(stats[mapped.output_index]),
            "meta/episodes/chunk_index": 0,
            "meta/episodes/file_index": 0,
        }
        for key in manifest.sources[0].metadata.video_keys:
            media = mapped.source.media_input(mapped.episode, key)
            assert isinstance(media, VideoSlice)
            chunk, file = media_locations[(key, media.path)]
            prefix = f"videos/{key}"
            row.update(
                {
                    f"{prefix}/chunk_index": chunk,
                    f"{prefix}/file_index": file,
                    f"{prefix}/from_timestamp": media.start,
                    f"{prefix}/to_timestamp": media.end,
                }
            )
        rows.append(row)
    episode_path = destination / "meta/episodes/chunk-000/file-000.parquet"
    episode_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), episode_path)
    write_json(
        destination / "meta/stats.json",
        aggregate_episode_stats([stats[index] for index in range(len(stats))]),
    )


def _execute_merge(
    manifest: _Manifest,
    destination: Path,
    plan: MergePlan,
    recorder: StageRecorder,
) -> tuple[int, int, int]:
    data_outputs = (
        _v21_data_outputs(manifest, destination, plan.chunks_size)
        if manifest.version == "v2.1"
        else _v30_data_outputs(manifest, destination, plan.chunks_size)
    )
    data_started = time.perf_counter()
    stats: dict[int, dict[str, dict[str, Any]]] = {}

    def rewrite(item: tuple[_DataResource, Path]) -> dict[int, dict[str, dict[str, Any]]]:
        return _write_parquet(item[0], item[1], manifest, plan.parquet_batch_rows)

    with ThreadPoolExecutor(max_workers=min(plan.data_workers, len(data_outputs) or 1)) as pool:
        for values in pool.map(rewrite, zip(manifest.data_resources, data_outputs, strict=True)):
            stats.update(values)
    recorder.add(
        "data_rewrite",
        time.perf_counter() - data_started,
        tasks=len(data_outputs),
        input_bytes=plan.data_bytes,
        output_bytes=sum(path.stat().st_size for path in data_outputs),
    )

    media_outputs, media_locations = _media_outputs(manifest, destination, plan.chunks_size)
    media_started = time.perf_counter()
    copy_results = clone_or_copy_files(
        [
            (resource.source_path, output)
            for resource, output in zip(manifest.media_resources, media_outputs, strict=True)
        ],
        plan.file_workers,
    )
    recorder.add(
        "media_clone_or_copy",
        time.perf_counter() - media_started,
        tasks=len(media_outputs),
        input_bytes=plan.file_bytes,
        output_bytes=sum(size for size, _ in copy_results),
    )
    metadata_started = time.perf_counter()
    if manifest.version == "v2.1":
        _write_v21_metadata(manifest, destination, plan, stats)
    else:
        _write_v30_metadata(manifest, destination, plan, stats, media_locations)
    recorder.add("metadata_write", time.perf_counter() - metadata_started)
    cloned = sum(cloned for _, cloned in copy_results)
    return cloned, len(copy_results) - cloned, sum(size for size, _ in copy_results)


def merge_datasets(
    sources: list[str | Path],
    destination: str | Path,
    *,
    auto: bool = True,
    data_workers: int | None = None,
    file_workers: int | None = None,
    overwrite: bool = False,
    validate: bool = True,
    use_cache: bool = True,
    calibration_seconds: float = 10.0,
    calibration_bytes: int = 1024**3,
) -> MergeResult:
    """Merge physical same-version datasets transactionally in input order."""

    started = time.perf_counter()
    recorder = StageRecorder()
    with recorder.measure("manifest_scan"):
        manifest = _build_manifest(sources)
    destination_path = Path(destination).resolve(strict=False)
    _validate_destination(manifest, destination_path)
    plan_started = time.perf_counter()
    with recorder.measure("plan"):
        plan = _heuristic_plan(
            manifest,
            destination_path,
            data_workers=data_workers,
            file_workers=file_workers,
        )
        cached = _load_cached_plan(_cache_path(plan.fingerprint)) if auto and use_cache else None
        if cached and data_workers is None and file_workers is None:
            plan = replace(
                plan,
                data_workers=cached[0],
                file_workers=cached[1],
                parquet_batch_rows=cached[2],
                confidence="cached",
                cache_hit=True,
                reasons=(*plan.reasons, "loaded a matching merge plan from the environment cache"),
            )
        elif auto and data_workers is None and file_workers is None:
            plan = _calibrate_plan(
                manifest,
                plan,
                max_seconds=calibration_seconds,
                max_bytes=calibration_bytes,
            )
            _save_cached_plan(_cache_path(plan.fingerprint), plan)
        plan = replace(plan, planning_seconds=time.perf_counter() - plan_started)
    if destination_path.exists() and not overwrite:
        raise FileExistsError(f"Destination already exists: {destination_path}")
    staging = destination_path.with_name(
        f".{destination_path.name}.letools-merge-{uuid.uuid4().hex}"
    )
    try:
        cloned, copied, copied_bytes = _execute_merge(manifest, staging, plan, recorder)
        if validate:
            from letools.validation import validate_dataset

            with recorder.measure("merge_validate"):
                report = validate_dataset(staging, deep=True)
                if not report.valid:
                    raise ValueError("Merged dataset is invalid: " + "; ".join(report.errors))
        with recorder.measure("publish_cleanup"):
            if destination_path.exists():
                shutil.rmtree(destination_path)
            staging.replace(destination_path)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return MergeResult(
        sources=manifest.roots,
        destination=destination_path,
        version=manifest.version,
        episodes=len(manifest.episodes),
        frames=manifest.total_frames,
        tasks=len(manifest.tasks),
        elapsed_seconds=time.perf_counter() - started,
        cloned_files=cloned,
        copied_files=copied,
        copied_bytes=copied_bytes,
        contributions=tuple(
            MergeContribution(root, source.metadata.total_episodes, source.metadata.total_frames)
            for root, source in zip(manifest.roots, manifest.sources, strict=True)
        ),
        plan=plan,
        stages=recorder.snapshot(),
    )


__all__ = ["MergePlan", "MergeResult", "merge_datasets", "plan_merge"]
