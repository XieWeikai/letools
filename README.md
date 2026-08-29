# letools

`letools` is a Python and Rust toolkit for creating, converting, and validating
[LeRobot](https://github.com/huggingface/lerobot) datasets. The current release
supports lossless semantic conversion in both directions between LeRobot v2.1
and v3.0.

The public API consistently uses LeRobot's `episode` terminology. Python owns
the plugin API and conversion plan; native Rust primitives accelerate work that
benefits from lower per-item overhead.

## Quick start

Python 3.12 or newer and [uv](https://docs.astral.sh/uv/) are required.

```bash
git clone https://github.com/XieWeikai/letools.git
cd letools
uv sync --locked
uv run letools doctor
```

No compiler, Rust toolchain, FFmpeg headers, `pkg-config`, libclang, or shell
environment file is required for this path. PyAV's official wheel includes the
FFmpeg runtime used by the portable Python implementation. A system or
user-installed `ffmpeg` executable is detected and reported, but it is not a
prerequisite.

## Command line

Convert v2.1 to v3.0:

```bash
uv run letools convert /data/dataset-v21 /data/dataset-v30 --to v3.0
```

Convert v3.0 to v2.1:

```bash
uv run letools convert /data/dataset-v30 /data/dataset-v21 --to v2.1
```

Validate one dataset or compare two datasets semantically:

```bash
uv run letools validate /data/dataset-v30 --deep
uv run letools compare /data/original /data/converted --videos
```

Useful conversion controls:

```text
--workers N              concurrent Parquet groups
--video-workers N        concurrent video remux jobs
--data-file-size-mb N    v3 Parquet target size
--video-file-size-mb N   v3 video target size
--overwrite              replace an existing destination
--no-validate            skip built-in shallow validation
```

Conversions write into a staging directory and publish the destination only
after success. Existing destinations are not replaced unless `--overwrite` is
provided.

On a Slurm cluster, submit conversion commands through the site's normal
wrapper. For example:

```bash
sbatch --cpus-per-task=8 --mem=48G --wrap \
  'cd /path/to/letools && uv run letools convert /data/v21 /data/v30 --to v3.0'
```

## Python API

```python
from letools import ConversionConfig, convert

result = convert(
    "/data/dataset-v21",
    "/data/dataset-v30",
    "v3.0",
    config=ConversionConfig(workers=8, video_workers=3),
)
print(result)
```

## Architecture

```text
CLI / Python API
       |
       v
DatasetSource plugin ---- Episode model ---- Conversion plan
       |                                         |
       |                                         v
       |                              v2.1 or v3.0 backend
       |                                |       |
       v                                v       v
metadata + Arrow tables              Parquet  video primitives
                                                  |
                                  +---------------+---------------+
                                  |                               |
                           portable PyAV                    letools-native
                           implementation                  Rust acceleration
```

Plugins read a physical dataset and expose metadata, episodes, Arrow tables,
and video slices. Backends consume that common model and write a target format.
This keeps format-specific layout out of conversion orchestration and lets
additional source plugins reuse both LeRobot backends.

The current built-in plugins are:

- `LeRobotV21Source`
- `LeRobotV30Source`

The current built-in backends write:

- LeRobot v2.1: one Parquet file and video file per episode
- LeRobot v3.0: size-grouped Parquet and video files with episode offsets

Reusable primitives live in `src/letools/_arrow.py`, `_video.py`, `_stats.py`,
and `_native.py`. The self-improvement protocol deliberately keeps performance
complexity inside these primitives and backends rather than leaking it into the
plugin API.

## FFmpeg providers

There are two deliberately isolated FFmpeg consumers:

1. PyAV uses the FFmpeg libraries bundled in its official wheel. This is the
   portable fallback and makes `uv sync --locked` work on a clean machine.
2. Released `letools-native` wheels bundle the FFmpeg libraries required by the
   Rust hot path. Wheel repair writes a relative runtime search path, so users
   do not configure `PATH`, `PKG_CONFIG_PATH`, or `LD_LIBRARY_PATH`.

The two libraries never exchange `AVPacket*`, `AVFrame*`, codec contexts, or
hardware contexts. Python passes paths, episode time ranges, and output paths;
Rust owns an entire open/demux/remux/hash/close operation. This coarse boundary
avoids packet payload copies and ABI coupling.

`letools doctor` reports the selected native provider, PyAV's FFmpeg versions,
and any system `ffmpeg` executable. System FFmpeg is useful for development but
is not silently installed or modified by letools.

## Native wheels

`native/` is a separate `letools-native` Python distribution built with PyO3
and maturin. Release tags named `native-vX.Y.Z` trigger GitHub Actions to build
abi3 wheels for Linux x86-64/aarch64, macOS x86-64/arm64, and Windows x86-64.
The wheels are attached to the GitHub release and indexed through GitHub Pages.

The main package remains pure Python. This separation is important: uv builds a
cloned root project in editable mode, so embedding maturin at the root would
force every user to install a compiler and native SDK.

## Development

Install test and native development tools explicitly:

```bash
uv sync --locked --group test --group native-dev
uv run maturin develop --manifest-path native/Cargo.toml --locked
uv run pytest -q
cargo clippy --manifest-path native/Cargo.toml --locked -- -D warnings
```

When native FFmpeg code is changed, developers may build against a compatible
system or user FFmpeg SDK. That SDK must provide headers, shared libraries, and
`libavformat.pc`, `libavcodec.pc`, and `libavutil.pc`; an `ffmpeg` executable
alone is not a development SDK. Released wheels remain self-contained.

## Correctness and performance

Semantic validation covers metadata, episode boundaries, Arrow schemas and
values, statistics, and encoded video packet payloads. Physical Parquet layout
and MP4 container metadata may differ when the resulting datasets are
semantically equivalent.

The frozen full benchmark contains 3,457 episodes, 2,415,341 frames, and 10,371
episode-camera video slices. See [BENCHMARK.md](BENCHMARK.md) for the current
official comparison and [self-improve/PROTOCOL.md](self-improve/PROTOCOL.md)
for profiling, correctness, resource, and acceptance rules.

## License

letools is released under the MIT License. FFmpeg and other dependencies retain
their own licenses; bundled native release artifacts include the corresponding
notices and build configuration.
