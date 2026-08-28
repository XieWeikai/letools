# letools

High-performance, local conversion tools for LeRobot datasets.

The MVP supports lossless semantic conversion in both directions:

- LeRobot v2.1 to v3.0
- LeRobot v3.0 to v2.1

## Install

    uv sync --dev
    uv run maturin develop --uv

## CLI

    letools convert /path/to/v21 /path/to/v30 --to v3.0 --workers 8
    letools convert /path/to/v30 /path/to/v21 --to v2.1 --workers 8
    letools validate /path/to/dataset --deep
    letools compare /path/to/left /path/to/right --videos

Conversions use a staging directory and publish the destination only after validation succeeds.
Existing destinations are never replaced unless `--overwrite` is supplied.
Video remux defaults to one worker because concurrent large-file streams are slower on many
network filesystems. Use `--video-workers` only after benchmarking the target storage.

## Python API

    from letools import ConversionConfig, convert

    convert(
        "/path/to/source",
        "/path/to/destination",
        "v3.0",
        config=ConversionConfig(workers=8),
    )

The public data model consistently uses `episode` and `episodes`, matching LeRobot.
