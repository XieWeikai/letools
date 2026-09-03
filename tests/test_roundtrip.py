from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from letools import (
    ConversionConfig,
    compare_datasets,
    convert,
    merge_datasets,
    open_dataset,
    validate_dataset,
)
from letools._native import clone_or_copy_files, file_sizes


def _stats(values: np.ndarray) -> dict[str, list[float] | list[int]]:
    values = np.asarray(values)
    return {
        "min": np.atleast_1d(values.min(axis=0)).tolist(),
        "max": np.atleast_1d(values.max(axis=0)).tolist(),
        "mean": np.atleast_1d(values.mean(axis=0)).tolist(),
        "std": np.atleast_1d(values.std(axis=0)).tolist(),
        "count": [len(values)],
    }


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def make_v21(root: Path) -> Path:
    lengths = [3, 4, 2]
    episodes = []
    episode_stats = []
    global_index = 0
    for episode_index, length in enumerate(lengths):
        state = np.arange(length * 2, dtype=np.float32).reshape(length, 2) + episode_index * 10
        action = state + np.float32(0.5)
        timestamp_dtype = np.float64 if episode_index == 1 else np.float32
        timestamp = np.arange(length, dtype=timestamp_dtype) / 30
        frame_index = np.arange(length, dtype=np.int64)
        table = pa.table(
            {
                "observation.state": pa.array(state.tolist(), type=pa.list_(pa.float32())),
                "action": pa.array(action.tolist(), type=pa.list_(pa.float32())),
                "timestamp": timestamp,
                "frame_index": frame_index,
                "episode_index": np.full(length, episode_index, dtype=np.int64),
                "index": np.arange(global_index, global_index + length, dtype=np.int64),
                "task_index": np.zeros(length, dtype=np.int64),
            }
        )
        path = root / f"data/chunk-000/episode_{episode_index:06d}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, path)
        episodes.append({"episode_index": episode_index, "tasks": ["test task"], "length": length})
        episode_stats.append(
            {
                "episode_index": episode_index,
                "stats": {
                    "observation.state": _stats(state),
                    "action": _stats(action),
                    "timestamp": _stats(timestamp),
                    "frame_index": _stats(frame_index),
                    "episode_index": _stats(np.full(length, episode_index)),
                    "index": _stats(np.arange(global_index, global_index + length)),
                    "task_index": _stats(np.zeros(length)),
                },
            }
        )
        global_index += length
    info = {
        "codebase_version": "v2.1",
        "robot_type": "test",
        "total_episodes": len(lengths),
        "total_frames": sum(lengths),
        "total_tasks": 1,
        "total_videos": 0,
        "total_chunks": 1,
        "chunks_size": 1000,
        "fps": 30,
        "splits": {"train": f"0:{len(lengths)}"},
        "data_path": "data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet",
        "video_path": None,
        "features": {
            "observation.state": {"dtype": "float32", "shape": [1, 2]},
            "action": {"dtype": "float32", "shape": [1, 2]},
            "timestamp": {"dtype": "float32", "shape": [1], "names": None},
            "frame_index": {"dtype": "int64", "shape": [1], "names": None},
            "episode_index": {"dtype": "int64", "shape": [1], "names": None},
            "index": {"dtype": "int64", "shape": [1], "names": None},
            "task_index": {"dtype": "int64", "shape": [1], "names": None},
        },
    }
    (root / "meta").mkdir(parents=True, exist_ok=True)
    (root / "meta/info.json").write_text(json.dumps(info), encoding="utf-8")
    _write_jsonl(root / "meta/tasks.jsonl", [{"task_index": 0, "task": "test task"}])
    _write_jsonl(root / "meta/episodes.jsonl", episodes)
    _write_jsonl(root / "meta/episodes_stats.jsonl", episode_stats)
    return root


def test_v21_v30_roundtrip(tmp_path: Path) -> None:
    source = make_v21(tmp_path / "v21")
    v30 = tmp_path / "v30"
    roundtrip = tmp_path / "roundtrip"
    config = ConversionConfig(workers=2)
    forward = convert(source, v30, "v3.0", config=config)
    assert {
        "source_open",
        "metadata_prepare",
        "data_plan",
        "data_execute",
        "video_plan",
        "video_execute",
        "metadata_finalize",
        "conversion_validate",
        "publish_cleanup",
    } <= forward.stages.keys()
    assert forward.stages["data_execute"].tasks > 0
    assert validate_dataset(v30, deep=True).valid
    v30_info = json.loads((v30 / "meta/info.json").read_text())
    assert v30_info["features"]["observation.state"]["shape"] == [2]
    assert v30_info["features"]["action"]["shape"] == [2]
    assert compare_datasets(source, v30).equal
    reverse = convert(v30, roundtrip, "v2.1", config=config)
    assert reverse.stages["data_execute"].tasks > 0
    assert reverse.elapsed_seconds >= sum(stage.elapsed_seconds for stage in reverse.stages.values())
    assert validate_dataset(roundtrip, deep=True).valid
    roundtrip_info = json.loads((roundtrip / "meta/info.json").read_text())
    assert roundtrip_info["features"]["observation.state"]["shape"] == [2]
    assert roundtrip_info["features"]["action"]["shape"] == [2]
    assert compare_datasets(source, roundtrip).equal


def test_conversion_failure_removes_unpublished_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A backend failure must never expose or retain a partial dataset."""

    source = make_v21(tmp_path / "v21")
    destination = tmp_path / "failed-v30"

    def fail_after_partial_write(_backend, _source, staging, _config, _recorder) -> None:
        partial = staging / "videos/camera/chunk-000/partial.mp4"
        partial.parent.mkdir(parents=True)
        partial.write_bytes(b"incomplete")
        raise RuntimeError("injected backend failure")

    monkeypatch.setattr("letools.conversion.LeRobotV30Backend.write", fail_after_partial_write)
    with pytest.raises(RuntimeError, match="injected backend failure"):
        convert(source, destination, "v3.0")

    assert not destination.exists()
    assert not list(tmp_path.glob(f".{destination.name}.letools-*"))


def test_file_sizes(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.write_bytes(b"abc")
    second.write_bytes(b"12345")
    assert file_sizes([first, second]) == [3, 5]

    first_copy = tmp_path / "copies/first"
    second_copy = tmp_path / "copies/second"
    results = clone_or_copy_files(
        [(first, first_copy), (second, second_copy)], workers=2
    )
    assert [size for size, _ in results] == [3, 5]
    assert first_copy.read_bytes() == b"abc"
    assert second_copy.read_bytes() == b"12345"
    with pytest.raises(ValueError, match="positive"):
        clone_or_copy_files([], workers=0)


def _assert_merged_data(first: Path, second: Path, merged: Path) -> None:
    inputs = (open_dataset(first), open_dataset(second))
    output = open_dataset(merged)
    expected = [(source, episode) for source in inputs for episode in source.episodes]
    assert len(output.episodes) == len(expected)
    global_index = 0
    for index, ((source, episode), actual_episode) in enumerate(
        zip(expected, output.episodes, strict=True)
    ):
        actual = output.read_episode(actual_episode)
        original = source.read_episode(episode)
        for column in original.column_names:
            if column not in {"episode_index", "index", "task_index"}:
                assert actual[column].equals(original[column])
        assert set(actual["episode_index"].to_pylist()) == {index}
        assert actual["index"].to_pylist() == list(
            range(global_index, global_index + episode.length)
        )
        global_index += episode.length


def test_same_version_merge_v21_and_v30(tmp_path: Path) -> None:
    first_v21 = make_v21(tmp_path / "first-v21")
    second_v21 = make_v21(tmp_path / "second-v21")
    merged_v21 = tmp_path / "merged-v21"
    result_v21 = merge_datasets(
        [first_v21, second_v21], merged_v21, data_workers=2, file_workers=1
    )
    assert (result_v21.episodes, result_v21.frames) == (6, 18)
    assert validate_dataset(merged_v21, deep=True).valid
    _assert_merged_data(first_v21, second_v21, merged_v21)

    first_v30 = tmp_path / "first-v30"
    second_v30 = tmp_path / "second-v30"
    config = ConversionConfig(workers=2)
    convert(first_v21, first_v30, "v3.0", config=config)
    convert(second_v21, second_v30, "v3.0", config=config)
    merged_v30 = tmp_path / "merged-v30"
    result_v30 = merge_datasets(
        [first_v30, second_v30], merged_v30, data_workers=2, file_workers=1
    )
    assert (result_v30.episodes, result_v30.frames) == (6, 18)
    assert validate_dataset(merged_v30, deep=True).valid
    _assert_merged_data(first_v30, second_v30, merged_v30)
