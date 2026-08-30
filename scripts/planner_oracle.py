#!/usr/bin/env python3
"""Exhaustively benchmark a bounded planner candidate lattice offline.

This is acceptance tooling, not production planning. It randomizes equivalent
candidate order, retains raw samples, and compares selected-stage medians with
the measured oracle while excluding fixture generation.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import tempfile
from dataclasses import asdict
from pathlib import Path
from statistics import median
from typing import Callable

from letools.planner import CalibrationOptions, plan_conversion
from letools.planner.calibrate import (
    _run_jobs,
    _v21_data_jobs,
    _v21_video_jobs,
    _v30_data_jobs,
    _v30_video_jobs,
)
from letools.planner.heuristic import DATA_TARGETS_MB, VIDEO_TARGETS_MB, worker_candidates
from letools.plugins import DatasetSource, open_dataset


Candidate = tuple[int, int | None]


def _csv_ints(value: str) -> tuple[int, ...]:
    values = tuple(sorted({int(item) for item in value.split(",") if item}))
    if not values or any(item < 1 for item in values):
        raise argparse.ArgumentTypeError("expected comma-separated positive integers")
    return values


def _benchmark_stage(
    stage: str,
    candidates: list[Candidate],
    jobs_for_size: Callable[[int | None], list],
    root: Path,
    repeats: int,
) -> tuple[list[dict], dict[Candidate, float]]:
    jobs_cache = {size: jobs_for_size(size) for _, size in candidates}
    feasible = [
        candidate for candidate in candidates if candidate[0] <= len(jobs_cache[candidate[1]])
    ]
    equivalence: dict[tuple[int, tuple[int, ...]], list[Candidate]] = {}
    for candidate in feasible:
        workers, size = candidate
        signature = tuple(input_bytes for input_bytes, _ in jobs_cache[size])
        equivalence.setdefault((workers, signature), []).append(candidate)
    samples: dict[tuple[int, tuple[int, ...]], list[float]] = {
        key: [] for key in equivalence
    }
    records: list[dict] = []
    for round_index in range(repeats):
        order = list(equivalence)
        random.Random(round_index).shuffle(order)
        for candidate_index, key in enumerate(order):
            workers, _ = key
            aliases = equivalence[key]
            size = aliases[0][1]
            output = root / f"{stage}-r{round_index}-{candidate_index}"
            try:
                elapsed = _run_jobs(jobs_cache[size], workers, output)
            finally:
                shutil.rmtree(output, ignore_errors=True)
            samples[key].append(elapsed)
            records.append(
                {
                    "stage": stage,
                    "round": round_index,
                    "workers": workers,
                    "target_size_mb": size,
                    "equivalent_target_sizes_mb": [candidate[1] for candidate in aliases],
                    "tasks": len(jobs_cache[size]),
                    "elapsed_seconds": elapsed,
                }
            )
    medians = {
        candidate: median(samples[key])
        for key, aliases in equivalence.items()
        for candidate in aliases
    }
    return records, medians


def _best(medians: dict[Candidate, float]) -> tuple[Candidate, float]:
    return min(medians.items(), key=lambda item: (item[1], item[0][0])) if medians else ((1, None), 0.0)


def _selected_time(
    medians: dict[Candidate, float],
    candidate: Candidate,
) -> float:
    if candidate in medians:
        return medians[candidate]
    raise RuntimeError(f"Planner candidate {candidate} was not benchmarked")


def run(args: argparse.Namespace) -> dict:
    """Execute the planner and oracle matrix, returning a serializable report."""

    source: DatasetSource = open_dataset(args.source)
    plan = plan_conversion(
        source,
        args.destination_parent / "planned-output",
        args.to,
        calibration=CalibrationOptions(
            enabled=args.calibrate,
            max_seconds=args.calibration_seconds,
            max_read_bytes=args.calibration_mb * 1024**2,
            max_write_bytes=args.calibration_mb * 1024**2,
        ),
        use_cache=not args.no_cache,
    )
    worker_values = tuple(
        value
        for value in (args.workers or worker_candidates(plan.resources.effective_cpus, 10**9))
        if value <= plan.resources.effective_cpus
    )
    data_sizes: tuple[int | None, ...] = (
        tuple(sorted({*args.data_sizes, plan.data_file_size_mb}))
        if plan.target_version == "v3.0"
        else (None,)
    )
    video_sizes: tuple[int | None, ...] = (
        tuple(sorted({*args.video_sizes, plan.video_file_size_mb}))
        if plan.target_version == "v3.0"
        else (None,)
    )
    data_workers = tuple(sorted({*worker_values, plan.workers}))
    video_workers = tuple(sorted({*worker_values, plan.video_workers}))
    data_candidates = [(workers, size) for size in data_sizes for workers in data_workers]
    video_candidates = [(workers, size) for size in video_sizes for workers in video_workers]

    root = Path(tempfile.mkdtemp(prefix=".letools-oracle-", dir=plan.destination_storage.existing_path))
    try:
        if plan.target_version == "v3.0":
            data_factory = lambda size: _v30_data_jobs(source, size or 100)
            video_factory = lambda size: _v30_video_jobs(source, size or 200)
        else:
            data_factory = lambda _size: _v21_data_jobs(source)
            video_factory = lambda _size: _v21_video_jobs(source)
        data_records, data_medians = _benchmark_stage(
            "data", data_candidates, data_factory, root, args.repeats
        )
        if source.metadata.video_keys:
            video_records, video_medians = _benchmark_stage(
                "video", video_candidates, video_factory, root, args.repeats
            )
        else:
            video_records, video_medians = [], {}
    finally:
        shutil.rmtree(root, ignore_errors=True)

    data_best, data_best_time = _best(data_medians)
    video_best, video_best_time = _best(video_medians)
    selected_data = (plan.workers, plan.data_file_size_mb)
    selected_video = (plan.video_workers, plan.video_file_size_mb)
    planner_data_time = _selected_time(data_medians, selected_data)
    planner_video_time = (
        _selected_time(video_medians, selected_video) if video_medians else 0.0
    )
    oracle_total = data_best_time + video_best_time
    planner_total = planner_data_time + planner_video_time
    report = {
        "schema_version": 3,
        "source": str(source.root),
        "destination_parent": str(args.destination_parent),
        "target_version": plan.target_version,
        "repeats": args.repeats,
        "plan": asdict(plan),
        "oracle": {
            "data": {
                "workers": data_best[0],
                "target_size_mb": data_best[1],
                "median_seconds": data_best_time,
            },
            "video": {
                "workers": video_best[0],
                "target_size_mb": video_best[1],
                "median_seconds": video_best_time,
                "dataset_stage_weight": 1,
            },
            "combined_stage_seconds": oracle_total,
        },
        "planner": {
            "data_median_seconds": planner_data_time,
            "video_median_seconds": planner_video_time,
            "combined_stage_seconds": planner_total,
            "execution_regret": planner_total / max(oracle_total, 1e-9),
            "cold_e2e_stage_regret": (
                plan.planning_seconds + planner_total
            ) / max(oracle_total, 1e-9),
        },
        "records": [*data_records, *video_records],
    }
    return report


def build_parser() -> argparse.ArgumentParser:
    """Build arguments shared by direct and storage-scenario oracle runners."""

    parser = argparse.ArgumentParser(description="Offline static planner oracle")
    parser.add_argument("source", type=Path)
    parser.add_argument("destination_parent", type=Path)
    parser.add_argument("--to", required=True, choices=["v2.1", "v3.0"])
    parser.add_argument("--workers", type=_csv_ints)
    parser.add_argument(
        "--data-sizes",
        type=_csv_ints,
        default=DATA_TARGETS_MB,
    )
    parser.add_argument(
        "--video-sizes",
        type=_csv_ints,
        default=VIDEO_TARGETS_MB,
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--calibrate", action="store_true")
    parser.add_argument("--calibration-seconds", type=float, default=10.0)
    parser.add_argument("--calibration-mb", type=int, default=1024)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--quiet", action="store_true")
    return parser


def main() -> int:
    """Run one configured oracle and publish its JSON evidence."""

    args = build_parser().parse_args()
    if args.repeats < 1:
        raise ValueError("--repeats must be positive")
    report = run(args)
    encoded = json.dumps(report, indent=2, default=str)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    if args.quiet:
        print(
            json.dumps(
                {
                    "target_version": report["target_version"],
                    "plan": report["plan"]["confidence"],
                    "oracle": report["oracle"],
                    "planner": report["planner"],
                },
                indent=2,
            )
        )
    else:
        print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
