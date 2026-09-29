"""Thin CLI over the optional editor's typed Python API."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from letools_editor.engine import edit_dataset, plan_edit
from letools_editor.model import EditConfig, VideoEdit


def episode_selection(value: str) -> frozenset[int]:
    """Parse source episode IDs; colon ranges are half-open like Python slices."""

    indices = set()
    try:
        for token in value.split(","):
            if ":" in token:
                start, stop = map(int, token.split(":"))
                if start < 0 or stop <= start or stop - start > 10000000:
                    raise ValueError
                indices.update(range(start, stop))
            else:
                index = int(token)
                if index < 0:
                    raise ValueError
                indices.add(index)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "Use non-negative IDs or half-open ranges, e.g. 1,4,10:15"
        ) from None
    return frozenset(indices)


def _size(value: str) -> tuple[int, int]:
    try:
        width, height = map(int, value.lower().split("x"))
        if min(width, height) <= 0:
            raise ValueError
        return width, height
    except ValueError:
        raise argparse.ArgumentTypeError(
            "Use a positive WIDTHxHEIGHT, e.g. 224x224"
        ) from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="letools editor",
        description="Edit a local LeRobot v2.1/v3.0 dataset into a separate output directory",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser(
        "inspect", help="List features, tasks, and episode summaries"
    )
    inspect.add_argument("source", type=Path)
    inspect.add_argument("--limit", type=int, default=20)
    for name in ("plan", "apply"):
        command = commands.add_parser(
            name,
            help="Inspect a read-only edit plan"
            if name == "plan"
            else "Apply one fused, validated edit",
        )
        command.add_argument("source", type=Path)
        command.add_argument("destination", type=Path)
        command.add_argument(
            "--set-task",
            action="append",
            default=[],
            metavar="EPISODE=TEXT",
            help="Repeat to replace tasks for source episode IDs",
        )
        command.add_argument(
            "--tasks-json",
            type=Path,
            help="JSON object mapping source episode IDs to task strings",
        )
        command.add_argument(
            "--delete-episodes",
            type=episode_selection,
            default=frozenset(),
            metavar="IDS",
            help="IDs or half-open ranges: 1,4,10:15",
        )
        command.add_argument(
            "--remove-feature",
            action="append",
            default=[],
            help="Repeat for numeric/image/video feature names",
        )
        command.add_argument(
            "--video-codec",
            choices=["libx264", "mjpeg"],
            help="Re-encode retained cameras; defaults to libx264 when resizing",
        )
        command.add_argument(
            "--resize",
            type=_size,
            metavar="WIDTHxHEIGHT",
            help="Exact resize; may change aspect ratio",
        )
        command.add_argument(
            "--video-key",
            action="append",
            default=[],
            help="Limit explicit transforms to these retained camera keys",
        )
        command.add_argument(
            "--pixel-format", help="Default yuv420p (H.264), yuvj420p (MJPEG)"
        )
        command.add_argument(
            "--crf", type=int, help="libx264 quality 0..51; default 23"
        )
        command.add_argument("--preset", help="libx264 speed preset; default veryfast")
        command.add_argument(
            "--quality", type=int, help="MJPEG quality 1..31; default 2"
        )
        command.add_argument(
            "--workers",
            type=int,
            help="Concurrent file jobs; default bounded auto (up to 8)",
        )
        command.add_argument(
            "--codec-threads", type=int, default=1, help="Encoder threads per video job"
        )
        command.add_argument(
            "--batch-rows",
            type=int,
            default=65536,
            help="Bounded Parquet read batch size",
        )
        command.add_argument(
            "--ffmpeg", help="Executable path; otherwise PATH, then bundled binary"
        )
        command.add_argument(
            "--overwrite",
            action="store_true",
            help="Replace an existing output dataset after successful validation",
        )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            from letools.plugins import open_dataset

            source = open_dataset(args.source)
            if args.limit < 0:
                raise ValueError("--limit must be non-negative")
            result = {
                "path": source.root,
                "version": source.metadata.version,
                "episodes": len(source.episodes),
                "frames": source.metadata.total_frames,
                "features": source.metadata.features,
                "tasks": source.metadata.tasks,
                "episode_preview": [
                    {"index": ep.index, "length": ep.length, "tasks": ep.tasks}
                    for ep in source.episodes[: args.limit]
                ],
            }
        else:
            tasks = {}
            if args.tasks_json:
                document = json.loads(args.tasks_json.read_text(encoding="utf-8"))
                if not isinstance(document, dict):
                    raise ValueError(
                        "--tasks-json must contain an object mapping IDs to strings"
                    )
                tasks.update({int(key): value for key, value in document.items()})
            for item in args.set_task:
                episode, text = item.split("=", 1)
                tasks[int(episode)] = text
            video_args = (
                args.video_codec,
                args.resize,
                args.pixel_format,
                args.crf,
                args.preset,
                args.quality,
            )
            video = None
            if any(value is not None for value in video_args):
                codec = args.video_codec or "libx264"
                if codec == "mjpeg" and (
                    args.crf is not None or args.preset is not None
                ):
                    raise ValueError(
                        "--crf/--preset apply only to libx264; use --quality for mjpeg"
                    )
                if codec != "mjpeg" and args.quality is not None:
                    raise ValueError(
                        "--quality applies only to mjpeg; use --crf for libx264"
                    )
                video = VideoEdit(
                    codec,
                    args.resize,
                    args.pixel_format,
                    23 if args.crf is None else args.crf,
                    args.preset or "veryfast",
                    2 if args.quality is None else args.quality,
                    tuple(args.video_key),
                )
            elif args.video_key:
                raise ValueError("--video-key requires a video transform")
            config = EditConfig(
                tasks,
                args.delete_episodes,
                frozenset(args.remove_feature),
                video,
                args.workers,
                args.codec_threads,
                args.batch_rows,
                args.ffmpeg,
                args.overwrite,
            )
            operation = plan_edit if args.command == "plan" else edit_dataset
            result = asdict(operation(args.source, args.destination, config))
        print(json.dumps(result, indent=2, default=str, ensure_ascii=False))
        return 0
    except (ValueError, TypeError, OSError, RuntimeError) as error:
        print(f"letools editor: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
