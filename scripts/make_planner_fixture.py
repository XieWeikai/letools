#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
from dataclasses import asdict, replace
from pathlib import Path

from letools.backends import LeRobotV21Backend
from letools.conversion_types import ConversionConfig
from letools.model import DatasetMetadata
from letools.plugins import DatasetSource, open_dataset
from letools.telemetry import StageRecorder
from letools.validation import validate_dataset


class SubsetSource(DatasetSource):
    def __init__(
        self,
        source: DatasetSource,
        episodes: int,
        *,
        include_videos: bool,
    ) -> None:
        if source.metadata.version != "v2.1":
            raise ValueError("Planner fixtures currently require a v2.1 source")
        if episodes < 1 or episodes > len(source.episodes):
            raise ValueError("Episode count is outside the source range")
        self.root = source.root
        self._source = source
        selected = source.episodes[:episodes]
        if not include_videos:
            selected = tuple(replace(episode, videos={}) for episode in selected)
        self.episodes = tuple(selected)
        info = copy.deepcopy(source.metadata.info)
        features = copy.deepcopy(source.metadata.features)
        if not include_videos:
            features = {
                key: value for key, value in features.items() if value.get("dtype") != "video"
            }
            info["video_path"] = None
        total_frames = sum(episode.length for episode in self.episodes)
        info.update(
            {
                "total_episodes": episodes,
                "total_frames": total_frames,
                "total_videos": episodes * len(
                    [
                        value
                        for value in features.values()
                        if value.get("dtype") == "video"
                    ]
                ),
                "features": features,
                "splits": {"train": f"0:{episodes}"},
            }
        )
        self.metadata = DatasetMetadata(
            version="v2.1",
            fps=source.metadata.fps,
            features=features,
            robot_type=source.metadata.robot_type,
            splits={"train": f"0:{episodes}"},
            total_frames=total_frames,
            total_episodes=episodes,
            tasks=source.metadata.tasks,
            info=info,
        )

    def read_episode(self, episode):
        return self._source.read_episode(episode)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a frozen v2.1 planner fixture")
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--episodes", type=int, required=True)
    parser.add_argument("--no-videos", action="store_true")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--video-workers", type=int, default=3)
    args = parser.parse_args()
    if args.destination.exists():
        raise FileExistsError(args.destination)
    source = SubsetSource(
        open_dataset(args.source),
        args.episodes,
        include_videos=not args.no_videos,
    )
    recorder = StageRecorder()
    LeRobotV21Backend().write(
        source,
        args.destination,
        ConversionConfig(
            workers=max(1, args.workers),
            video_workers=max(1, args.video_workers),
            validate=False,
        ),
        recorder,
    )
    report = validate_dataset(args.destination, deep=True)
    value = {
        "destination": str(args.destination),
        "valid": report.valid,
        "errors": report.errors,
        "warnings": report.warnings,
        "episodes": report.episodes,
        "frames": report.frames,
        "stages": {key: asdict(stage) for key, stage in recorder.snapshot().items()},
    }
    print(json.dumps(value, indent=2))
    return 0 if report.valid and not report.warnings else 1


if __name__ == "__main__":
    raise SystemExit(main())
