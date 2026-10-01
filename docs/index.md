# LeTools

**High-performance tools for building and operating LeRobot datasets.**

LeTools converts LeRobot v2.1 and v3.0 in both directions, imports mapped HDF5
and AgileX recordings, merges same-version datasets, validates outputs, and
scales conversion from one machine to Slurm or Kubernetes. Python owns the
public dataset abstractions while coarse Rust primitives accelerate filesystem
and video operations.

The optional [letools-editor](EDITOR.md) adds task relabeling, episode/feature
deletion, and video resizing/re-encoding for both LeRobot layouts.

[:material-download: Install LeTools](INSTALLATION.md){ .md-button .md-button--primary }
[:material-console: Command reference](USAGE.md){ .md-button }

## Get started

Install the user-level command with Python 3.12 or newer and `uv`:

```bash
git clone --recurse-submodules https://github.com/XieWeikai/letools.git
cd letools
uv tool install .
letools doctor
```

Convert a substantial dataset with static resource and storage planning:

```bash
letools convert /data/dataset-v21 /data/dataset-v30 \
  --to v3.0 --auto
```

The destination is staged, validated, and published only after conversion
succeeds. Existing output is preserved unless `--overwrite` is explicit.

## Choose a workflow

<div class="grid cards" markdown>

-   :material-swap-horizontal-bold:{ .lg .middle } **Convert formats**

    ---

    Convert LeRobot v2.1 and v3.0 in either direction through one semantic
    episode model.

    [:octicons-arrow-right-24: Conversion architecture](ARCHITECTURE.md)

-   :material-tune-variant:{ .lg .middle } **Plan for the machine**

    ---

    Inspect CPU, memory, source and destination storage, then select a static
    worker and shard configuration.

    [:octicons-arrow-right-24: Static planner](PLANNER.md)

-   :material-file-tree:{ .lg .middle } **Import raw sources**

    ---

    Map HDF5 fields explicitly or synchronize timestamped AgileX recordings
    without teaching target backends about either source layout.

    [:octicons-arrow-right-24: HDF5 presets](HDF5_PRESETS.md)

-   :material-movie-cog:{ .lg .middle } **Control image encoding**

    ---

    Preserve JPEG packets by default or explicitly choose a codec, pixel
    format, frame-batch size, and encoder thread count for image sources.

    [:octicons-arrow-right-24: Video encoding](VIDEO_ENCODING.md)

-   :material-server-network:{ .lg .middle } **Use a cluster**

    ---

    Run one immutable task protocol through Local, Slurm, or Kubernetes while
    retaining retry-safe shared state and transactional publication.

    [:octicons-arrow-right-24: Distributed conversion](DISTRIBUTED.md)

-   :material-call-merge:{ .lg .middle } **Merge datasets**

    ---

    Combine same-version LeRobot datasets through a specialized streaming path
    with direct whole-file media reuse.

    [:octicons-arrow-right-24: Merge engine](MERGE.md)

-   :material-pencil:{ .lg .middle } **Edit a dataset**

    ---

    Install the optional editor to change tasks, delete episodes or features,
    and resize or re-encode existing MP4 cameras into a separate dataset.

    [:octicons-arrow-right-24: Editor installation and usage](EDITOR.md)

-   :material-stethoscope:{ .lg .middle } **Inspect and validate**

    ---

    Run structural and semantic validation, the complete Dataset Doctor suite,
    or the integrated web Visualizer.

    [:octicons-arrow-right-24: Dataset Doctor](DOCTOR.md)

</div>

## How conversion is organized

```text
CLI / Python API
       |
SourceProvider -> DatasetSource -> format-neutral episodes
                                      |
                         static plan -> backend
                                      |
                    Parquet / metadata / video
                                      |
                             validate -> publish
```

Source plugins own physical input semantics. Backends own v2.1 or v3.0 output
layout. The planner selects performance parameters but never changes dataset
meaning. This separation lets HDF5, AgileX, and future sources reuse both target
formats while keeping the normal LeRobot conversion path fast.

Merge and the optional editor have specialized physical-file plans outside
this conversion model. The editor uses Python for policy and bounded scheduling,
existing Rust primitives for file reuse/remux, and FFmpeg plus a separate Rust
reducer for encoding and exact output-pixel statistics. Its automatic resource
heuristic is independent of the conversion planner and its calibration cache.

## Measured performance

The published conversion comparison measured a 4.51× v2.1-to-v3.0 speedup on
300 episodes; see [Performance](PERFORMANCE.md) for the exact revisions,
resources and storage conditions. Editor results are a separate workload:
on a 12-episode H.264 fixture, deletion measured 0.805 s versus 29.924 s
official, while reencoding measured 34.459 s versus 30.984 s official.
Reencoding has not surpassed the tuned official tool. Codec/keyframe and
statistics policies differ; see [Editor benchmarks](EDITOR_BENCHMARK.md).

## Current boundaries

- Distributed conversion currently requires shared POSIX paths visible under
  the same absolute names on every worker.
- Cluster-wide I/O calibration and direct final-shard writers remain future
  work; the MVP composes the existing conversion and merge engines.
- The optional editor preserves the source version, writes a separate output,
  and runs on one node. It has no distributed edit or in-place mode.
- Training, robot control, and dataset upload are intentionally outside the
  project.

Continue with the [complete usage guide](USAGE.md), or read the
[architecture reference](ARCHITECTURE.md) before extending a source, backend,
planner, or scheduler boundary.
For tests, optional builds, agent skills and this site's deployment, see
[Development](DEVELOPMENT.md).
