"""Merge validation — check compatibility before merging datasets, fix after.

Common merge issues:
- Episode index collisions
- Schema mismatches (different features, dtypes)
- FPS mismatch between datasets
- Timestamp discontinuities after concatenation
- Feature naming conflicts (action vs action.0, action.1)
"""

from __future__ import annotations

import json
from pathlib import Path
from dataclasses import dataclass, field

import numpy as np
import pyarrow.parquet as pq


@dataclass
class MergeCheckResult:
    compatible: bool = True
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)


def check_merge_compatibility(paths: list[Path]) -> MergeCheckResult:
    """Check if multiple datasets can be safely merged.

    Run BEFORE merging to catch incompatibilities.
    """
    result = MergeCheckResult()

    if len(paths) < 2:
        result.issues.append("Need at least 2 datasets to check merge compatibility")
        result.compatible = False
        return result

    infos = []
    for p in paths:
        info_path = p / "meta" / "info.json"
        if not info_path.exists():
            result.issues.append(f"Missing info.json in {p}")
            result.compatible = False
            continue
        infos.append(json.loads(info_path.read_text()))

    if len(infos) < 2:
        return result

    # Check FPS compatibility
    fps_values = [info.get("fps") for info in infos]
    if len(set(fps_values)) > 1:
        result.issues.append(f"FPS mismatch: {fps_values}. Datasets must have same FPS to merge.")
        result.compatible = False

    # Check feature compatibility
    features_list = [set(info.get("features", {}).keys()) for info in infos]
    common_features = set.intersection(*features_list)
    all_features = set.union(*features_list)

    missing_features = all_features - common_features
    if missing_features:
        result.warnings.append(f"Features not in all datasets: {sorted(missing_features)}")
        result.suggestions.append("Missing features will be filled with zeros/defaults after merge")

    # Check feature shapes/dtypes compatibility
    for feat_name in common_features:
        shapes = []
        dtypes = []
        for info in infos:
            feat = info.get("features", {}).get(feat_name, {})
            shapes.append(feat.get("shape"))
            dtypes.append(feat.get("dtype"))

        if len(set(str(s) for s in shapes)) > 1:
            result.issues.append(f"Shape mismatch for '{feat_name}': {shapes}")
            result.compatible = False

        if len(set(str(d) for d in dtypes)) > 1:
            result.warnings.append(f"Dtype mismatch for '{feat_name}': {dtypes}")

    # Check robot type
    robot_types = [info.get("robot_type") for info in infos if info.get("robot_type")]
    if len(set(robot_types)) > 1:
        result.warnings.append(f"Different robot types: {robot_types}")

    # Check codebase versions
    versions = [info.get("codebase_version") for info in infos]
    if len(set(versions)) > 1:
        result.warnings.append(f"Different codebase versions: {versions}")

    return result


def validate_merged_dataset(root: Path) -> MergeCheckResult:
    """Validate a dataset after merging. Catches common post-merge issues."""
    result = MergeCheckResult()

    info_path = root / "meta" / "info.json"
    if not info_path.exists():
        result.issues.append("No info.json")
        result.compatible = False
        return result

    info = json.loads(info_path.read_text())
    data_dir = root / "data"

    if not data_dir.exists():
        result.issues.append("No data directory")
        result.compatible = False
        return result

    # Check episode index contiguity
    parquet_files = sorted(data_dir.rglob("*.parquet"))
    all_episodes = set()
    all_frame_counts = {}

    for pf in parquet_files:
        try:
            table = pq.read_table(pf, columns=["episode_index"])
            eps = table.column("episode_index").to_pylist()
            all_episodes.update(eps)
            for ep in eps:
                all_frame_counts[ep] = all_frame_counts.get(ep, 0) + 1
        except Exception as e:
            result.issues.append(f"Cannot read {pf.name}: {e}")

    # Check contiguity
    if all_episodes:
        sorted_eps = sorted(all_episodes)
        expected = list(range(min(sorted_eps), max(sorted_eps) + 1))
        gaps = set(expected) - all_episodes
        if gaps:
            result.issues.append(f"Episode index gaps: {sorted(list(gaps))[:10]}...")
            result.suggestions.append("Run 'lerobot-doctor fix --fixes reindex' to fix gaps")
            result.compatible = False

    # Check metadata matches data
    claimed_episodes = info.get("total_episodes", 0)
    claimed_frames = info.get("total_frames", 0)
    actual_episodes = len(all_episodes)
    actual_frames = sum(all_frame_counts.values())

    if claimed_episodes != actual_episodes:
        result.issues.append(
            f"Metadata claims {claimed_episodes} episodes but data has {actual_episodes}"
        )
        result.suggestions.append("Run 'lerobot-doctor fix --fixes metadata' to correct")
        result.compatible = False

    if claimed_frames != actual_frames:
        result.issues.append(
            f"Metadata claims {claimed_frames} frames but data has {actual_frames}"
        )
        result.compatible = False

    # Check episodes metadata matches
    episodes_dir = root / "meta" / "episodes"
    if episodes_dir.exists():
        ep_parquets = sorted(episodes_dir.rglob("*.parquet"))
        meta_lengths = {}
        for epf in ep_parquets:
            try:
                table = pq.read_table(epf)
                for i in range(len(table)):
                    ep_idx = table.column("episode_index")[i].as_py()
                    length = table.column("length")[i].as_py()
                    meta_lengths[ep_idx] = length
            except Exception:
                continue

        for ep_idx, actual_len in all_frame_counts.items():
            meta_len = meta_lengths.get(ep_idx)
            if meta_len is not None and meta_len != actual_len:
                result.issues.append(
                    f"Episode {ep_idx}: metadata says {meta_len} frames, data has {actual_len}"
                )
                result.compatible = False

    if result.compatible and not result.issues:
        result.suggestions.append("Merged dataset looks healthy")

    return result
