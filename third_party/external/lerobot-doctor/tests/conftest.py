"""Shared test fixtures for lerobot-doctor tests."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest


@pytest.fixture
def tmp_dataset(tmp_path):
    """Create a minimal valid LeRobot v3 dataset."""
    return create_dataset(tmp_path / "dataset", n_episodes=3, n_frames_per_ep=10, fps=10)


def create_dataset(
    root: Path,
    n_episodes: int = 3,
    n_frames_per_ep: int = 10,
    fps: int = 10,
    action_dims: int = 2,
    state_dims: int = 4,
    include_videos: bool = False,
    include_stats: bool = True,
) -> Path:
    """Create a synthetic LeRobot v3 dataset directory."""
    root.mkdir(parents=True, exist_ok=True)
    meta_dir = root / "meta"
    meta_dir.mkdir(exist_ok=True)
    data_dir = root / "data" / "chunk-000"
    data_dir.mkdir(parents=True, exist_ok=True)
    episodes_dir = meta_dir / "episodes" / "chunk-000"
    episodes_dir.mkdir(parents=True, exist_ok=True)

    features = {
        "timestamp": {"dtype": "float32", "shape": [1], "names": None},
        "frame_index": {"dtype": "int64", "shape": [1], "names": None},
        "episode_index": {"dtype": "int64", "shape": [1], "names": None},
        "index": {"dtype": "int64", "shape": [1], "names": None},
        "task_index": {"dtype": "int64", "shape": [1], "names": None},
        "action": {"dtype": "float32", "shape": [action_dims], "names": None},
        "observation.state": {"dtype": "float32", "shape": [state_dims], "names": None},
    }

    total_frames = n_episodes * n_frames_per_ep

    # info.json
    info = {
        "codebase_version": "v3.0",
        "robot_type": "test_robot",
        "total_episodes": n_episodes,
        "total_frames": total_frames,
        "total_tasks": 1,
        "chunks_size": 1000,
        "fps": fps,
        "splits": {"train": f"0:{n_episodes}"},
        "data_path": "data/chunk-{episode_chunk:03d}/file-{episode_index:03d}.parquet",
        "video_path": None,
        "features": features,
    }
    (meta_dir / "info.json").write_text(json.dumps(info, indent=2))

    # tasks.parquet
    tasks_table = pa.table({"task_index": [0], "task": ["pick and place"]})
    pq.write_table(tasks_table, meta_dir / "tasks.parquet")

    # Episode data and metadata
    global_idx = 0
    ep_meta_rows = []
    interval = 1.0 / fps

    for ep in range(n_episodes):
        timestamps = [i * interval for i in range(n_frames_per_ep)]
        frame_indices = list(range(n_frames_per_ep))
        episode_indices = [ep] * n_frames_per_ep
        indices = list(range(global_idx, global_idx + n_frames_per_ep))
        task_indices = [0] * n_frames_per_ep
        actions = np.random.randn(n_frames_per_ep, action_dims).astype(np.float32)
        states = np.random.randn(n_frames_per_ep, state_dims).astype(np.float32)

        table = pa.table({
            "timestamp": pa.array(timestamps, type=pa.float32()),
            "frame_index": pa.array(frame_indices, type=pa.int64()),
            "episode_index": pa.array(episode_indices, type=pa.int64()),
            "index": pa.array(indices, type=pa.int64()),
            "task_index": pa.array(task_indices, type=pa.int64()),
            "action": [actions[i].tolist() for i in range(n_frames_per_ep)],
            "observation.state": [states[i].tolist() for i in range(n_frames_per_ep)],
        })
        pq.write_table(table, data_dir / f"file-{ep:03d}.parquet")

        ep_meta_rows.append({
            "episode_index": ep,
            "length": n_frames_per_ep,
            "tasks": ["pick and place"],
            "dataset_from_index": global_idx,
            "dataset_to_index": global_idx + n_frames_per_ep,
        })
        global_idx += n_frames_per_ep

    # Episodes metadata parquet
    ep_table = pa.table({
        "episode_index": [r["episode_index"] for r in ep_meta_rows],
        "length": [r["length"] for r in ep_meta_rows],
        "dataset_from_index": [r["dataset_from_index"] for r in ep_meta_rows],
        "dataset_to_index": [r["dataset_to_index"] for r in ep_meta_rows],
    })
    pq.write_table(ep_table, episodes_dir / "file-000.parquet")

    # stats.json
    if include_stats:
        stats = {
            "action": {
                "mean": [0.0] * action_dims,
                "std": [1.0] * action_dims,
                "min": [-3.0] * action_dims,
                "max": [3.0] * action_dims,
            },
            "observation.state": {
                "mean": [0.0] * state_dims,
                "std": [1.0] * state_dims,
                "min": [-3.0] * state_dims,
                "max": [3.0] * state_dims,
            },
        }
        (meta_dir / "stats.json").write_text(json.dumps(stats, indent=2))

    return root


def write_minimal_mp4(path: Path, n_frames: int, fps: int = 10, width: int = 64, height: int = 48) -> None:
    """Write a tiny MP4 for video integrity tests (requires PyAV + mpeg4 encoder)."""
    import av

    path.parent.mkdir(parents=True, exist_ok=True)
    container = av.open(str(path), mode="w")
    try:
        stream = container.add_stream("mpeg4", rate=fps)
        stream.width = width
        stream.height = height
        stream.pix_fmt = "yuv420p"
        for i in range(n_frames):
            frame = av.VideoFrame(width, height, "yuv420p")
            frame.pts = i
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    finally:
        container.close()


def create_consolidated_v3_dataset(
    root: Path,
    n_episodes: int = 4,
    n_frames_per_ep: int = 8,
    fps: int = 10,
    video_key: str = "observation.images.front",
    action_dims: int = 2,
    state_dims: int = 4,
) -> Path:
    """LeRobot v3 layout: one data parquet + one MP4 shard shared by all episodes."""
    root.mkdir(parents=True, exist_ok=True)
    meta_dir = root / "meta"
    meta_dir.mkdir(exist_ok=True)
    data_dir = root / "data" / "chunk-000"
    data_dir.mkdir(parents=True, exist_ok=True)
    episodes_meta_dir = meta_dir / "episodes" / "chunk-000"
    episodes_meta_dir.mkdir(parents=True, exist_ok=True)

    width, height = 64, 48
    features = {
        "timestamp": {"dtype": "float32", "shape": [1], "names": None},
        "frame_index": {"dtype": "int64", "shape": [1], "names": None},
        "episode_index": {"dtype": "int64", "shape": [1], "names": None},
        "index": {"dtype": "int64", "shape": [1], "names": None},
        "task_index": {"dtype": "int64", "shape": [1], "names": None},
        "action": {"dtype": "float32", "shape": [action_dims], "names": None},
        "observation.state": {"dtype": "float32", "shape": [state_dims], "names": None},
        video_key: {"dtype": "video", "shape": [3, height, width], "names": None},
    }

    total_frames = n_episodes * n_frames_per_ep
    interval = 1.0 / fps

    info = {
        "codebase_version": "v3.0",
        "robot_type": "test_robot",
        "total_episodes": n_episodes,
        "total_frames": total_frames,
        "total_tasks": 1,
        "chunks_size": 1000,
        "fps": fps,
        "splits": {"train": f"0:{n_episodes}"},
        "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
        "video_path": "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4",
        "features": features,
    }
    (meta_dir / "info.json").write_text(json.dumps(info, indent=2))

    tasks_table = pa.table({"task_index": [0], "task": ["pick and place"]})
    pq.write_table(tasks_table, meta_dir / "tasks.parquet")

    all_timestamps: list[float] = []
    all_frame_indices: list[int] = []
    all_episode_indices: list[int] = []
    all_indices: list[int] = []
    all_task_indices: list[int] = []
    all_actions: list[list[float]] = []
    all_states: list[list[float]] = []

    ep_meta = {
        "episode_index": [],
        "length": [],
        "data/chunk_index": [],
        "data/file_index": [],
        "dataset_from_index": [],
        "dataset_to_index": [],
        f"videos/{video_key}/chunk_index": [],
        f"videos/{video_key}/file_index": [],
        f"videos/{video_key}/from_timestamp": [],
        f"videos/{video_key}/to_timestamp": [],
    }

    global_idx = 0
    video_ts = 0.0
    for ep in range(n_episodes):
        for i in range(n_frames_per_ep):
            all_timestamps.append(global_idx * interval)
            all_frame_indices.append(i)
            all_episode_indices.append(ep)
            all_indices.append(global_idx)
            all_task_indices.append(0)
            action = np.random.randn(action_dims).astype(np.float32).tolist()
            state = np.random.randn(state_dims).astype(np.float32).tolist()
            all_actions.append(action)
            all_states.append(state)
            global_idx += 1

        ep_meta["episode_index"].append(ep)
        ep_meta["length"].append(n_frames_per_ep)
        ep_meta["data/chunk_index"].append(0)
        ep_meta["data/file_index"].append(0)
        ep_meta["dataset_from_index"].append(ep * n_frames_per_ep)
        ep_meta["dataset_to_index"].append((ep + 1) * n_frames_per_ep)
        ep_meta[f"videos/{video_key}/chunk_index"].append(0)
        ep_meta[f"videos/{video_key}/file_index"].append(0)
        ep_meta[f"videos/{video_key}/from_timestamp"].append(video_ts)
        video_ts += n_frames_per_ep * interval
        ep_meta[f"videos/{video_key}/to_timestamp"].append(video_ts)

    data_table = pa.table({
        "timestamp": pa.array(all_timestamps, type=pa.float32()),
        "frame_index": pa.array(all_frame_indices, type=pa.int64()),
        "episode_index": pa.array(all_episode_indices, type=pa.int64()),
        "index": pa.array(all_indices, type=pa.int64()),
        "task_index": pa.array(all_task_indices, type=pa.int64()),
        "action": all_actions,
        "observation.state": all_states,
    })
    pq.write_table(data_table, data_dir / "file-000.parquet")
    pq.write_table(pa.table(ep_meta), episodes_meta_dir / "file-000.parquet")

    video_path = root / "videos" / video_key / "chunk-000" / "file-000.mp4"
    write_minimal_mp4(video_path, n_frames=total_frames, fps=fps, width=width, height=height)

    return root
