"""Episode quality scoring — identify bad episodes before they waste training time.

Scores each episode on multiple quality dimensions and produces a
ranked list with recommendations for which episodes to drop.
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass, field
from pathlib import Path

import pyarrow.parquet as pq


@dataclass
class EpisodeScore:
    episode_index: int
    overall_score: float  # 0-100
    smoothness: float
    coverage: float
    consistency: float
    length_score: float
    is_outlier: bool = False
    reasons: list[str] = field(default_factory=list)


@dataclass
class ScoreResult:
    scores: list[EpisodeScore]
    recommended_drops: list[int]  # episode indices to drop
    mean_score: float = 0.0

    def get_sorted(self) -> list[EpisodeScore]:
        return sorted(self.scores, key=lambda s: s.overall_score)


def score_episodes(
    root: Path,
    max_episodes: int | None = None,
    drop_threshold: float = 30.0,
) -> ScoreResult:
    """Score all episodes on quality. Returns ranked list + drop recommendations.

    Scoring criteria:
    - Smoothness: action jerk (second derivative) — jerky = bad demo
    - Coverage: action space utilization — more coverage = more useful
    - Consistency: similarity to other episodes of same task
    - Length: too short = useless, too long = probably has idle time
    """
    data_dir = root / "data"
    if not data_dir.exists():
        return ScoreResult(scores=[], recommended_drops=[])

    # Load all episode data
    parquet_files = sorted(data_dir.rglob("*.parquet"))
    episodes = _load_episodes(parquet_files, max_episodes)

    if not episodes:
        return ScoreResult(scores=[], recommended_drops=[])

    # Compute scores
    scores = []
    all_actions = []

    for ep_idx, ep_data in episodes.items():
        actions = ep_data.get("actions")
        if actions is None or len(actions) < 3:
            scores.append(EpisodeScore(
                episode_index=ep_idx, overall_score=10.0,
                smoothness=0, coverage=0, consistency=0, length_score=0,
                reasons=["Too short"]
            ))
            continue

        all_actions.append(actions)
        smoothness = _score_smoothness(actions)
        coverage = _score_coverage(actions)
        length_score = _score_length(len(actions), episodes)

        scores.append(EpisodeScore(
            episode_index=ep_idx,
            overall_score=0,  # computed after consistency
            smoothness=smoothness,
            coverage=coverage,
            consistency=0,  # computed below
            length_score=length_score,
        ))

    # Compute consistency (requires all episodes)
    if len(all_actions) > 3:
        _compute_consistency(scores, all_actions, episodes)

    # Compute overall score
    for s in scores:
        s.overall_score = (
            0.3 * s.smoothness +
            0.25 * s.coverage +
            0.25 * s.consistency +
            0.2 * s.length_score
        )
        if s.overall_score < drop_threshold:
            s.is_outlier = True

    # Outlier detection via z-score on overall
    overall_scores = np.array([s.overall_score for s in scores])
    if len(overall_scores) > 5:
        mean, std = overall_scores.mean(), overall_scores.std()
        if std > 0:
            for s in scores:
                z = (s.overall_score - mean) / std
                if z < -2.0:
                    s.is_outlier = True
                    s.reasons.append(f"Statistical outlier (z={z:.1f})")

    recommended_drops = [s.episode_index for s in scores if s.is_outlier]

    result = ScoreResult(
        scores=scores,
        recommended_drops=recommended_drops,
        mean_score=float(overall_scores.mean()) if len(overall_scores) > 0 else 0,
    )
    return result


def _load_episodes(parquet_files, max_episodes):
    """Load episode action data."""
    episodes = {}
    for pf in parquet_files:
        try:
            table = pq.read_table(pf)
            action_cols = [c for c in table.column_names if c.startswith("action")]
            if not action_cols:
                continue

            ep_col = table.column("episode_index").to_pylist()
            for i, ep_idx in enumerate(ep_col):
                if max_episodes and ep_idx >= max_episodes:
                    continue
                if ep_idx not in episodes:
                    episodes[ep_idx] = {"rows": []}
                episodes[ep_idx]["rows"].append(i)

            # Extract actions
            for ep_idx, ep_data in episodes.items():
                if "actions" not in ep_data:
                    rows = ep_data["rows"]
                    actions = []
                    for col in action_cols:
                        try:
                            vals = [table.column(col)[r].as_py() for r in rows]
                            actions.append(np.array(vals, dtype=np.float64))
                        except (ValueError, TypeError):
                            continue
                    if actions:
                        ep_data["actions"] = np.column_stack(actions) if len(actions) > 1 else actions[0].reshape(-1, 1)
        except Exception:
            continue

    return episodes


def _score_smoothness(actions: np.ndarray) -> float:
    """Score based on action smoothness (low jerk = smooth = good)."""
    if len(actions) < 3:
        return 50.0

    # Compute jerk (second derivative)
    velocity = np.diff(actions, axis=0)
    acceleration = np.diff(velocity, axis=0)
    jerk = np.mean(np.abs(acceleration))

    # Normalize: low jerk = high score
    # Empirical: jerk < 0.01 is very smooth, > 0.1 is very jerky
    score = 100.0 * np.exp(-jerk * 30)
    return float(np.clip(score, 0, 100))


def _score_coverage(actions: np.ndarray) -> float:
    """Score based on action space coverage (more variety = more useful)."""
    if len(actions) < 5:
        return 30.0

    # Use standard deviation as coverage proxy
    stds = np.std(actions, axis=0)
    mean_std = np.mean(stds)

    # Penalize zero-variance dimensions
    zero_dims = np.sum(stds < 1e-6) / len(stds)

    # Score: higher std = more coverage
    coverage = min(mean_std * 200, 100) * (1 - zero_dims)
    return float(np.clip(coverage, 0, 100))


def _score_length(length: int, episodes: dict) -> float:
    """Score based on episode length relative to dataset median."""
    lengths = [len(ep.get("actions", [])) for ep in episodes.values() if ep.get("actions") is not None]
    if not lengths:
        return 50.0

    median_len = np.median(lengths)
    if median_len == 0:
        return 50.0

    ratio = length / median_len
    # Penalize very short and very long
    if ratio < 0.3:
        return float(ratio * 100)
    elif ratio > 3.0:
        return float(100 / ratio)
    else:
        return min(100.0, 50 + 50 * min(ratio, 1.0 / max(ratio, 0.01)))


def _compute_consistency(scores, all_actions, episodes):
    """Score each episode on consistency with peers."""
    if len(all_actions) < 3:
        return

    # Compute mean action trajectory (padded to max length)
    max_len = max(len(a) for a in all_actions)
    n_dims = all_actions[0].shape[1] if all_actions[0].ndim > 1 else 1

    # Compare each episode's action distribution to the mean
    global_mean = np.mean([a.mean(axis=0) for a in all_actions], axis=0)
    global_std = np.std([a.mean(axis=0) for a in all_actions], axis=0)
    global_std = np.maximum(global_std, 1e-6)

    for i, (score, actions) in enumerate(zip(scores, all_actions)):
        ep_mean = actions.mean(axis=0)
        z_distance = np.mean(np.abs((ep_mean - global_mean) / global_std))
        # Low z-distance = consistent
        consistency = 100.0 * np.exp(-z_distance)
        score.consistency = float(np.clip(consistency, 0, 100))
        if z_distance > 3.0:
            score.reasons.append(f"Action distribution deviates from peers (z={z_distance:.1f})")
