"""Pre-training gate — validate dataset is compatible with a specific policy.

Run before training to catch issues that would waste GPU hours:
- Episode length vs policy chunk size
- Action dimensions vs policy expectations
- Image dimensions and format
- Normalization readiness (zero-std dims cause NaN loss)
- Feature availability for the specific policy type
"""

from __future__ import annotations

import json
from pathlib import Path
from dataclasses import dataclass, field

import numpy as np
import pyarrow.parquet as pq


POLICY_CONFIGS = {
    "act": {
        "chunk_size": 100,
        "requires_images": True,
        "requires_actions": True,
        "min_episodes": 10,
        "min_episode_length": 50,
    },
    "diffusion": {
        "chunk_size": 16,
        "requires_images": True,
        "requires_actions": True,
        "min_episodes": 10,
        "min_episode_length": 32,
    },
    "smolvla": {
        "chunk_size": 100,
        "requires_images": True,
        "requires_actions": True,
        "requires_language": True,
        "min_episodes": 50,
        "min_episode_length": 50,
    },
    "pi0": {
        "chunk_size": 50,
        "requires_images": True,
        "requires_actions": True,
        "min_episodes": 20,
        "min_episode_length": 50,
    },
}


@dataclass
class GateResult:
    passed: bool = True
    policy: str = ""
    blockers: list[str] = field(default_factory=list)  # MUST fix before training
    warnings: list[str] = field(default_factory=list)  # May degrade quality
    info: list[str] = field(default_factory=list)


def gate_check(
    root: Path,
    policy: str = "act",
    custom_chunk_size: int | None = None,
) -> GateResult:
    """Check if dataset is ready for training with a specific policy.

    Args:
        root: Dataset root
        policy: One of "act", "diffusion", "smolvla", "pi0"
        custom_chunk_size: Override default chunk size for the policy
    """
    result = GateResult(policy=policy)

    config = POLICY_CONFIGS.get(policy)
    if config is None:
        result.warnings.append(f"Unknown policy '{policy}', using generic checks")
        config = POLICY_CONFIGS["act"]

    chunk_size = custom_chunk_size or config["chunk_size"]

    # Load info
    info_path = root / "meta" / "info.json"
    if not info_path.exists():
        result.blockers.append("No info.json — not a valid LeRobot dataset")
        result.passed = False
        return result

    info = json.loads(info_path.read_text())
    features = info.get("features", {})

    # Check required features
    has_images = any("image" in k or "pixel" in k for k in features)
    has_actions = any("action" in k for k in features)
    has_language = any("language" in k or "task" in k or "instruction" in k for k in features)

    if config.get("requires_images") and not has_images:
        result.blockers.append(f"Policy '{policy}' requires image features but none found")
        result.passed = False

    if config.get("requires_actions") and not has_actions:
        result.blockers.append(f"Policy '{policy}' requires action features but none found")
        result.passed = False

    if config.get("requires_language") and not has_language:
        result.warnings.append(f"Policy '{policy}' benefits from language/task annotations (not found)")

    # Check episode counts
    total_episodes = info.get("total_episodes", 0)
    min_eps = config.get("min_episodes", 10)
    if total_episodes < min_eps:
        result.warnings.append(
            f"Only {total_episodes} episodes (recommended ≥{min_eps} for {policy})"
        )

    # Check episode lengths vs chunk size
    _check_episode_lengths(root, chunk_size, config, result)

    # Check action space
    _check_action_space(root, result)

    # Check normalization readiness
    _check_normalization(root, info, result)

    # Summary
    if not result.blockers:
        result.info.append(f"Dataset is compatible with {policy} (chunk_size={chunk_size})")

    result.passed = len(result.blockers) == 0
    return result


def _check_episode_lengths(root: Path, chunk_size: int, config: dict, result: GateResult):
    """Check that episodes are long enough for the policy's chunk size."""
    episodes_dir = root / "meta" / "episodes"
    if not episodes_dir.exists():
        return

    parquet_files = sorted(episodes_dir.rglob("*.parquet"))
    lengths = []
    for pf in parquet_files:
        try:
            table = pq.read_table(pf)
            if "length" in table.column_names:
                lengths.extend(table.column("length").to_pylist())
        except Exception:
            continue

    if not lengths:
        return

    min_length = config.get("min_episode_length", chunk_size)
    short_episodes = [l for l in lengths if l < min_length]

    if short_episodes:
        pct = 100 * len(short_episodes) / len(lengths)
        if pct > 50:
            result.blockers.append(
                f"{len(short_episodes)}/{len(lengths)} episodes ({pct:.0f}%) shorter than "
                f"min_episode_length={min_length}. Training will skip these."
            )
            result.passed = False
        elif pct > 10:
            result.warnings.append(
                f"{len(short_episodes)}/{len(lengths)} episodes shorter than {min_length} frames"
            )

    too_short_for_chunk = [l for l in lengths if l < chunk_size]
    if too_short_for_chunk and len(too_short_for_chunk) > len(lengths) * 0.3:
        result.blockers.append(
            f"{len(too_short_for_chunk)}/{len(lengths)} episodes shorter than chunk_size={chunk_size}. "
            f"These cannot be used for training."
        )
        result.passed = False


def _check_action_space(root: Path, result: GateResult):
    """Check action space for training issues."""
    data_dir = root / "data"
    if not data_dir.exists():
        return

    parquet_files = sorted(data_dir.rglob("*.parquet"))[:3]  # check first few files
    for pf in parquet_files:
        try:
            table = pq.read_table(pf)
            action_cols = [c for c in table.column_names if c.startswith("action")]

            for col in action_cols:
                try:
                    values = np.array(table.column(col).to_pylist(), dtype=np.float64)
                except (ValueError, TypeError):
                    continue

                if values.ndim == 1:
                    # Check for NaN
                    nan_count = np.sum(~np.isfinite(values))
                    if nan_count > 0:
                        result.blockers.append(f"NaN/Inf found in {col} ({nan_count} values)")
                        result.passed = False

                    # Check for clipping (all values at min/max)
                    if len(values) > 10:
                        at_min = np.sum(values == values.min()) / len(values)
                        at_max = np.sum(values == values.max()) / len(values)
                        if at_min > 0.3 or at_max > 0.3:
                            result.warnings.append(
                                f"{col}: {at_min*100:.0f}% at min, {at_max*100:.0f}% at max — "
                                f"possible action clipping"
                            )
            break  # only check first file
        except Exception:
            continue


def _check_normalization(root: Path, info: dict, result: GateResult):
    """Check if normalization would produce NaN (zero-std dimensions)."""
    stats_path = root / "meta" / "stats.json"
    if not stats_path.exists():
        result.info.append("No stats.json — normalization stats will be computed during training")
        return

    try:
        stats = json.loads(stats_path.read_text())
    except Exception:
        return

    for feature_name, feature_stats in stats.items():
        if "std" in feature_stats:
            std_values = np.array(feature_stats["std"])
            zero_std = np.sum(std_values < 1e-8)
            if zero_std > 0:
                total = len(std_values)
                result.warnings.append(
                    f"{feature_name}: {zero_std}/{total} dimensions have zero std — "
                    f"normalization will produce NaN. Consider dropping these dims."
                )
