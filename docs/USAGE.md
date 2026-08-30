# Usage guide

## 1. Install

`letools` requires Python 3.12 or newer and `uv`.

```bash
git clone https://github.com/XieWeikai/letools.git
cd letools
uv sync --locked
uv run letools doctor
```

`uv sync --locked` installs the Python package, PyArrow, PyAV, and the matching
`letools-native` wheel from the package index recorded in `uv.lock`. Normal
users do not need Rust, a C compiler, FFmpeg headers, `pkg-config`, libclang, or
an environment script.

On Linux x86-64, the released native wheel contains its own minimal FFmpeg 8
runtime for Rust video operations. PyAV contains its own FFmpeg runtime. These
two consumers exchange only paths and timestamps, so their FFmpeg ABIs do not
need to match. On platforms without native video symbols, conversion falls back
to PyAV automatically.

Run `doctor` after installation to see the selected providers:

```bash
uv run letools doctor
```

The report includes the letools and native-wheel versions, native build
features, PyAV's linked FFmpeg libraries, and any system `ffmpeg` executable.
The system executable is informational and is not required for conversion.

## 2. CLI overview

```text
letools convert SOURCE DESTINATION --to VERSION [options]
letools plan SOURCE DESTINATION --to VERSION [options]
letools validate DATASET [--deep]
letools compare LEFT RIGHT [--skip-data] [--videos]
letools doctor
```

All result-producing commands print JSON to standard output. `validate` and
`compare` return exit status zero on success/equality and one on an invalid or
different result. Parsing and execution failures return a nonzero status.

Accepted target spellings are `v2.1`, `2.1`, `v3.0`, and `3.0`.

## 3. Convert a dataset

### v2.1 to v3.0

For a substantial conversion, automatic planning is the recommended entry
point:

```bash
uv run letools convert \
  /data/dataset-v21 \
  /data/dataset-v30 \
  --to v3.0 \
  --auto
```

### v3.0 to v2.1

```bash
uv run letools convert \
  /data/dataset-v30 \
  /data/dataset-v21 \
  --to v2.1 \
  --auto
```

The source must be a local filesystem path containing a supported LeRobot
layout. "Local" here means path-based access; the path may reside on a shared
filesystem such as JuiceFS, NFS, Ceph, Lustre, or GPFS.

### Publication and overwrite behavior

Conversion writes a complete dataset into a unique hidden sibling staging
directory. By default, an existing destination is rejected:

```text
FileExistsError: Destination already exists: ...
```

Allow replacement explicitly:

```bash
uv run letools convert SOURCE DESTINATION --to v3.0 --auto --overwrite
```

The previous destination is not removed until backend writing and built-in
validation of the staging dataset succeed. A failed conversion removes its
staging directory.

### Fixed configuration

Without `--auto`, the CLI uses fixed defaults: up to eight data workers, up to
three video workers, 100 MiB v3 Parquet targets, and 200 MiB v3 video targets.

```bash
uv run letools convert SOURCE DESTINATION --to v3.0 \
  --workers 8 \
  --video-workers 3 \
  --data-file-size-mb 100 \
  --video-file-size-mb 200
```

Use a fixed configuration for reproducibility experiments or when parameters
have already been established for a controlled environment. Prefer `--auto`
when resources, storage, or dataset shape are not already characterized.

### Conversion options

| Option | Meaning |
| --- | --- |
| `--auto` | Plan a static configuration, then execute it |
| `--workers N` | Maximum concurrent Parquet groups |
| `--video-workers N` | Maximum concurrent video remux jobs |
| `--data-file-size-mb N` | Approximate uncompressed Parquet group target for v3 output |
| `--video-file-size-mb N` | Approximate physical video group target for v3 output |
| `--overwrite` | Replace an existing destination after staging succeeds |
| `--no-validate` | Skip the built-in shallow validation gate |
| `--calibration-seconds N` | Auto-planner calibration time budget; default 10 seconds |
| `--calibration-mb N` | Auto-planner read and temporary-write budget; default 1024 MiB each |
| `--no-cache` | Do not read or write planner cache for this auto run |

The two target-size options affect only v3 output. They control grouping rather
than exact encoded file size: Parquet compression and MP4 container overhead
mean resulting files need not equal the target. Do not pass these options for
v2.1 auto planning; v2.1 always emits per-episode data and video files.

When performance options are supplied together with `--auto`, they are hard
constraints. The planner fills only omitted fields:

```bash
uv run letools convert SOURCE DESTINATION --to v3.0 \
  --auto \
  --workers 4
```

This fixes data concurrency at four while still planning video concurrency and
v3 target sizes.

### Conversion output

A fixed conversion prints a `ConversionResult`. An auto conversion prints both
the selected `plan` and the nested `conversion` result. Useful fields include:

- source and target versions;
- episode and frame totals;
- total conversion elapsed time;
- per-stage timing and task counts;
- selected workers and target sizes;
- planner confidence, evidence, fingerprint, and cache-hit state.

Redirect JSON when retaining a run record:

```bash
uv run letools convert SOURCE DESTINATION --to v3.0 --auto > conversion.json
```

## 4. Inspect a plan without converting

Without `--calibrate`, plan mode performs read-only profiling of resources,
dataset shape, and both storage paths, then prints a static plan:

```bash
uv run letools plan SOURCE DESTINATION --to v3.0
```

It does not create the destination and does not execute a full conversion.
Metadata and Parquet footers are read to build the dataset profile.

Add bounded real-work calibration:

```bash
uv run letools plan SOURCE DESTINATION --to v3.0 \
  --calibrate \
  --calibration-seconds 10 \
  --calibration-mb 1024
```

Calibration reads representative source jobs, executes the real Parquet or
video operation, writes temporary results on the destination filesystem, and
removes them. Small inputs skip calibration when its expected cost is not
recoverable.

`plan` prints JSON; it does not create an executable plan file. A later
`convert --auto` runs the lightweight inspection again and can reuse a matching
calibrated cache entry. A plain `plan` without `--calibrate` normally does not
create a new cache entry.

Important plan fields are:

| Field | Interpretation |
| --- | --- |
| `workers`, `video_workers` | Selected concurrency |
| `data_file_size_mb`, `video_file_size_mb` | Selected v3 grouping targets, or null for v2.1 |
| `resources` | Effective Slurm/cgroup/affinity CPU and memory view |
| `dataset` | Episode, frame, file, camera, and size distributions |
| `source_storage`, `destination_storage` | Separate mount and filesystem profiles |
| `confidence` | `heuristic` or `calibrated` |
| `measurements` | Bounded real-work worker measurements |
| `reasons` | Rules supporting the selected choice |
| `fingerprint` | Cache identity for this environment/workload/override tuple |
| `cache_hit` | Whether calibrated parameters were loaded from cache |

### Planner cache

The default directory is:

```text
${XDG_CACHE_HOME:-$HOME/.cache}/letools/planner-v1/
```

Each file is named `<fingerprint>.json`. Entries expire after seven days by
default. Changed resource limits, CPU model, dataset profile, storage mount,
direction, explicit overrides, or planner algorithm produce a different
fingerprint.

Ignore cache for one independent run:

```bash
uv run letools convert SOURCE DESTINATION --to v3.0 --auto --no-cache
```

Inspect or clear the default cache:

```bash
ls -lh "${XDG_CACHE_HOME:-$HOME/.cache}/letools/planner-v1"
rm -rf "${XDG_CACHE_HOME:-$HOME/.cache}/letools/planner-v1"
```

Deleting the directory does not affect datasets; it is recreated after a later
calibration that produces measurements.

## 5. Validate a dataset

Shallow validation checks metadata totals, contiguous episode indices,
referenced file existence, Parquet row counts, and basic feature-shape
consistency:

```bash
uv run letools validate /data/dataset-v30
```

Deep validation additionally reads every episode table, verifies episode and
frame index columns, and checks that video durations cover all referenced
slices:

```bash
uv run letools validate /data/dataset-v30 --deep
```

Conversions run shallow validation automatically before publication. Use deep
validation for release artifacts, format changes, and correctness acceptance.
`--no-validate` skips only the conversion-time shallow gate; it does not alter
the standalone `validate` command.

Validation reports distinguish `errors` from `warnings`. `valid` is false only
when errors exist.

## 6. Compare datasets semantically

Compare metadata, tasks, normalized feature schemas, episode statistics, and
all Arrow values:

```bash
uv run letools compare /data/original /data/converted
```

Also compare encoded video packet payloads per episode and camera:

```bash
uv run letools compare /data/original /data/converted --videos
```

Skip Arrow value comparison when checking only metadata/statistics or isolating
video work:

```bash
uv run letools compare /data/original /data/converted --skip-data --videos
```

Video comparison hashes encoded packet payloads, not complete MP4 files. This
accepts valid differences in container metadata while detecting changed encoded
media. It does not decode pixels.

For conversion acceptance, use both deep validation and full comparison:

```bash
uv run letools validate /data/converted --deep
uv run letools compare /data/original /data/converted --videos
```

## 7. Run under Slurm

Plan and convert inside the allocation whose resources should be detected.
Running `plan` on a login node and converting inside Slurm produces a different
resource fingerprint and may select inappropriate parameters.

Example submission:

```bash
sbatch \
  --job-name=letools-convert \
  --cpus-per-task=8 \
  --mem=48G \
  --wrap 'cd /path/to/letools && uv run letools convert /data/v21 /data/v30 --to v3.0 --auto'
```

For an interactive allocation:

```bash
salloc --cpus-per-task=8 --mem=48G
srun --pty bash
cd /path/to/letools
uv run letools convert /data/v21 /data/v30 --to v3.0 --auto
```

The planner takes the minimum valid CPU limit visible through process affinity,
cgroup cpusets, and Slurm. It similarly respects host, cgroup, and Slurm memory
limits. Request resources from Slurm first; the planner never submits jobs or
changes the allocation.

## 8. Python API

### Fixed conversion

```python
from letools import ConversionConfig, convert

result = convert(
    "/data/dataset-v21",
    "/data/dataset-v30",
    "v3.0",
    config=ConversionConfig(
        workers=8,
        video_workers=3,
        data_file_size_mb=100,
        video_file_size_mb=200,
        overwrite=False,
        validate=True,
    ),
)

print(result.elapsed_seconds)
print(result.stages["video_execute"].elapsed_seconds)
```

`ConversionConfig.chunks_size` defaults to 1000. It controls file indices per
directory chunk in generated layouts and is currently configurable only through
the Python API.

### Plan only

```python
from letools import PerformanceOverrides, plan_conversion
from letools.planner import CalibrationOptions

plan = plan_conversion(
    "/data/dataset-v21",
    "/data/dataset-v30",
    "v3.0",
    overrides=PerformanceOverrides(workers=4),
    calibration=CalibrationOptions(
        enabled=True,
        max_seconds=10.0,
        max_read_bytes=1024**3,
        max_write_bytes=1024**3,
    ),
    use_cache=True,
)

print(plan.workers, plan.video_workers, plan.cache_hit)
```

### Plan and convert

```python
from letools import plan_and_convert

result = plan_and_convert(
    "/data/dataset-v21",
    "/data/dataset-v30",
    "v3.0",
    overwrite=False,
    validate=True,
)

print(result.plan)
print(result.conversion)
```

`plan_and_convert()` enables bounded calibration by default. `plan_conversion()`
does not enable calibration unless requested explicitly.

### Validation and comparison

```python
from letools import compare_datasets, validate_dataset

validation = validate_dataset("/data/dataset-v30", deep=True)
comparison = compare_datasets(
    "/data/dataset-v21",
    "/data/dataset-v30",
    check_data=True,
    check_videos=True,
)

if not validation.valid:
    raise RuntimeError(validation.errors)
if not comparison.equal:
    raise RuntimeError(comparison.errors)
```

## 9. Custom source plugins

A custom input format can reuse both built-in output backends by implementing
`DatasetSource`. No file registration is required when using the Python API:

```python
from pathlib import Path

import pyarrow as pa

from letools import DatasetSource, convert
from letools.model import DatasetMetadata, Episode


class MySource(DatasetSource):
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.metadata = DatasetMetadata(
            version="my-format-v1",
            fps=30,
            features={...},
            robot_type=None,
            splits={"train": "0:100"},
            total_frames=...,
            total_episodes=100,
            tasks={0: "example task"},
            info={...},
        )
        self.episodes = tuple(
            Episode(
                index=index,
                length=...,
                tasks=("example task",),
                stats={...},
                data_path=...,
                data_end=...,
                videos={},
            )
            for index in range(100)
        )

    def read_episode(self, episode: Episode) -> pa.Table:
        # Decode this format directly into one Arrow table.
        ...


source = MySource("/data/custom")
convert(source, "/data/lerobot-v30", "v3.0")
```

The ellipses represent format-specific values, so this is an interface template
rather than a runnable dataset generator. A production source must satisfy:

- episode indices are ordered and contiguous from zero;
- the dataset is nonempty;
- every table has exactly `Episode.length` rows;
- schemas are consistent and agree with `metadata.features`;
- dataset and episode frame totals agree;
- tasks and episode statistics are complete;
- `metadata.info` contains the LeRobot fields required by the target backend;
- every declared video feature has a media input for every episode;
- `data_profile()` describes logical/physical data size and shared locality;
- `media_profile()` describes input size, locality, and encoding requirements.

Override `read_episodes()` when the input can batch reads more efficiently.
Override the profile methods for any source that is not path-based Parquet.
Custom sources work directly with `convert()` and `compare_datasets()` when
passed as objects. `plan_conversion()` now uses only the format-neutral
profile methods and does not assume that source data is stored as Parquet.
Standalone validation, the CLI, and `open_dataset()` auto-detection currently
support only physical LeRobot v2.1 and v3.0 directories.

## 10. Development setup

Install test and native development groups:

```bash
uv sync --locked --group test --group native-dev
uv run pytest -q
cargo test --manifest-path native/Cargo.toml --locked
cargo clippy --manifest-path native/Cargo.toml --locked -- -D warnings
```

Build the default portable native extension into the environment:

```bash
uv run maturin develop --manifest-path native/Cargo.toml --locked
```

Native FFmpeg development requires a compatible SDK containing headers,
libraries, and pkg-config files. Scope those paths to the development command:

```bash
PKG_CONFIG_PATH=/path/to/ffmpeg/lib/pkgconfig \
LIBRARY_PATH=/path/to/ffmpeg/lib \
LD_LIBRARY_PATH=/path/to/ffmpeg/lib \
LIBCLANG_PATH=/path/to/libclang \
uv run maturin develop \
  --manifest-path native/Cargo.toml \
  --locked \
  --features video
```

Normal users should not set these variables. Release wheels carry their own
runtime dependencies and relative rpaths.

## 11. Common failures

### Source and target versions match

Direct same-version rewriting is rejected. Select the other supported version
or use a dedicated copy/repair workflow.

### Destination exists

Choose a new destination or pass `--overwrite`. Do not use `--overwrite` when
the old destination must be retained independently.

### Planner selects unexpected resources

Inspect the `resources`, `source_storage`, and `destination_storage` sections of
`letools plan` output. Run inside the intended Slurm allocation. Use
`--no-cache` to rule out a matching calibrated entry, and use explicit worker
flags only when deliberately imposing hard constraints.

### Native video capability is unavailable

Run `uv run letools doctor`. Conversion should use the PyAV fallback. If the
native wheel is missing entirely, rerun `uv sync --locked`; do not manually
combine unrelated native and Python package versions.

### Validation differs but files are not byte-identical

Use `letools compare` instead of file checksums. Equivalent Parquet row-group
layout and MP4 container metadata can differ. `--videos` checks encoded packet
payloads when media equality is required.
