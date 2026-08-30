"""Episode-stat flattening and weighted dataset-stat aggregation."""

from __future__ import annotations

from typing import Any

import numpy as np


def aggregate_episode_stats(
    episodes: list[dict[str, dict[str, Any]]],
) -> dict[str, dict[str, list[Any]]]:
    """Combine per-episode moments using counts and between-mean variance."""

    result: dict[str, dict[str, list[Any]]] = {}
    for feature in sorted({key for episode in episodes for key in episode}):
        values = [episode[feature] for episode in episodes if feature in episode]
        means = np.stack([np.asarray(value["mean"]) for value in values])
        variances = np.stack([np.asarray(value["std"]) ** 2 for value in values])
        counts = np.stack([np.asarray(value["count"]) for value in values])
        total_count = counts.sum(axis=0)
        expanded_counts = counts
        while expanded_counts.ndim < means.ndim:
            expanded_counts = np.expand_dims(expanded_counts, axis=-1)
        mean = (means * expanded_counts).sum(axis=0) / total_count
        variance = ((variances + (means - mean) ** 2) * expanded_counts).sum(axis=0) / total_count
        aggregated: dict[str, Any] = {
            "min": np.min(np.stack([np.asarray(value["min"]) for value in values]), axis=0),
            "max": np.max(np.stack([np.asarray(value["max"]) for value in values]), axis=0),
            "mean": mean,
            "std": np.sqrt(variance),
            "count": total_count,
        }
        quantiles = [
            key
            for key in values[0]
            if key.startswith("q") and key[1:].isdigit() and all(key in value for value in values)
        ]
        for key in quantiles:
            stacked = np.stack([np.asarray(value[key]) for value in values])
            aggregated[key] = np.min(stacked, axis=0) if int(key[1:]) <= 50 else np.max(stacked, axis=0)
        result[feature] = {key: np.asarray(value).tolist() for key, value in aggregated.items()}
    return result


def flatten_stats(stats: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Flatten nested feature/stat keys into v3 episode column names."""

    return {
        f"stats/{feature}/{statistic}": value
        for feature, feature_stats in stats.items()
        for statistic, value in feature_stats.items()
    }
