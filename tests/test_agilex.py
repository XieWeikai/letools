from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from letools import (
    AgileXSource,
    ConversionConfig,
    compare_datasets,
    convert,
    open_dataset,
    validate_dataset,
)
from letools.cli import main
from test_video import _make_jpegs


def _write_joint(path: Path, timestamp: float, base: float) -> None:
    path.mkdir(parents=True, exist_ok=True)
    value = {"position": [base + joint for joint in range(7)]}
    (path / f"{timestamp:.6f}.json").write_text(json.dumps(value), encoding="utf-8")


def make_agilex(root: Path) -> Path:
    episode = root / "episode0"
    camera_timestamps = {
        "left": (0.00, 0.10, 0.20, 0.30, 0.40),
        "front": (0.11, 0.21, 0.31, 0.41),
        "right": (0.12, 0.22, 0.32, 0.42),
    }
    for camera_index, (camera, timestamps) in enumerate(camera_timestamps.items()):
        frames = _make_jpegs(20 + camera_index * 20, count=len(timestamps))
        directory = episode / "camera" / "color" / camera
        directory.mkdir(parents=True)
        for timestamp, frame in zip(timestamps, frames, strict=True):
            (directory / f"{timestamp:.6f}.jpg").write_bytes(frame)

    for role_index, role in enumerate(
        ("puppetLeft", "puppetRight", "masterLeft", "masterRight")
    ):
        directory = episode / "arm" / "jointState" / role
        for sample_index, timestamp in enumerate((0.05, 0.15, 0.25, 0.35, 0.45)):
            _write_joint(directory, timestamp, role_index * 100 + sample_index * 10)
    return root


def test_agilex_source_synchronizes_and_converts_both_versions(tmp_path: Path) -> None:
    root = make_agilex(tmp_path / "raw")
    source = AgileXSource(root, "pick up the object", fps=10)

    assert source.metadata.tasks == {0: "pick up the object"}
    assert source.episodes[0].length == 4
    table = source.read_episode(source.episodes[0])
    assert table["observation.state"].to_pylist()[0] == [
        *[float(joint) for joint in range(7)],
        *[100.0 + joint for joint in range(7)],
    ]
    assert table["observation.state"].to_pylist()[1][0] == 10.0
    assert table["action"].to_pylist()[0][0] == 200.0
    assert table["task_index"].to_pylist() == [0, 0, 0, 0]

    config = ConversionConfig(workers=2, video_workers=2)
    v21 = tmp_path / "v21"
    v30 = tmp_path / "v30"
    convert(source, v21, "v2.1", config=config)
    convert(source, v30, "v3.0", config=config)

    assert validate_dataset(v21, deep=True).valid
    assert validate_dataset(v30, deep=True).valid
    assert open_dataset(v21).metadata.tasks == {0: "pick up the object"}
    assert open_dataset(v30).metadata.tasks == {0: "pick up the object"}
    assert compare_datasets(v21, v30, check_videos=True).equal
    assert pq.read_table(v21 / "data/chunk-000/episode_000000.parquet").num_rows == 4


def test_agilex_cli_requires_and_writes_instruction(tmp_path: Path) -> None:
    root = make_agilex(tmp_path / "raw")
    destination = tmp_path / "v21"
    assert main(
        [
            "convert",
            str(root),
            str(destination),
            "--source-format",
            "agilex",
            "--instruction",
            "move the block",
            "--fps",
            "10",
            "--to",
            "v2.1",
        ]
    ) == 0
    assert open_dataset(destination).metadata.tasks == {0: "move the block"}


def test_agilex_rejects_empty_instruction(tmp_path: Path) -> None:
    root = make_agilex(tmp_path / "raw")
    try:
        AgileXSource(root, "  ")
    except ValueError as error:
        assert "instruction" in str(error)
    else:
        raise AssertionError("An empty instruction should be rejected")
