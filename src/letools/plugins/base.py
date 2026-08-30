"""Read-side plugin protocol and compatibility adapters for path-based sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from letools.model import (
    DatasetMetadata,
    Episode,
    EpisodeDataProfile,
    FrameSequence,
    MediaInput,
    MediaProfile,
    VideoSlice,
)


class DatasetSource(ABC):
    """Format plugin contract exposed to target backends and the planner.

    A source owns format parsing. Consumers see Arrow tables, media inputs, and
    size/locality profiles instead of source-format storage details.
    """

    root: Path
    metadata: DatasetMetadata
    episodes: tuple[Episode, ...]

    def planner_identity(self) -> tuple[str, str]:
        """Return stable plugin and configuration identities for plan caching."""

        kind = f"{type(self).__module__}.{type(self).__qualname__}"
        return kind, ""

    def iter_episodes(self) -> Iterator[Episode]:
        """Iterate episodes in stable dataset order."""

        return iter(self.episodes)

    @abstractmethod
    def read_episode(self, episode: Episode) -> pa.Table:
        """Materialize one episode as a target-ready Arrow table."""

        raise NotImplementedError

    def read_episodes(self, episodes: Sequence[Episode]) -> list[pa.Table]:
        """Materialize a group, with an override point for batched readers."""

        return [self.read_episode(episode) for episode in episodes]

    def data_profile(self, episode: Episode) -> EpisodeDataProfile:
        """Describe data size and shared-resource locality for an episode.

        This compatibility implementation serves path-based Parquet plugins.
        Other formats override it instead of emulating Parquet files.
        """

        cache: dict[Path, EpisodeDataProfile] = getattr(self, "_data_profile_cache", {})
        if episode.data_path in cache:
            return cache[episode.data_path]
        metadata = pq.read_metadata(episode.data_path)
        logical = sum(
            metadata.row_group(row_group).column(column).total_uncompressed_size
            for row_group in range(metadata.num_row_groups)
            for column in range(metadata.row_group(row_group).num_columns)
        )
        profile = EpisodeDataProfile(
            locality_key=str(episode.data_path),
            episode_logical_bytes=logical,
            resource_logical_bytes=logical,
            resource_physical_bytes=episode.data_path.stat().st_size,
            resource_rows=metadata.num_rows,
        )
        cache[episode.data_path] = profile
        self._data_profile_cache = cache
        return profile

    def media_input(self, episode: Episode, key: str) -> MediaInput:
        """Return an encoded video slice or an encoded frame sequence."""

        return episode.videos[key]

    def media_profile(self, episode: Episode, key: str) -> MediaProfile:
        """Describe media bytes, locality, and whether encoding is required."""

        media = self.media_input(episode, key)
        if isinstance(media, VideoSlice):
            cache: dict[Path, MediaProfile] = getattr(self, "_media_profile_cache", {})
            if media.path not in cache:
                cache[media.path] = MediaProfile(
                    str(media.path), media.path.stat().st_size, "video_slice", False
                )
                self._media_profile_cache = cache
            return cache[media.path]
        if isinstance(media, FrameSequence):
            return MediaProfile(
                f"{type(media).__module__}.{type(media).__qualname__}:{id(media)}",
                media.estimated_size_bytes,
                "frame_sequence",
                True,
            )
        raise TypeError(f"Unsupported media input for {key!r}: {type(media).__name__}")
