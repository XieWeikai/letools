# Optional dataset editor

`letools-editor` edits local LeRobot **v2.1 → v2.1** and **v3.0 → v3.0** datasets.
It can change an episode's task, delete episodes, remove features, re-encode
videos, and resize camera images. Multiple edits are fused into one transaction.
Version conversion continues to use `letools convert`.

## Install

The editor is a separate Python/Rust distribution under
`packages/letools-editor`. The base install does not depend on it.

From a recursive source checkout (Rust 1.88 or newer must be installed to build
this new extension from source):

```bash
uv sync --locked
uv pip install ./packages/letools-editor
uv run --no-sync letools editor --help
```

For a direct `letools` command installed with uv tools:

```bash
uv tool install --force --with ./packages/letools-editor --with-executables-from letools-editor .
letools editor --help
letools-editor --help
```

For development, install editable with `uv pip install -e
./packages/letools-editor`, and use `uv run --no-sync` so a normal base-only
`uv sync` does not remove the optional package. The root lockfile intentionally
remains base-only; the addon has its own build configuration and Cargo lock.
This initial branch does not claim that editor wheels are already on PyPI.

FFmpeg is found from `--ffmpeg`, `LETOOLS_EDITOR_FFMPEG`, `PATH`, then the
`imageio-ffmpeg` packaged executable. No FFmpeg headers, libclang, shared-library
setup, or source-built PyAV is necessary for this addon. Only video transcoding
and pixel-stat recomputation need the executable. Existing base Rust media
primitives continue to use the installed `letools-native` capabilities.

## CLI recipes

Inspect before editing:

```bash
letools editor inspect /datasets/demo --limit 10
letools editor plan /datasets/demo /datasets/edited \
  --delete-episodes 1,4,10:15 --set-task '3=Fold the cloth'
```

`plan` is read-only: headers and metadata are read; no dataset is written, no
calibration is run, and no video is decoded. `apply` builds its own fresh plan;
running `plan` first is optional. Indices always refer to the **source**, before
deletion. A colon range is half-open: `10:15` selects 10 through 14.

```bash
# All frames of episode 3 get the replacement task.
letools editor apply /datasets/demo /datasets/relabeled \
  --set-task '3=Fold the cloth' --set-task '8=Put the cloth away'

# Or provide a JSON object, e.g. {"3": "Fold the cloth", "8": "Put it away"}.
letools editor apply /datasets/demo /datasets/relabeled --tasks-json tasks.json

# Deletions and numeric/camera feature removals can be combined.
letools editor apply /datasets/demo /datasets/filtered \
  --delete-episodes 0,5:9 \
  --remove-feature observation.images.wrist --remove-feature observation.velocity

# Re-encode all retained cameras with H.264 at exactly 224 x 224.
letools editor apply /datasets/demo /datasets/small \
  --resize 224x224 --video-codec libx264 --crf 23 --preset veryfast --workers 4

# Select particular camera features; other cameras preserve their media.
letools editor apply /datasets/demo /datasets/front-only-resized \
  --resize 224x224 --video-key observation.images.front

# MJPEG is useful for independent frames and packet-preserving later edits.
letools editor apply /datasets/demo /datasets/mjpeg \
  --video-codec mjpeg --quality 2 --pixel-format yuvj420p
```

All results are JSON, including wall time, episodes/s, frames/s, stage timings,
file reuse/remux/transcode counts, effective resources, warnings, and the old →
new episode index mapping. `--overwrite` replaces an existing *output dataset*
only after the new output validates. Source/destination overlap is rejected.
The five required system features (`timestamp`, `frame_index`, `episode_index`,
`index`, `task_index`) cannot be removed. Removing an entire video feature
removes its files, pointers, metadata, and statistics without decoding it.

| Option | Meaning |
| --- | --- |
| `--set-task ID=TEXT` | Repeatable source-episode task override; replaces all its frame tasks |
| `--tasks-json FILE` | Bulk overrides; explicit `--set-task` wins for duplicate IDs |
| `--delete-episodes IDS` | Comma-separated IDs / half-open ranges; at least one episode must remain |
| `--remove-feature KEY` | Repeatable numeric, image, or camera feature removal |
| `--video-codec` | `libx264` or `mjpeg`; omitted means no explicit transcode |
| `--resize WxH` | Exact size, default encoder libx264; aspect ratio can change |
| `--video-key KEY` | Repeatable selection for explicit transforms; default all cameras |
| `--pixel-format` | Default yuv420p for H.264, yuvj420p for MJPEG |
| `--crf` / `--preset` | H.264 quality 0–51 / speed; defaults 23 / veryfast |
| `--quality` | MJPEG quantizer 1–31 (lower is better); default 2 |
| `--workers` | Concurrent physical file jobs; automatically bounded by CPU/memory. When omitted for a transcode, the automatic planner uses eight workers on the supported 16-CPU profile. |
| `--codec-threads` | Threads per encoder; default 1 for explicit worker settings. When both `--workers` and this option are omitted for a transcode, the automatic planner uses two codec threads per job; explicit values are honored. |
| `--batch-rows` | Parquet read batch rows; default 65,536 |
| `--ffmpeg` | Explicit executable, including a user-level FFmpeg installation |
| `--overwrite` | Replace an existing valid-looking output dataset after success |

## Python API

```python
from letools_editor import EditConfig, VideoEdit, edit_dataset, plan_edit

config = EditConfig(
    task_by_episode={3: "Fold the cloth"},
    delete_episodes=frozenset({1, 4}),
    remove_features=frozenset({"observation.images.wrist"}),
    video=VideoEdit(size=(224, 224), codec="libx264", crf=23),
    workers=4,
)
plan = plan_edit("/datasets/input", "/datasets/output", config)
result = edit_dataset("/datasets/input", "/datasets/output", config)
```

## Architecture and implementation boundaries

| Component | Ownership and boundary |
| --- | --- |
| Base `letools.cli` | Lazy optional dispatch only; no editor dependency in existing conversion/merge paths |
| `letools_editor.model` | Immutable edit request, plan/result values, internal physical jobs |
| `letools_editor.engine` | Source inspection, episode/task/index mappings, splits, Arrow jobs, metadata, validation, transaction publication |
| PyArrow C++ kernels | Column projection, bounded Parquet reads/writes; no Python row loop |
| Existing `letools-native` Rust | Parallel reflink/copy and packet-preserving video split/concat; coarse calls release the GIL |
| `letools_editor.media` | Build safe subprocess argument arrays; execute one physical media job at a time |
| FFmpeg subprocess | Native decode, frame selection, scale, encode; no frames passed through Python |
| Editor Rust `_native` | Drain decoded RGB, check exact frame count, compute per-episode channel moments with the GIL released |

For a transcode with omitted concurrency options, the planner's measured default
is eight media workers and two FFmpeg codec threads per job on the 16-CPU
allocation used by the acceptance benchmark. This is intentionally limited to
the automatic path: passing either option keeps the caller's explicit choice
and the conservative existing caps. Delete/remux operations retain the normal
eight-worker automatic default because they do not create codec workers.

The editor is a specialized physical-layout engine, not a new `DatasetSource`
provider or conversion backend. It reuses core readers and validators without
changing their contracts. It is single-node and does not introduce new
distributed scheduling semantics.

Execution is: **inspect → plan physical files → reuse unchanged files → rewrite
affected Parquet → process affected video + statistics → rebuild metadata →
validate → publish**. Parallelism is inside the file stages. Keeping a single
bounded pool at a time prevents simultaneous Arrow and encoder pools from
oversubscribing the allocation. The heuristic sees CPU affinity, cgroups,
Slurm CPU/memory limits, file counts, and image sizes; it caps requested workers.
It is a conservative first version, not the conversion planner's calibrated
near-optimality guarantee. Memory is bounded per Parquet batch and RGB frame;
the full episode manifest remains in memory.

### Avoiding unnecessary work

Task-only edits rewrite affected data shards and metadata; all videos are
byte-preserving reflinks/copies. Deleting a v2.1 episode drops its files; surviving
media is reusable. Dropping a camera never opens its video files. Dropping a
numeric feature projects it out at read time, avoiding decompression of that
column. A v3 Parquet shard is read once rather than once per episode.

For v3 partial-video deletion, MJPEG's independent frames permit packet-preserving
compaction. Existing native split/concat are reused; temporary slices live only
inside staging. H.264 is compacted without decoding when a bounded packet audit
proves all of the following: one frame per packet, PTS equals DTS, timestamps
are contiguous, there are no B/P inter-frame dependencies, and every retained
range starts with an IDR packet. This preserves the encoded packet payloads and
pixel values. If any proof condition fails, the editor fails closed to H.264
CRF 0 re-encoding only for the affected physical files, recomputes their pixel
stats, and reports the fallback in `plan.warnings`. Fully retained files in the
same camera are reused with their original packets and statistics.
Explicit transcoding uses the requested quality instead. This fallback can cost
substantially more than metadata-only editing and may change color conversion.
Deleted frames are physically absent from the published output, not merely
hidden by episode pointers.

New H.264 output has no B-frames and forces an IDR at every episode boundary.
That preserves the existing core's packet-remux conversion assumptions for
subsequent v3 → v2.1 conversion. FPS, episode lengths, and per-episode timestamps
remain unchanged.

### Metadata and statistics

Episode IDs and global frame IDs are compacted after deletion. Existing task IDs
are retained (including unused task names) so unrelated frame files need no
rewrite; new task strings are appended and identical strings reuse an ID.
Untouched per-frame multitask labels stay intact. Named `start:end` splits are
mapped to the surviving episodes. Non-standard episode metadata columns survive;
standard pointers/statistics are rebuilt. Arbitrary root files such as README,
Hub upload bookkeeping, or user sidecars are not copied to the new dataset.

The task Parquet in v3 preserves the official pandas task-text index as well as
its Arrow-readable physical column, so official loaders return task strings.
Resizing preserves the feature's declared channel-first/channel-last axis order
and updates both image dimensions and video header metadata.

Unchanged feature statistics are preserved. Index moments are shifted exactly;
task/episode constant moments are updated. Dropped feature statistics disappear.
After a video transcode, the **encoded output is decoded again** and all its
pixels contribute to RGB min/max/mean/population-std, normalized to [0, 1] with
shape [3, 1, 1] and `count=[episode_frames]`. Stale video quantiles are omitted.
Dataset aggregates are rebuilt. Rust owns this streaming second pass, retaining
one RGB frame per worker; it validates both truncated and extra frame streams.

### Safety and current limits

The source must remain immutable while an edit runs. Readers see a new output
only after validation. Files are reflinked/copied, never hard-linked. A sibling
exclusive lock prevents two editor writers targeting the same output. A failed
edit removes staging and keeps the old output. Overwrite uses a backup rename
and rollback; it can briefly make the output path absent and does not promise
power-loss durability. A forcibly killed process may leave staging/lock files;
inspect these before manually removing them.

Only fixed-FPS RGB MP4 video with known frame counts and frame-aligned episode
ranges is supported for media editing. Unknown codecs may be preserved without
transcoding; new encoders are initially limited to libx264/MJPEG. Transcoding
depth or audio video is rejected rather than silently corrupting it. Embedded
image features can be removed but are not resized. There is no in-place mode,
FPS change, arbitrary frame trimming, image-feature-to-video conversion, UI,
or Hub upload in this MVP. The built-in validation checks structural integrity;
the acceptance suite separately checks rows, decoded pixels, packet payloads,
statistics, rollback, and normal core operations.

## Acceptance and performance

See [Editor acceptance and benchmark](EDITOR_BENCHMARK.md) for real XVLA sample
measurements, official v2.1/v3 metadata and dataset loader checks, failure and
roundtrip coverage, baseline conversion comparisons, and reproduction commands.
Run `scripts/test_editor.sbatch` through Slurm on cluster installations; a local
developer can run its pytest command directly on an ordinary workstation.

## References

The operation semantics were cross-checked against the official
[LeRobot dataset tools](https://huggingface.co/docs/lerobot/using_dataset_tools)
and its [implementation](https://github.com/huggingface/lerobot/blob/main/src/lerobot/datasets/dataset_tools.py).
The editor implementation is original and does not vendor those tools or
require the training/runtime dependencies of official LeRobot.
