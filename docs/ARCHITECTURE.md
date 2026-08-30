# Architecture

## 1. System purpose

`letools` converts datasets through a format-neutral episode model. A source
plugin maps physical files into that model, and a backend maps the model into a
target LeRobot layout. Conversion policy and format semantics stay in Python;
coarse Rust operations accelerate filesystem and FFmpeg work without exposing
native objects across the language boundary.

The implemented product boundary is intentionally narrow:

- read LeRobot v2.1 and v3.0 datasets;
- write LeRobot v2.1 and v3.0 datasets;
- accept a custom `DatasetSource` through the Python API;
- validate one dataset and compare two datasets semantically;
- choose a static conversion configuration before execution;
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
                | VideoSlice / Arrow Table      |
                +---------------+---------------+
                                ^
                  reads         |         consumes
           +--------------------+--------------------+
           |                                         |
           v                                         v
   DatasetSource plugins                        output backends
   - LeRobotV21Source                          - LeRobotV21Backend
   - LeRobotV30Source                          - LeRobotV30Backend
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
and start/end seconds. `FrameSequence` exposes encoded image bytes in batches,
which lets HDF5 and image-backed sources avoid one plugin callback per frame.
Encoded packets, decoded frames, and FFmpeg contexts remain inside the media
primitive that owns the whole operation.

## 4. Module ownership and boundaries

| Module | Owns | Must not own |
| --- | --- | --- |
| `cli.py` | Argument parsing, exit status, JSON serialization | Format logic, planning policy |
| `conversion.py` | Version dispatch, staging, validation gate, publication, stage lifecycle | File-layout details, worker selection |
| `conversion_types.py` | Explicit execution configuration and result types | Resource discovery or heuristics |
| `model.py` | Version-neutral dataset and episode contracts | Filesystem parsing or output layout |
| `plugins/base.py` | `DatasetSource` read protocol | Target writing |
| `plugins/lerobot.py` | v2.1/v3.0 metadata parsing and logical slicing | Target writing and concurrency policy |
| `backends/base.py` | Backend write protocol | Source-format parsing |
| `backends/v21.py` | v2.1 paths, metadata, per-episode Parquet/video output | v3 layout parsing |
| `backends/v30.py` | v3 grouping, offsets, metadata, aggregate stats | v2 layout parsing |
| `_arrow.py` | Canonical schemas, casts, safe feature-shape normalization | Dataset traversal policy |
| `_video.py` | Media dispatch, packet remux, frame decode/encode, and native fallback | Source parsing or episode metadata policy |
| `_stats.py` | Vectorized dataset-stat aggregation and flattening | Physical metadata layout |
| `_io.py` | Small JSON/JSONL write primitives | Conversion orchestration |
| `_native.py` | Capability detection and narrow PyO3 wrappers | Silent semantic differences |
| `planner/*` | Static performance choices and supporting evidence | Conversion semantics or runtime adaptation |
| `telemetry.py` | Thread-safe stage aggregation | Optimization decisions |
| `validation.py` | Structural checks and semantic comparison | Repair or mutation |
| `doctor.py` | Installed-provider report | Installation or environment mutation |
| `native/` | Parallel file primitives and optional FFmpeg hot paths | Python model or planner policy |

## 5. Source plugin contract

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
A custom source is passed as an object to `convert()` or `plan_conversion()`;
there is currently no CLI registration mechanism.

Source implementations must provide stable episode order, contiguous indices
starting at zero, accurate lengths and totals, consistent Arrow schemas, and a
media input and profile for every declared video key. They read source data
only and must not write the destination.

## 6. Backend contract

A backend consumes a `DatasetSource`, a staging destination,
`ConversionConfig`, and `StageRecorder`. It owns the complete target layout and
records metadata, data, video, and finalization stages.

Backends are selected centrally by `convert()` from the requested target
version. Custom backend injection is not a public API yet. This keeps the
supported output-format surface explicit and makes validation behavior
predictable.

## 7. Conversion coordinator

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

## 8. v2.1 to v3.0 pipeline

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

## 9. v3.0 to v2.1 pipeline

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

## 10. Planner interaction

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

## 11. Native acceleration boundary

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

## 12. Validation boundary

Conversion's built-in gate is shallow validation: metadata totals, contiguous
episode indices, referenced files, Parquet row counts, and basic schema-shape
consistency. Deep validation additionally reads every episode, checks episode
and frame indices, and verifies that video files cover referenced durations.

Semantic comparison checks dataset metadata, tasks, normalized feature schemas,
episode lengths/statistics, and by default all Arrow values. Optional video
comparison hashes encoded packet payloads per episode and camera. It does not
require byte-identical Parquet files or MP4 containers because both formats
permit semantically equivalent physical layouts.

## 13. Extension rules

When adding a source format, prefer a new `DatasetSource` that maps it into the
existing model. Do not teach both LeRobot backends how to parse that format.
The plugin owns source-specific size accounting and locality; the planner and
backends consume only source profiles. Batch frame reads prevent high-latency
sources from forcing one Python callback per encoded frame.
Promote a repeated operation into a reusable primitive only when it removes
meaningful duplication or creates a measurable hot-path boundary.

When adding an output format, add a backend and explicit coordinator dispatch;
define its validator expectations before exposing it publicly. Planner policy
should depend on format-neutral profiles and execution parameters, not inspect
backend private objects.

Any performance change must preserve deep validation and bidirectional semantic
comparison. Follow the [self-improvement protocol](../self-improve/PROTOCOL.md)
for profiling, resource accounting, acceptance, and reporting.
