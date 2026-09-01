"""Thin JSON command-line frontend over the public conversion APIs."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from letools.conversion import ConversionConfig, convert
from letools.doctor import environment_report
from letools.doctor_external import run_doctor
from letools.merge import merge_datasets, plan_merge
from letools.planner import (
    CalibrationOptions,
    PerformanceOverrides,
    plan_and_convert,
    plan_conversion,
)
from letools.plugins import DatasetSource
from letools.source_providers import (
    SourceProvider,
    SourceProviderContext,
    source_providers,
)
from letools.tools.hdf5_preset import list_presets, load_preset
from letools.tools.hdf5_tui import (
    run_hdf5_preset_wizard,
)
from letools.validation import compare_datasets, validate_dataset
from letools.visualizer import (
    VisualizerConfig,
    prepare_visualizer,
    serve_visualizer,
)


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(type(value).__name__)


def _print(value: Any) -> None:
    print(json.dumps(asdict(value), indent=2, default=_json_default))


def _add_source_options(
    parser: argparse.ArgumentParser,
    provider: SourceProvider[Any] | None,
) -> None:
    """Add common source selection and only the selected provider's options."""

    parser.add_argument(
        "--source-format",
        choices=source_providers.choices(),
        default="auto",
        help="source provider; raw formats require explicit selection",
    )
    selected = provider or source_providers.get("auto")
    selected.add_arguments(parser)
    parser.set_defaults(_source_provider=selected)


def _open_cli_source(args: argparse.Namespace) -> DatasetSource:
    """Delegate source construction to the provider selected during parsing."""

    provider: SourceProvider[Any] = args._source_provider
    context = SourceProviderContext(
        interactive=sys.stdin.isatty() and sys.stdout.isatty()
    )
    return provider.create(args.source, args, context)


def build_parser(
    source_provider: SourceProvider[Any] | None = None,
) -> argparse.ArgumentParser:
    """Build the CLI with source-specific options from one selected provider."""

    parser = argparse.ArgumentParser(prog="letools")
    commands = parser.add_subparsers(dest="command", required=True)
    conversion = commands.add_parser("convert", help="Convert a local dataset")
    conversion.add_argument("source", type=Path)
    conversion.add_argument("destination", type=Path)
    _add_source_options(conversion, source_provider)
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
    _add_source_options(planning, source_provider)
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
    merging = commands.add_parser(
        "merge", help="Merge same-version physical LeRobot datasets"
    )
    merging.add_argument("sources", nargs="+", type=Path)
    merging.add_argument("--output", required=True, type=Path)
    merging.add_argument("--data-workers", type=int)
    merging.add_argument("--file-workers", type=int)
    merging.add_argument("--auto", action="store_true")
    merging.add_argument("--plan-only", action="store_true")
    merging.add_argument("--calibration-seconds", type=float, default=10.0)
    merging.add_argument("--calibration-mb", type=int, default=1024)
    merging.add_argument("--no-cache", action="store_true")
    merging.add_argument("--overwrite", action="store_true")
    merging.add_argument("--no-validate", action="store_true")
    commands.add_parser(
        "doctor",
        help="Report the environment or run dataset quality/curation commands",
        description=(
            "With no arguments, report the letools environment. Dataset commands "
            "are: check, fix, trim, score, gate, and merge-check."
        ),
    )
    visualizer = commands.add_parser(
        "visualizer",
        help="Set up or run the integrated LeRobot Dataset Visualizer",
    )
    visualizer_commands = visualizer.add_subparsers(
        dest="visualizer_command", required=True
    )
    visualizer_setup = visualizer_commands.add_parser(
        "setup", help="Prepare the pinned application and install locked Bun dependencies"
    )
    visualizer_setup.add_argument("--cache-dir", type=Path)
    visualizer_setup.add_argument("--bun")
    visualizer_setup.add_argument("--force", action="store_true")
    visualizer_serve = visualizer_commands.add_parser(
        "serve", help="Run the visualizer for a local path or Hub org/dataset"
    )
    visualizer_serve.add_argument("target")
    visualizer_serve.add_argument("--host", default="127.0.0.1")
    visualizer_serve.add_argument("--port", type=int, default=3000)
    visualizer_serve.add_argument("--data-port", type=int, default=8765)
    visualizer_serve.add_argument("--annotation-port", type=int, default=7861)
    visualizer_serve.add_argument("--public-data-url")
    visualizer_serve.add_argument("--public-annotation-url")
    visualizer_serve.add_argument("--no-annotations", action="store_true")
    visualizer_serve.add_argument(
        "--doctor-max-episodes",
        type=int,
        default=20,
        help="local Doctor sample size; 0 scans all episodes",
    )
    visualizer_serve.add_argument("--production", action="store_true")
    visualizer_serve.add_argument("--open", action="store_true", dest="open_browser")
    visualizer_serve.add_argument("--cache-dir", type=Path)
    visualizer_serve.add_argument("--bun")
    visualizer_serve.add_argument("--force-setup", action="store_true")
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


def parse_cli_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Select a source provider first, then parse its isolated option surface."""

    tokens = list(sys.argv[1:] if argv is None else argv)
    provider: SourceProvider[Any] | None = None
    if tokens and tokens[0] in {"convert", "plan"}:
        bootstrap = argparse.ArgumentParser(add_help=False)
        bootstrap.add_argument(
            "--source-format",
            choices=source_providers.choices(),
            default="auto",
        )
        # Preserve the original `--preset` shorthand for HDF5 while keeping it
        # absent from every unrelated provider's final argument surface.
        bootstrap.add_argument("--preset")
        selected, _ = bootstrap.parse_known_args(tokens[1:])
        name = (
            "hdf5"
            if selected.preset and selected.source_format == "auto"
            else selected.source_format
        )
        provider = source_providers.get(name)
    return build_parser(provider).parse_args(tokens)


def main(argv: list[str] | None = None) -> int:
    """Dispatch one CLI command and return a process exit status."""

    tokens = list(sys.argv[1:] if argv is None else argv)
    # Preserve the original no-argument environment report while delegating
    # every dataset operation to the complete pinned Doctor CLI. This early
    # dispatch also preserves upstream parsing, help text, and exit semantics.
    if tokens and tokens[0] == "doctor" and len(tokens) > 1:
        if tokens[1] == "environment":
            if len(tokens) != 2:
                print("letools doctor environment takes no arguments", file=sys.stderr)
                return 2
            print(json.dumps(environment_report(), indent=2))
            return 0
        return run_doctor(tokens[1:])

    args = parse_cli_args(tokens)
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
    if args.command == "visualizer":
        if args.visualizer_command == "setup":
            _print(
                prepare_visualizer(
                    cache_dir=args.cache_dir,
                    bun=args.bun,
                    force=args.force,
                )
            )
            return 0
        if args.doctor_max_episodes < 0:
            print("--doctor-max-episodes cannot be negative", file=sys.stderr)
            return 2
        return serve_visualizer(
            args.target,
            VisualizerConfig(
                host=args.host,
                port=args.port,
                data_port=args.data_port,
                annotation_port=args.annotation_port,
                public_data_url=args.public_data_url,
                public_annotation_url=args.public_annotation_url,
                annotations=not args.no_annotations,
                doctor_max_episodes=args.doctor_max_episodes or None,
                production=args.production,
                open_browser=args.open_browser,
                cache_dir=args.cache_dir,
                bun=args.bun,
                force_setup=args.force_setup,
            ),
        )
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
    if args.command == "merge":
        if args.plan_only:
            _print(
                plan_merge(
                    args.sources,
                    args.output,
                    data_workers=args.data_workers,
                    file_workers=args.file_workers,
                    calibrate=args.auto,
                    calibration_seconds=args.calibration_seconds,
                    calibration_bytes=args.calibration_mb * 1024**2,
                    use_cache=not args.no_cache,
                )
            )
            return 0
        _print(
            merge_datasets(
                args.sources,
                args.output,
                auto=args.auto,
                data_workers=args.data_workers,
                file_workers=args.file_workers,
                overwrite=args.overwrite,
                validate=not args.no_validate,
                use_cache=not args.no_cache,
                calibration_seconds=args.calibration_seconds,
                calibration_bytes=args.calibration_mb * 1024**2,
            )
        )
        return 0
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
