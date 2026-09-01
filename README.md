# letools

`letools` is a Python and Rust toolkit for creating, converting, and validating
[LeRobot](https://github.com/huggingface/lerobot) datasets. The current release
supports lossless semantic conversion in both directions between LeRobot v2.1
and v3.0, explicit mapping-driven HDF5 export, and timestamp-aligned AgileX
directory export to either LeRobot version. It also has a specialized high-speed
engine for merging multiple same-version LeRobot datasets.
Pinned integrations add the complete LeRobot Doctor quality/curation suite and
the Hugging Face Dataset Visualizer for local or Hub datasets.

The public API consistently uses LeRobot's `episode` terminology. Python owns
the plugin API and conversion plan; native Rust primitives accelerate work that
benefits from lower per-item overhead.

Detailed documentation:

- [Usage guide](docs/USAGE.md)
- [Installation and direct command setup](docs/INSTALLATION.md)
- [Dataset Doctor](docs/DOCTOR.md)
- [Dataset Visualizer](docs/VISUALIZER.md)
- [External source and update policy](docs/THIRD_PARTY.md)
- [Architecture and module boundaries](docs/ARCHITECTURE.md)
- [Static planner design](docs/PLANNER.md)
- [Specialized merge engine](docs/MERGE.md)
- [HDF5 source MVP acceptance](docs/HDF5_MVP.md)
- [HDF5 mapping presets](docs/HDF5_PRESETS.md)
- [AgileX source acceptance](docs/AGILEX.md)
- [Documentation index](docs/README.md)

## Quick start

Python 3.12 or newer and [uv](https://docs.astral.sh/uv/) are required.

```bash
git clone --recurse-submodules https://github.com/XieWeikai/letools.git
cd letools
uv tool install .
letools doctor
```

Doctor and Visualizer are pinned Git submodules and are required build inputs.
For an existing non-recursive clone, run
`git submodule update --init --recursive` before installing.

`uv tool install .` creates an isolated user-level environment and publishes the
`letools` executable, normally under `~/.local/bin`. No virtual-environment
activation is needed. If the command is not in `PATH`, run `uv tool update-shell`
once and open a new shell. Developers who want a lockfile-exact environment can
instead run `./scripts/link_letools.sh`; see the
[installation guide](docs/INSTALLATION.md).

Update or remove the standalone user command with:

```bash
git pull
git submodule update --init --recursive
uv tool install --force .
# uv tool uninstall letools
```

For an editable command that follows Python source changes in the checkout:

```bash
uv tool install --editable .
```

The install includes the Python frontend and matching prebuilt native wheel. On
Linux x86-64 the native wheel includes the minimal FFmpeg 8 runtime used by Rust
packet hashing, concatenation, and splitting. No compiler, Rust toolchain,
FFmpeg headers, `pkg-config`, libclang, `LD_LIBRARY_PATH`, or shell environment
file is needed.

macOS, Windows, and Linux aarch64 receive the portable native filesystem
primitives while PyAV handles video. PyAV's official wheel carries its own
FFmpeg runtime, so these platforms also need no system FFmpeg. A system or
user-installed `ffmpeg` executable is detected by `letools doctor`, but runtime
conversion never depends on finding it in `PATH`.

## Command reference

All commands support `-h` or `--help`. Result-producing commands write JSON to
standard output and return a nonzero exit status on failure.

### Convert

```text
letools convert SOURCE DESTINATION --to {v2.1,v3.0} [options]
```

Convert v2.1 to v3.0:

```bash
letools convert /data/dataset-v21 /data/dataset-v30 --to v3.0
```

Convert v3.0 to v2.1:

```bash
letools convert /data/dataset-v30 /data/dataset-v21 --to v2.1
```

Use automatic planning when the environment or dataset has not already been
characterized:

```bash
letools convert /data/dataset-v21 /data/dataset-v30 --to v3.0 --auto
```

Convert HDF5 through an explicit mapping preset:

```bash
letools tools hdf5-preset create /data/hdf5 --name my-dataset
letools convert /data/hdf5 /data/dataset-v30 \
  --source-format hdf5 --preset my-dataset --to v3.0 --auto
```

Convert an AgileX recording and add one instruction to every episode:

```bash
letools convert /data/agilex /data/dataset-v30 \
  --source-format agilex --instruction "pick up the object" --to v3.0 --auto
```

JPEG-valued HDF5 cameras are preserved as MJPEG packets by default. This avoids
lossy transcoding and is substantially faster, but the generated videos remain
close to the source JPEG size. Python callers can select compact MPEG-4 output
through `VideoEncodingConfig`. HDF5 media jobs automatically use spawn-based
process isolation to bypass h5py's process-wide native lock; `--video-workers`
still controls the job count and no multiprocessing setup is required. See
[detailed usage](docs/USAGE.md#8-python-api).

| Option | Meaning |
| --- | --- |
| `--to VERSION` | Required target; accepts `v2.1`, `2.1`, `v3.0`, or `3.0` |
| `--source-format auto\|lerobot\|hdf5\|agilex` | Select source parsing; default is LeRobot auto-detection |
| `--preset NAME_OR_PATH` | Load an HDF5 preset by user-store name or JSON path; implies HDF5 |
| `--instruction TEXT` | Fixed task instruction required by the AgileX source |
| `--fps N` | AgileX output FPS; default 30 |
| `--robot-type NAME` | AgileX robot type metadata; default `cobot_magic` |
| `--workers N` | Concurrent Parquet/data groups |
| `--video-workers N` | Concurrent video remux or encode jobs |
| `--data-file-size-mb N` | Approximate uncompressed Parquet shard target for v3 output only |
| `--video-file-size-mb N` | Approximate video shard target for v3 output only |
| `--auto` | Plan a static execution configuration, then convert |
| `--calibration-seconds N` | Auto-planner calibration time budget; default 10 seconds |
| `--calibration-mb N` | Auto-planner read and write budget; default 1024 MiB each |
| `--no-cache` | Ignore and do not write the planner cache for this run |
| `--overwrite` | Replace an existing destination only after staged output validates |
| `--no-validate` | Skip the built-in shallow validation gate |

Conversions write into a staging directory and publish the destination only
after success. Existing destinations are not replaced unless `--overwrite` is
provided. Without `--auto`, explicit worker values or fixed defaults are used.
The two file-size controls do not apply to v2.1 output.

### Merge

Merge two or more physical datasets of the same LeRobot version:

```bash
letools merge /data/part-a-v30 /data/part-b-v30 \
  --output /data/combined-v30 --auto
```

The supported combinations are exclusively v2.1 + v2.1 -> v2.1 and
v3.0 + v3.0 -> v3.0. Input order determines output episode order. Merge is a
specialized path: it streams Parquet while rewriting only `episode_index`,
global `index`, and `task_index`, and clones or copies complete video files
without FFmpeg. It never creates an intermediate converted dataset.

```bash
# Inspect or calibrate without publishing the destination.
letools merge PART_A PART_B --output COMBINED --plan-only --auto

# Reproduce a fixed plan.
letools merge PART_A PART_B --output COMBINED \
  --data-workers 16 --file-workers 1
```

| Option | Meaning |
| --- | --- |
| `--output PATH` | Required destination |
| `--auto` | Run bounded real-work calibration on a cache miss |
| `--plan-only` | Print the plan without merging |
| `--data-workers N` | Fixed concurrent Parquet resources |
| `--file-workers N` | Fixed concurrent whole-file media operations |
| `--calibration-seconds N` | Calibration wall budget; default 10 seconds |
| `--calibration-mb N` | Aggregate calibration I/O budget; default 1024 MiB |
| `--no-cache` | Ignore and do not write the merge-plan cache |
| `--overwrite` | Transactionally replace an existing destination |
| `--no-validate` | Skip built-in deep validation |

Inputs must agree on version, FPS, robot type, features, video keys, and one
full-dataset split. Tasks are deduplicated by text and frame task indices are
remapped. See [the merge design and acceptance report](docs/MERGE.md).

### Plan

```text
letools plan SOURCE DESTINATION --to {v2.1,v3.0} [options]
```

Inspect the environment and print a read-only static plan:

```bash
letools plan /data/dataset-v21 /data/dataset-v30 --to v3.0
```

Add bounded workload calibration when the plan will be used for a substantial
conversion:

```bash
letools plan /data/dataset-v21 /data/dataset-v30 --to v3.0 --calibrate
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

`plan` accepts the same source, preset, worker, target-size, calibration-budget,
and cache options as automatic conversion. `--calibrate` enables bounded real
work for `plan`; `convert --auto` enables it by default when useful. `plan` never
writes the destination and does not create a separate plan file. Automatic
conversion invokes the same planner and immediately executes the returned
immutable configuration.

### HDF5 preset tools

```text
letools tools hdf5-preset create SOURCE [--name NAME] [--output FILE]
letools tools hdf5-preset list
letools tools hdf5-preset show NAME_OR_PATH
```

`create` scans a representative HDF5 episode and interactively selects numeric,
task, and encoded-image fields. `--episode-glob PATTERN` changes episode
discovery, `--output FILE` creates a portable project preset, and `--overwrite`
permits replacing that file. Without `--output`, presets live under
`${XDG_CONFIG_HOME:-$HOME/.config}/letools/hdf5-presets/`.

`list` prints summaries from the user store. `show` prints the full versioned
JSON mapping. In a TTY, `convert --source-format hdf5` can present a stored-preset
menu; non-interactive and Slurm jobs must pass `--preset`. See
[HDF5 mapping presets](docs/HDF5_PRESETS.md) for the schema and supported HDF5
representations.

### Validate and compare

```bash
letools validate /data/dataset-v30
letools validate /data/dataset-v30 --deep
letools compare /data/original /data/converted
letools compare /data/original /data/converted --videos
```

Shallow validation checks metadata, paths, episode ranges, schemas, and file
availability. `--deep` also reads complete data tables and decodes media.
`compare` checks semantic metadata and data by default; `--skip-data` omits table
values and `--videos` adds encoded packet-payload comparison. It returns zero
only when the requested checks are equal.

### Doctor

```bash
letools doctor
letools doctor check /data/dataset --max-episodes 20
letools doctor check /data/dataset --ci --fail-on warn
```

With no arguments, `doctor` reports the Python package, native provider and
capabilities, PyAV's linked FFmpeg libraries, system `ffmpeg`, and pinned
external commits. Dataset arguments invoke the complete pinned Doctor: 12
quality checks, JSON/Markdown/CI output, auto-repair, idle-frame trimming,
episode scoring, policy gates, and merge compatibility. Preview `fix` and
`trim` with `--dry-run`; see [the Doctor guide](docs/DOCTOR.md).

### Visualizer

Install the locked web dependencies once, then open a local path or Hub ID:

```bash
letools visualizer setup
letools visualizer serve /data/dataset --open
letools visualizer serve lerobot/pusht --open
```

The complete pinned Hugging Face UI includes synchronized cameras and charts,
statistics, action insights, filtering, URDF views, annotations, and a Doctor
tab. Local mode serves files directly from the dataset with byte-range support;
it does not upload or copy the dataset. Bun is the one additional executable
prerequisite. Setup, cache behavior, annotations, ports, Slurm forwarding, and
security are detailed in [the Visualizer guide](docs/VISUALIZER.md).

### Slurm

On a Slurm cluster, submit conversion commands through the site's normal
wrapper. A user-level command installed on a shared home is visible on compute
nodes when the job inherits `~/.local/bin` in `PATH`:

```bash
sbatch --cpus-per-task=8 --mem=48G --wrap \
  'letools convert /data/v21 /data/v30 --to v3.0 --auto'
```

## Python API

```python
from letools import ConversionConfig, convert, merge_datasets

result = convert(
    "/data/dataset-v21",
    "/data/dataset-v30",
    "v3.0",
    config=ConversionConfig(workers=8, video_workers=3),
)
print(result)

merged = merge_datasets(
    ["/data/part-a-v30", "/data/part-b-v30"],
    "/data/combined-v30",
    auto=True,
)
print(merged.plan)
```

The public API also exports `plan_conversion()`, `plan_and_convert()`,
`validate_dataset()`, `compare_datasets()`, `open_dataset()`, the built-in
LeRobot source classes, `AgileXSource`, `HDF5Source`, their typed source
configuration classes, and the source-provider registry. The
[usage guide](docs/USAGE.md) contains complete Python examples, custom-source
and provider requirements, result types, and validation behavior.

## Architecture

```text
CLI --source-format              Python API
       |                              |
       v                              |
SourceProvider -> typed config        |
       |                              |
       +-------------+----------------+
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

Same-version LeRobot paths -> specialized merge manifest
                                      |
                        streaming Parquet index rewrite
                        + bounded Rust clone/copy pool
                                      |
                         deep validation + publish

Physical/Hub dataset -> pinned Doctor -> checks, repair, score, and gates

Local/Hub target -> letools Visualizer supervisor
                         |
             pinned Next.js UI + annotation API
                         |
           local Hub bridge + embedded Doctor
```

Source providers own CLI option registration, typed configuration, and source
construction. Plugins read a physical dataset and expose metadata, episodes,
Arrow tables, and video slices. Backends consume that common model and write a
target format. This keeps frontend configuration and format-specific layout out
of conversion orchestration and lets additional source plugins reuse both
LeRobot backends.

The current built-in plugins are:

- `LeRobotV21Source`
- `LeRobotV30Source`
- `HDF5Source` (Python API or CLI with an explicit mapping preset)
- `AgileXSource` (Python API or CLI with an explicit instruction)

The current built-in backends write:

- LeRobot v2.1: one Parquet file and video file per episode
- LeRobot v3.0: size-grouped Parquet and video files with episode offsets

Reusable primitives live in `src/letools/_arrow.py`, `_video.py`, `_stats.py`,
and `_native.py`. The self-improvement protocol deliberately keeps performance
complexity inside these primitives and backends rather than leaking it into the
plugin API.

Merge deliberately does not use the plugin/backend path. Its permanently narrow
same-version contract enables whole-file video copying and direct physical-layout
rewrites. This separation also guarantees that merge development cannot regress
conversion dispatch or third-party source behavior.

Doctor and Visualizer are also separate from conversion. Their complete
upstream implementations are pinned as submodules under `third_party/external`;
thin letools adapters provide deterministic packaging, local-path access,
process lifecycle, and CLI composition without adding branches to conversion
or merge hot paths.

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
notices and build configuration. The pinned Doctor and Visualizer submodules are
Apache-2.0 and retain their upstream license files and provenance under
`third_party/`; see [the external-source policy](docs/THIRD_PARTY.md).
