"""Independent official LeRobot loader smoke; run in its own environment.

Usage: official-python scripts/check_editor_loaders.py DATASET [DATASET ...]
This deliberately imports no letools code. It checks real boundary frames and
task strings, not just that metadata construction returns without an exception.
Use LeRobot 0.3.3 for v2.1, or a v3-compatible LeRobot version for v3.0.
"""

import json
import sys
from pathlib import Path

import pyarrow.parquet as pq
from lerobot.datasets.lerobot_dataset import LeRobotDataset, LeRobotDatasetMetadata

for argument in sys.argv[1:]:
    root = Path(argument)
    info = json.loads((root / "meta/info.json").read_text())
    metadata = LeRobotDatasetMetadata("local/editor-acceptance", root=root)
    dataset = LeRobotDataset("local/editor-acceptance", root=root, video_backend="pyav")
    assert len(dataset) == info["total_frames"]
    rows = []
    if info["codebase_version"] == "v2.1":
        offset = 0
        for line in (root / "meta/episodes.jsonl").read_text().splitlines():
            row = json.loads(line)
            row["dataset_from_index"] = offset
            offset += row["length"]
            row["dataset_to_index"] = offset
            rows.append(row)
    else:
        for path in sorted((root / "meta/episodes").glob("*/*.parquet")):
            rows.extend(pq.read_table(path).to_pylist())
    checked = 0
    for episode in rows:
        for index in (episode["dataset_from_index"], episode["dataset_to_index"] - 1):
            item = dataset[index]
            assert int(item["episode_index"]) == episode["episode_index"]
            assert isinstance(item["task"], str), type(item["task"])
            assert item["task"] in episode["tasks"], (item["task"], episode["tasks"])
            for key, feature in info["features"].items():
                if feature["dtype"] == "video":
                    names = feature.get("names")
                    shape = feature["shape"]
                    expected = (
                        tuple(shape)
                        if names == ["channels", "height", "width"]
                        else (shape[-1], shape[0], shape[1])
                    )
                    assert tuple(item[key].shape) == expected, (
                        key,
                        tuple(item[key].shape),
                        expected,
                    )
            checked += 1
    print(
        json.dumps(
            {
                "path": str(root),
                "frames": len(dataset),
                "episodes": len(rows),
                "boundary_frames_checked": checked,
                "official_metadata_and_dataset_loaders": "passed",
            }
        ),
        flush=True,
    )
