"""Explicit, mapping-driven HDF5 input plugin.

The plugin intentionally knows nothing about a specific robot or public HDF5
dataset. A mapping names every exported numeric and camera field. This keeps
source parsing independent of LeRobot target layout and prevents silent guesses
about joint order, task semantics, or fields that may be discarded.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import h5py
import numpy as np
import pyarrow as pa

from letools._arrow import numpy_to_arrow
from letools.model import (
    DatasetMetadata,
    Episode,
    EpisodeDataProfile,
    FrameSequence,
    MediaProfile,
)
from letools.plugins.base import DatasetSource


_GENERATED_FEATURES = {
    "timestamp": ("float32", np.float32),
    "frame_index": ("int64", np.int64),
    "episode_index": ("int64", np.int64),
    "index": ("int64", np.int64),
    "task_index": ("int64", np.int64),
}

_STREAMING_METADATA_CACHE_INITIAL_BYTES = 1024**2
_STREAMING_METADATA_CACHE_MAX_BYTES = 2 * 1024**2


def _open_streaming_file(path: Path) -> h5py.File:
    """Open one sequential reader with a bounded HDF5 metadata cache.

    HDF5's default per-file metadata cache may grow to 32 MiB. A video worker
    keeps this handle open for an episode, but only walks one contiguous vlen
    index, so a much smaller cache avoids multiplying idle metadata by the
    worker count.
    """

    access = h5py.h5p.create(h5py.h5p.FILE_ACCESS)
    cache = access.get_mdc_config()
    cache.set_initial_size = 1
    cache.initial_size = _STREAMING_METADATA_CACHE_INITIAL_BYTES
    cache.min_size = _STREAMING_METADATA_CACHE_INITIAL_BYTES
    cache.max_size = _STREAMING_METADATA_CACHE_MAX_BYTES
    access.set_mdc_config(cache)
    try:
        identifier = h5py.h5f.open(
            os.fsencode(path),
            flags=h5py.h5f.ACC_RDONLY,
            fapl=access,
        )
    finally:
        access.close()
    return h5py.File(identifier)


@dataclass(frozen=True)
class HDF5NumericField:
    """Map one frame-aligned HDF5 array to one LeRobot data feature."""

    source_key: str
    target_key: str
    dtype: str | None = None
    names: tuple[str, ...] | None = None


@dataclass(frozen=True)
class HDF5VideoField:
    """Map one frame-aligned encoded-image array to one LeRobot video feature."""

    source_key: str
    target_key: str
    width: int
    height: int
    encoded_format: str = "jpeg"


@dataclass(frozen=True)
class HDF5Mapping:
    """Complete declarative policy for a one-file-per-episode HDF5 dataset."""

    fps: int
    numeric_fields: tuple[HDF5NumericField, ...]
    video_fields: tuple[HDF5VideoField, ...] = ()
    task_key: str | None = None
    default_task: str | None = None
    robot_type: str | None = None
    episode_glob: str = "*.hdf5"


@dataclass(frozen=True)
class HDF5FrameSequence(FrameSequence):
    """Batch reader for variable-length encoded image values in one HDF5 file."""

    path: Path
    dataset_key: str
    frame_count: int
    width: int
    height: int
    encoded_format: str
    estimated_size_bytes: int

    def read_batch(self, start: int, stop: int) -> tuple[bytes, ...]:
        """Open the file for this worker and materialize one encoded-image batch."""

        if not 0 <= start <= stop <= self.frame_count:
            raise IndexError(
                f"Invalid frame range [{start}, {stop}) for {self.frame_count} frames"
            )
        with h5py.File(self.path, "r") as handle:
            values = handle[self.dataset_key][start:stop]
        return tuple(
            value.tobytes() if isinstance(value, np.ndarray) else bytes(value)
            for value in values
        )

    def iter_batches(self, batch_frames: int) -> Iterator[tuple[bytes, ...]]:
        """Yield every batch while holding one read-only HDF5 file handle."""

        if batch_frames <= 0:
            raise ValueError("Frame batch size must be positive")
        with _open_streaming_file(self.path) as handle:
            dataset = handle[self.dataset_key]
            for start in range(0, self.frame_count, batch_frames):
                stop = min(self.frame_count, start + batch_frames)
                values = dataset[start:stop]
                batch = tuple(
                    value.tobytes() if isinstance(value, np.ndarray) else bytes(value)
                    for value in values
                )
                # A suspended generator retains its locals. Drop the HDF5 slice
                # before yielding so each worker holds only one JPEG batch.
                del values
                yield batch


def _natural_key(path: Path) -> tuple[Any, ...]:
    return tuple(
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", path.name)
    )


def _read_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, np.ndarray) and value.ndim == 0:
        return _read_text(value.item())
    return str(value)


def _statistics(values: np.ndarray) -> dict[str, list[Any]]:
    values = np.asarray(values)
    return {
        "min": np.atleast_1d(values.min(axis=0)).tolist(),
        "max": np.atleast_1d(values.max(axis=0)).tolist(),
        "mean": np.atleast_1d(values.mean(axis=0)).tolist(),
        "std": np.atleast_1d(values.std(axis=0)).tolist(),
        "count": [len(values)],
    }


def _normalized_key(key: str) -> str:
    value = key.strip("/")
    if not value:
        raise ValueError("HDF5 dataset keys cannot be empty")
    return value


class HDF5Source(DatasetSource):
    """Read one-file-per-episode HDF5 data using an explicit field mapping.

    Construction performs a metadata/schema scan and computes numeric episode
    statistics. Frame payloads are not decoded; camera values are opened later
    in bounded batches by worker threads.
    """

    def __init__(self, root: str | Path, mapping: HDF5Mapping):
        self.root = Path(root).resolve()
        self.mapping = self._validate_mapping(mapping)
        paths = self._episode_paths()
        if not paths:
            raise FileNotFoundError(
                f"No HDF5 episodes matching {mapping.episode_glob!r} under {self.root}"
            )

        episodes: list[Episode] = []
        profiles: dict[int, EpisodeDataProfile] = {}
        global_offsets: dict[int, int] = {}
        feature_schema: dict[str, dict[str, Any]] | None = None
        tasks: dict[int, str] = {}
        task_indices: dict[str, int] = {}
        total_frames = 0
        for episode_index, path in enumerate(paths):
            with h5py.File(path, "r") as handle:
                length = self._episode_length(handle, path)
                schema = self._numeric_schema(handle, path)
                if feature_schema is None:
                    feature_schema = schema
                elif schema != feature_schema:
                    raise ValueError(f"Mapped numeric schema differs in {path}")
                task = self._episode_task(handle, path)
                task_index = task_indices.setdefault(task, len(task_indices))
                tasks.setdefault(task_index, task)
                stats, logical_bytes, physical_bytes = self._numeric_stats(
                    handle, episode_index, total_frames, task_index, length
                )
                videos = self._video_inputs(handle, path, length)

            global_offsets[episode_index] = total_frames
            profiles[episode_index] = EpisodeDataProfile(
                locality_key=str(path),
                episode_logical_bytes=logical_bytes,
                resource_logical_bytes=logical_bytes,
                resource_physical_bytes=physical_bytes,
                resource_rows=length,
            )
            episodes.append(
                Episode(
                    index=episode_index,
                    length=length,
                    tasks=(task,),
                    stats=stats,
                    data_path=path,
                    data_end=length,
                    videos=videos,
                )
            )
            total_frames += length

        assert feature_schema is not None
        features = {
            **feature_schema,
            **self._video_schema(),
            **{
                key: {"dtype": dtype, "shape": [1], "names": None, "fps": mapping.fps}
                for key, (dtype, _) in _GENERATED_FEATURES.items()
            },
        }
        info = {
            "codebase_version": "hdf5-v1",
            "robot_type": mapping.robot_type,
            "total_episodes": len(episodes),
            "total_frames": total_frames,
            "total_tasks": len(tasks),
            "total_videos": len(episodes) * len(mapping.video_fields),
            "total_chunks": 1,
            "chunks_size": 1000,
            "fps": mapping.fps,
            "splits": {"train": f"0:{len(episodes)}"},
            "data_path": None,
            "video_path": None,
            "features": features,
        }
        self.metadata = DatasetMetadata(
            version="hdf5-v1",
            fps=mapping.fps,
            features=features,
            robot_type=mapping.robot_type,
            splits=info["splits"],
            total_frames=total_frames,
            total_episodes=len(episodes),
            tasks=tasks,
            info=info,
        )
        self.episodes = tuple(episodes)
        self._profiles = profiles
        self._global_offsets = global_offsets

    @staticmethod
    def _validate_mapping(mapping: HDF5Mapping) -> HDF5Mapping:
        if mapping.fps <= 0:
            raise ValueError("HDF5 mapping FPS must be positive")
        if not mapping.numeric_fields:
            raise ValueError("At least one numeric HDF5 field is required")
        if (mapping.task_key is None) == (mapping.default_task is None):
            raise ValueError("Set exactly one of task_key or default_task")
        targets = [field.target_key for field in mapping.numeric_fields]
        targets.extend(field.target_key for field in mapping.video_fields)
        duplicates = sorted({key for key in targets if targets.count(key) > 1})
        if duplicates:
            raise ValueError(f"Duplicate target feature keys: {duplicates}")
        reserved = sorted(set(targets) & _GENERATED_FEATURES.keys())
        if reserved:
            raise ValueError(f"Mapped fields use generated target keys: {reserved}")
        if any(field.width <= 0 or field.height <= 0 for field in mapping.video_fields):
            raise ValueError("Mapped video dimensions must be positive")
        return HDF5Mapping(
            fps=mapping.fps,
            numeric_fields=tuple(
                HDF5NumericField(
                    _normalized_key(field.source_key),
                    field.target_key,
                    field.dtype,
                    field.names,
                )
                for field in mapping.numeric_fields
            ),
            video_fields=tuple(
                HDF5VideoField(
                    _normalized_key(field.source_key),
                    field.target_key,
                    field.width,
                    field.height,
                    field.encoded_format,
                )
                for field in mapping.video_fields
            ),
            task_key=_normalized_key(mapping.task_key) if mapping.task_key else None,
            default_task=mapping.default_task,
            robot_type=mapping.robot_type,
            episode_glob=mapping.episode_glob,
        )

    def _episode_paths(self) -> list[Path]:
        if self.root.is_file():
            return [self.root]
        return sorted(self.root.glob(self.mapping.episode_glob), key=_natural_key)

    def _episode_length(self, handle: h5py.File, path: Path) -> int:
        fields: tuple[HDF5NumericField | HDF5VideoField, ...] = (
            *self.mapping.numeric_fields,
            *self.mapping.video_fields,
        )
        lengths = []
        for field in fields:
            if field.source_key not in handle:
                raise KeyError(f"Missing HDF5 dataset {field.source_key!r} in {path}")
            dataset = handle[field.source_key]
            if not isinstance(dataset, h5py.Dataset) or dataset.ndim < 1:
                raise ValueError(f"Mapped HDF5 key {field.source_key!r} is not frame-aligned")
            lengths.append(int(dataset.shape[0]))
        if not lengths or lengths[0] <= 0 or len(set(lengths)) != 1:
            raise ValueError(f"Mapped HDF5 fields have inconsistent episode lengths in {path}")
        return lengths[0]

    def _numeric_schema(self, handle: h5py.File, path: Path) -> dict[str, dict[str, Any]]:
        schema = {}
        for field in self.mapping.numeric_fields:
            dataset = handle[field.source_key]
            dtype = np.dtype(field.dtype or dataset.dtype)
            if dtype.kind not in "biuf":
                raise TypeError(f"Mapped field {field.source_key!r} has unsupported dtype in {path}")
            shape = list(dataset.shape[1:]) or [1]
            if field.names is not None and len(field.names) != int(np.prod(shape)):
                raise ValueError(
                    f"Feature {field.target_key!r} has {len(field.names)} names for shape {shape}"
                )
            schema[field.target_key] = {
                "dtype": dtype.name,
                "shape": shape,
                "names": list(field.names) if field.names is not None else None,
            }
        return schema

    def _video_schema(self) -> dict[str, dict[str, Any]]:
        return {
            field.target_key: {
                "dtype": "video",
                "shape": [3, field.height, field.width],
                "names": ["channels", "height", "width"],
                "info": {
                    "video.height": field.height,
                    "video.width": field.width,
                    "video.codec": "mpeg4",
                    "video.pix_fmt": "yuv420p",
                    "video.is_depth_map": False,
                    "video.fps": self.mapping.fps,
                    "video.channels": 3,
                    "has_audio": False,
                },
                "video_info": {
                    "video.fps": self.mapping.fps,
                    "video.codec": "mpeg4",
                    "video.pix_fmt": "yuv420p",
                    "video.is_depth_map": False,
                    "has_audio": False,
                },
            }
            for field in self.mapping.video_fields
        }

    def _episode_task(self, handle: h5py.File, path: Path) -> str:
        if self.mapping.default_task is not None:
            return self.mapping.default_task
        assert self.mapping.task_key is not None
        if self.mapping.task_key not in handle:
            raise KeyError(f"Missing task dataset {self.mapping.task_key!r} in {path}")
        task = _read_text(handle[self.mapping.task_key][()]).strip()
        if not task:
            raise ValueError(f"Task text is empty in {path}")
        return task

    def _numeric_stats(
        self,
        handle: h5py.File,
        episode_index: int,
        global_offset: int,
        task_index: int,
        length: int,
    ) -> tuple[dict[str, dict[str, list[Any]]], int, int]:
        stats = {}
        logical_bytes = 0
        physical_bytes = 0
        for field in self.mapping.numeric_fields:
            dataset = handle[field.source_key]
            values = np.asarray(dataset, dtype=field.dtype)
            stats[field.target_key] = _statistics(values)
            logical_bytes += values.nbytes
            physical_bytes += int(dataset.id.get_storage_size())
        generated = self._generated_arrays(episode_index, global_offset, task_index, length)
        for key, values in generated.items():
            stats[key] = _statistics(values)
            logical_bytes += values.nbytes
        if not self.mapping.video_fields:
            physical_bytes = max(physical_bytes, int(handle.id.get_filesize()))
        return stats, logical_bytes, physical_bytes

    def _video_inputs(
        self, handle: h5py.File, path: Path, length: int
    ) -> dict[str, HDF5FrameSequence]:
        if not self.mapping.video_fields:
            return {}
        numeric_storage = sum(
            int(handle[field.source_key].id.get_storage_size())
            for field in self.mapping.numeric_fields
        )
        media_budget = max(0, path.stat().st_size - numeric_storage)
        weights = [field.width * field.height for field in self.mapping.video_fields]
        total_weight = sum(weights)
        result = {}
        for field, weight in zip(self.mapping.video_fields, weights, strict=True):
            estimated = max(1, round(media_budget * weight / total_weight))
            result[field.target_key] = HDF5FrameSequence(
                path=path,
                dataset_key=field.source_key,
                frame_count=length,
                width=field.width,
                height=field.height,
                encoded_format=field.encoded_format,
                estimated_size_bytes=estimated,
            )
        return result

    def _generated_arrays(
        self, episode_index: int, global_offset: int, task_index: int, length: int
    ) -> dict[str, np.ndarray]:
        return {
            "timestamp": np.arange(length, dtype=np.float32) / np.float32(self.mapping.fps),
            "frame_index": np.arange(length, dtype=np.int64),
            "episode_index": np.full(length, episode_index, dtype=np.int64),
            "index": np.arange(global_offset, global_offset + length, dtype=np.int64),
            "task_index": np.full(length, task_index, dtype=np.int64),
        }

    def read_episode(self, episode: Episode) -> pa.Table:
        """Read mapped numeric arrays and append canonical generated columns."""

        with h5py.File(episode.data_path, "r") as handle:
            columns = {
                field.target_key: numpy_to_arrow(
                    np.asarray(handle[field.source_key], dtype=field.dtype)
                )
                for field in self.mapping.numeric_fields
            }
        task_index = next(
            index for index, task in self.metadata.tasks.items() if task == episode.tasks[0]
        )
        generated = self._generated_arrays(
            episode.index,
            self._global_offsets[episode.index],
            task_index,
            episode.length,
        )
        columns.update({key: pa.array(values) for key, values in generated.items()})
        return pa.table(columns)

    def data_profile(self, episode: Episode) -> EpisodeDataProfile:
        """Return the profile computed during the constructor's schema scan."""

        return self._profiles[episode.index]

    def media_profile(self, episode: Episode, key: str) -> MediaProfile:
        """Describe one stable HDF5 dataset as an encoding-required media input."""

        media = self.media_input(episode, key)
        assert isinstance(media, HDF5FrameSequence)
        return MediaProfile(
            locality_key=f"{media.path}::{media.dataset_key}",
            input_bytes=media.estimated_size_bytes,
            kind="frame_sequence",
            requires_encoding=True,
        )

    def planner_identity(self) -> tuple[str, str]:
        """Fingerprint every mapping field so semantic plans cannot collide."""

        payload = json.dumps(asdict(self.mapping), sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return "letools.plugins.hdf5.HDF5Source", digest
