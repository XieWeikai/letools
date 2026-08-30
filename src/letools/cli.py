from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from letools.conversion import ConversionConfig, convert
from letools.doctor import environment_report
from letools.planner import PerformanceOverrides, plan_conversion
from letools.validation import compare_datasets, validate_dataset


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(type(value).__name__)


def _print(value: Any) -> None:
    print(json.dumps(asdict(value), indent=2, default=_json_default))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="letools")
    commands = parser.add_subparsers(dest="command", required=True)
    conversion = commands.add_parser("convert", help="Convert a local LeRobot dataset")
    conversion.add_argument("source", type=Path)
    conversion.add_argument("destination", type=Path)
    conversion.add_argument("--to", required=True, choices=["v2.1", "v3.0", "2.1", "3.0"])
    conversion.add_argument("--workers", type=int, default=ConversionConfig().workers)
    conversion.add_argument("--video-workers", type=int, default=ConversionConfig().video_workers)
    conversion.add_argument("--data-file-size-mb", type=int, default=100)
    conversion.add_argument("--video-file-size-mb", type=int, default=200)
    conversion.add_argument("--overwrite", action="store_true")
    conversion.add_argument("--no-validate", action="store_true")
    planning = commands.add_parser("plan", help="Plan a local LeRobot conversion")
    planning.add_argument("source", type=Path)
    planning.add_argument("destination", type=Path)
    planning.add_argument("--to", required=True, choices=["v2.1", "v3.0", "2.1", "3.0"])
    planning.add_argument("--workers", type=int)
    planning.add_argument("--video-workers", type=int)
    planning.add_argument("--data-file-size-mb", type=int)
    planning.add_argument("--video-file-size-mb", type=int)
    validation = commands.add_parser("validate", help="Validate a LeRobot dataset")
    validation.add_argument("dataset", type=Path)
    validation.add_argument("--deep", action="store_true")
    comparison = commands.add_parser("compare", help="Compare two datasets semantically")
    comparison.add_argument("left", type=Path)
    comparison.add_argument("right", type=Path)
    comparison.add_argument("--skip-data", action="store_true")
    comparison.add_argument("--videos", action="store_true")
    commands.add_parser("doctor", help="Report native and FFmpeg providers")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "convert":
        result = convert(
            args.source,
            args.destination,
            args.to,
            config=ConversionConfig(
                workers=max(1, args.workers),
                video_workers=max(1, args.video_workers),
                data_file_size_mb=args.data_file_size_mb,
                video_file_size_mb=args.video_file_size_mb,
                overwrite=args.overwrite,
                validate=not args.no_validate,
            ),
        )
        _print(result)
        return 0
    if args.command == "plan":
        plan = plan_conversion(
            args.source,
            args.destination,
            args.to,
            overrides=PerformanceOverrides(
                workers=args.workers,
                video_workers=args.video_workers,
                data_file_size_mb=args.data_file_size_mb,
                video_file_size_mb=args.video_file_size_mb,
            ),
        )
        _print(plan)
        return 0
    if args.command == "validate":
        report = validate_dataset(args.dataset, deep=args.deep)
        _print(report)
        return 0 if report.valid else 1
    if args.command == "doctor":
        print(json.dumps(environment_report(), indent=2))
        return 0
    report = compare_datasets(
        args.left,
        args.right,
        check_data=not args.skip_data,
        check_videos=args.videos,
    )
    _print(report)
    return 0 if report.equal else 1


if __name__ == "__main__":
    raise SystemExit(main())
