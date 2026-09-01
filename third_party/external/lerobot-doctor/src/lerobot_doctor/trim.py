"""Episode trimming — remove idle/static frames from episodes.

Detects and removes frames where the robot isn't moving (common at
the start/end of teleoperation recordings). These idle frames teach
the policy to "do nothing" which causes stuck behaviors at inference.
"""

from __future__ import annotations

import json
from pathlib import Path
from dataclasses import dataclass

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq


@dataclass
class TrimResult:
    episodes_trimmed: int
    frames_removed: int
    episodes_removed: int
    details: list[str]


def trim_dataset(
    root: Path,
    action_threshold: float = 0.01,
    min_active_frames: int = 10,
    trim_start: bool = True,
    trim_end: bool = True,
    remove_fully_static: bool = True,
    dry_run: bool = False,
) -> TrimResult:
    """Trim idle frames from episodes.

    Args:
        root: Dataset root path
        action_threshold: Frames with action std below this are "idle"
        min_active_frames: Minimum active frames to keep an episode
        trim_start: Remove idle frames at start of episodes
        trim_end: Remove idle frames at end of episodes
        remove_fully_static: Remove episodes that are entirely static
        dry_run: Only report, don't modify
    """
    result = TrimResult(episodes_trimmed=0, frames_removed=0, episodes_removed=0, details=[])

    data_dir = root / "data"
    if not data_dir.exists():
        return result

    parquet_files = sorted(data_dir.rglob("*.parquet"))

    for pf in parquet_files:
        try:
            table = pq.read_table(pf)
            if "episode_index" not in table.column_names:
                continue

            # Find action columns
            action_cols = [c for c in table.column_names if c.startswith("action")]
            if not action_cols:
                continue

            ep_col = table.column("episode_index").to_pylist()
            episodes = {}
            for i, ep in enumerate(ep_col):
                if ep not in episodes:
                    episodes[ep] = []
                episodes[ep].append(i)

            rows_to_keep = []
            for ep_idx in sorted(episodes.keys()):
                row_indices = episodes[ep_idx]

                # Get action values for this episode
                actions = []
                for col in action_cols:
                    try:
                        vals = np.array([table.column(col)[i].as_py() for i in row_indices], dtype=np.float64)
                        if vals.ndim == 1:
                            actions.append(vals)
                    except (ValueError, TypeError):
                        continue

                if not actions:
                    rows_to_keep.extend(row_indices)
                    continue

                action_matrix = np.column_stack(actions) if len(actions) > 1 else actions[0].reshape(-1, 1)

                # Compute per-frame "activity" as action change magnitude
                if len(action_matrix) < 2:
                    rows_to_keep.extend(row_indices)
                    continue

                diffs = np.abs(np.diff(action_matrix, axis=0))
                activity = np.concatenate([[0], diffs.mean(axis=1)])
                is_active = activity > action_threshold

                # Find first and last active frames
                active_indices = np.where(is_active)[0]

                if len(active_indices) < min_active_frames:
                    if remove_fully_static:
                        result.episodes_removed += 1
                        result.frames_removed += len(row_indices)
                        result.details.append(f"Episode {ep_idx}: removed (fully static)")
                        continue
                    else:
                        rows_to_keep.extend(row_indices)
                        continue

                start = active_indices[0] if trim_start else 0
                end = active_indices[-1] + 1 if trim_end else len(row_indices)

                trimmed_indices = row_indices[start:end]
                n_removed = len(row_indices) - len(trimmed_indices)

                if n_removed > 0:
                    result.episodes_trimmed += 1
                    result.frames_removed += n_removed
                    result.details.append(
                        f"Episode {ep_idx}: trimmed {n_removed} idle frames "
                        f"({len(row_indices)} → {len(trimmed_indices)})"
                    )

                rows_to_keep.extend(trimmed_indices)

            # Write trimmed table
            if len(rows_to_keep) < len(ep_col) and not dry_run:
                trimmed_table = table.take(rows_to_keep)
                # Reindex frames within each episode
                trimmed_table = _reindex_frames(trimmed_table)
                pq.write_table(trimmed_table, pf)

        except Exception as e:
            result.details.append(f"Error processing {pf.name}: {e}")

    # Update metadata
    if not dry_run and (result.frames_removed > 0 or result.episodes_removed > 0):
        _update_metadata_after_trim(root)

    return result


def _reindex_frames(table: pa.Table) -> pa.Table:
    """Rebuild frame_index to be 0-based per episode."""
    if "frame_index" not in table.column_names:
        return table

    ep_col = table.column("episode_index").to_pylist()
    new_frames = []
    current_ep = None
    counter = 0

    for ep in ep_col:
        if ep != current_ep:
            current_ep = ep
            counter = 0
        new_frames.append(counter)
        counter += 1

    return table.set_column(
        table.column_names.index("frame_index"),
        "frame_index",
        pa.array(new_frames)
    )


def _update_metadata_after_trim(root: Path):
    """Update info.json and episodes metadata after trimming."""
    from .fix import _fix_metadata, _fix_episode_metadata, FixResult
    result = FixResult(fixed=[], skipped=[], errors=[])
    _fix_metadata(root, result, dry_run=False)
    _fix_episode_metadata(root, result, dry_run=False)
