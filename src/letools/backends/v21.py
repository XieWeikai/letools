"""LeRobot v2.1 writer: per-episode data/video files and JSONL metadata."""

from __future__ import annotations

import copy
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pyarrow.parquet as pq

from letools._arrow import normalize_feature_shapes
from letools._io import write_json, write_jsonl
from letools._media_executor import EpisodeMediaJob, run_episode_media_jobs
from letools._video import apply_encoding_metadata
from letools.backends.base import DatasetBackend
from letools.conversion_types import ConversionConfig
from letools.model import Episode
from letools.plugins import DatasetSource
from letools.telemetry import StageRecorder


class LeRobotV21Backend(DatasetBackend):
    """Fan format-neutral episodes out into the legacy v2.1 layout."""

    version = "v2.1"

    def write(
        self,
        source: DatasetSource,
        destination: Path,
        config: ConversionConfig,
        recorder: StageRecorder,
    ) -> None:
        """Write metadata, locality-grouped data, and per-episode media.

        Locality grouping ensures a shared v3 shard is opened once per worker
        group. Media dispatch preserves VideoSlice packet payloads and encodes
        FrameSequence inputs only when the source requires it.
        """

        metadata_started = time.perf_counter()
        info = copy.deepcopy(source.metadata.info)
        info["codebase_version"] = "v2.1"
        info.pop("data_files_size_in_mb", None)
        info.pop("video_files_size_in_mb", None)
        info["chunks_size"] = config.chunks_size
        info["total_chunks"] = (source.metadata.total_episodes + config.chunks_size - 1) // config.chunks_size
        info["total_videos"] = source.metadata.total_episodes * len(source.metadata.video_keys)
        info["data_path"] = "data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet"
        info["video_path"] = (
            "videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4"
            if source.metadata.video_keys
            else None
        )
        normalize_feature_shapes(source, info["features"])
        for key, feature in info["features"].items():
            if feature["dtype"] != "video":
                feature.pop("fps", None)
            elif any(
                source.media_profile(episode, key).requires_encoding
                for episode in source.episodes
            ):
                apply_encoding_metadata(
                    feature,
                    source.metadata.fps,
                    config.video_encoding,
                    include_legacy_video_info=True,
                )
        write_json(destination / "meta/info.json", info)
        write_jsonl(
            destination / "meta/tasks.jsonl",
            [
                {"task_index": index, "task": task}
                for index, task in sorted(source.metadata.tasks.items())
            ],
        )
        write_jsonl(
            destination / "meta/episodes.jsonl",
            [
                {"episode_index": episode.index, "tasks": list(episode.tasks), "length": episode.length}
                for episode in source.episodes
            ],
        )
        write_jsonl(
            destination / "meta/episodes_stats.jsonl",
            [
                {"episode_index": episode.index, "stats": episode.stats}
                for episode in source.episodes
            ],
        )
        recorder.add("metadata_prepare", time.perf_counter() - metadata_started)

        data_plan_started = time.perf_counter()
        data_groups: dict[str, list[Episode]] = defaultdict(list)
        shared_shard = False
        for episode in source.episodes:
            profile = source.data_profile(episode)
            data_groups[profile.locality_key].append(episode)
            shared_shard |= profile.resource_rows > episode.length
        recorder.add("data_plan", time.perf_counter() - data_plan_started)

        def write_episode(episode: Episode) -> None:
            table = source.read_episode(episode)
            path = destination / info["data_path"].format(
                episode_chunk=episode.index // config.chunks_size,
                episode_index=episode.index,
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(table, path)

        def write_data_group(group: list[Episode]) -> None:
            for episode in group:
                write_episode(episode)

        # A v3 shard can contain many logical episodes. Flatten only this
        # source capability so one large shard does not pin a single worker;
        # HDF5/AgileX profiles have one resource per episode and retain the
        # existing locality-group scheduling.
        data_jobs = list(source.episodes) if shared_shard else list(data_groups.values())
        writer = write_episode if shared_shard else write_data_group

        data_started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=min(config.workers, len(data_jobs) or 1)) as pool:
            list(pool.map(writer, data_jobs))
        recorder.add(
            "data_execute", time.perf_counter() - data_started, tasks=len(data_jobs)
        )

        video_plan_started = time.perf_counter()
        jobs: list[EpisodeMediaJob] = []
        for video_key in source.metadata.video_keys:
            groups: dict[str, list[tuple[Episode, Path]]] = defaultdict(list)
            for episode in source.episodes:
                target = destination / info["video_path"].format(
                    episode_chunk=episode.index // config.chunks_size,
                    episode_index=episode.index,
                    video_key=video_key,
                )
                locality = source.media_profile(episode, video_key).locality_key
                groups[locality].append((episode, target))
            for group in groups.values():
                inputs = [
                    (source.media_input(episode, video_key), target)
                    for episode, target in group
                ]
                jobs.append(
                    EpisodeMediaJob(
                        outputs=tuple(inputs),
                        fps=source.metadata.fps,
                        encoding=config.video_encoding,
                        # The destination is a hidden transaction root. A
                        # failed remux removes the entire tree, so per-file
                        # temporary rename would be redundant.
                        atomic_output=False,
                    )
                )
        recorder.add("video_plan", time.perf_counter() - video_plan_started)
        video_started = time.perf_counter()
        run_episode_media_jobs(jobs, config.video_workers)
        recorder.add(
            "video_execute", time.perf_counter() - video_started, tasks=len(jobs)
        )
        recorder.add("metadata_finalize", 0.0)
