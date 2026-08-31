"""Thin JSON command-line frontend over the public conversion APIs."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from letools.conversion import ConversionConfig, convert
from letools.doctor import environment_report
from letools.planner import (
    CalibrationOptions,
    PerformanceOverrides,
    plan_and_convert,
    plan_conversion,
)
from letools.plugins import AgileXSource, HDF5Source
from letools.tools.hdf5_preset import list_presets, load_preset
from letools.tools.hdf5_tui import (
    require_interactive_terminal,
    run_hdf5_preset_wizard,
    select_hdf5_preset,
)
from letools.validation import compare_datasets, validate_dataset


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(type(value).__name__)


def _print(value: Any) -> None:
    print(json.dumps(asdict(value), indent=2, default=_json_default))


def _add_source_options(parser: argparse.ArgumentParser) -> None:
    """Add source-plugin selection shared by convert and plan."""

    parser.add_argument(
        "--source-format",
        choices=["auto", "lerobot", "hdf5", "agilex"],
        default="auto",
        help="source plugin; HDF5 requires a mapping preset",
    )
    parser.add_argument(
        "--preset",
        help="HDF5 preset name from the user store or an explicit JSON path",
    )
    parser.add_argument(
        "--instruction",
        help="fixed task instruction required by the AgileX source",
    )
    parser.add_argument(
        "--fps",
        type=int,
        default=30,
        help="AgileX output sampling rate (default: 30)",
    )
    parser.add_argument(
        "--robot-type",
        default="cobot_magic",
        help="AgileX robot_type metadata (default: cobot_magic)",
    )


def _open_cli_source(args: argparse.Namespace) -> Path | HDF5Source | AgileXSource:
    """Resolve CLI source options into a path or an explicit source plugin."""

    if args.source_format == "agilex":
        if args.preset is not None:
            raise ValueError("--preset cannot be combined with --source-format agilex")
        instruction = getattr(args, "instruction", None)
        if instruction is None:
            raise ValueError("--instruction is required with --source-format agilex")
        return AgileXSource(
            args.source,
            instruction,
            fps=getattr(args, "fps", 30),
            robot_type=getattr(args, "robot_type", "cobot_magic"),
        )
    if getattr(args, "instruction", None) is not None:
        raise ValueError("--instruction is only supported with --source-format agilex")
    hdf5_selected = args.source_format == "hdf5" or args.preset is not None
    if not hdf5_selected:
        return args.source
    if args.source_format == "lerobot":
        raise ValueError("--preset cannot be combined with --source-format lerobot")
    if args.preset is not None:
        preset = load_preset(args.preset)
    else:
        require_interactive_terminal()
        preset = select_hdf5_preset()
    return HDF5Source(args.source, preset.mapping)


def build_parser() -> argparse.ArgumentParser:
    """Build the stable CLI surface without embedding execution policy."""

    parser = argparse.ArgumentParser(prog="letools")
    commands = parser.add_subparsers(dest="command", required=True)
    conversion = commands.add_parser("convert", help="Convert a local dataset")
    conversion.add_argument("source", type=Path)
    conversion.add_argument("destination", type=Path)
    _add_source_options(conversion)
    conversion.add_argument("--to", required=True, choices=["v2.1", "v3.0", "2.1", "3.0"])
    conversion.add_argument("--workers", type=int)
    conversion.add_argument("--video-workers", type=int)
    conversion.add_argument("--data-file-size-mb", type=int)
    conversion.add_argument("--video-file-size-mb", type=int)
    conversion.add_argument("--overwrite", action="store_true")
    conversion.add_argument("--no-validate", action="store_true")
    conversion.add_argument("--auto", action="store_true")
    conversion.add_argument("--calibration-seconds", type=float, default=10.0)
    conversion.add_argument("--calibration-mb", type=int, default=1024)
    conversion.add_argument("--no-cache", action="store_true")
    planning = commands.add_parser("plan", help="Plan a local dataset conversion")
    planning.add_argument("source", type=Path)
    planning.add_argument("destination", type=Path)
    _add_source_options(planning)
    planning.add_argument("--to", required=True, choices=["v2.1", "v3.0", "2.1", "3.0"])
    planning.add_argument("--workers", type=int)
    planning.add_argument("--video-workers", type=int)
    planning.add_argument("--data-file-size-mb", type=int)
    planning.add_argument("--video-file-size-mb", type=int)
    planning.add_argument("--calibrate", action="store_true")
    planning.add_argument("--calibration-seconds", type=float, default=10.0)
    planning.add_argument("--calibration-mb", type=int, default=1024)
    planning.add_argument("--no-cache", action="store_true")
    validation = commands.add_parser("validate", help="Validate a LeRobot dataset")
    validation.add_argument("dataset", type=Path)
    validation.add_argument("--deep", action="store_true")
    comparison = commands.add_parser("compare", help="Compare two datasets semantically")
    comparison.add_argument("left", type=Path)
    comparison.add_argument("right", type=Path)
    comparison.add_argument("--skip-data", action="store_true")
    comparison.add_argument("--videos", action="store_true")
    commands.add_parser("doctor", help="Report native and FFmpeg providers")
    utilities = commands.add_parser("tools", help="Run auxiliary dataset utilities")
    utility_commands = utilities.add_subparsers(dest="tool", required=True)
    hdf5_preset = utility_commands.add_parser(
        "hdf5-preset", help="Create and inspect HDF5 mapping presets"
    )
    preset_commands = hdf5_preset.add_subparsers(dest="preset_command", required=True)
    preset_create = preset_commands.add_parser(
        "create", help="Interactively create a preset from a representative episode"
    )
    preset_create.add_argument("source", type=Path)
    preset_create.add_argument("--name")
    preset_create.add_argument("--output", type=Path)
    preset_create.add_argument("--episode-glob", default="*.hdf5")
    preset_create.add_argument("--overwrite", action="store_true")
    preset_commands.add_parser("list", help="List presets in the user store")
    preset_show = preset_commands.add_parser("show", help="Print one preset as JSON")
    preset_show.add_argument("preset")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Dispatch one CLI command and return a process exit status."""

    args = build_parser().parse_args(argv)
    if args.command == "tools":
        if args.preset_command == "create":
            preset, path = run_hdf5_preset_wizard(
                args.source,
                name=args.name,
                output=args.output,
                episode_glob=args.episode_glob,
                overwrite=args.overwrite,
            )
            print(json.dumps({"name": preset.name, "path": str(path)}, indent=2))
            return 0
        if args.preset_command == "show":
            print(json.dumps(load_preset(args.preset).to_dict(), indent=2))
            return 0
        summaries = [
            {
                "name": preset.name,
                "path": str(path),
                "description": preset.description,
                "fps": preset.mapping.fps,
                "numeric_features": len(preset.mapping.numeric_fields),
                "video_features": len(preset.mapping.video_fields),
            }
            for path, preset in list_presets()
        ]
        print(json.dumps(summaries, indent=2))
        return 0
    if args.command == "convert":
        source = _open_cli_source(args)
        if args.auto:
            result = plan_and_convert(
                source,
                args.destination,
                args.to,
                overrides=PerformanceOverrides(
                    workers=args.workers,
                    video_workers=args.video_workers,
                    data_file_size_mb=args.data_file_size_mb,
                    video_file_size_mb=args.video_file_size_mb,
                ),
                calibration=CalibrationOptions(
                    enabled=True,
                    max_seconds=args.calibration_seconds,
                    max_read_bytes=args.calibration_mb * 1024**2,
                    max_write_bytes=args.calibration_mb * 1024**2,
                ),
                use_cache=not args.no_cache,
                overwrite=args.overwrite,
                validate=not args.no_validate,
            )
        else:
            defaults = ConversionConfig()
            result = convert(
                source,
                args.destination,
                args.to,
                config=ConversionConfig(
                    workers=max(1, args.workers or defaults.workers),
                    video_workers=max(1, args.video_workers or defaults.video_workers),
                    data_file_size_mb=args.data_file_size_mb or defaults.data_file_size_mb,
                    video_file_size_mb=args.video_file_size_mb or defaults.video_file_size_mb,
                    overwrite=args.overwrite,
                    validate=not args.no_validate,
                ),
            )
        _print(result)
        return 0
    if args.command == "plan":
        source = _open_cli_source(args)
        plan = plan_conversion(
            source,
            args.destination,
            args.to,
            overrides=PerformanceOverrides(
                workers=args.workers,
                video_workers=args.video_workers,
                data_file_size_mb=args.data_file_size_mb,
                video_file_size_mb=args.video_file_size_mb,
            ),
            calibration=CalibrationOptions(
                enabled=args.calibrate,
                max_seconds=args.calibration_seconds,
                max_read_bytes=args.calibration_mb * 1024**2,
                max_write_bytes=args.calibration_mb * 1024**2,
            ),
            use_cache=not args.no_cache,
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
