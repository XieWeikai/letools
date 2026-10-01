"""Alternating editor/official benchmarks; execute through Slurm.

Each child writes a fresh dataset; environment setup and independent acceptance
checks are outside the timed interval. Checkout overrides also select that
checkout's native extension, so native A/B comparisons cannot share a rebuilt
candidate accidentally. OS cache is warm/uncontrolled, never labeled cold.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import time

def process_tree(pid):
    """Include children spawned by any Python or Rust worker thread."""
    pending, seen = [pid], set()
    rss = threads = 0
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        try:
            for line in Path(f"/proc/{current}/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    rss += int(line.split()[1]) * 1024
                elif line.startswith("Threads:"):
                    threads += int(line.split()[1])
            for task in Path(f"/proc/{current}/task").iterdir():
                pending.extend(map(int, (task / "children").read_text().split()))
        except (OSError, ValueError):
            pass
    return rss, threads, len(seen)


def worker(args):
    if args.implementation == "official":
        from lerobot.configs.video import RGBEncoderConfig
        from lerobot.datasets import LeRobotDataset, delete_episodes, reencode_dataset
        if args.case == "reencode":
            # Preserve source for the official in-place API. This copy is part
            # of the fresh-output contract, reported separately from API time.
            start = time.perf_counter()
            shutil.copytree(args.source, args.output)
            copy_seconds = time.perf_counter() - start
            dataset = LeRobotDataset("local/editor-bench", root=args.output, video_backend="pyav")
            start = time.perf_counter()
            reencode_dataset(dataset, rgb_encoder=RGBEncoderConfig(
                vcodec="h264", pix_fmt="yuv420p", g=2, crf=23, preset="veryfast"),
                encoder_threads=1, num_workers=args.workers)
            stages = {"copy": copy_seconds, "official_api": time.perf_counter() - start}
        elif args.case == "delete":
            dataset = LeRobotDataset("local/editor-bench", root=args.source, video_backend="pyav")
            start = time.perf_counter()
            dataset = delete_episodes(dataset, args.delete, output_dir=args.output,
                                      repo_id="local/editor-bench-out")
            stages = {"official_api": time.perf_counter() - start}
        else:
            raise ValueError("Official lane supports delete/reencode only")
        print(json.dumps({"plan": {"episodes": dataset.meta.total_episodes,
                                   "frames": dataset.meta.total_frames}, "stages": stages}))
        return
    from letools_editor import EditConfig, VideoEdit, edit_dataset
    # workers=0 is the benchmark spelling for the editor's automatic planner.
    # Keep the explicit path available so a baseline can reproduce its old
    # fixed-worker behavior in the same process-tree measurement harness.
    requested_workers = None if args.workers == 0 else args.workers
    config = EditConfig(workers=requested_workers, codec_threads=args.codec_threads,
        delete_episodes=frozenset(args.delete) if args.case == "delete" else frozenset(),
        video=VideoEdit(size=(224, 224) if args.case == "resize" else None)
              if args.case in {"reencode", "resize"} else None)
    print(json.dumps(asdict(edit_dataset(args.source, args.output, config)), default=str))


def measure(command, env, log):
    start_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    start = time.perf_counter()
    peaks = [0, 0, 0]
    weighted = [0.0, 0.0]
    previous = start
    with log.open("w") as stdout, log.with_suffix(".stderr").open("w") as stderr:
        process = subprocess.Popen(command, env=env, stdout=stdout, stderr=stderr)
        while process.poll() is None:
            sample = process_tree(process.pid)
            now = time.perf_counter()
            weighted[0] += sample[1] * (now - previous)
            weighted[1] += sample[2] * (now - previous)
            previous = now
            peaks = [max(a, b) for a, b in zip(peaks, sample, strict=True)]
            time.sleep(0.05)
        if process.returncode:
            raise RuntimeError(f"Run failed ({log}): {log.with_suffix('.stderr').read_text()[-16000:]}")
    wall = time.perf_counter() - start
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    delta = lambda key: getattr(usage, key) - getattr(start_usage, key)
    cpu = delta("ru_utime") + delta("ru_stime")
    result = json.loads(log.read_text().splitlines()[-1])
    return {"wall_seconds": wall, "cpu_seconds": cpu, "mean_cores": cpu / wall,
            "peak_tree_rss_bytes": peaks[0], "peak_threads": peaks[1], "peak_processes": peaks[2],
            "mean_threads": weighted[0] / wall, "mean_processes": weighted[1] / wall,
            "read_bytes": delta("ru_inblock") * 512, "write_bytes": delta("ru_oublock") * 512,
            "minor_faults": delta("ru_minflt"), "major_faults": delta("ru_majflt"),
            "voluntary_switches": delta("ru_nvcsw"), "involuntary_switches": delta("ru_nivcsw"),
            "episodes_per_second": result["plan"]["episodes"] / wall,
            "frames_per_second": result["plan"]["frames"] / wall,
            "result": result}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--work", type=Path)
    p.add_argument("--output", type=Path)
    p.add_argument("--baseline", type=Path)
    p.add_argument("--candidate", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--official-python", type=Path)
    p.add_argument("--implementations", nargs="+", default=["baseline", "candidate"])
    p.add_argument("--cases", nargs="+", default=["delete", "reencode"])
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--workers", type=int, default=4,
                   help="Editor workers; 0 delegates to the automatic planner")
    p.add_argument("--codec-threads", type=int, default=1)
    p.add_argument("--delete", nargs="+", type=int, default=[1, 5, 9])
    p.add_argument("--worker", action="store_true")
    p.add_argument("--implementation")
    p.add_argument("--case")
    args = p.parse_args()
    if args.worker:
        worker(args)
        return
    if not os.environ.get("SLURM_JOB_ID"):
        p.error("Benchmarks must run in a Slurm allocation")
    args.work.mkdir(parents=True, exist_ok=False)
    record = {"slurm_job": os.environ["SLURM_JOB_ID"], "node": os.uname().nodename,
        "cpus": os.environ.get("SLURM_CPUS_PER_TASK"), "memory_mb": os.environ.get("SLURM_MEM_PER_NODE"),
        "affinity": sorted(os.sched_getaffinity(0)), "cache": "warm/uncontrolled OS cache",
        "source": str(args.source), "workers": args.workers,
        "codec_threads": args.codec_threads, "rows": []}
    for repeat in range(args.repeats):
        for case in args.cases:
            # Exact B C B C order; official, when requested, follows candidate.
            for implementation in args.implementations:
                env = dict(os.environ)
                python = sys.executable
                if implementation == "official":
                    if args.official_python is None:
                        p.error("--official-python required for official lane")
                    python = str(args.official_python)
                    env.pop("PYTHONPATH", None)
                else:
                    root = args.baseline if implementation == "baseline" else args.candidate
                    env["PYTHONPATH"] = os.pathsep.join([str(root / "packages/letools-editor/src"),
                                                       str(root / "src")])
                label = f"{implementation}-{case}-{repeat}"
                command = [python, __file__, "--worker", "--implementation", implementation,
                           "--case", case, "--source", str(args.source), "--output", str(args.work / label),
                           "--workers", str(args.workers), "--codec-threads",
                           str(args.codec_threads), "--delete", *map(str, args.delete)]
                row = measure(command, env, args.work / f"{label}.stdout")
                row.update(implementation=implementation, case=case, repeat=repeat, output=str(args.work / label))
                record["rows"].append(row)
                (args.work / "results.json").write_text(json.dumps(record, indent=2))
                print(json.dumps({k:v for k,v in row.items() if k != "result"}), flush=True)


if __name__ == "__main__":
    main()
