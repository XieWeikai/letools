"""LeRobot v2.1/v3.0 readers normalized into format-neutral episodes."""

from __future__ import annotations

import json
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from letools.model import DatasetMetadata, Episode, EpisodeDataProfile, VideoSlice
from letools.plugins.base import DatasetSource


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _metadata(version: str, info: dict[str, Any], tasks: dict[int, str]) -> DatasetMetadata:
    return DatasetMetadata(
        version=version,
        fps=int(info["fps"]),
        features=info["features"],
        robot_type=info.get("robot_type"),
        splits=info.get("splits", {"train": f"0:{info['total_episodes']}"}),
        total_frames=int(info["total_frames"]),
        total_episodes=int(info["total_episodes"]),
        tasks=tasks,
        info=info,
    )


class LeRobotV21Source(DatasetSource):
    """Read legacy JSONL metadata and one Parquet/video file per episode."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        info = _read_json(self.root / "meta/info.json")
        if info.get("codebase_version") != "v2.1":
            raise ValueError(f"Expected LeRobot v2.1, got {info.get('codebase_version')!r}")
        tasks = {
            int(row["task_index"]): row["task"]
            for row in _read_jsonl(self.root / "meta/tasks.jsonl")
        }
        episode_rows = _read_jsonl(self.root / "meta/episodes.jsonl")
        stats_rows = {
            int(row["episode_index"]): row["stats"]
            for row in _read_jsonl(self.root / "meta/episodes_stats.jsonl")
        }
        self.metadata = _metadata("v2.1", info, tasks)
        chunks_size = int(info.get("chunks_size", 1000))
        data_template = info["data_path"]
        video_template = info.get("video_path")
        episodes: list[Episode] = []
        for row in episode_rows:
            index = int(row["episode_index"])
            values = {"episode_index": index, "episode_chunk": index // chunks_size}
            data_path = self.root / data_template.format(**values)
            videos = {}
            if video_template:
                duration = int(row["length"]) / self.metadata.fps
                for video_key in self.metadata.video_keys:
                    path = self.root / video_template.format(video_key=video_key, **values)
                    videos[video_key] = VideoSlice(path, 0.0, duration)
            episodes.append(
                Episode(
                    index=index,
                    length=int(row["length"]),
                    tasks=tuple(row.get("tasks", ())),
                    stats=stats_rows[index],
                    data_path=data_path,
                    data_end=int(row["length"]),
                    videos=videos,
                )
            )
        self.episodes = tuple(episodes)

    def read_episode(self, episode: Episode) -> pa.Table:
        """Read the episode's complete v2.1 Parquet file."""

        return pq.read_table(episode.data_path)


class LeRobotV30Source(DatasetSource):
    """Read grouped v3 shards and expose logical row/time episode ranges."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        info = _read_json(self.root / "meta/info.json")
        if info.get("codebase_version") != "v3.0":
            raise ValueError(f"Expected LeRobot v3.0, got {info.get('codebase_version')!r}")
        task_table = pq.read_table(self.root / "meta/tasks.parquet")
        tasks = {
            int(row["task_index"]): row["task"] for row in task_table.to_pylist()
        }
        self.metadata = _metadata("v3.0", info, tasks)
        episode_files = sorted((self.root / "meta/episodes").glob("*/*.parquet"))
        if not episode_files:
            raise FileNotFoundError(f"No episode metadata under {self.root / 'meta/episodes'}")
        episode_table = pa.concat_tables([pq.read_table(path) for path in episode_files])
        rows = episode_table.to_pylist()
        first_offsets: dict[tuple[int, int], int] = {}
        for row in rows:
            key = (int(row["data/chunk_index"]), int(row["data/file_index"]))
            first_offsets.setdefault(key, int(row["dataset_from_index"]))
        episodes = []
        for row in rows:
            index = int(row["episode_index"])
            data_chunk = int(row["data/chunk_index"])
            data_file = int(row["data/file_index"])
            data_path = self.root / info["data_path"].format(
                chunk_index=data_chunk, file_index=data_file
            )
            base = first_offsets[(data_chunk, data_file)]
            start = int(row["dataset_from_index"]) - base
            end = int(row["dataset_to_index"]) - base
            videos = {}
            for video_key in self.metadata.video_keys:
                prefix = f"videos/{video_key}"
                chunk = int(row[f"{prefix}/chunk_index"])
                file = int(row[f"{prefix}/file_index"])
                path = self.root / info["video_path"].format(
                    video_key=video_key, chunk_index=chunk, file_index=file
                )
                videos[video_key] = VideoSlice(
                    path=path,
                    start=float(row[f"{prefix}/from_timestamp"]),
                    end=float(row[f"{prefix}/to_timestamp"]),
                )
            stats: dict[str, dict[str, Any]] = defaultdict(dict)
            for key, value in row.items():
                if key.startswith("stats/"):
                    feature, statistic = key[6:].rsplit("/", 1)
                    stats[feature][statistic] = value
            episodes.append(
                Episode(
                    index=index,
                    length=int(row["length"]),
                    tasks=tuple(row.get("tasks", ())),
                    stats=dict(stats),
                    data_path=data_path,
                    data_start=start,
                    data_end=end,
                    videos=videos,
                )
            )
        self.episodes = tuple(episodes)
        self._local = threading.local()

    def read_episode(self, episode: Episode) -> pa.Table:
        """Slice one episode while reusing the current shard per worker thread."""

        if getattr(self._local, "path", None) != episode.data_path:
            self._local.path = episode.data_path
            self._local.table = pq.read_table(episode.data_path)
        return self._local.table.slice(episode.data_start, episode.length)

    def data_profile(self, episode: Episode) -> EpisodeDataProfile:
        """Scale a shared v3 shard profile to this episode's row contribution."""

        resource = super().data_profile(episode)
        rows = max(1, resource.resource_rows)
        return EpisodeDataProfile(
            locality_key=resource.locality_key,
            episode_logical_bytes=max(
                1, round(resource.resource_logical_bytes * episode.length / rows)
            ),
            resource_logical_bytes=resource.resource_logical_bytes,
            resource_physical_bytes=resource.resource_physical_bytes,
            resource_rows=resource.resource_rows,
        )


def open_dataset(root: str | Path) -> DatasetSource:
    """Detect a physical LeRobot version from info.json and open its reader."""

    root = Path(root)
    info = _read_json(root / "meta/info.json")
    version = info.get("codebase_version")
    if version == "v2.1":
        return LeRobotV21Source(root)
    if version == "v3.0":
        return LeRobotV30Source(root)
    raise ValueError(f"Unsupported LeRobot dataset version: {version!r}")
