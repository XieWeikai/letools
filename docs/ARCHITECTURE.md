# Architecture

## 1. System purpose

`letools` converts datasets through a format-neutral episode model. A source
plugin maps physical files into that model, and a backend maps the model into a
target LeRobot layout. Conversion policy and format semantics stay in Python;
coarse Rust operations accelerate filesystem and FFmpeg work without exposing
native objects across the language boundary.

The implemented product boundary is intentionally narrow:

- read LeRobot v2.1 and v3.0 datasets;
- read one-file-per-episode HDF5 datasets through an explicit mapping;
- read timestamped AgileX episode directories with an explicit instruction;
- write LeRobot v2.1 and v3.0 datasets;
- accept a custom `DatasetSource` through the Python API;
- validate one dataset and compare two datasets semantically;
- choose a static conversion configuration before execution;
- merge multiple physical same-version LeRobot datasets through a specialized path;
- diagnose, repair, curate, and gate LeRobot datasets through pinned Doctor;
- visualize local or Hub datasets through the pinned Hugging Face web application;
- report environment capabilities and conversion stage timings.

It is not a training, robot-control, dataset-upload, distributed-conversion, or
runtime autoscaling system. The CLI does not yet discover third-party source
plugins or third-party backends by entry point.

## 2. Component view

```text
                         user-facing layer
              +-----------------------------------+
              | CLI (cli.py) | Python API (__init__) |
              +-----------------+-----------------+
                                |
              source provider -> typed source config
              bootstrap parse -> registry -> final parse
                                |
                   HDF5 tools: inspect -> preset
                                |
                +---------------+---------------+
                |                               |
                v                               v
       conversion coordinator             static planner
         (conversion.py)               (planner/api.py)
                |                    inspect -> heuristic
                |                    -> cache/calibration
                |                               |
                +---------------+---------------+
                                | ConversionConfig
                                v
                +-------------------------------+
                | format-neutral episode model |
                | DatasetMetadata / Episode /   |
                | MediaInput / Arrow Table      |
                +---------------+---------------+
                                ^
                  reads         |         consumes
           +--------------------+--------------------+
           |                                         |
           v                                         v
   DatasetSource plugins                        output backends
   - LeRobotV21Source                          - LeRobotV21Backend
   - LeRobotV30Source                          - LeRobotV30Backend
   - HDF5Source                                - LeRobotV21Backend
   - AgileXSource                              - LeRobotV30Backend
   - custom Python source                      - LeRobotV30Backend
           |                                         |
           +--------------------+--------------------+
                                |
                                v
          Arrow / Parquet | metadata / stats | video operations
                                |
                       capability dispatch
                     +----------+----------+
                     |                     |
                     v                     v
                  PyAV                  Rust/PyO3
              portable fallback        native hot path
```

The dependency direction matters. Plugins and backends depend on the shared
model and reusable primitives. The shared model never depends on a physical
LeRobot version. The planner produces `ConversionConfig`; it does not call
backend internals or change dataset semantics.

Doctor, Visualizer, and the merge engine are intentionally outside this
diagram's conversion pipeline. Doctor consumes physical datasets through its
own diagnostic model. Visualizer reads physical files through Hub-compatible
HTTP and its browser-side Parquet/video stack. Neither is a `DatasetSource`, a
backend, or a planner consumer.

The merge engine is also outside this diagram's conversion pipeline.
Its permanently fixed LeRobot-to-same-LeRobot contract does not benefit from
source plugins or generic backends and can exploit physical file identity.

## 3. Shared data model

The objects in `src/letools/model.py` are the contract between readers and
writers.

### `DatasetMetadata`

Holds dataset-wide facts: source version, FPS, feature descriptions, robot
type, splits, frame and episode totals, the task table, and the original
`info.json` object. `video_keys` is derived from features whose dtype is
`video`.

### `Episode`

Describes one logical episode without requiring one physical file per episode:

- `index`, `length`, `tasks`, and per-feature `stats` describe semantics;
- `data_path`, `data_start`, and `data_end` are compatibility references
  used by path-based sources;
- `videos` maps each video feature to a `VideoSlice` or `FrameSequence`.

For v2.1, an episode normally owns one Parquet file and each video slice begins
at zero. For v3.0, multiple episodes can refer to one Parquet/video file using
row and timestamp ranges. Backends therefore do not need version-specific read
logic.

### Arrow tables and media inputs

`DatasetSource.read_episode()` returns a `pyarrow.Table` containing exactly the
episode's rows. Arrow is the in-process point-batch representation; individual
Python row objects are deliberately avoided. `VideoSlice` carries only a path
and start/end seconds. `FrameSequence` exposes encoded image bytes through both
random-access batches and a sequential `iter_batches()` contract. The default
iterator delegates to random access, while a source may override it to retain
an expensive resource for one sequence. The HDF5 plugin uses that hook to open
an episode file once per camera encoding job and bounds the long-lived HDF5
metadata cache to avoid multiplying the library's default cache by the video
worker count. Batch elements implement the Python buffer protocol: HDF5 yields
memoryviews that retain the vlen NumPy allocations through packet consumption,
avoiding an intermediate full-payload `bytes` copy. Encoded packets, decoded
frames, HDF5 handles, and FFmpeg contexts remain inside the primitive that owns
the whole operation. `FrameSequence.worker_isolation` is a source capability,
not a worker-count policy. Its default is `thread`; a pickleable source backed
by a native process-wide lock may opt into `process`. The HDF5 sequence does so
because h5py serializes HDF5 C API calls inside one process.

## 4. Module ownership and boundaries

| Module | Owns | Must not own |
| --- | --- | --- |
| `cli.py` | Argument parsing, exit status, JSON serialization | Format logic, planning policy |
| `source_providers/base.py` | CLI source-factory contract and frontend context | Dataset parsing or conversion execution |
| `source_providers/registry.py` | Provider names, aliases, and conflict-free lookup | Source detection or plugin semantics |
| `source_providers/{lerobot,hdf5,agilex}.py` | Provider-specific arguments, immutable config, and source construction | Episode reads, planning, or target writing |
| `conversion.py` | Version dispatch, staging, validation gate, publication, stage lifecycle | File-layout details, worker selection |
| `conversion_types.py` | Explicit execution configuration and result types | Resource discovery or heuristics |
| `merge.py` | Same-version manifest, compatibility, autotune, streaming rewrites, metadata, publication | New source formats or version conversion |
| `merge_types.py` | Immutable merge plans, contributions, and results | Execution or storage inspection |
| `model.py` | Version-neutral dataset and episode contracts | Filesystem parsing or output layout |
| `plugins/base.py` | `DatasetSource` read protocol | Target writing |
| `plugins/lerobot.py` | v2.1/v3.0 metadata parsing and logical slicing | Target writing and concurrency policy |
| `plugins/hdf5.py` | Explicit HDF5 mapping, schema scan, Arrow reads, and frame batches | Target layout or robot-specific presets |
| `plugins/agilex.py` | AgileX directory parsing, timestamp alignment, task injection, and JPEG frame batches | Target layout or backend concurrency policy |
| `tools/hdf5_preset.py` | Versioned preset JSON, user-store lookup, read-only HDF5 inventory | Robot semantic decisions or conversion execution |
| `tools/hdf5_tui.py` | Interactive mapping authoring and stored-preset selection | Source reading, backend policy, or silent inference |
| `backends/base.py` | Backend write protocol | Source-format parsing |
| `backends/v21.py` | v2.1 paths, metadata, per-episode Parquet/video output | v3 layout parsing |
| `backends/v30.py` | v3 grouping, offsets, metadata, aggregate stats | v2 layout parsing |
| `_arrow.py` | Canonical schemas, casts, safe feature-shape normalization | Dataset traversal policy |
| `_media_executor.py` | Pickleable media jobs and thread/spawn executor selection | Source parsing, target grouping, or worker-count planning |
| `_video.py` | Media dispatch, packet remux, frame decode/encode, and native fallback | Source parsing or episode metadata policy |
| `_stats.py` | Vectorized dataset-stat aggregation and flattening | Physical metadata layout |
| `_io.py` | Small JSON/JSONL write primitives | Conversion orchestration |
| `_native.py` | Capability detection and narrow PyO3 wrappers | Silent semantic differences |
| `planner/*` | Static performance choices and supporting evidence | Conversion semantics or runtime adaptation |
| `telemetry.py` | Thread-safe stage aggregation | Optimization decisions |
| `validation.py` | Structural checks and semantic comparison | Repair or mutation |
| `doctor.py` | Installed-provider report | Installation or environment mutation |
| `doctor_external.py` | Exact upstream Doctor CLI delegation | Reimplemented checks or repair policy |
| `external.py` | Checkout/wheel resource resolution and provenance | Upstream mutation or execution policy |
| `visualizer.py` | App cache, patch/install fingerprints, target adapters, process lifecycle | Browser feature implementation or conversion |
| `visualizer_server.py` | Confined local Hub routes, Range I/O, embedded Doctor report | General file serving or dataset rewriting |
| `third_party/external/` | Exact upstream commits through Git submodules | LeTools-owned edits |
| `third_party/patches/` | Reviewable transformations applied to cache copies | Runtime state or generated dependencies |
| `native/` | Parallel file primitives and optional FFmpeg hot paths | Python model or planner policy |

## 5. Source provider contract

`SourceProvider` is a frontend factory, not a data reader. It exists because
different source families require different construction inputs. Each provider
owns three operations:

```python
def add_arguments(parser: argparse.ArgumentParser) -> None: ...
def config_from_args(args, context) -> SourceConfig: ...
def open(source: Path, config: SourceConfig) -> DatasetSource: ...
```

The CLI first parses only `--source-format` using a bootstrap parser. It looks
up that name in `SourceProviderRegistry`, rebuilds the final parser with only
the selected provider's arguments, creates an immutable typed config, and then
constructs the source. The original `--preset NAME` shorthand still selects
HDF5 when `--source-format` is omitted. Other cross-provider option combinations
are rejected by `argparse` because the unrelated options were never registered.

Built-in configurations are `LeRobotSourceConfig`, `HDF5SourceConfig`, and
`AgileXSourceConfig`. Configs contain resolved constructor inputs rather than
the complete parsed namespace. In particular, `HDF5SourceProvider` owns preset
lookup and optional TUI selection and produces a config containing an
`HDF5Mapping`; `AgileXSourceProvider` validates and normalizes instruction, FPS,
and robot type before constructing `AgileXSource`.

Providers must not traverse episodes, parse frame payloads, profile resources,
choose workers, call planners, or write targets. Those responsibilities remain
with `DatasetSource`, planner, coordinator, primitives, and backends. Once
`create()` returns, downstream code sees only `DatasetSource`, so adding a
provider does not add type branches to the conversion path.

The built-in registry is deterministic and rejects duplicate names or aliases.
Applications may register a provider in-process. The installed CLI does not yet
discover external providers through Python package entry points.

## 6. Source plugin contract

`DatasetSource` exposes three attributes and one required data method:

```python
root: pathlib.Path
metadata: DatasetMetadata
episodes: tuple[Episode, ...]

def read_episode(self, episode: Episode) -> pyarrow.Table: ...
```

`read_episodes()` has a correct sequential default and may be overridden to
batch shared-file reads. The built-in v3 source instead keeps a thread-local
table cache: consecutive episodes in the same worker and Parquet shard reuse
one loaded table.

Backends and planner code access physical resources through three capability
methods:

```python
def data_profile(self, episode: Episode) -> EpisodeDataProfile: ...
def media_input(self, episode: Episode, key: str) -> MediaInput: ...
def media_profile(self, episode: Episode, key: str) -> MediaProfile: ...
```

`data_profile()` separates one episode's logical output contribution from a
possibly shared resource's logical and physical sizes. Both profiles carry a
stable locality key so consumers can group work without interpreting paths or
container internals. The default implementations adapt path-based Parquet and
MP4 sources and cache resource inspection. Other formats override them.

`open_dataset()` reads `meta/info.json` and dispatches only `v2.1` and `v3.0`.
The Python API passes `HDF5Source`, `AgileXSource`, and custom sources as
objects. The CLI uses explicit source selection to construct HDF5 and AgileX
plugins; it does not add raw-format guessing to `open_dataset()`.

Source implementations must provide stable episode order, contiguous indices
starting at zero, accurate lengths and totals, consistent Arrow schemas, and a
media input and profile for every declared video key. They read source data
only and must not write the destination.

## 7. Backend contract

A backend consumes a `DatasetSource`, a staging destination,
`ConversionConfig`, and `StageRecorder`. It owns the complete target layout and
records metadata, data, video, and finalization stages.

Backends are selected centrally by `convert()` from the requested target
version. Custom backend injection is not a public API yet. This keeps the
supported output-format surface explicit and makes validation behavior
predictable.

## 8. Conversion coordinator

The conversion coordinator implements the lifecycle common to both formats:

```text
open source
    -> normalize and reject same-version targets
    -> reject an existing destination unless overwrite is allowed
    -> create a unique sibling staging path
    -> backend writes complete target into staging
    -> optional built-in shallow validation on staging
    -> remove old destination only after successful staging/validation
    -> rename staging to destination
```

If backend execution or validation fails, the staging directory is removed and
the exception is returned to the caller. With `--overwrite`, the existing
destination remains untouched until the new staging dataset has passed the
validation gate; publication then removes it and renames the sibling staging
directory.

### Recorded stages

`ConversionResult.stages` can contain:

- `source_open`
- `staging_prepare`
- `metadata_prepare`
- `data_plan`
- `data_execute`
- `video_plan`
- `video_execute`
- `metadata_finalize`
- `conversion_validate`
- `publish_cleanup`

Stage metrics contain elapsed seconds and optional task/input/output counts.
They diagnose internal phase costs; external wall time remains the authoritative
end-to-end benchmark because it also includes process startup and serialization.

## 9. Specialized same-version merge engine

```text
physical LeRobot roots
       |
       v
strict manifest + episode/frame/task maps
       |
       +-- real bounded calibration -> immutable MergePlan + cache
       |
       +-- Parquet RecordBatch stream -> replace 3 system columns -> Parquet
       |
       +-- complete MP4 paths -> Rust bounded reflink-or-copy pool
       |
       v
metadata rebuild -> deep validation -> atomic publication
```

Merge supports only v2.1-to-v2.1 and v3.0-to-v3.0 concatenation. It does not
instantiate `SourceProvider`, call a conversion backend, remux MP4 packets, or
create an intermediate dataset. This duplication of a small amount of layout
logic is deliberate: the narrow operation can preserve v3 shard boundaries and
copy media as complete files, while conversion must handle arbitrary logical
sources and target grouping.

Python owns manifest semantics, PyArrow streaming, calibration, metadata, and
the transaction. Rust receives the complete list of media path pairs once,
releases the GIL, creates an explicitly bounded Rayon pool, tries Linux
`FICLONE`, and falls back to `std::fs::copy`. No FFmpeg library participates.

Only `episode_index`, global `index`, and `task_index` are replaced in Arrow
batches. All feature arrays remain Arrow-native. `parquet_batch_rows` and a
half-allocation memory budget bound in-flight decoded data. v2.1 emits one data
and media path per output episode. v3.0 preserves each input data/video resource
as one output resource and rewrites episode resource references and global
offsets.

The merge planner is separate from `planner/*`. It has no runtime controller and
never changes a plan after publication begins. See [MERGE.md](MERGE.md).

## 10. v2.1 to v3.0 pipeline

```text
v2.1 JSON/JSONL metadata + per-episode files
    -> LeRobotV21Source
    -> Episode objects and Arrow tables
    -> choose Parquet groups by uncompressed size
    -> canonicalize schemas and concatenate episode tables
    -> choose video groups per camera by physical size
    -> remux encoded streams into grouped MP4 files
    -> write episode row offsets and video timestamp offsets
    -> aggregate dataset statistics
    -> write v3 metadata and publish
```

Video concatenation is remuxing, not decoding and re-encoding. Packet payloads
are preserved while valid MP4 container metadata and timestamps may differ.
`data_file_size_mb` and `video_file_size_mb` influence group boundaries and
therefore only have target-layout meaning for v3 output.

## 11. v3.0 to v2.1 pipeline

```text
v3 Parquet metadata + grouped data/video shards
    -> LeRobotV30Source
    -> row/timestamp ranges represented as Episodes
    -> group work by shared source Parquet file
    -> slice and write one Parquet file per episode
    -> group video work by shared source MP4
    -> remux each timestamp range to an episode MP4
    -> write v2.1 info/tasks/episodes/stats JSONL
    -> publish
```

Grouping by shared input avoids reopening a v3 shard once for every contained
episode. v2.1 path fan-out is controlled by `chunks_size`; v3 target-size
parameters are absent for this direction.

## 12. HDF5 to LeRobot pipeline

```text
explicit HDF5Mapping + one HDF5 file per episode
explicit JSON preset or Python mapping
    -> construct HDF5Mapping
    -> metadata/schema scan and frame-count validation
    -> numeric datasets become fixed-shape Arrow columns
    -> canonical timestamp/index/task columns are generated
    -> variable-length JPEG values become HDF5FrameSequence batches
    -> backend media jobs use spawned workers to overlap independent HDF5 reads
    -> v2.1 backend: one Parquet/video file per episode
       or v3 backend: size-grouped Parquet/video shards
    -> JPEG payloads are timestamped and muxed directly into MJPEG MP4 streams
       (other format/codec combinations use PyAV decode/encode)
    -> target metadata records actual encoding settings
```

The source never guesses robot semantics. Numeric and video keys, target names,
FPS, dimensions, task source/default, and robot type come from `HDF5Mapping`.
The MVP assumes each matched file is exactly one episode and all mapped arrays
share their first dimension. It computes numeric statistics during the metadata
scan but does not decode camera pixels for image statistics.

Canonical `timestamp`, `frame_index`, `episode_index`, global `index`, and
`task_index` columns are generated. Timestamp is `frame_index / fps`; a source
timestamp can be preserved under another explicitly mapped target key. This is
a mechanical base policy, not an XVLA preset or a decision about joint names,
field selection, or final task metadata.

The `tools` package sits before this pipeline. Its scanner reads one episode's
dataset inventory and encoded-image dimensions; its TUI records user choices in
a portable JSON preset. Loading a preset is a pure adapter from JSON to
`HDF5Mapping`. All-episode validation remains owned by `HDF5Source`, and target
metadata layout remains owned by the backend. Consequently, the authoring tool
is outside timed conversion stages and adds no per-frame or per-episode overhead.

## 13. AgileX to LeRobot pipeline

```text
episode directories with timestamped JSON and JPEG files
    -> natural-sort episodeN directories and timestamp-sort each stream
    -> retain the newest common frame count across the three cameras
    -> use left-camera timestamps as the synchronization clock
    -> zero-order hold each joint stream at each camera timestamp
    -> puppet left/right positions become observation.state
    -> master left/right positions become action
    -> generate canonical timestamp/index/task columns
    -> expose JPEG paths as bounded AgileXFrameSequence batches
    -> v2.1 or v3.0 backend writes the standard target layout
```

`AgileXSource` owns these robot-specific semantics. The CLI requires a fixed,
non-empty instruction rather than inferring one from a directory name. The
source publishes that instruction as task 0, assigns it to every episode, and
fills every frame's `task_index` with zero. Backends consequently remain unaware
of AgileX paths, timestamp rules, and instruction policy.

Joint JSON is parsed once during source construction, retained as dense NumPy
arrays, and exposed as Arrow fixed-size list columns. JPEG bytes remain lazy
until media execution. The default MJPEG target path muxes original JPEG packet
payloads without pixel decode; an explicitly selected compact codec uses the
shared PyAV decode/encode path.

## 14. Planner interaction

The planner is optional and sits before the coordinator:

```text
source + destination + target + optional hard overrides
    -> inspect effective resources, dataset shape, and both storage endpoints
    -> choose a safe heuristic plan
    -> reuse a matching calibrated cache entry, or run bounded real work
    -> ConversionPlan
    -> ConversionPlan.conversion_config()
    -> unchanged convert() lifecycle
```

`letools plan` stops after producing the plan. `letools convert --auto` calls
the same planner and then calls `convert()`. A cache hit skips bounded
calibration, not source/dataset/storage inspection. See [PLANNER.md](PLANNER.md)
for exact rules and limitations.

## 15. Native acceleration boundary

`_native.py` performs capability-based dispatch. Filesystem operations have a
portable Python implementation. Video concat, split, and packet digests use
the Rust implementation when the installed native wheel exposes those symbols;
otherwise `_video.py` uses PyAV.

The Rust boundary is deliberately coarse:

```text
Python: paths + ordered timestamp ranges
Rust: open -> demux -> remux/hash -> trailer -> close
Python: success/error + output paths/digests
```

No `AVPacket`, `AVFrame`, stream, codec, or allocator crosses the boundary.
This prevents per-packet Python calls and avoids coupling PyAV's bundled FFmpeg
ABI to the native wheel's separately bundled FFmpeg ABI.

Frame-sequence inputs take a separate PyAV path because their source contains
still images rather than an encoded video stream. The plugin returns batches of
encoded images; the media writer decodes those images and feeds a single output
encoder per shard. `video_workers` limits concurrent output jobs, while
`VideoEncodingConfig.codec_threads` limits threads inside each encoder. Existing
`VideoSlice` groups are dispatched directly to the unchanged remux primitives.
When encoding occurs, the backend records the selected codec, pixel format, FPS,
and lack of audio in the target video feature metadata. Remux-only conversions
preserve the source codec metadata unchanged.

The backend owns media-job construction and `_media_executor.py` owns executor
selection. Thread-safe `FrameSequence` and every `VideoSlice` workload retain
the thread/native path. A homogeneous set of process-isolated sequences uses a
`spawn` pool with the same `video_workers` bound. Spawn startup is paid once per
media phase; it cannot inherit an HDF5 handle, Arrow thread state, or FFmpeg
context from the coordinator. V3 process-isolated jobs from all cameras share
that pool, whereas the LeRobot `VideoSlice` path preserves its measured
one-camera-at-a-time I/O topology.

FrameSequence output placement follows target granularity. V2.1 first muxes its
many small episode files on node-local storage, then copies them into the hidden
dataset staging tree. V3 writes its larger grouped shards directly into that
tree and removes a partial shard on failure. In both cases the conversion
coordinator remains the only dataset publication boundary.

## 16. Validation boundary

Conversion's built-in gate is shallow validation: metadata totals, contiguous
episode indices, referenced files, Parquet row counts, and basic schema-shape
consistency. Deep validation additionally reads every episode, checks episode
and frame indices, and verifies that video files cover referenced durations.

Semantic comparison checks dataset metadata, tasks, normalized feature schemas,
episode lengths/statistics, and by default all Arrow values. Optional video
comparison hashes encoded packet payloads per episode and camera. It does not
require byte-identical Parquet files or MP4 containers because both formats
permit semantically equivalent physical layouts.

## 17. Extension rules

When adding a source format, prefer a new `DatasetSource` that maps it into the
existing model. Do not teach both LeRobot backends how to parse that format.
The plugin owns source-specific size accounting and locality; the planner and
backends consume only source profiles. Batch frame reads prevent high-latency
sources from forcing one Python callback per encoded frame.

When that format needs CLI support, add an immutable source config and a
`SourceProvider`, then register the provider. Do not add source-specific options
or a source-type conditional to `cli.py`. Python-only custom sources do not need
a provider: callers may continue to construct and pass `DatasetSource` objects
directly.
Promote a repeated operation into a reusable primitive only when it removes
meaningful duplication or creates a measurable hot-path boundary.

When adding an output format, add a backend and explicit coordinator dispatch;
define its validator expectations before exposing it publicly. Planner policy
should depend on format-neutral profiles and execution parameters, not inspect
backend private objects.

Any performance change must preserve deep validation and bidirectional semantic
comparison. Follow the [self-improvement protocol](../self-improve/PROTOCOL.md)
for profiling, resource accounting, acceptance, and reporting.

## 18. Code documentation conventions

Every production Python module states its ownership boundary in a module
docstring. Public classes, functions, abstract methods, plugin capabilities, and
planner evidence types document their contract and important invariants. Rust
entry points document the GIL boundary, atomic publication, and packet/timestamp
semantics. Benchmark and release scripts state which setup work is excluded from
measurement.

Inline comments are reserved for constraints that are not clear from types and
control flow, such as FFmpeg codec-tag reset, timestamp rebasing, cache
compatibility, or a deliberate compatibility adapter. Comments should explain
why a boundary or invariant exists; they should not restate assignments or loop
syntax. When behavior changes, the corresponding docstring and the user-facing
architecture/usage document are reviewed in the same commit.

## 19. External application boundary

```text
                       letools CLI
                    /               \
          doctor environment       visualizer setup/serve
                  |                         |
        letools provider report      provenance + fingerprint
                  |                         |
      dataset args delegated          pinned Visualizer submodule
                  |                         |
       pinned Doctor package         cache copy + reviewed patch
                  |                    /          |          \
        physical/Hub dataset    local HTTP   annotation API   Next.js
                                  |                |             |
                             local files      v3 rewrites     browser UI
                                  |
                           Doctor HTML/JSON
```

Git submodule links are the source of truth for checked-out upstream commits;
`third_party/UPSTREAM.toml` mirrors repository, commit, retrieval date, license,
and integration purpose for packaging and diagnostics. The Python wheel includes
the Doctor package plus Visualizer source, lockfile, patch, provenance, and
license. An installed wheel is never changed: Visualizer preparation copies
source into an XDG cache and atomically replaces that copy only when its
fingerprint changes.

Doctor's CLI owns dataset parsing, checks, output, repair semantics, and exit
codes. LeTools reserves only the no-argument/environment command and otherwise
delegates the raw argument vector. This prevents a second parser or a partial
feature mirror from drifting from upstream.

Visualizer's UI and annotation implementation remain upstream. LeTools owns the
missing integration concerns: deterministic setup, local-path identity, strict
root confinement, HTTP byte ranges, embedded local Doctor, configuration, and
child-process cleanup. Its one source patch makes the Doctor origin configurable
so local targets can use the bundled Doctor rather than a remote Space.

These integrations do not import conversion coordinator, planner, backends, or
Rust media primitives. Consequently their presence adds installation size and
optional runtime dependencies, but no branches or overhead to conversion and
merge hot paths. See [external policy](THIRD_PARTY.md), [Doctor](DOCTOR.md), and
[Visualizer](VISUALIZER.md) for update and operational details.
