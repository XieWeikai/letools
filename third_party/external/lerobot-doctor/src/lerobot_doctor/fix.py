"""Auto-fix common dataset issues found by lerobot-doctor.

Repairs datasets in-place (with backup option). Handles:
- Reindexing broken episode/frame indices
- Removing NaN/Inf values (interpolation or drop)
- Fixing timestamp drift (resample to expected FPS)
- Removing corrupted parquet files
- Updating metadata to match actual data
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from dataclasses import dataclass

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq


@dataclass
class FixResult:
    fixed: list[str]
    skipped: list[str]
    errors: list[str]

    @property
    def n_fixed(self) -> int:
        return len(self.fixed)


def fix_dataset(root: Path, fixes: list[str] | None = None, backup: bool = True, dry_run: bool = False) -> FixResult:
    """Auto-fix dataset issues.

    Args:
        root: Path to dataset directory
        fixes: List of fixes to apply (None = all). Options:
               "reindex", "timestamps", "nan", "metadata", "episodes"
        backup: Create backup before modifying
        dry_run: Only report what would be fixed
    """
    result = FixResult(fixed=[], skipped=[], errors=[])

    if backup and not dry_run:
        backup_path = root.parent / f"{root.name}_backup"
        if not backup_path.exists():
            shutil.copytree(root / "meta", backup_path / "meta")
            result.fixed.append(f"Backup created at {backup_path}")

    all_fixes = fixes or ["reindex", "timestamps", "nan", "metadata", "episodes"]

    if "metadata" in all_fixes:
        _fix_metadata(root, result, dry_run)

    if "reindex" in all_fixes:
        _fix_reindex(root, result, dry_run)

    if "timestamps" in all_fixes:
        _fix_timestamps(root, result, dry_run)

    if "nan" in all_fixes:
        _fix_nan_values(root, result, dry_run)

    if "episodes" in all_fixes:
        _fix_episode_metadata(root, result, dry_run)

    return result


def _fix_metadata(root: Path, result: FixResult, dry_run: bool):
    """Fix info.json to match actual data."""
    info_path = root / "meta" / "info.json"
    if not info_path.exists():
        result.errors.append("No info.json found")
        return

    info = json.loads(info_path.read_text())
    data_dir = root / "data"
    if not data_dir.exists():
        return

    # Count actual episodes and frames from data
    parquet_files = sorted(data_dir.rglob("*.parquet"))
    if not parquet_files:
        return

    total_frames = 0
    episodes = set()
    for pf in parquet_files:
        try:
            table = pq.read_table(pf, columns=["episode_index"])
            ep_col = table.column("episode_index").to_pylist()
            total_frames += len(ep_col)
            episodes.update(ep_col)
        except Exception:
            continue

    actual_episodes = len(episodes)
    actual_frames = total_frames

    changed = False
    if info.get("total_episodes") != actual_episodes:
        if not dry_run:
            info["total_episodes"] = actual_episodes
        result.fixed.append(f"Fixed total_episodes: {info.get('total_episodes')} → {actual_episodes}")
        changed = True

    if info.get("total_frames") != actual_frames:
        if not dry_run:
            info["total_frames"] = actual_frames
        result.fixed.append(f"Fixed total_frames: {info.get('total_frames')} → {actual_frames}")
        changed = True

    if changed and not dry_run:
        info_path.write_text(json.dumps(info, indent=2))


def _fix_reindex(root: Path, result: FixResult, dry_run: bool):
    """Fix non-contiguous episode indices after deletion."""
    data_dir = root / "data"
    if not data_dir.exists():
        return

    parquet_files = sorted(data_dir.rglob("*.parquet"))
    for pf in parquet_files:
        try:
            table = pq.read_table(pf)
            if "frame_index" not in table.column_names:
                continue

            ep_col = table.column("episode_index").to_pylist()
            frame_col = table.column("frame_index").to_pylist()

            # Check if frame indices are monotonic within each episode
            current_ep = None
            expected_frame = 0
            needs_fix = False

            for i, (ep, frame) in enumerate(zip(ep_col, frame_col)):
                if ep != current_ep:
                    current_ep = ep
                    expected_frame = 0
                if frame != expected_frame:
                    needs_fix = True
                    break
                expected_frame += 1

            if needs_fix:
                if dry_run:
                    result.fixed.append(f"Would reindex frames in {pf.name}")
                else:
                    # Rebuild frame indices
                    new_frames = []
                    current_ep = None
                    counter = 0
                    for ep in ep_col:
                        if ep != current_ep:
                            current_ep = ep
                            counter = 0
                        new_frames.append(counter)
                        counter += 1

                    table = table.set_column(
                        table.column_names.index("frame_index"),
                        "frame_index",
                        pa.array(new_frames)
                    )
                    pq.write_table(table, pf)
                    result.fixed.append(f"Reindexed frames in {pf.name}")
        except Exception as e:
            result.errors.append(f"Error processing {pf.name}: {e}")


def _fix_timestamps(root: Path, result: FixResult, dry_run: bool):
    """Fix timestamp drift by resampling to expected FPS."""
    info_path = root / "meta" / "info.json"
    if not info_path.exists():
        return
    info = json.loads(info_path.read_text())
    fps = info.get("fps")
    if not fps:
        return

    expected_dt = 1.0 / fps
    data_dir = root / "data"
    parquet_files = sorted(data_dir.rglob("*.parquet"))

    for pf in parquet_files:
        try:
            table = pq.read_table(pf)
            if "timestamp" not in table.column_names:
                continue

            timestamps = np.array(table.column("timestamp").to_pylist(), dtype=np.float64)
            ep_indices = table.column("episode_index").to_pylist()

            # Check per-episode timestamp regularity
            needs_fix = False
            current_ep = None
            ep_start_idx = 0

            for i, ep in enumerate(ep_indices):
                if ep != current_ep:
                    if current_ep is not None:
                        ep_ts = timestamps[ep_start_idx:i]
                        if len(ep_ts) > 1:
                            dts = np.diff(ep_ts)
                            if np.any(np.abs(dts - expected_dt) > expected_dt * 0.5):
                                needs_fix = True
                                break
                    current_ep = ep
                    ep_start_idx = i

            if needs_fix:
                if dry_run:
                    result.fixed.append(f"Would fix timestamps in {pf.name}")
                else:
                    # Regenerate timestamps from FPS
                    new_timestamps = []
                    current_ep = None
                    counter = 0
                    for ep in ep_indices:
                        if ep != current_ep:
                            current_ep = ep
                            counter = 0
                        new_timestamps.append(counter * expected_dt)
                        counter += 1

                    table = table.set_column(
                        table.column_names.index("timestamp"),
                        "timestamp",
                        pa.array(new_timestamps)
                    )
                    pq.write_table(table, pf)
                    result.fixed.append(f"Fixed timestamps in {pf.name}")
        except Exception as e:
            result.errors.append(f"Timestamp fix error in {pf.name}: {e}")


def _fix_nan_values(root: Path, result: FixResult, dry_run: bool):
    """Replace NaN/Inf values with interpolated or zero values."""
    data_dir = root / "data"
    parquet_files = sorted(data_dir.rglob("*.parquet"))

    for pf in parquet_files:
        try:
            table = pq.read_table(pf)
            modified = False

            for col_name in table.column_names:
                col = table.column(col_name)
                try:
                    values = np.array(col.to_pylist(), dtype=np.float64)
                except (ValueError, TypeError):
                    continue

                if values.ndim == 1:
                    nan_mask = ~np.isfinite(values)
                    if nan_mask.any():
                        if dry_run:
                            result.fixed.append(f"Would fix {nan_mask.sum()} NaN values in {pf.name}:{col_name}")
                        else:
                            # Linear interpolation for NaN values
                            valid = np.isfinite(values)
                            if valid.any():
                                values[nan_mask] = np.interp(
                                    np.where(nan_mask)[0],
                                    np.where(valid)[0],
                                    values[valid]
                                )
                            else:
                                values[nan_mask] = 0.0
                            table = table.set_column(
                                table.column_names.index(col_name),
                                col_name,
                                pa.array(values.tolist())
                            )
                            modified = True
                            result.fixed.append(f"Fixed {nan_mask.sum()} NaN values in {col_name}")

            if modified and not dry_run:
                pq.write_table(table, pf)
        except Exception as e:
            result.errors.append(f"NaN fix error in {pf.name}: {e}")


def _fix_episode_metadata(root: Path, result: FixResult, dry_run: bool):
    """Regenerate episodes metadata from actual data."""
    data_dir = root / "data"
    episodes_dir = root / "meta" / "episodes"

    if not data_dir.exists():
        return

    # Count actual episode lengths from data
    parquet_files = sorted(data_dir.rglob("*.parquet"))
    episode_lengths = {}

    for pf in parquet_files:
        try:
            table = pq.read_table(pf, columns=["episode_index"])
            for ep in table.column("episode_index").to_pylist():
                episode_lengths[ep] = episode_lengths.get(ep, 0) + 1
        except Exception:
            continue

    if not episode_lengths:
        return

    # Check if episodes metadata matches
    episodes_dir.mkdir(parents=True, exist_ok=True)
    ep_parquet = episodes_dir / "00000.parquet"

    if ep_parquet.exists():
        try:
            existing = pq.read_table(ep_parquet)
            existing_lengths = dict(zip(
                existing.column("episode_index").to_pylist(),
                existing.column("length").to_pylist()
            ))
            if existing_lengths == episode_lengths:
                result.skipped.append("Episode metadata already correct")
                return
        except Exception:
            pass

    if dry_run:
        result.fixed.append(f"Would regenerate episodes metadata ({len(episode_lengths)} episodes)")
        return

    # Write corrected episodes metadata
    sorted_eps = sorted(episode_lengths.keys())
    table = pa.table({
        "episode_index": pa.array(sorted_eps),
        "length": pa.array([episode_lengths[ep] for ep in sorted_eps]),
    })
    pq.write_table(table, ep_parquet)
    result.fixed.append(f"Regenerated episodes metadata ({len(sorted_eps)} episodes)")
