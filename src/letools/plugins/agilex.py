"""AgileX directory source for timestamped JSON joint states and JPEG cameras."""

from __future__ import annotations

import bisect
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import av
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


_CAMERAS = ("left", "front", "right")
_JOINT_STREAMS = {
    "observation.state": ("puppetLeft", "puppetRight"),
    "action": ("masterLeft", "masterRight"),
}
_GENERATED_DTYPES = {
    "timestamp": np.float32,
    "frame_index": np.int64,
    "episode_index": np.int64,
    "index": np.int64,
    "task_index": np.int64,
}


def _natural_key(path: Path) -> tuple[Any, ...]:
    return tuple(
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", path.name)
    )


def _timestamp(path: Path) -> float:
    try:
        return float(path.stem)
    except ValueError as error:
        raise ValueError(f"Expected a numeric timestamp filename: {path}") from error


def _timestamped_files(path: Path, suffix: str) -> tuple[Path, ...]:
    files = tuple(sorted(path.glob(f"*{suffix}"), key=_timestamp))
    if not files:
        raise FileNotFoundError(f"No {suffix} files found under {path}")
    return files


def _statistics(values: np.ndarray) -> dict[str, list[Any]]:
    return {
        "min": np.atleast_1d(values.min(axis=0)).tolist(),
        "max": np.atleast_1d(values.max(axis=0)).tolist(),
        "mean": np.atleast_1d(values.mean(axis=0)).tolist(),
        "std": np.atleast_1d(values.std(axis=0)).tolist(),
        "count": [len(values)],
    }


def _joint_names(role: str) -> list[str]:
    return [
        f"arm.jointStatePosition.{role}{side}.joint{joint}"
        for side in ("Left", "Right")
        for joint in range(7)
    ]


@dataclass(frozen=True)
class AgileXFrameSequence(FrameSequence):
    """Ordered JPEG files belonging to one episode camera."""

    paths: tuple[Path, ...]
    frame_count: int
    width: int
    height: int
    encoded_format: str
    estimated_size_bytes: int

    def read_batch(self, start: int, stop: int) -> tuple[bytes, ...]:
        if not 0 <= start <= stop <= self.frame_count:
            raise IndexError(
                f"Invalid frame range [{start}, {stop}) for {self.frame_count} frames"
            )
        return tuple(path.read_bytes() for path in self.paths[start:stop])


class AgileXSource(DatasetSource):
    """Read AgileX episodes recorded as timestamp-named JSON and JPEG files.

    The left camera is the synchronization clock. All camera streams retain
    their newest common frame count, and each joint sample is the most recent
    value at or before the corresponding left-camera timestamp.
    """

    def __init__(
        self,
        root: str | Path,
        instruction: str,
        *,
        fps: int = 30,
        robot_type: str = "cobot_magic",
    ):
        self.root = Path(root).resolve()
        self.instruction = instruction.strip()
        self.fps = fps
        self.robot_type = robot_type.strip()
        if not self.instruction:
            raise ValueError("AgileX instruction cannot be empty")
        if fps <= 0:
            raise ValueError("AgileX FPS must be positive")
        if not self.robot_type:
            raise ValueError("AgileX robot type cannot be empty")

        episode_paths = sorted(
            (path for path in self.root.glob("episode*") if path.is_dir()),
            key=_natural_key,
        )
        if not episode_paths:
            raise FileNotFoundError(f"No episode directories found under {self.root}")

        self._numeric: dict[int, dict[str, np.ndarray]] = {}
        self._profiles: dict[int, EpisodeDataProfile] = {}
        episodes: list[Episode] = []
        total_frames = 0
        video_dimensions: dict[str, tuple[int, int]] | None = None
        for episode_index, path in enumerate(episode_paths):
            cameras = {
                name: _timestamped_files(path / "camera" / "color" / name, ".jpg")
                for name in _CAMERAS
            }
            length = min(len(files) for files in cameras.values())
            cameras = {name: files[-length:] for name, files in cameras.items()}
            anchor_timestamps = np.asarray(
                [_timestamp(frame) for frame in cameras["left"]], dtype=np.float64
            )
            numeric, numeric_physical_bytes = self._read_numeric(path, anchor_timestamps)
            generated = self._generated_arrays(episode_index, total_frames, length)
            values = {**numeric, **generated}
            self._numeric[episode_index] = values

            if video_dimensions is None:
                video_dimensions = {
                    name: self._image_dimensions(files[0])
                    for name, files in cameras.items()
                }
            videos = {
                f"observation.images.{name}": AgileXFrameSequence(
                    paths=files,
                    frame_count=length,
                    width=video_dimensions[name][0],
                    height=video_dimensions[name][1],
                    encoded_format="jpeg",
                    estimated_size_bytes=sum(item.stat().st_size for item in files),
                )
                for name, files in cameras.items()
            }
            stats = {key: _statistics(value) for key, value in values.items()}
            logical_bytes = sum(value.nbytes for value in values.values())
            self._profiles[episode_index] = EpisodeDataProfile(
                locality_key=str(path),
                episode_logical_bytes=logical_bytes,
                resource_logical_bytes=logical_bytes,
                resource_physical_bytes=numeric_physical_bytes,
                resource_rows=length,
            )
            episodes.append(
                Episode(
                    index=episode_index,
                    length=length,
                    tasks=(self.instruction,),
                    stats=stats,
                    data_path=path,
                    data_end=length,
                    videos=videos,
                )
            )
            total_frames += length

        assert video_dimensions is not None
        features = self._features(video_dimensions)
        info = {
            "codebase_version": "agilex-v1",
            "robot_type": self.robot_type,
            "total_episodes": len(episodes),
            "total_frames": total_frames,
            "total_tasks": 1,
            "total_videos": len(episodes) * len(_CAMERAS),
            "total_chunks": 1,
            "chunks_size": 1000,
            "fps": fps,
            "splits": {"train": f"0:{len(episodes)}"},
            "data_path": None,
            "video_path": None,
            "features": features,
        }
        self.metadata = DatasetMetadata(
            version="agilex-v1",
            fps=fps,
            features=features,
            robot_type=self.robot_type,
            splits=info["splits"],
            total_frames=total_frames,
            total_episodes=len(episodes),
            tasks={0: self.instruction},
            info=info,
        )
        self.episodes = tuple(episodes)

    @staticmethod
    def _image_dimensions(path: Path) -> tuple[int, int]:
        with av.open(str(path)) as container:
            stream = container.streams.video[0]
            return stream.width, stream.height

    @staticmethod
    def _read_position(path: Path) -> np.ndarray:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))["position"]
        except (OSError, json.JSONDecodeError, KeyError) as error:
            raise ValueError(f"Cannot read joint position from {path}") from error
        result = np.asarray(value, dtype=np.float64)
        if result.shape != (7,) or not np.isfinite(result).all():
            raise ValueError(f"Joint position in {path} must contain 7 finite values")
        return result

    def _read_numeric(
        self, episode_path: Path, anchor_timestamps: np.ndarray
    ) -> tuple[dict[str, np.ndarray], int]:
        result: dict[str, np.ndarray] = {}
        physical_bytes = 0
        for target, roles in _JOINT_STREAMS.items():
            sides = []
            for role in roles:
                files = _timestamped_files(
                    episode_path / "arm" / "jointState" / role, ".json"
                )
                timestamps = [_timestamp(path) for path in files]
                indices = [
                    max(0, bisect.bisect_right(timestamps, timestamp) - 1)
                    for timestamp in anchor_timestamps
                ]
                selected = [files[index] for index in indices]
                sides.append(np.stack([self._read_position(path) for path in selected]))
                physical_bytes += sum(path.stat().st_size for path in set(selected))
            result[target] = np.concatenate(sides, axis=1)
        return result, physical_bytes

    def _generated_arrays(
        self, episode_index: int, global_offset: int, length: int
    ) -> dict[str, np.ndarray]:
        return {
            "timestamp": np.arange(length, dtype=np.float32) / np.float32(self.fps),
            "frame_index": np.arange(length, dtype=np.int64),
            "episode_index": np.full(length, episode_index, dtype=np.int64),
            "index": np.arange(global_offset, global_offset + length, dtype=np.int64),
            "task_index": np.zeros(length, dtype=np.int64),
        }

    def _features(
        self, dimensions: dict[str, tuple[int, int]]
    ) -> dict[str, dict[str, Any]]:
        features: dict[str, dict[str, Any]] = {
            "observation.state": {
                "dtype": "float64",
                "shape": [14],
                "names": [_joint_names("puppet")],
            },
            "action": {
                "dtype": "float64",
                "shape": [14],
                "names": [_joint_names("master")],
            },
        }
        for name, (width, height) in dimensions.items():
            video_info = {
                "video.fps": self.fps,
                "video.codec": "mjpeg",
                "video.pix_fmt": "yuvj420p",
                "video.is_depth_map": False,
                "has_audio": False,
            }
            features[f"observation.images.{name}"] = {
                "dtype": "video",
                "shape": [3, height, width],
                "names": ["channels", "height", "width"],
                "info": {
                    "video.height": height,
                    "video.width": width,
                    "video.codec": "mjpeg",
                    "video.pix_fmt": "yuvj420p",
                    "video.is_depth_map": False,
                    "video.fps": self.fps,
                    "video.channels": 3,
                    "has_audio": False,
                },
                "video_info": video_info,
            }
        features.update(
            {
                key: {
                    "dtype": np.dtype(dtype).name,
                    "shape": [1],
                    "names": None,
                    "fps": self.fps,
                }
                for key, dtype in _GENERATED_DTYPES.items()
            }
        )
        return features

    def read_episode(self, episode: Episode) -> pa.Table:
        values = self._numeric[episode.index]
        return pa.Table.from_arrays(
            [numpy_to_arrow(values[key]) for key in values], names=list(values)
        )

    def data_profile(self, episode: Episode) -> EpisodeDataProfile:
        return self._profiles[episode.index]

    def media_profile(self, episode: Episode, key: str) -> MediaProfile:
        media = episode.videos[key]
        return MediaProfile(
            locality_key=f"{episode.data_path}:{key}",
            input_bytes=media.estimated_size_bytes,
            kind="frame_sequence",
            requires_encoding=True,
        )

    def planner_identity(self) -> tuple[str, str]:
        payload = {
            "fps": self.fps,
            "robot_type": self.robot_type,
            "instruction": self.instruction,
            "episodes": [
                (episode.data_path.name, episode.length) for episode in self.episodes
            ],
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return "letools.plugins.agilex.AgileXSource", digest
