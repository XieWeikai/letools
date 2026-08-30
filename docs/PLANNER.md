# Static conversion planner

## 1. Purpose and boundary

The planner selects one fixed performance configuration before conversion. It
tries to find a high-throughput point that fits the CPU and memory allocation
visible to the current process and is suitable for the dataset and source/
destination storage pair.

It owns four choices:

- concurrent Parquet groups (`workers`);
- concurrent video remux jobs (`video_workers`);
- v3 Parquet target size (`data_file_size_mb`);
- v3 video target size (`video_file_size_mb`).

It does not request Slurm resources, change dataset semantics, decide whether
overwrite or validation is allowed, or alter worker counts after conversion
starts. Runtime adaptation belongs to a future executor/controller, not this
planner.

Explicit user values are hard constraints. The planner fills only unspecified
performance fields.

## 2. Public flows

```text
letools plan
    -> inspect -> heuristic -> cache/calibration -> print ConversionPlan

letools convert --auto
    -> inspect -> heuristic -> cache/calibration -> ConversionPlan
    -> convert using plan.conversion_config()

letools convert (without --auto)
    -> fixed defaults/explicit values -> convert
```

`letools plan` does not create a plan file or execute a full conversion. Its
JSON is printed to standard output. `convert --auto` does not consume a prior
JSON file; it invokes the same planner and may reuse a matching calibrated cache
entry.

## 3. Planning pipeline

```text
source, destination, target, overrides
                  |
                  v
       open DatasetSource metadata
                  |
       +----------+----------+
       |          |          |
       v          v          v
   resources   dataset    source/destination
   inspector   inspector  storage inspectors
       |          |          |
       +----------+----------+
                  |
                  v
          static heuristic
                  |
                  v
        environment fingerprint
                  |
          +-------+-------+
          |               |
      cache hit        cache miss
          |               |
          |        optional bounded
          |        real-work calibration
          +-------+-------+
                  |
                  v
    ConversionPlan + evidence + estimates
```

Inspection and the heuristic always run, including on a cache hit. The cache
avoids repeated real-work calibration; it does not bypass all planning work.

## 4. Inputs

### 4.1 Effective resources

CPU inspection reads:

- process CPU affinity;
- cgroup effective CPU sets;
- `SLURM_CPUS_PER_TASK`, `SLURM_CPUS_ON_NODE`, or the leading value in
  `SLURM_JOB_CPUS_PER_NODE`.

The effective CPU count is the smallest available valid limit. Memory
inspection takes the smallest valid value from host memory, cgroup
`memory.max`, and Slurm per-node or per-CPU memory variables.

Planning must run inside the intended Slurm allocation. The planner reports
resources; it never submits or resizes a job.

### 4.2 Dataset profile

The dataset inspector reads metadata, physical file sizes, and Parquet footers.
It does not decode frames or video during the profiling phase. It records:

- version, episodes, frames, and camera count;
- data and video file counts;
- uncompressed Parquet size distribution;
- physical Parquet and video size distributions;
- episodes-per-data-file distribution.

These values determine available task parallelism, grouping targets, memory
estimates, and cache identity.

### 4.3 Source and destination storage

The two paths are inspected separately through `/proc/self/mountinfo` and
`statvfs`. Each `StorageProfile` records:

- requested path and nearest existing ancestor;
- mount point, filesystem type, and device/source identity;
- storage class: `memory`, `local`, `network`, or `unknown`;
- currently available bytes.

Known network classes include JuiceFS/FUSE, NFS, Ceph, CIFS, Lustre, and GPFS.
Ext-family filesystems, XFS, Btrfs, and ZFS are classified as local; tmpfs is
classified as memory.

The current heuristic uses a combined `network_io` condition: if either source
or destination is network storage, it starts from the network policy. The two
profiles remain distinct in plan output and the cache fingerprint, so
network-to-local and local-to-network environments do not share entries when
their mount identities differ.

The planner does not currently run independent synthetic source-read and
destination-write bandwidth tests. Bounded calibration measures their combined
effect through real conversion work, as described below.

## 5. Static heuristic

The heuristic produces a useful plan even when calibration is disabled, skipped,
or cannot write temporary data.

### 5.1 Candidate values

The worker lattice used by helper and oracle tooling is:

```text
1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64, 96
```

Values are clipped by effective CPUs and available task count. Implemented v3
target candidates are:

```text
Parquet: 32, 64, 100, 128, 200, 256, 512 MiB
Video:   64, 100, 200, 256, 400, 800 MiB
```

V2.1 output has no target-size choices because it writes one data and video file
per episode.

### 5.2 Data choices

For v3 output, target selection simulates distributing size groups across
workers and balances the maximum estimated worker load, including a fixed
per-task overhead. Worker count is capped by effective CPUs, group count, a
conservative default ceiling of eight, and an estimated memory limit.

The memory model reserves 15 percent of effective memory. Its per-worker
estimate uses the larger of the target size and p95 uncompressed source file,
doubles that data allowance, and adds fixed overhead.

For v2.1 output, data tasks are the number of physical source data files. The
same CPU, task, and memory bounds apply.

### 5.3 Video choices

Without an override, the heuristic starts video calibration at up to three
workers when either endpoint is network storage and up to eight workers for
non-network storage. It is always clipped by CPUs and available work.

For v3 output, local storage starts with 100 MiB video groups. Network storage
selects from the video target candidates to balance group loads at the chosen
worker count. Dataset size and camera count determine the estimated number of
video jobs.

These are starting policies, not claims about universal storage performance.
Real-work calibration can replace worker counts when enabled.

## 6. Bounded real-work calibration

Calibration executes the same core operations as conversion:

```text
real source Parquet/video files
    -> real Arrow cast/write or FFmpeg concat/split
    -> temporary files on destination storage
    -> throughput measurement
    -> temporary files removed
```

This jointly captures source reads, PyArrow/FFmpeg work, destination writes,
filesystem metadata costs, and concurrency effects. It does not separately
attribute elapsed time to source bandwidth, destination bandwidth, and CPU.

### 6.1 Admission and budgets

CLI defaults are 10 seconds, 1 GiB admitted reads, and 1 GiB admitted temporary
writes. The byte accounting uses selected input bytes as the estimate for both
read and write consumption. Budgets are checked before each sample; an admitted
sample is allowed to finish and can therefore make observed wall time exceed
the nominal time budget.

Temporary calibration directories are created under the nearest existing
ancestor of the requested destination, ensuring writes exercise the target
filesystem. Cleanup runs even when a measurement fails.

Calibration is skipped when:

- it is not enabled;
- total physical source data is below 64 MiB; or
- a data-only input has less than 512 MiB of physical Parquet data.

For a video dataset, the data phase is calibrated only when Parquet accounts
for at least 10 percent of total physical bytes. This preserves budget for the
dominant video phase.

### 6.2 Worker samples

Each stage tests a small set containing one worker, the heuristic choice, and
the available CPU/task ceiling. Explicitly fixed worker values are measured but
not replaced. Samples use disjoint jobs when enough work is available; bounded
prefix reuse is used when necessary to make comparisons possible.

The selected point changes only when throughput improves by more than three
percent. After at least three measurements, calibration stops when the newest
point does not beat the two preceding points by that margin.

When the byte budget cannot admit the requested video ceiling, the planner may
extrapolate from the last two measurements only when observed scaling is at
least 80 percent of ideal. Network extrapolation is capped at 16 workers.

Current calibration directly selects worker counts. V3 data target size remains
the heuristic grouping choice. V3 video target size is rebalanced after a
calibrated video-worker choice, with different local and network policies.

## 7. Overrides and feasibility

`workers` and `video_workers` must be positive and cannot exceed effective CPU
capacity. A data-worker override is also rejected when it exceeds the planner's
memory safety estimate. V3 target-size overrides must be positive.

Target-size overrides are rejected for v2.1 planning because those fields have
no meaning in the v2.1 layout.

With auto conversion, an override remains fixed during calibration and
execution:

```bash
uv run letools convert SOURCE DESTINATION --to v3.0 --auto --workers 4
```

## 8. Fingerprint and cache

The default cache directory is:

```text
${XDG_CACHE_HOME:-$HOME/.cache}/letools/planner-v1/
```

The SHA-256 fingerprint contains:

- cache schema and planner algorithm version;
- target direction;
- effective CPUs and whole-GiB memory bucket;
- CPU model;
- explicit overrides;
- the complete dataset profile;
- source and destination mount point, filesystem, storage class, and device.

Hostname and free-space values are reported but are not fingerprint inputs.
Entries contain selected parameters, calibration measurements, creation time,
and expiration time. Default TTL is seven days.

A cache entry is written only when calibration produces at least one
measurement. Heuristic-only planning therefore does not normally create an
entry. Invalid JSON, an incompatible cache schema, an expired entry, or a
different fingerprint is treated as a miss; cache errors never prevent
planning.

Use `--no-cache` to disable both reading and writing for one invocation. See the
[usage guide](USAGE.md#planner-cache) for inspection and cleanup commands.

## 9. `ConversionPlan` evidence

The serializable plan contains:

- source/target versions and resolved paths;
- selected execution parameters;
- resource, dataset, source-storage, and destination-storage profiles;
- planning seconds and estimated peak memory/task counts;
- `heuristic` or `calibrated` confidence;
- human-readable selection reasons;
- calibration measurements;
- fingerprint and cache-hit state.

`ConversionPlan.conversion_config()` deliberately transfers only execution
parameters plus caller-owned overwrite/validation choices. This keeps planner
policy separate from conversion semantics and publication safety.

## 10. Known limitations

- Plans are static; changing external I/O load during conversion is ignored.
- Source and destination bandwidth are not measured independently.
- The heuristic reduces any path with one network endpoint to the same broad
  network policy before workload calibration.
- Calibration samples a small worker set rather than exhaustively searching
  every worker/target combination.
- File-size targets are approximate grouping thresholds, not exact output sizes.
- Metadata and Parquet-footer inspection can be visible for datasets containing
  thousands of small files even when calibration is skipped.
- Cache reuse assumes the fingerprint captures the stable environment factors
  that materially affect the plan; transient contention is intentionally out of
  scope.

## 11. Verification

Planner correctness and performance are tested separately:

- focused tests cover resource limits, storage classification, heuristics,
  overrides, calibration, cache behavior, and auto-conversion integration;
- conversion outputs undergo deep validation and bidirectional semantic
  comparison;
- Slurm oracle scenarios cross CPU/memory allocations, local and JuiceFS
  storage, data-only and video-heavy inputs, and both conversion directions.

The executed matrix, regret definitions, resource accounting, and current
results are retained in [PLANNER_BENCHMARK.md](PLANNER_BENCHMARK.md). Future
performance changes must also follow the
[self-improvement protocol](../self-improve/PROTOCOL.md).
