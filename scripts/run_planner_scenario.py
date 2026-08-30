#!/usr/bin/env python3
"""Wrap the planner oracle with optional node-local source/destination copies."""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path

from planner_oracle import build_parser, run


def main() -> int:
    """Prepare a storage topology outside timing, then execute the shared oracle."""

    parser = build_parser()
    parser.description = "Run a planner oracle with optional node-local source/destination"
    parser.add_argument("--local-source", action="store_true")
    parser.add_argument("--local-destination", action="store_true")
    args = parser.parse_args()
    original_source = args.source
    original_destination = args.destination_parent
    setup_started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="letools-planner-scenario-", dir="/tmp") as value:
        temporary = Path(value)
        if args.local_source:
            local_source = temporary / "source"
            shutil.copytree(args.source, local_source, copy_function=shutil.copyfile)
            args.source = local_source
        if args.local_destination:
            args.destination_parent = temporary / "destination"
            args.destination_parent.mkdir()
        setup_seconds = time.perf_counter() - setup_started
        report = run(args)
        report["scenario"] = {
            "source_storage": "local" if args.local_source else "original",
            "destination_storage": "local" if args.local_destination else "original",
            "original_source": str(original_source),
            "original_destination": str(original_destination),
            "setup_seconds_excluded": setup_seconds,
        }
        encoded = json.dumps(report, indent=2, default=str)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(encoded + "\n", encoding="utf-8")
        if args.quiet:
            print(
                json.dumps(
                    {
                        "scenario": report["scenario"],
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
