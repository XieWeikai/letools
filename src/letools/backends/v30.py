from __future__ import annotations

import copy
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from letools._arrow import canonical_data_schema, cast_data_table, normalize_feature_shapes
from letools._io import write_json
from letools._stats import aggregate_episode_stats, flatten_stats
from letools._video import apply_encoding_metadata, media_duration, write_media_group
from letools.backends.base import DatasetBackend
from letools.conversion_types import ConversionConfig
from letools.model import Episode
from letools.plugins import DatasetSource
from letools.telemetry import StageRecorder


def _groups_by_size(items: list[Episode], sizes: list[float], limit: int) -> list[list[Episode]]:
    groups: list[list[Episode]] = []
    current: list[Episode] = []
    current_size = 0.0
    for item, size in zip(items, sizes, strict=True):
        if current and current_size + size >= limit:
            groups.append(current)
            current = []
            current_size = 0.0
        current.append(item)
        current_size += size
    if current:
        groups.append(current)
    return groups


class LeRobotV30Backend(DatasetBackend):
    version = "v3.0"

    def write(
        self,
        source: DatasetSource,
        destination: Path,
        config: ConversionConfig,
        recorder: StageRecorder,
    ) -> None:
        metadata_started = time.perf_counter()
        info = copy.deepcopy(source.metadata.info)
        info["codebase_version"] = "v3.0"
        info.pop("total_chunks", None)
        info.pop("total_videos", None)
        info["data_files_size_in_mb"] = config.data_file_size_mb
        info["video_files_size_in_mb"] = config.video_file_size_mb
        info["data_path"] = "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet"
        info["video_path"] = (
            "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4"
            if source.metadata.video_keys
            else None
        )
        info["fps"] = int(source.metadata.fps)
        normalize_feature_shapes(source, info["features"])
        for key, feature in info["features"].items():
            if feature["dtype"] != "video":
                feature["fps"] = source.metadata.fps
            elif any(
                source.media_profile(episode, key).requires_encoding
                for episode in source.episodes
            ):
                apply_encoding_metadata(
                    feature,
                    source.metadata.fps,
                    config.video_encoding,
                    include_legacy_video_info=False,
                )
        write_json(destination / "meta/info.json", info)
        task_rows = [
            {"task_index": index, "task": task}
            for index, task in sorted(source.metadata.tasks.items())
        ]
        task_table = pa.Table.from_pylist(
            task_rows,
            schema=pa.schema([("task_index", pa.int64()), ("task", pa.string())]),
        )
        task_path = destination / "meta/tasks.parquet"
        task_path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(task_table, task_path)
        recorder.add("metadata_prepare", time.perf_counter() - metadata_started)

        data_plan_started = time.perf_counter()
        episode_sizes = [
            source.data_profile(episode).episode_logical_bytes / (1024**2)
            for episode in source.episodes
        ]
        data_groups = _groups_by_size(list(source.episodes), episode_sizes, config.data_file_size_mb)
        data_schema = canonical_data_schema(source)
        rows: dict[int, dict[str, Any]] = {}
        global_offset = 0
        for file_number, group in enumerate(data_groups):
            chunk_index, file_index = divmod(file_number, config.chunks_size)
            for episode in group:
                rows[episode.index] = {
                    "episode_index": episode.index,
                    "data/chunk_index": chunk_index,
                    "data/file_index": file_index,
                    "dataset_from_index": global_offset,
                    "dataset_to_index": global_offset + episode.length,
                }
                global_offset += episode.length
        recorder.add("data_plan", time.perf_counter() - data_plan_started)

        def write_data_group(item: tuple[int, list[Episode]]) -> None:
            file_number, group = item
            chunk_index, file_index = divmod(file_number, config.chunks_size)
            path = destination / info["data_path"].format(
                chunk_index=chunk_index, file_index=file_index
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            tables = [cast_data_table(table, data_schema) for table in source.read_episodes(group)]
            pq.write_table(pa.concat_tables(tables), path)

        data_started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=min(config.workers, len(data_groups) or 1)) as pool:
            list(pool.map(write_data_group, enumerate(data_groups)))
        recorder.add(
            "data_execute", time.perf_counter() - data_started, tasks=len(data_groups)
        )

        video_plan_elapsed = 0.0
        video_execute_elapsed = 0.0
        video_tasks = 0
        for video_key in source.metadata.video_keys:
            video_plan_started = time.perf_counter()
            episodes = list(source.episodes)
            sizes = [
                source.media_profile(episode, video_key).input_bytes / (1024**2)
                for episode in episodes
            ]
            video_groups = _groups_by_size(episodes, sizes, config.video_file_size_mb)
            jobs = []
            for file_number, group in enumerate(video_groups):
                chunk_index, file_index = divmod(file_number, config.chunks_size)
                elapsed = 0.0
                for episode in group:
                    media = source.media_input(episode, video_key)
                    duration = media_duration(media, source.metadata.fps)
                    rows[episode.index].update(
                        {
                            f"videos/{video_key}/chunk_index": chunk_index,
                            f"videos/{video_key}/file_index": file_index,
                            f"videos/{video_key}/from_timestamp": elapsed,
                            f"videos/{video_key}/to_timestamp": elapsed + duration,
                        }
                    )
                    elapsed += duration
                output = destination / info["video_path"].format(
                    video_key=video_key, chunk_index=chunk_index, file_index=file_index
                )
                inputs = [source.media_input(episode, video_key) for episode in group]
                jobs.append((inputs, output))
            video_plan_elapsed += time.perf_counter() - video_plan_started
            video_started = time.perf_counter()
            with ThreadPoolExecutor(max_workers=min(config.video_workers, len(jobs) or 1)) as pool:
                list(
                    pool.map(
                        lambda job: write_media_group(
                            *job, source.metadata.fps, config.video_encoding
                        ),
                        jobs,
                    )
                )
            video_execute_elapsed += time.perf_counter() - video_started
            video_tasks += len(jobs)
        recorder.add("video_plan", video_plan_elapsed)
        recorder.add("video_execute", video_execute_elapsed, tasks=video_tasks)

        metadata_started = time.perf_counter()
        episode_rows = []
        for episode in source.episodes:
            row = rows[episode.index]
            row.update(
                {
                    "tasks": list(episode.tasks),
                    "length": episode.length,
                    **flatten_stats(episode.stats),
                    "meta/episodes/chunk_index": 0,
                    "meta/episodes/file_index": 0,
                }
            )
            episode_rows.append(row)
        episode_path = destination / "meta/episodes/chunk-000/file-000.parquet"
        episode_path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(episode_rows), episode_path)
        stats = aggregate_episode_stats([episode.stats for episode in source.episodes])
        write_json(destination / "meta/stats.json", stats)
        recorder.add("metadata_finalize", time.perf_counter() - metadata_started)
