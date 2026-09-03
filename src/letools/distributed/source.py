"""Portable source reconstruction and format-neutral episode slicing."""

from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path
from typing import Sequence

import numpy as np
import pyarrow as pa

from letools.model import (
    DatasetMetadata,
    Episode,
    EpisodeDataProfile,
    MediaInput,
    MediaProfile,
)
from letools.plugins import (
    AgileXSource,
    DatasetSource,
    HDF5Mapping,
    HDF5Source,
    open_dataset,
)
from letools.tools.hdf5_preset import HDF5Preset

from .types import SourceSpec


def open_source_spec(spec: SourceSpec) -> DatasetSource:
    """Reconstruct a source using only data stored in the job manifest."""

    if spec.kind == "lerobot":
        return open_dataset(spec.root)
    if spec.kind == "hdf5":
        preset = HDF5Preset.from_dict(spec.options["preset"])
        return HDF5Source(spec.root, preset.mapping)
    if spec.kind == "agilex":
        return AgileXSource(
            spec.root,
            str(spec.options["instruction"]),
            fps=int(spec.options.get("fps", 30)),
            robot_type=str(spec.options.get("robot_type", "cobot_magic")),
        )
    raise ValueError(f"Unsupported distributed source kind: {spec.kind}")


def _constant_stats(value: int, count: int) -> dict[str, list[int | float]]:
    return {
        "min": [value],
        "max": [value],
        "mean": [float(value)],
        "std": [0.0],
        "count": [count],
    }


def _index_stats(start: int, length: int) -> dict[str, list[int | float]]:
    values = np.arange(start, start + length, dtype=np.int64)
    return {
        "min": [int(values.min())],
        "max": [int(values.max())],
        "mean": [float(values.mean())],
        "std": [float(values.std())],
        "count": [length],
    }


class EpisodeSubsetSource(DatasetSource):
    """Present a contiguous source interval as a standalone zero-based dataset.

    Backends require zero-based episode and frame indices. This adapter keeps
    payload access delegated to the original source while rewriting only those
    generated columns and their statistics. It is deliberately internal to
    distributed execution and adds no branches to normal conversion.
    """

    def __init__(self, source: DatasetSource, start: int, stop: int):
        if not 0 <= start < stop <= len(source.episodes):
            raise ValueError(f"Invalid episode interval [{start}, {stop})")
        self.root = source.root
        self._source = source
        originals = source.episodes[start:stop]
        episodes: list[Episode] = []
        self._originals: list[Episode] = []
        frame_offset = 0
        for new_index, original in enumerate(originals):
            # Keep source statistics unchanged. The part's physical rows use
            # zero-based local system columns, but final merge recomputes only
            # system-column statistics from those rows. Feature and source
            # statistics must remain identical to a non-distributed conversion.
            episodes.append(replace(original, index=new_index))
            self._originals.append(original)
            frame_offset += original.length
        self.episodes = tuple(episodes)
        info = copy.deepcopy(source.metadata.info)
        info["total_episodes"] = len(episodes)
        info["total_frames"] = frame_offset
        info["splits"] = {"train": f"0:{len(episodes)}"}
        self.metadata = DatasetMetadata(
            version=source.metadata.version,
            fps=source.metadata.fps,
            features=copy.deepcopy(source.metadata.features),
            robot_type=source.metadata.robot_type,
            splits={"train": f"0:{len(episodes)}"},
            total_frames=frame_offset,
            total_episodes=len(episodes),
            tasks=dict(source.metadata.tasks),
            info=info,
        )
        self._global_offsets: list[int] = []
        offset = 0
        for episode in episodes:
            self._global_offsets.append(offset)
            offset += episode.length

    def _original(self, episode: Episode) -> Episode:
        if not 0 <= episode.index < len(self._originals):
            raise IndexError(f"Unknown subset episode: {episode.index}")
        return self._originals[episode.index]

    def _rewrite(self, table: pa.Table, episode: Episode) -> pa.Table:
        replacements: dict[str, pa.Array] = {}
        if "episode_index" in table.column_names:
            replacements["episode_index"] = pa.array(
                np.full(len(table), episode.index, dtype=np.int64)
            )
        if "index" in table.column_names:
            start = self._global_offsets[episode.index]
            replacements["index"] = pa.array(
                np.arange(start, start + len(table), dtype=np.int64)
            )
        for name, values in replacements.items():
            position = table.schema.get_field_index(name)
            table = table.set_column(position, name, values)
        return table

    def read_episode(self, episode: Episode) -> pa.Table:
        return self._rewrite(self._source.read_episode(self._original(episode)), episode)

    def read_episodes(self, episodes: Sequence[Episode]) -> list[pa.Table]:
        originals = [self._original(episode) for episode in episodes]
        tables = self._source.read_episodes(originals)
        return [
            self._rewrite(table, episode)
            for table, episode in zip(tables, episodes, strict=True)
        ]

    def data_profile(self, episode: Episode) -> EpisodeDataProfile:
        return self._source.data_profile(self._original(episode))

    def media_input(self, episode: Episode, key: str) -> MediaInput:
        return self._source.media_input(self._original(episode), key)

    def media_profile(self, episode: Episode, key: str) -> MediaProfile:
        return self._source.media_profile(self._original(episode), key)


def hdf5_source_spec(root: Path, mapping: HDF5Mapping) -> SourceSpec:
    """Embed a mapping so compute nodes do not depend on a user preset store."""

    preset = HDF5Preset(name="distributed", mapping=mapping)
    return SourceSpec("hdf5", str(root.resolve()), {"preset": preset.to_dict()})


def agilex_source_spec(
    root: Path, instruction: str, fps: int, robot_type: str
) -> SourceSpec:
    return SourceSpec(
        "agilex",
        str(root.resolve()),
        {"instruction": instruction, "fps": fps, "robot_type": robot_type},
    )


__all__ = [
    "EpisodeSubsetSource",
    "agilex_source_spec",
    "hdf5_source_spec",
    "open_source_spec",
]
