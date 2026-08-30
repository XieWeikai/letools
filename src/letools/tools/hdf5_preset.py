"""Portable HDF5 mapping presets and read-only source inspection.

Presets capture user decisions, not inferred robotics semantics. Inspection is
limited to facts available from one representative episode: HDF5 paths,
storage types, array shapes, and encoded-image dimensions where readable.
"""

from __future__ import annotations

import io
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import av
import h5py
import numpy as np
from av.error import FFmpegError

from letools.plugins.hdf5 import (
    HDF5Mapping,
    HDF5NumericField,
    HDF5VideoField,
)


_PRESET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_GENERATED_TARGETS = {
    "timestamp",
    "frame_index",
    "episode_index",
    "index",
    "task_index",
}


@dataclass(frozen=True)
class HDF5FieldDescription:
    """One dataset discovered in a representative HDF5 episode."""

    key: str
    shape: tuple[int, ...]
    dtype: str
    kind: str
    width: int | None = None
    height: int | None = None
    encoded_format: str | None = None


@dataclass(frozen=True)
class HDF5Preset:
    """Named, JSON-serializable policy for constructing an HDF5Source."""

    SCHEMA_VERSION: ClassVar[int] = 1

    name: str
    mapping: HDF5Mapping
    description: str = ""

    def __post_init__(self) -> None:
        if not _PRESET_NAME.fullmatch(self.name):
            raise ValueError(
                "Preset names must start with an alphanumeric character and only "
                "contain letters, numbers, '.', '_', or '-'"
            )
        if self.mapping.fps <= 0:
            raise ValueError("Preset FPS must be positive")
        if not self.mapping.numeric_fields:
            raise ValueError("Preset must map at least one numeric field")
        if (self.mapping.task_key is None) == (self.mapping.default_task is None):
            raise ValueError("Preset must set exactly one of task_key or default_task")
        targets = [field.target_key for field in self.mapping.numeric_fields]
        targets.extend(field.target_key for field in self.mapping.video_fields)
        if any(not target for target in targets):
            raise ValueError("Preset target feature keys cannot be empty")
        if len(targets) != len(set(targets)):
            raise ValueError("Preset contains duplicate target feature keys")
        reserved = sorted(set(targets) & _GENERATED_TARGETS)
        if reserved:
            raise ValueError(f"Preset maps generated target feature keys: {reserved}")
        if any(field.width <= 0 or field.height <= 0 for field in self.mapping.video_fields):
            raise ValueError("Preset video dimensions must be positive")

    def to_dict(self) -> dict[str, Any]:
        """Return the stable on-disk representation without Python type details."""

        return {
            "schema_version": self.SCHEMA_VERSION,
            "name": self.name,
            "description": self.description,
            "mapping": {
                "fps": self.mapping.fps,
                "episode_glob": self.mapping.episode_glob,
                "robot_type": self.mapping.robot_type,
                "task_key": self.mapping.task_key,
                "default_task": self.mapping.default_task,
                "numeric_fields": [
                    {
                        "source_key": field.source_key,
                        "target_key": field.target_key,
                        "dtype": field.dtype,
                        "names": list(field.names) if field.names is not None else None,
                    }
                    for field in self.mapping.numeric_fields
                ],
                "video_fields": [
                    {
                        "source_key": field.source_key,
                        "target_key": field.target_key,
                        "width": field.width,
                        "height": field.height,
                        "encoded_format": field.encoded_format,
                    }
                    for field in self.mapping.video_fields
                ],
            },
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> HDF5Preset:
        """Parse a preset and reject incompatible or incomplete schemas early."""

        if value.get("schema_version") != cls.SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported HDF5 preset schema: {value.get('schema_version')!r}"
            )
        try:
            raw = value["mapping"]
            mapping = HDF5Mapping(
                fps=int(raw["fps"]),
                numeric_fields=tuple(
                    HDF5NumericField(
                        source_key=str(field["source_key"]),
                        target_key=str(field["target_key"]),
                        dtype=str(field["dtype"]) if field.get("dtype") else None,
                        names=tuple(map(str, field["names"]))
                        if field.get("names") is not None
                        else None,
                    )
                    for field in raw["numeric_fields"]
                ),
                video_fields=tuple(
                    HDF5VideoField(
                        source_key=str(field["source_key"]),
                        target_key=str(field["target_key"]),
                        width=int(field["width"]),
                        height=int(field["height"]),
                        encoded_format=str(field.get("encoded_format", "jpeg")),
                    )
                    for field in raw.get("video_fields", ())
                ),
                task_key=str(raw["task_key"]) if raw.get("task_key") else None,
                default_task=str(raw["default_task"])
                if raw.get("default_task") is not None
                else None,
                robot_type=str(raw["robot_type"]) if raw.get("robot_type") else None,
                episode_glob=str(raw.get("episode_glob", "*.hdf5")),
            )
            return cls(
                name=str(value["name"]),
                description=str(value.get("description", "")),
                mapping=mapping,
            )
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            if isinstance(error, ValueError) and str(error).startswith("Preset names"):
                raise
            raise ValueError(f"Invalid HDF5 preset: {error}") from error


def preset_directory() -> Path:
    """Return the XDG-compatible per-user preset directory."""

    config_home = os.environ.get("XDG_CONFIG_HOME")
    root = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return root / "letools" / "hdf5-presets"


def save_preset(
    preset: HDF5Preset,
    path: str | Path | None = None,
    *,
    overwrite: bool = False,
) -> Path:
    """Atomically save a preset to an explicit path or the user preset store."""

    destination = (
        Path(path).expanduser()
        if path is not None
        else preset_directory() / f"{preset.name}.json"
    )
    destination = destination.resolve(strict=False)
    if destination.exists() and not overwrite:
        raise FileExistsError(f"Preset already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    temporary.write_text(json.dumps(preset.to_dict(), indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)
    return destination


def load_preset(reference: str | Path) -> HDF5Preset:
    """Load a preset by explicit JSON path or by name from the user store."""

    candidate = Path(reference).expanduser()
    if not candidate.is_file():
        name = candidate.stem if candidate.suffix == ".json" else str(reference)
        candidate = preset_directory() / f"{name}.json"
    if not candidate.is_file():
        raise FileNotFoundError(f"HDF5 preset not found: {reference}")
    try:
        raw = json.loads(candidate.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON in HDF5 preset {candidate}: {error}") from error
    if not isinstance(raw, dict):
        raise ValueError(f"HDF5 preset must contain a JSON object: {candidate}")
    return HDF5Preset.from_dict(raw)


def list_presets() -> tuple[tuple[Path, HDF5Preset], ...]:
    """Return valid user presets in stable name order."""

    root = preset_directory()
    if not root.is_dir():
        return ()
    return tuple((path, load_preset(path)) for path in sorted(root.glob("*.json")))


def _natural_key(path: Path) -> tuple[Any, ...]:
    return tuple(
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", path.name)
    )


def _representative_episode(root: Path, episode_glob: str) -> Path:
    if root.is_file():
        return root
    paths = sorted(root.glob(episode_glob), key=_natural_key)
    if not paths:
        raise FileNotFoundError(
            f"No HDF5 episodes matching {episode_glob!r} under {root}"
        )
    return paths[0]


def _encoded_image_details(dataset: h5py.Dataset) -> tuple[int, int, str] | None:
    """Recognize the encoded-frame representation supported by HDF5Source."""

    element_dtype = h5py.check_dtype(vlen=dataset.dtype)
    if dataset.ndim != 1 or element_dtype is None or np.dtype(element_dtype) != np.uint8:
        return None
    if len(dataset) == 0:
        return None
    value = dataset[0]
    payload = value.tobytes() if isinstance(value, np.ndarray) else bytes(value)
    if not payload:
        return None
    try:
        with av.open(io.BytesIO(payload), mode="r") as container:
            codec_name = container.streams.video[0].codec_context.name
            frame = next(container.decode(video=0))
        encoded_format = "jpeg" if codec_name == "mjpeg" else codec_name
        return frame.width, frame.height, encoded_format
    except (FFmpegError, EOFError, StopIteration, ValueError):
        return None


def inspect_hdf5(
    root: str | Path,
    *,
    episode_glob: str = "*.hdf5",
) -> tuple[Path, tuple[HDF5FieldDescription, ...]]:
    """Describe all datasets in one episode without changing source data."""

    sample = _representative_episode(Path(root).expanduser().resolve(), episode_glob)
    fields: list[HDF5FieldDescription] = []
    with h5py.File(sample, "r") as handle:
        def visit(key: str, value: h5py.Group | h5py.Dataset) -> None:
            if not isinstance(value, h5py.Dataset):
                return
            image = _encoded_image_details(value)
            if image is not None:
                width, height, encoded_format = image
                kind = "encoded_image"
            elif value.ndim >= 1 and value.dtype.kind in "biuf":
                width = height = None
                encoded_format = None
                kind = "numeric"
            elif h5py.check_string_dtype(value.dtype) is not None and value.ndim == 0:
                width = height = None
                encoded_format = None
                kind = "text"
            else:
                width = height = None
                encoded_format = None
                kind = "unsupported"
            fields.append(
                HDF5FieldDescription(
                    key=key,
                    shape=tuple(map(int, value.shape)),
                    dtype=str(value.dtype),
                    kind=kind,
                    width=width,
                    height=height,
                    encoded_format=encoded_format,
                )
            )

        handle.visititems(visit)
    return sample, tuple(sorted(fields, key=lambda field: field.key))
