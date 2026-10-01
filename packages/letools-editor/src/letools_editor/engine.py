"""Read-only planning, bounded execution, and same-version metadata publication.

Edits are fused: each physical Parquet shard is rewritten at most once and each
physical video shard is processed at most once (plus the decoded-output stats
pass). Unchanged files use the core's Rust reflink/copy primitive, never hard
links. Python operates on episode metadata and Arrow slices, not frame objects.
"""

from __future__ import annotations

import copy
import json
import math
import os
import shutil
import tempfile
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from letools._io import write_json, write_jsonl
from letools._native import clone_or_copy_files
from letools._stats import aggregate_episode_stats, flatten_stats
from letools.model import VideoSlice
from letools.planner.inspect import inspect_resources
from letools.plugins import DatasetSource, open_dataset
from letools.validation import validate_dataset
from letools_editor.media import (
    execute_media,
    packet_remux_safe,
    probe,
    resolve_ffmpeg,
)
from letools_editor.model import (
    EditConfig,
    EditPlan,
    EditResult,
    EpisodeEdit,
    MediaJob,
    VideoEdit,
)


@dataclass(frozen=True)
class _DataJob:
    source: Path
    relative_output: Path
    episodes: tuple[EpisodeEdit, ...]
    rewrite: bool


@dataclass
class _Manifest:
    source: DatasetSource
    destination: Path
    config: EditConfig
    episodes: list[EpisodeEdit]
    tasks: dict[int, str]
    splits: dict[str, str]
    data: list[_DataJob]
    media: list[MediaJob]
    headers: dict[Path, dict[str, Any]]
    info: dict[str, Any]
    plan: EditPlan


def _shift_stats(stats: dict[str, Any], delta: int) -> None:
    """An additive index shift changes location statistics, never variance."""

    for name in tuple(stats):
        if name in {"min", "max", "mean"} or (
            name.startswith("q") and name[1:].isdigit()
        ):
            stats[name] = (np.asarray(stats[name]) + delta).tolist()


def _constant_stats(
    value: int, length: int, template: dict[str, Any]
) -> dict[str, Any]:
    result = {
        "min": [value],
        "max": [value],
        "mean": [float(value)],
        "std": [0.0],
        "count": [length],
    }
    for name in template:
        if name.startswith("q") and name[1:].isdigit():
            result[name] = [value]
    return result


def _remap_splits(source: DatasetSource, episodes: list[EpisodeEdit]) -> dict[str, str]:
    """Preserve named half-open splits rather than silently replacing with train."""

    result = {}
    indices = np.asarray([episode.original.index for episode in episodes])
    for name, value in source.metadata.splits.items():
        try:
            start, end = map(int, value.split(":"))
        except (ValueError, AttributeError):
            raise ValueError(
                f"Editor requires numeric half-open episode splits; got {name}={value!r}"
            ) from None
        if not 0 <= start <= end <= source.metadata.total_episodes:
            raise ValueError(f"Invalid source split: {name}={value!r}")
        first, stop = np.searchsorted(indices, [start, end]).tolist()
        result[name] = f"{first}:{stop}"
    return result


def _safe_input(path: Path, root: Path) -> None:
    if not path.resolve().is_relative_to(root) or not path.is_file():
        raise ValueError(f"Missing input file or reference outside dataset: {path}")


def _manifest(
    source_path: str | Path, destination: str | Path, config: EditConfig
) -> _Manifest:
    """Plan entirely from metadata, Parquet footers, and video headers."""

    root = Path(source_path).resolve()
    raw_destination = Path(destination).absolute()
    if raw_destination.is_symlink():
        raise ValueError("Destination must not be a symlink")
    destination = raw_destination.resolve()
    if (
        destination == root
        or destination.is_relative_to(root)
        or root.is_relative_to(destination)
    ):
        raise ValueError(
            "Source and destination must not contain one another; in-place edits are unsupported"
        )
    if destination.exists() and (not config.overwrite or not destination.is_dir()):
        raise FileExistsError(
            f"Destination already exists: {destination}; use --overwrite for a dataset directory"
        )
    if destination.exists() and not (destination / "meta/info.json").is_file():
        raise ValueError(
            "Refusing to overwrite a directory that is not a LeRobot dataset"
        )
    source = open_dataset(root)
    if [episode.index for episode in source.episodes] != list(
        range(source.metadata.total_episodes)
    ):
        raise ValueError("Source episode indices must be contiguous and ordered")
    if not source.episodes or any(episode.length <= 0 for episode in source.episodes):
        raise ValueError("The editor requires nonempty episodes")
    if (
        sum(episode.length for episode in source.episodes)
        != source.metadata.total_frames
    ):
        raise ValueError("Source total_frames differs from episode metadata")
    if config.remove_features - source.metadata.features.keys():
        raise ValueError(
            f"Unknown features: {sorted(config.remove_features - source.metadata.features.keys())}"
        )
    requested = set(config.task_by_episode) | config.delete_episodes
    if requested - set(range(len(source.episodes))):
        raise ValueError("Edit refers to an unknown source episode index")
    if len(config.delete_episodes) == len(source.episodes):
        raise ValueError(
            "Deleting every episode would create an unsupported empty dataset"
        )
    video_keys = tuple(
        key for key in source.metadata.video_keys if key not in config.remove_features
    )
    if any(key in {".", ".."} or "/" in key or "\\" in key for key in video_keys):
        raise ValueError("Video feature names must be single safe path components")
    if config.video and (not video_keys or set(config.video.keys) - set(video_keys)):
        raise ValueError("Video transform requires existing, retained video features")
    tasks = dict(source.metadata.tasks)
    if sorted(tasks) != list(range(len(tasks))):
        raise ValueError("Source task indices must be contiguous from zero")
    tasks_by_text = {text: index for index, text in reversed(list(tasks.items()))}
    episodes = []
    original_frame_start = 0
    frame_start = 0
    for original in source.episodes:
        old_start = original_frame_start
        original_frame_start += original.length
        if original.index in config.delete_episodes:
            continue
        task_override = None
        episode_tasks = original.tasks
        if original.index in config.task_by_episode:
            text = config.task_by_episode[original.index]
            if text not in tasks_by_text:
                tasks_by_text[text] = len(tasks)
                tasks[tasks_by_text[text]] = text
            task_override = tasks_by_text[text]
            episode_tasks = (text,)
        stats = copy.deepcopy(
            {
                key: value
                for key, value in original.stats.items()
                if key not in config.remove_features
            }
        )
        index = len(episodes)
        if "index" in stats:
            _shift_stats(stats["index"], frame_start - old_start)
        stats["episode_index"] = _constant_stats(
            index, original.length, stats.get("episode_index", {})
        )
        if task_override is not None:
            stats["task_index"] = _constant_stats(
                task_override, original.length, stats.get("task_index", {})
            )
        episodes.append(
            EpisodeEdit(
                original, index, frame_start, episode_tasks, task_override, stats
            )
        )
        frame_start += original.length

    chunks_size = int(source.metadata.info.get("chunks_size", 1000))
    if chunks_size <= 0:
        raise ValueError("chunks_size must be positive")
    v21 = source.metadata.version == "v2.1"
    data_groups: dict[Path, list[EpisodeEdit]] = defaultdict(list)
    all_data_groups: dict[Path, list[Any]] = defaultdict(list)
    for original in source.episodes:
        all_data_groups[original.data_path].append(original)
    for episode in episodes:
        data_groups[episode.original.data_path].append(episode)
    data = []
    for number, (path, group) in enumerate(data_groups.items()):
        _safe_input(path, root)
        metadata = pq.read_metadata(path)
        if metadata.num_rows != sum(
            episode.length for episode in all_data_groups[path]
        ):
            raise ValueError(f"Parquet row count differs from episode metadata: {path}")
        schema_names = pq.read_schema(path).names
        if not {
            "episode_index",
            "frame_index",
            "index",
            "task_index",
            "timestamp",
        } <= set(schema_names):
            raise ValueError(f"Missing system columns in {path}")
        if v21:
            if len(group) != 1:
                raise ValueError("v2.1 requires one physical Parquet file per episode")
            index = group[0].index
            location = index // chunks_size, index % chunks_size
            output = Path(f"data/chunk-{location[0]:03d}/episode_{index:06d}.parquet")
        else:
            location = divmod(number, chunks_size)
            output = Path(
                f"data/chunk-{location[0]:03d}/file-{location[1]:03d}.parquet"
            )
        for episode in group:
            episode.data_location = location
        rewrite = (
            len(group) != len(all_data_groups[path])
            or bool(config.remove_features & set(schema_names))
            or any(
                episode.task_override is not None
                or episode.index != episode.original.index
                for episode in group
            )
        )
        data.append(_DataJob(path, output, tuple(group), rewrite))

    media_groups: dict[tuple[str, Path], list[EpisodeEdit]] = defaultdict(list)
    for key in video_keys:
        for episode in episodes:
            video = episode.original.videos[key]
            if not isinstance(video, VideoSlice):
                raise TypeError("The editor accepts only physical LeRobot video files")
            media_groups[(key, video.path)].append(episode)
    headers = {}
    raw_jobs = []
    key_numbers: dict[str, int] = defaultdict(int)
    fallback_keys = set()
    packet_remux_paths = set()
    for (key, path), group in media_groups.items():
        _safe_input(path, root)
        header = headers.setdefault(path, probe(path))
        fps = source.metadata.fps
        if abs(header["fps"] - fps) > 1e-4 or header["frames"] <= 0:
            raise ValueError(
                f"Editor requires CFR video with a known frame count: {path}"
            )
        ranges = []
        group.sort(key=lambda episode: episode.original.videos[key].start)
        for episode in group:
            video = episode.original.videos[key]
            start, end = round(video.start * fps), round(video.end * fps)
            if (
                abs(video.start * fps - start) > 0.01
                or abs(video.end * fps - end) > 0.01
                or end - start != episode.original.length
            ):
                raise ValueError(
                    f"Video range is not frame aligned: episode {episode.original.index}, {key}"
                )
            if (
                start < 0
                or end > header["frames"]
                or (ranges and start < ranges[-1][1])
            ):
                raise ValueError(f"Invalid or overlapping video ranges: {path}")
            ranges.append((start, end))
        partial = sum(end - start for start, end in ranges) != header["frames"]
        transform = (
            config.video
            if config.video and (not config.video.keys or key in config.video.keys)
            else None
        )
        if partial and header["codec"] != "mjpeg" and transform is None:
            if header["codec"] == "h264" and packet_remux_safe(
                path, tuple(ranges)
            ):
                packet_remux_paths.add(path)
            else:
                fallback_keys.add(key)
        number = key_numbers[key]
        key_numbers[key] += 1
        if v21:
            if len(group) != 1:
                raise ValueError("v2.1 requires one physical video per episode/camera")
            location = group[0].index // chunks_size, group[0].index % chunks_size
            output = Path(
                f"videos/chunk-{location[0]:03d}/{key}/episode_{group[0].index:06d}.mp4"
            )
        else:
            location = divmod(number, chunks_size)
            output = Path(
                f"videos/{key}/chunk-{location[0]:03d}/file-{location[1]:03d}.mp4"
            )
        raw_jobs.append(
            (key, path, output, group, tuple(ranges), partial, transform, location)
        )

    media = []
    warnings = []
    for key, path, output, group, ranges, partial, transform, location in raw_jobs:
        header = headers[path]
        if (
            partial
            and header["codec"] != "mjpeg"
            and path not in packet_remux_paths
            and transform is None
        ):
            # Fully retained files keep their original packets and statistics.
            # Only a physical file with removed inter-frame packets needs a
            # decoder and encoder. CRF=0 minimizes additional quantization;
            # the changed file's pixel statistics are recomputed from output.
            transform = VideoEdit(crf=0, pixel_format=header["pixel_format"])
        mode = (
            "transcode"
            if transform
            else "remux"
            if partial
            else "reuse"
        )
        feature = source.metadata.features[key]
        depth = any(
            feature.get(namespace, {}).get("video.is_depth_map", False)
            for namespace in ("info", "video_info")
        )
        if mode == "transcode" and (depth or header["audio"]):
            raise ValueError(
                "Transcoding depth/audio videos is unsupported; refusing silent data loss"
            )
        if mode == "remux" and header["audio"]:
            raise ValueError("Compacting videos with audio is unsupported")
        shape = (
            transform.size
            if transform and transform.size
            else (header["width"], header["height"])
        )
        pixel_format = transform.pixel_format if transform else None
        if (
            transform
            and (pixel_format is None or "420" in pixel_format)
            and (shape[0] % 2 or shape[1] % 2)
        ):
            raise ValueError(
                "4:2:0 output needs even width and height; select yuv444p/yuvj444p for odd sizes"
            )
        offset = 0
        for episode, (start, end) in zip(group, ranges, strict=True):
            new_start = start if mode == "reuse" else offset
            episode.video_locations[key] = (
                *location,
                new_start / source.metadata.fps,
                (new_start + end - start) / source.metadata.fps,
            )
            offset += end - start
        media.append(
            MediaJob(
                key,
                path,
                output,
                tuple(group),
                ranges,
                mode,
                transform,
                header["width"],
                header["height"],
                source.metadata.fps,
            )
        )
    if fallback_keys:
        warnings.append(
            "Deleting partial inter-frame video requires decoding: re-encode only affected files of "
            + ", ".join(sorted(fallback_keys))
            + " as libx264 CRF 0 (no B-frames); recompute output pixel stats."
        )
    resources = inspect_resources()
    transcodes = any(job.mode == "transcode" for job in media)
    auto_resources = (
        transcodes and config.workers is None and config.codec_threads is None
    )
    effective_codec_threads = 2 if auto_resources else (config.codec_threads or 1)
    # Encoding has one decoder plus the requested encoder threads. The later
    # statistics phase has one decoder plus one Rust reducer.
    if auto_resources:
        # Keep the worker count fixed while using the spare CPU observed in
        # the benchmark; explicit values remain governed by the old cap.
        cpu_cap = max(1, resources.effective_cpus * 3 // 4)
    else:
        threads_per_job = max(2, effective_codec_threads + 1) if transcodes else 1
        cpu_cap = max(1, resources.effective_cpus // threads_per_job)
    # Conservative codec working-set allowance plus bounded Parquet batches.
    # Metadata still scales with episode count; this is not an RSS hard limit.
    pixels = max(
        (
            max(
                job.width * job.height,
                math.prod(job.transform.size)
                if job.transform and job.transform.size
                else 0,
            )
            for job in media
            if job.mode == "transcode"
        ),
        default=0,
    )
    memory_per_job = max(128 * 1024**2, pixels * 128)
    memory_budget = max(1, resources.effective_memory_bytes // 2)
    memory_cap = max(1, memory_budget // memory_per_job)
    requested_workers = 8 if auto_resources else (config.workers or 8)
    workers = min(requested_workers, cpu_cap, memory_cap, max(1, len(data), len(media)))
    # An automatic plan with fewer jobs is normal, not a user limit being
    # ignored. Preserve cap warnings for explicitly requested concurrency.
    if config.workers is not None and workers < config.workers:
        warnings.append(
            f"Requested concurrency capped to {workers} by effective CPU/memory/job limits"
        )
    effective_config = replace(
        config,
        workers=int(workers),
        codec_threads=int(effective_codec_threads),
    )
    info = copy.deepcopy(source.metadata.info)
    info["features"] = {
        key: value
        for key, value in info["features"].items()
        if key not in config.remove_features
    }
    plan = EditPlan(
        root,
        destination,
        source.metadata.version,
        len(source.episodes),
        len(episodes),
        frame_start,
        sum(job.rewrite for job in data),
        sum(not job.rewrite for job in data)
        + sum(job.mode == "reuse" for job in media),
        sum(job.mode == "remux" for job in media),
        sum(job.mode == "transcode" for job in media),
        int(workers),
        effective_codec_threads,
        resources.effective_cpus,
        memory_budget,
        tuple(warnings),
    )
    return _Manifest(
        source,
        destination,
        effective_config,
        episodes,
        tasks,
        _remap_splits(source, episodes),
        data,
        media,
        headers,
        info,
        plan,
    )


def plan_edit(
    source: str | Path, destination: str | Path, config: EditConfig | None = None
) -> EditPlan:
    """Inspect a request without writing files or running calibration."""

    return _manifest(source, destination, config or EditConfig()).plan


def _replace(table: pa.Table, key: str, values: np.ndarray) -> pa.Table:
    position = table.schema.get_field_index(key)
    return table.set_column(
        position, table.schema.field(position), pa.array(values, type=table[key].type)
    )


def _write_data(job: _DataJob, staging: Path, config: EditConfig) -> None:
    """Read only retained columns and stream each shared shard once.

    Episode row intervals are known from metadata. Slicing Arrow batches avoids
    Python row objects and avoids loading a large shard independently for each
    episode. Memory is O(batch_rows * retained_row_width) per worker.
    """

    output = staging / job.relative_output
    output.parent.mkdir(parents=True, exist_ok=True)
    with pq.ParquetFile(job.source) as parquet:
        columns = [
            name
            for name in parquet.schema_arrow.names
            if name not in config.remove_features
        ]
        schema = pa.schema([parquet.schema_arrow.field(name) for name in columns])
        # Embedded HF/pandas schemas may describe deleted fields. LeRobot's
        # info.json remains authoritative; let consumers infer the actual
        # physical columns instead of retaining stale embedded annotations.
        schema = schema.remove_metadata()
        episode_number = 0
        batch_start = 0
        written = 0
        with pq.ParquetWriter(output, schema) as writer:
            for batch in parquet.iter_batches(
                batch_size=config.batch_rows, columns=columns, use_threads=False
            ):
                table = pa.Table.from_batches([batch]).replace_schema_metadata(None)
                batch_end = batch_start + table.num_rows
                while episode_number < len(job.episodes):
                    episode = job.episodes[episode_number]
                    original = episode.original
                    first, end = (
                        original.data_start,
                        original.data_start + original.length,
                    )
                    if first >= batch_end:
                        break
                    start, stop = max(first, batch_start), min(end, batch_end)
                    if stop > start:
                        part = table.slice(start - batch_start, stop - start)
                        if not np.all(
                            part["episode_index"].to_numpy() == original.index
                        ):
                            raise ValueError(
                                f"Source episode_index disagrees with metadata: {job.source}"
                            )
                        relative = start - first
                        frames = np.arange(
                            relative, relative + part.num_rows, dtype=np.int64
                        )
                        if not np.array_equal(part["frame_index"].to_numpy(), frames):
                            raise ValueError(
                                f"Source frame_index is not contiguous: {job.source}"
                            )
                        part = _replace(
                            part,
                            "episode_index",
                            np.full(part.num_rows, episode.index, dtype=np.int64),
                        )
                        part = _replace(part, "index", frames + episode.frame_start)
                        if episode.task_override is not None:
                            part = _replace(
                                part,
                                "task_index",
                                np.full(
                                    part.num_rows, episode.task_override, dtype=np.int64
                                ),
                            )
                        writer.write_table(part)
                        written += part.num_rows
                    if end <= batch_end:
                        episode_number += 1
                    else:
                        break
                batch_start = batch_end
        if written != sum(episode.original.length for episode in job.episodes):
            raise ValueError(f"Missing source rows while rewriting {job.source}")


def _original_rows(source: DatasetSource) -> dict[int, dict[str, Any]]:
    if source.metadata.version == "v2.1":
        with (source.root / "meta/episodes.jsonl").open(encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
    else:
        rows = []
        for path in sorted((source.root / "meta/episodes").glob("*/*.parquet")):
            rows.extend(pq.read_table(path).to_pylist())
    return {int(row["episode_index"]): row for row in rows}


def _metadata(manifest: _Manifest, staging: Path) -> None:
    """Rebuild every affected pointer, count, split, and normalization statistic."""

    info = manifest.info
    v21 = manifest.plan.version == "v2.1"
    chunks = int(info.get("chunks_size", 1000))
    info.update(
        total_episodes=manifest.plan.episodes,
        total_frames=manifest.plan.frames,
        total_tasks=len(manifest.tasks),
        splits=manifest.splits,
    )
    video_keys = tuple(
        key for key, feature in info["features"].items() if feature["dtype"] == "video"
    )
    if v21:
        info.update(
            total_videos=len(manifest.media),
            total_chunks=math.ceil(len(manifest.episodes) / chunks),
            data_path="data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet",
            video_path="videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4"
            if video_keys
            else None,
        )
    else:
        info.update(
            data_path="data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
            video_path="videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4"
            if video_keys
            else None,
        )
    write_json(staging / "meta/info.json", info)
    task_rows = [
        {"task_index": index, "task": text}
        for index, text in sorted(manifest.tasks.items())
    ]
    originals = _original_rows(manifest.source)
    rows = []
    for episode in manifest.episodes:
        row = copy.deepcopy(originals[episode.original.index])
        row.update(
            episode_index=episode.index,
            tasks=list(episode.tasks),
            length=episode.original.length,
        )
        if not v21:
            # Drop all old standard stats and video pointers, then write only
            # retained features. Unknown custom episode columns are preserved.
            row = {
                key: value
                for key, value in row.items()
                if not key.startswith(("stats/", "videos/"))
            }
            row.update(
                {
                    "data/chunk_index": episode.data_location[0],
                    "data/file_index": episode.data_location[1],
                    "dataset_from_index": episode.frame_start,
                    "dataset_to_index": episode.frame_start + episode.original.length,
                    "meta/episodes/chunk_index": 0,
                    "meta/episodes/file_index": 0,
                    **flatten_stats(episode.stats),
                }
            )
            for key, (chunk, file, start, end) in episode.video_locations.items():
                row.update(
                    {
                        f"videos/{key}/chunk_index": chunk,
                        f"videos/{key}/file_index": file,
                        f"videos/{key}/from_timestamp": start,
                        f"videos/{key}/to_timestamp": end,
                    }
                )
        rows.append(row)
    stats = [episode.stats for episode in manifest.episodes]
    if v21:
        write_jsonl(staging / "meta/tasks.jsonl", task_rows)
        write_jsonl(staging / "meta/episodes.jsonl", rows)
        write_jsonl(
            staging / "meta/episodes_stats.jsonl",
            [
                {"episode_index": episode.index, "stats": episode.stats}
                for episode in manifest.episodes
            ],
        )
    else:
        # Official LeRobot uses the task text as the pandas index. Preserve
        # that schema metadata as well as the physical `task` column needed by
        # Arrow-only readers; a plain RangeIndex silently yields integer tasks
        # in official Dataset loaders.
        task_frame = pd.DataFrame(
            {"task_index": [row["task_index"] for row in task_rows]},
            index=pd.Index([row["task"] for row in task_rows], name="task"),
        )
        pq.write_table(pa.Table.from_pandas(task_frame), staging / "meta/tasks.parquet")
        path = staging / "meta/episodes/chunk-000/file-000.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        # Union keys avoid losing a statistic that happens to be absent in the
        # first episode. Standard input stats are expected to be consistent.
        keys = dict.fromkeys(key for row in rows for key in row)
        pq.write_table(
            pa.Table.from_pylist([{key: row.get(key) for key in keys} for row in rows]),
            path,
        )
    write_json(staging / "meta/stats.json", aggregate_episode_stats(stats))


def _publish(staging: Path, destination: Path, overwrite: bool) -> None:
    """Rollback an existing output if publication fails; never mutate the source.

    The sibling lock serializes editor writers. Replacing an existing directory
    requires two renames, so readers can see a brief missing path (not a partial
    dataset). This is failure-atomic, not a power-loss/fsync durability promise.
    """

    backup = None
    if destination.exists():
        if not overwrite:
            raise FileExistsError(destination)
        backup = destination.with_name(
            f".{destination.name}.editor-backup-{uuid.uuid4().hex}"
        )
        destination.rename(backup)
    try:
        staging.rename(destination)
    except BaseException:
        if backup is not None:
            backup.rename(destination)
        raise
    if backup is not None:
        try:
            shutil.rmtree(backup)
        except OSError:
            import warnings

            warnings.warn(
                f"Published output; previous output retained at {backup}", stacklevel=2
            )


def edit_dataset(
    source: str | Path, destination: str | Path, config: EditConfig | None = None
) -> EditResult:
    """Fuse requested edits into one validated, separately published dataset."""

    started = time.perf_counter()
    config = config or EditConfig()
    stages = {}
    manifest = _manifest(source, destination, config)
    stages["plan"] = time.perf_counter() - started
    executable = (
        resolve_ffmpeg(manifest.config.ffmpeg)
        if manifest.plan.videos_transcode
        else None
    )
    destination = manifest.destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    lock = destination.with_name(f".{destination.name}.letools-editor.lock")
    try:
        lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise RuntimeError(
            f"Another edit owns {lock}; remove only after confirming no editor is running"
        ) from None
    os.close(lock_fd)
    staging: Path | None = None
    copies: list[tuple[int, bool]] = []
    try:
        staging = Path(
            tempfile.mkdtemp(
                prefix=f".{destination.name}.letools-editor-", dir=destination.parent
            )
        )
        phase = time.perf_counter()
        files = [
            (job.source, staging / job.relative_output)
            for job in manifest.data
            if not job.rewrite
        ]
        files += [
            (job.source, staging / job.relative_output)
            for job in manifest.media
            if job.mode == "reuse"
        ]
        if files:
            copies = clone_or_copy_files(files, manifest.plan.workers)
        stages["reuse"] = time.perf_counter() - phase
        phase = time.perf_counter()
        with ThreadPoolExecutor(max_workers=manifest.plan.workers) as pool:
            list(
                pool.map(
                    lambda job: _write_data(job, staging, manifest.config),
                    [job for job in manifest.data if job.rewrite],
                )
            )
        stages["parquet"] = time.perf_counter() - phase
        phase = time.perf_counter()
        changed = [job for job in manifest.media if job.mode != "reuse"]
        with ThreadPoolExecutor(max_workers=manifest.plan.workers) as pool:
            results = pool.map(
                lambda job: execute_media(
                    job, staging, executable, manifest.config
                ),
                changed,
            )
            for job, (header, stats) in zip(changed, results, strict=True):
                for episode in job.episodes:
                    if episode.index in stats:
                        episode.stats[job.key] = stats[episode.index]
                if job.mode == "transcode":
                    feature = manifest.info["features"][job.key]
                    dimensions = {
                        "height": header["height"],
                        "width": header["width"],
                        "channels": 3,
                    }
                    names = feature.get("names")
                    if (
                        isinstance(names, list)
                        and len(names) == 3
                        and set(names) == set(dimensions)
                    ):
                        feature["shape"] = [dimensions[name] for name in names]
                    elif (
                        feature.get("shape", [None])[0] == 3
                        and feature.get("shape", [None])[-1] != 3
                    ):
                        feature["shape"] = [3, header["height"], header["width"]]
                    else:
                        feature["shape"] = [header["height"], header["width"], 3]
                    for namespace in ("info", "video_info"):
                        if (
                            namespace == "video_info"
                            and manifest.plan.version == "v3.0"
                            and namespace not in feature
                        ):
                            continue
                        feature.setdefault(namespace, {}).update(
                            {
                                "video.codec": header["codec"],
                                "video.pix_fmt": header["pixel_format"],
                                "video.fps": job.fps,
                                "video.height": header["height"],
                                "video.width": header["width"],
                                "video.channels": 3,
                                "video.is_depth_map": False,
                                "has_audio": False,
                            }
                        )
        stages["video_and_stats"] = time.perf_counter() - phase
        phase = time.perf_counter()
        _metadata(manifest, staging)
        stages["metadata"] = time.perf_counter() - phase
        phase = time.perf_counter()
        report = validate_dataset(staging)
        if not report.valid:
            raise ValueError(
                "Edited dataset failed validation: " + "; ".join(report.errors)
            )
        stages["validate"] = time.perf_counter() - phase
        phase = time.perf_counter()
        _publish(staging, destination, config.overwrite)
        stages["publish"] = time.perf_counter() - phase
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging)
        lock.unlink(missing_ok=True)
    elapsed = time.perf_counter() - started
    return EditResult(
        manifest.plan,
        elapsed,
        manifest.plan.episodes / elapsed,
        manifest.plan.frames / elapsed,
        sum(cloned for _, cloned in copies),
        sum(not cloned for _, cloned in copies),
        sum(size for size, _ in copies),
        stages,
        {episode.original.index: episode.index for episode in manifest.episodes},
    )


__all__ = ["EditConfig", "EditResult", "edit_dataset", "plan_edit"]
