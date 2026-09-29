"""Reproducible editor benchmark; run through Slurm on a compute node.

Fixture preparation and semantic validation are outside timed CLI runs. Warm
page-cache and XFS reflink results are explicitly labeled; these are operation
throughputs, not claims about the physical storage bandwidth.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

from letools_editor import EditConfig, edit_dataset

from letools import (
    ConversionConfig,
    compare_datasets,
    convert,
    open_dataset,
    validate_dataset,
)
from letools._video import packet_digests


def verify_edit(source_path: Path, output: Path, case: str) -> None:
    """Untimed semantic checks, including all retained compressed video packets.

    Lossy resize deliberately changes image payloads; its exact output moments
    and decoded-frame equivalence after remux are checked by the acceptance
    tests. Here every retained numeric row and task assignment is compared.
    """

    report = validate_dataset(output, deep=True)
    if not report.valid:
        raise RuntimeError(str(report.errors))
    source, target = open_dataset(source_path), open_dataset(output)
    kept = [
        episode
        for episode in source.episodes
        if case != "delete-feature" or episode.index % 4 != 1
    ]
    frame_start = 0
    for old, new in zip(kept, target.episodes, strict=True):
        expected, actual = source.read_episode(old), target.read_episode(new)
        assert old.length == new.length
        for column in expected.column_names:
            if column in {"episode_index", "index", "task_index"}:
                continue
            if case == "delete-feature" and column == "action":
                assert column not in actual.column_names
            else:
                assert actual[column].equals(expected[column]), (
                    case,
                    old.index,
                    column,
                )
        assert actual["index"].to_pylist() == list(
            range(frame_start, frame_start + new.length)
        )
        assert actual["episode_index"].to_pylist() == [new.index] * new.length
        if case == "task" and old.index == len(source.episodes) - 1:
            assert new.tasks == ("Fold the cloth carefully",)
            assert all(
                target.metadata.tasks[index] == "Fold the cloth carefully"
                for index in actual["task_index"].to_pylist()
            )
        else:
            assert actual["task_index"].equals(expected["task_index"])
            assert new.tasks == old.tasks
        if case != "resize-h264":
            for key in source.metadata.video_keys:
                assert packet_digests([old.videos[key]]) == packet_digests(
                    [new.videos[key]]
                )
        frame_start += new.length
    assert frame_start == target.metadata.total_frames


def process_tree(pid: int) -> tuple[int, int, int]:
    """Sample aggregate RSS/thread/process counts, including FFmpeg children."""

    pending = [pid]
    seen = set()
    rss = threads = 0
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        try:
            status = Path(f"/proc/{current}/status").read_text().splitlines()
            for line in status:
                if line.startswith("VmRSS:"):
                    rss += int(line.split()[1]) * 1024
                if line.startswith("Threads:"):
                    threads += int(line.split()[1])
            # A Rust worker thread can spawn its own FFmpeg child. Linux tracks
            # those children under that thread rather than just the main TID.
            for task in Path(f"/proc/{current}/task").iterdir():
                pending.extend(map(int, (task / "children").read_text().split()))
        except (OSError, ValueError):
            pass
    return rss, threads, len(seen)


def measure(command: list[str], env: dict[str, str], log: Path) -> dict:
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    started = time.perf_counter()
    with log.open("w") as output, log.with_suffix(".stderr").open("w") as errors:
        process = subprocess.Popen(command, stdout=output, stderr=errors, env=env)
        peaks = [0, 0, 0]
        while process.poll() is None:
            peaks = [
                max(a, b) for a, b in zip(peaks, process_tree(process.pid), strict=True)
            ]
            time.sleep(0.05)
        if process.returncode:
            raise RuntimeError(
                f"Benchmark failed: {log.with_suffix('.stderr').read_text()}"
            )
    wall = time.perf_counter() - started
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu = after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime
    return {
        "command": command,
        "wall_seconds": wall,
        "cpu_seconds": cpu,
        "mean_cores": cpu / wall,
        "peak_tree_rss_bytes": peaks[0],
        "peak_threads": peaks[1],
        "peak_processes": peaks[2],
        "read_blocks": after.ru_inblock - before.ru_inblock,
        "write_blocks": after.ru_oublock - before.ru_oublock,
        "result": json.loads(log.read_text()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source", type=Path, help="Real v2.1 source used only for fixture preparation"
    )
    parser.add_argument("work", type=Path)
    parser.add_argument("--episodes", type=int, default=12)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--baseline-src", type=Path)
    parser.add_argument(
        "--workers",
        nargs="+",
        default=["1", "4"],
        help="Concurrency sweep, including 'auto' for the default heuristic",
    )
    parser.add_argument(
        "--cases",
        nargs="+",
        choices=["task", "delete-feature", "resize-h264"],
        default=["task", "delete-feature", "resize-h264"],
    )
    parser.add_argument(
        "--report", default="results.json", help="Report filename under work"
    )
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)
    inputs = {"v2.1": args.work / "input-v21", "v3.0": args.work / "input-v30"}
    original = open_dataset(args.source)
    if not inputs["v2.1"].exists():
        edit_dataset(
            args.source,
            inputs["v2.1"],
            EditConfig(
                delete_episodes=frozenset(range(args.episodes, len(original.episodes))),
                workers=8,
            ),
        )
    if not inputs["v3.0"].exists():
        convert(
            inputs["v2.1"],
            inputs["v3.0"],
            "v3.0",
            config=ConversionConfig(workers=8, video_workers=3, video_file_size_mb=128),
        )
    fixture = open_dataset(inputs["v2.1"])
    manifest = {
        "episodes": len(fixture.episodes),
        "frames": fixture.metadata.total_frames,
        "bytes": sum(
            path.stat().st_size for path in inputs["v2.1"].rglob("*") if path.is_file()
        ),
        "features": fixture.metadata.features,
        "cache": "warm/OS-managed; no drop_caches",
        "filesystem": "local /scratch XFS, reflinks enabled",
        "slurm_job": os.environ.get("SLURM_JOB_ID"),
        "cpus": os.environ.get("SLURM_CPUS_PER_TASK"),
        "memory_mb": os.environ.get("SLURM_MEM_PER_NODE"),
    }
    env = dict(os.environ)
    repo = Path(__file__).resolve().parents[1]
    env["PYTHONPATH"] = str(repo / "src")
    cases = {
        "task": ["--set-task", f"{len(fixture.episodes) - 1}=Fold the cloth carefully"],
        "delete-feature": [
            "--delete-episodes",
            ",".join(str(index) for index in range(1, len(fixture.episodes), 4)),
            "--remove-feature",
            "action",
        ],
        "resize-h264": [
            "--resize",
            "224x224",
            "--video-codec",
            "libx264",
            "--crf",
            "23",
        ],
    }
    measurements = []
    for repeat in range(args.repeats):
        for version in ["v2.1", "v3.0"] if repeat % 2 == 0 else ["v3.0", "v2.1"]:
            for name, flags in cases.items():
                if name not in args.cases:
                    continue
                worker_values = (
                    args.workers if repeat % 2 == 0 else list(reversed(args.workers))
                )
                for workers in worker_values:
                    output = args.work / f"{version}-{name}-{workers}w"
                    command = [
                        sys.executable,
                        "-m",
                        "letools_editor.cli",
                        "apply",
                        str(inputs[version]),
                        str(output),
                        *flags,
                        *([] if workers == "auto" else ["--workers", str(workers)]),
                        "--overwrite",
                    ]
                    record = measure(
                        command,
                        env,
                        args.work / f"{version}-{name}-{workers}w-{repeat}.json",
                    )
                    record.update(
                        version=version, case=name, workers=workers, repeat=repeat
                    )
                    result = record["result"]
                    record["episodes_per_second"] = (
                        result["plan"]["episodes"] / record["wall_seconds"]
                    )
                    record["frames_per_second"] = (
                        result["plan"]["frames"] / record["wall_seconds"]
                    )
                    measurements.append(record)
                    print(
                        json.dumps(
                            {
                                key: value
                                for key, value in record.items()
                                if key not in {"command", "result"}
                            }
                        ),
                        flush=True,
                    )
                    verify_edit(inputs[version], output, name)
                    (args.work / args.report).write_text(
                        json.dumps(
                            {"fixture": manifest, "measurements": measurements},
                            indent=2,
                        )
                    )
    if args.baseline_src:
        for repeat in range(args.repeats):
            for version, source in inputs.items():
                target = "v3.0" if version == "v2.1" else "v2.1"
                for label in (
                    ["main", "branch"] if repeat % 2 == 0 else ["branch", "main"]
                ):
                    local_env = {
                        **env,
                        "PYTHONPATH": str(
                            args.baseline_src if label == "main" else repo / "src"
                        ),
                    }
                    output = args.work / f"regression-{label}-{target}"
                    record = measure(
                        [
                            sys.executable,
                            "-m",
                            "letools.cli",
                            "convert",
                            str(source),
                            str(output),
                            "--to",
                            target,
                            "--workers",
                            "8",
                            "--video-workers",
                            "3",
                            "--overwrite",
                        ],
                        local_env,
                        args.work / f"regression-{label}-{target}-{repeat}.json",
                    )
                    record.update(
                        version=version,
                        case="convert-regression",
                        revision=label,
                        repeat=repeat,
                    )
                    record["episodes_per_second"] = (
                        len(fixture.episodes) / record["wall_seconds"]
                    )
                    record["frames_per_second"] = (
                        fixture.metadata.total_frames / record["wall_seconds"]
                    )
                    measurements.append(record)
                    print(
                        json.dumps(
                            {
                                key: value
                                for key, value in record.items()
                                if key not in {"command", "result"}
                            }
                        ),
                        flush=True,
                    )
                    if not compare_datasets(source, output, check_videos=True).equal:
                        raise RuntimeError("Conversion regression semantic mismatch")
                    (args.work / args.report).write_text(
                        json.dumps(
                            {"fixture": manifest, "measurements": measurements},
                            indent=2,
                        )
                    )


if __name__ == "__main__":
    main()
