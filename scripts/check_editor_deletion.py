"""Verify an editor deletion against its immutable source; run via Slurm.

The generic dataset comparator expects identical episode sets, so it cannot
compare a deletion directly. This checker maps retained episode IDs instead.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path

import numpy as np
from letools import open_dataset, validate_dataset
from letools_editor.media import probe


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--delete", type=int, nargs="+", required=True)
    args = parser.parse_args()
    source = open_dataset(args.source)
    output = open_dataset(args.output)
    retained = [ep for ep in source.episodes if ep.index not in args.delete]
    assert len(output.episodes) == len(retained)
    assert output.metadata.total_frames == sum(ep.length for ep in retained)
    deleted = set(args.delete)
    original_by_video: dict[tuple[str, Path], list[int]] = defaultdict(list)
    for episode in source.episodes:
        for key, video in episode.videos.items():
            original_by_video[key, video.path].append(episode.index)
    reused, altered = set(), set()
    for new, old in zip(output.episodes, retained, strict=True):
        assert new.length == old.length and new.tasks == old.tasks
        old_data = source.read_episode(old)
        new_data = output.read_episode(new)
        for column in old_data.column_names:
            if column in {"index", "episode_index"}:
                continue
            assert old_data[column].equals(new_data[column]), (old.index, column)
        assert new_data["episode_index"].to_pylist() == [new.index] * new.length
        for key, old_video in old.videos.items():
            new_video = new.videos[key]
            if not deleted.intersection(original_by_video[key, old_video.path]):
                pair = (old_video.path, new_video.path)
                if pair not in reused:
                    assert sha256(pair[0]) == sha256(pair[1]), pair
                    reused.add(pair)
                for stat, value in old.stats[key].items():
                    np.testing.assert_allclose(new.stats[key][stat], value, rtol=0, atol=0)
            else:
                altered.add((old_video.path, new_video.path))
    for _, path in reused | altered:
        assert probe(path)["frames"] > 0
    report = validate_dataset(args.output, deep=True)
    assert report.valid and not report.warnings, report
    print(json.dumps({"episodes": len(retained), "frames": output.metadata.total_frames,
                      "whole_video_files_byte_identical": len(reused),
                      "partial_video_files": len(altered), "deep_validation": "passed"}))


if __name__ == "__main__":
    main()
