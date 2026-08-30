# letools

`letools` is a Python and Rust toolkit for creating, converting, and validating
[LeRobot](https://github.com/huggingface/lerobot) datasets. The current release
supports lossless semantic conversion in both directions between LeRobot v2.1
and v3.0, plus explicit mapping-driven HDF5 export to either LeRobot version.

The public API consistently uses LeRobot's `episode` terminology. Python owns
the plugin API and conversion plan; native Rust primitives accelerate work that
benefits from lower per-item overhead.

Detailed documentation:

- [Usage guide](docs/USAGE.md)
- [Architecture and module boundaries](docs/ARCHITECTURE.md)
- [Static planner design](docs/PLANNER.md)
- [Documentation index](docs/README.md)

## Quick start

Python 3.12 or newer and [uv](https://docs.astral.sh/uv/) are required.

```bash
git clone https://github.com/XieWeikai/letools.git
cd letools
uv sync --locked
uv run letools doctor
```

That single sync command installs both the Python frontend and the matching
prebuilt native wheel from the letools package index. On Linux x86-64 the native
wheel includes the minimal FFmpeg 8 runtime used by Rust packet hashing,
concatenation, and splitting. No compiler, Rust toolchain, FFmpeg headers,
`pkg-config`, libclang, `LD_LIBRARY_PATH`, or shell environment file is needed.

macOS, Windows, and Linux aarch64 receive the portable native filesystem
primitives while PyAV handles video. PyAV's official wheel carries its own
FFmpeg runtime, so these platforms also need no system FFmpeg. A system or
user-installed `ffmpeg` executable is detected by `letools doctor`, but runtime
conversion never depends on finding it in `PATH`.

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

### Automatic planning

Inspect the environment and print a read-only static plan:

```bash
uv run letools plan /data/dataset-v21 /data/dataset-v30 --to v3.0
```

Add bounded workload calibration when the plan will be used for a substantial
conversion:

```bash
uv run letools plan /data/dataset-v21 /data/dataset-v30 --to v3.0 --calibrate
```

Plan and execute in one command:

```bash
uv run letools convert /data/dataset-v21 /data/dataset-v30 --to v3.0 --auto
```

The planner reads Slurm, process-affinity, cgroup, memory, dataset, and source
and destination filesystem limits. It chooses workers and, for v3 output, file
targets before conversion begins. Explicit performance flags remain hard
constraints when combined with `--auto`; unspecified fields are planned.

Calibration is limited by default to 10 seconds, 1 GiB of source reads, and
1 GiB of temporary writes. Temporary outputs are removed before conversion.
Plans and their evidence are cached by resource, storage, dataset, direction,
and planner-algorithm fingerprint. Use `--no-cache` for an independent cold
measurement. See [the planner design](docs/PLANNER.md) and
[Slurm acceptance results](docs/PLANNER_BENCHMARK.md).

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
- `HDF5Source` (Python API with an explicit `HDF5Mapping`)

The current built-in backends write:

- LeRobot v2.1: one Parquet file and video file per episode
- LeRobot v3.0: size-grouped Parquet and video files with episode offsets

Reusable primitives live in `src/letools/_arrow.py`, `_video.py`, `_stats.py`,
and `_native.py`. The self-improvement protocol deliberately keeps performance
complexity inside these primitives and backends rather than leaking it into the
plugin API.

The Rust boundary is deliberately coarse. Python passes paths and episode time
ranges once per file; Rust owns open/demux/remux/hash/trailer/close and releases
the GIL for the entire operation. No packet, frame, or FFmpeg context crosses
the language boundary.

## FFmpeg providers

There are two deliberately isolated FFmpeg consumers:

1. PyAV uses the FFmpeg libraries bundled in its official wheel. This is the
   portable fallback and makes `uv sync --locked` work on a clean machine.
2. The released Linux x86-64 `letools-native` wheel bundles the FFmpeg libraries
   required by the Rust hot path. Wheel repair writes a relative runtime search
   path, so users do not configure `PATH`, `PKG_CONFIG_PATH`, or
   `LD_LIBRARY_PATH`. Other released native wheels omit FFmpeg and select the
   PyAV video fallback automatically.

The two libraries never exchange `AVPacket*`, `AVFrame*`, codec contexts, or
hardware contexts. Python passes paths, episode time ranges, and output paths;
Rust owns an entire open/demux/remux/hash/close operation. This coarse boundary
avoids packet payload copies and ABI coupling.

`letools doctor` reports the selected native provider, PyAV's FFmpeg versions,
and any system `ffmpeg` executable. System FFmpeg is useful for development but
is not silently installed or modified by letools.

## Native wheels

`native/` is a separate `letools-native` Python distribution built with PyO3
and maturin. It is a normal locked dependency of the root package, resolved
from `https://xieweikai.github.io/letools/simple`. Release tags named
`native-vX.Y.Z` trigger GitHub Actions to build abi3 wheels for Linux
x86-64/aarch64, macOS x86-64/arm64, and Windows x86-64. The wheels are attached
to the GitHub release; a default-branch workflow then updates the Pages index.

The Linux x86-64 job downloads FFmpeg 8.0.3 from ffmpeg.org, verifies its pinned
SHA-256, builds a minimal LGPL configuration, compiles the Rust video feature,
and lets maturin/auditwheel bundle only required shared libraries. A clean venv
then imports the wheel with every FFmpeg/compiler library environment variable
removed. Exact configure flags and notices are shipped in
`scripts/build_ffmpeg_linux.sh` and `native/THIRD_PARTY_NOTICES.md`.

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

For the video feature, expose that SDK only to the development command:

```bash
PKG_CONFIG_PATH=/path/to/ffmpeg/lib/pkgconfig \
LIBRARY_PATH=/path/to/ffmpeg/lib \
LD_LIBRARY_PATH=/path/to/ffmpeg/lib \
LIBCLANG_PATH=/path/to/libclang \
uv run maturin develop --manifest-path native/Cargo.toml --locked --features video
cargo clippy --manifest-path native/Cargo.toml --locked --features video -- -D warnings
```

These variables are build-time developer inputs, not user setup. CI exercises
the locked user install, forced Python fallback, portable source build, FFmpeg
source build, five-platform wheel matrix, clean wheel import, release upload,
and package-index deployment.

## Correctness and performance

Semantic validation covers metadata, episode boundaries, Arrow schemas and
values, statistics, and encoded video packet payloads. Physical Parquet layout
and MP4 container metadata may differ when the resulting datasets are
semantically equivalent.

The frozen full benchmark contains 3,457 episodes, 2,415,341 frames, 10,371
episode-camera video slices, and about 39 GiB. With 8 CPUs, 48 GiB, and three
video workers, the accepted Rust concat path measured 52.09 seconds for
v2.1-to-v3.0 and the Rust split path measured 109.01 seconds for v3.0-to-v2.1.
See [BENCHMARK.md](BENCHMARK.md) for medians and resource data, and
[self-improve/PROTOCOL.md](self-improve/PROTOCOL.md) for profiling,
correctness, resource, and acceptance rules.

## License

letools is released under the MIT License. FFmpeg and other dependencies retain
their own licenses; bundled native release artifacts include the corresponding
notices and build configuration.
