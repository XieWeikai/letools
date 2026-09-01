# LeTools

**High-performance tools for building and operating LeRobot datasets.**

LeTools converts LeRobot v2.1 and v3.0 in both directions, imports mapped HDF5
and AgileX recordings, merges same-version datasets, validates outputs, and
scales conversion from one machine to Slurm or Kubernetes. Python owns the
public dataset abstractions while coarse Rust primitives accelerate filesystem
and video operations.

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

## Current boundaries

- Distributed conversion currently requires shared POSIX paths visible under
  the same absolute names on every worker.
- Cluster-wide I/O calibration and direct final-shard writers remain future
  work; the MVP composes the existing conversion and merge engines.
- Training, robot control, and dataset upload are intentionally outside the
  project.

Continue with the [complete usage guide](USAGE.md), or read the
[architecture reference](ARCHITECTURE.md) before extending a source, backend,
planner, or scheduler boundary.
