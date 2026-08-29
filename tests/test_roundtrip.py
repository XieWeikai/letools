from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from letools import ConversionConfig, compare_datasets, convert, validate_dataset
from letools._native import file_sizes


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
    convert(source, v30, "v3.0", config=config)
    assert validate_dataset(v30, deep=True).valid
    v30_info = json.loads((v30 / "meta/info.json").read_text())
    assert v30_info["features"]["observation.state"]["shape"] == [2]
    assert v30_info["features"]["action"]["shape"] == [2]
    assert compare_datasets(source, v30).equal
    convert(v30, roundtrip, "v2.1", config=config)
    assert validate_dataset(roundtrip, deep=True).valid
    roundtrip_info = json.loads((roundtrip / "meta/info.json").read_text())
    assert roundtrip_info["features"]["observation.state"]["shape"] == [2]
    assert roundtrip_info["features"]["action"]["shape"] == [2]
    assert compare_datasets(source, roundtrip).equal


def test_file_sizes(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.write_bytes(b"abc")
    second.write_bytes(b"12345")
    assert file_sizes([first, second]) == [3, 5]
