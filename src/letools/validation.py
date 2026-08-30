"""Structural validation and semantic cross-layout dataset comparison."""

from __future__ import annotations

import copy
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from letools._arrow import canonical_data_schema, cast_data_table, normalize_feature_shapes
from letools._video import packet_digests, video_duration
from letools.model import VideoSlice
from letools.plugins import DatasetSource, open_dataset


@dataclass
class ValidationReport:
    """Validity, checked totals, and non-mutating diagnostics for one dataset."""

    path: Path
    version: str | None
    valid: bool
    episodes: int = 0
    frames: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ComparisonReport:
    """Semantic equality result and the amount of data actually compared."""

    left: Path
    right: Path
    equal: bool
    episodes_checked: int
    frames_checked: int
    videos_checked: int
    errors: list[str] = field(default_factory=list)


def validate_dataset(path: str | Path, *, deep: bool = False) -> ValidationReport:
    """Validate one physical LeRobot layout without attempting repair.

    Shallow mode checks metadata, referenced files, row totals, and basic shape
    consistency. Deep mode additionally reads every episode and verifies frame
    indices and required video durations.
    """

    path = Path(path).resolve()
    try:
        source = open_dataset(path)
    except Exception as error:
        return ValidationReport(path, None, False, errors=[str(error)])
    errors: list[str] = []
    warnings: list[str] = []
    metadata = source.metadata
    if len(source.episodes) != metadata.total_episodes:
        errors.append(
            f"info.total_episodes={metadata.total_episodes}, metadata has {len(source.episodes)}"
        )
    indices = [episode.index for episode in source.episodes]
    if indices != list(range(len(indices))):
        errors.append("episode indices are not contiguous from zero")
    frames = sum(episode.length for episode in source.episodes)
    if frames != metadata.total_frames:
        errors.append(f"info.total_frames={metadata.total_frames}, episodes contain {frames}")
    data_files: dict[Path, list[Any]] = defaultdict(list)
    for episode in source.episodes:
        data_files[episode.data_path].append(episode)
        if not episode.data_path.is_file():
            errors.append(f"missing data file: {episode.data_path}")
        for video_key in metadata.video_keys:
            video = episode.videos.get(video_key)
            if video is None:
                errors.append(f"episode {episode.index} has no {video_key} video reference")
            elif not video.path.is_file():
                errors.append(f"missing video file: {video.path}")
    for data_path, episodes in data_files.items():
        if not data_path.is_file():
            continue
        rows = pq.read_metadata(data_path).num_rows
        expected = sum(episode.length for episode in episodes)
        if rows != expected:
            errors.append(f"{data_path}: {rows} rows, expected {expected}")
    if source.episodes and source.episodes[0].data_path.is_file():
        schema = pq.read_schema(source.episodes[0].data_path)
        for field in schema:
            feature = metadata.features.get(field.name)
            if not feature or feature.get("dtype") in {"image", "video"}:
                continue
            depth = 0
            data_type = field.type
            while pa.types.is_list(data_type) or pa.types.is_large_list(data_type):
                depth += 1
                data_type = data_type.value_type
            shape = feature.get("shape", [])
            if len(shape) > 1 and depth != len(shape):
                warnings.append(
                    f"{field.name}: metadata shape {shape} implies {len(shape)} nested dimensions, "
                    f"but Parquet has {depth}"
                )
    if deep and not errors:
        for episode in source.episodes:
            table = source.read_episode(episode)
            if table.num_rows != episode.length:
                errors.append(
                    f"episode {episode.index}: {table.num_rows} rows, expected {episode.length}"
                )
                continue
            if "episode_index" in table.column_names:
                values = table["episode_index"].combine_chunks().to_numpy()
                if not np.all(values == episode.index):
                    errors.append(f"episode {episode.index}: episode_index column mismatch")
            if "frame_index" in table.column_names:
                values = table["frame_index"].combine_chunks().to_numpy()
                if not np.array_equal(values, np.arange(episode.length)):
                    errors.append(f"episode {episode.index}: frame_index is not contiguous")
        required_durations: dict[Path, float] = defaultdict(float)
        for episode in source.episodes:
            for video in episode.videos.values():
                required_durations[video.path] = max(required_durations[video.path], video.end)
        for video_path, required in required_durations.items():
            if not video_path.exists():
                continue
            duration = video_duration(video_path)
            if duration + 1 / metadata.fps < required:
                errors.append(
                    f"{video_path}: duration {duration:.6f}s is shorter than {required:.6f}s"
                )
    return ValidationReport(
        path=path,
        version=metadata.version,
        valid=not errors,
        episodes=len(source.episodes),
        frames=frames,
        errors=errors,
        warnings=warnings,
    )


def _normalized_features(source: DatasetSource) -> dict[str, dict[str, Any]]:
    features = copy.deepcopy(source.metadata.features)
    normalize_feature_shapes(source, features)
    normalized: dict[str, dict[str, Any]] = {}
    for key, value in features.items():
        item = dict(value)
        if item.get("dtype") != "video":
            item.pop("fps", None)
        normalized[key] = item
    return normalized


def _nested_close(left: Any, right: Any) -> bool:
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            _nested_close(left[key], right[key]) for key in left
        )
    try:
        return bool(np.allclose(np.asarray(left), np.asarray(right), rtol=1e-6, atol=1e-7))
    except (TypeError, ValueError):
        return left == right


def _video_digest_map(source: DatasetSource) -> dict[tuple[int, str], str]:
    grouped: dict[tuple[str, Path], list[tuple[int, VideoSlice]]] = defaultdict(list)
    for episode in source.episodes:
        for key, video in episode.videos.items():
            grouped[(key, video.path)].append((episode.index, video))
    def digest_group(
        item: tuple[tuple[str, Path], list[tuple[int, VideoSlice]]],
    ) -> list[tuple[tuple[int, str], str]]:
        (key, _), items = item
        items.sort(key=lambda item: item[1].start)
        digests = packet_digests([item[1] for item in items])
        return [
            ((episode_index, key), digest)
            for (episode_index, _), digest in zip(items, digests, strict=True)
        ]

    result = {}
    with ThreadPoolExecutor(max_workers=min(2, len(grouped) or 1)) as pool:
        for values in pool.map(digest_group, grouped.items()):
            result.update(values)
    return result


def compare_datasets(
    left: str | Path | DatasetSource,
    right: str | Path | DatasetSource,
    *,
    check_data: bool = True,
    check_videos: bool = False,
) -> ComparisonReport:
    """Compare two sources semantically rather than requiring byte identity.

    Data comparison canonicalizes Arrow schemas and checks all values. Optional
    video comparison hashes encoded packet payloads per episode/camera, allowing
    harmless MP4 container differences while detecting changed media content.
    """

    lhs = open_dataset(left) if isinstance(left, (str, Path)) else left
    rhs = open_dataset(right) if isinstance(right, (str, Path)) else right
    errors: list[str] = []
    if lhs.metadata.fps != rhs.metadata.fps:
        errors.append(f"fps differs: {lhs.metadata.fps} != {rhs.metadata.fps}")
    if _normalized_features(lhs) != _normalized_features(rhs):
        errors.append("feature schemas differ")
    if lhs.metadata.tasks != rhs.metadata.tasks:
        errors.append("tasks differ")
    if len(lhs.episodes) != len(rhs.episodes):
        errors.append(f"episode counts differ: {len(lhs.episodes)} != {len(rhs.episodes)}")
    episodes_checked = 0
    frames_checked = 0
    left_schema = canonical_data_schema(lhs)
    right_schema = canonical_data_schema(rhs)
    if not left_schema.equals(right_schema, check_metadata=False):
        errors.append("canonical Arrow schemas differ")
    for left_episode, right_episode in zip(lhs.episodes, rhs.episodes, strict=False):
        if left_episode.index != right_episode.index:
            errors.append(f"episode index differs: {left_episode.index} != {right_episode.index}")
            continue
        episodes_checked += 1
        if left_episode.length != right_episode.length:
            errors.append(f"episode {left_episode.index}: length differs")
            continue
        frames_checked += left_episode.length
        if left_episode.tasks != right_episode.tasks:
            errors.append(f"episode {left_episode.index}: tasks differ")
        if not _nested_close(left_episode.stats, right_episode.stats):
            errors.append(f"episode {left_episode.index}: statistics differ")
        if check_data:
            left_table = cast_data_table(lhs.read_episode(left_episode), left_schema)
            right_table = cast_data_table(rhs.read_episode(right_episode), left_schema)
            if not left_table.equals(right_table, check_metadata=False):
                errors.append(f"episode {left_episode.index}: frame data differs")
        if len(errors) >= 50:
            errors.append("comparison stopped after 50 errors")
            break
    videos_checked = 0
    if check_videos and not errors:
        left_digests = _video_digest_map(lhs)
        right_digests = _video_digest_map(rhs)
        videos_checked = len(left_digests)
        if left_digests.keys() != right_digests.keys():
            errors.append("video episode/key sets differ")
        else:
            for key in left_digests:
                if left_digests[key] != right_digests[key]:
                    errors.append(f"episode {key[0]} video {key[1]} packet payload differs")
                    if len(errors) >= 50:
                        break
    return ComparisonReport(
        left=lhs.root,
        right=rhs.root,
        equal=not errors,
        episodes_checked=episodes_checked,
        frames_checked=frames_checked,
        videos_checked=videos_checked,
        errors=errors,
    )
