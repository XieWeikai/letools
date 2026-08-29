# letools Single-Node Self-Improvement Protocol

## 1. Scope

This protocol governs iterative optimization of /workspace/shrelic/letools.
The official LeRobot repository and loaders define compatibility, while the
official pre-conversion dataset remains the immutable correctness oracle.

Only single-node optimization is in scope. Every data conversion and full
benchmark runs through Slurm with one task on one node. A job may request at
most half of that node's logical CPUs and half of its physical memory.

## 2. Non-Negotiable Rules

1. Correctness is a hard gate and is evaluated before performance.
2. Every iteration starts from the current clean letools/main.
3. A draft is archived before implementation.
4. Baseline and candidate use the same dataset, node class, resource request,
   dependency lock, cache classification, and explicit worker settings.
5. Only accepted candidates are committed and fast-forwarded into main.
6. Rejected candidates are archived with their diff and report, then their
   disposable branch/worktree is removed.
7. Raw records, drafts, reports, and profiles stay under this ignored directory
   and never enter either repository's Git history.
8. Reusable benchmark infrastructure is versioned in letools; iteration-specific
   artifacts are not.

## 3. Fixed Workloads

| Tier | Workload | Purpose |
| --- | --- | --- |
| Micro | Deterministic synthetic datasets | Fast metadata, Parquet, schema, and scheduling feedback |
| Medium | Frozen real dagger subset with all camera streams | Daily profiling and flame graphs |
| Full | Complete dagger dataset | Final acceptance |

The synthetic suite contains many-short-episode, few-long-episode,
wide-feature, and short-video variants. Generation is deterministic and occurs
outside the timed conversion.

Each tier can exercise v2.1 to v3.0, v3.0 to v2.1, and both roundtrip
directions. Full bidirectional conversion and roundtrip correctness are
required before an optimization is accepted.

## 4. Conversion Stage Model

End-to-end production time spans source_open through publish_cleanup. Dataset
generation, environment setup, and the external acceptance suite are reported
separately.

| Stage | Definition |
| --- | --- |
| source_open | Detect version; load info, tasks, episodes, and stats; construct episode indices |
| staging_prepare | Check destination, create staging path, and select backend |
| metadata_prepare | Normalize features and write initial target metadata |
| data_plan | Inspect Parquet metadata, compute sizes, group work, and calculate offsets |
| data_read | Materialize source Arrow tables |
| data_transform | Cast schema and concatenate or slice episode tables |
| data_write | Encode and write target Parquet files |
| video_plan | Inspect file sizes, group videos, and calculate video slices |
| video_remux | Concatenate or split encoded packets without transcoding |
| metadata_finalize | Write episode metadata and flatten or aggregate statistics |
| conversion_validate | Run the conversion's built-in shallow validation |
| publish_cleanup | Atomically publish staging output or remove failed staging data |

Nested data spans distinguish footer scan, read, cast, concat or slice, and
write where the Arrow API permits it. Video spans distinguish concat and split.
ConversionResult.elapsed_seconds currently excludes source_open; official
performance decisions therefore use external CLI wall time.

## 5. Metrics

Every timed run records:

- wall time and per-stage wall time
- episodes/s, frames/s, input GiB/s, and encoded video-seconds/s
- peak RSS for the entire Slurm cgroup/process tree
- CPU seconds, mean utilized cores, and CPU utilization
- peak and time-weighted process/thread counts
- read/write bytes and I/O wait
- context switches and page faults
- Slurm request, node model, NUMA layout, dependency versions, commit, and CLI

Efficiency metrics:

    memory_efficiency = frames_per_second / peak_RSS_GiB
    cpu_efficiency    = frames / CPU_seconds
    io_efficiency     = useful_input_bytes / physical_IO_bytes

Profiles are collected separately from timed acceptance runs. Prefer perf stat
and mixed-stack perf record flame graphs when available. If a node lacks perf,
archive that fact and use stage spans, cgroup and /proc sampling, plus an
available Python/native profiler. Profiled wall time is never a performance
result.

## 6. Statistical Procedure

Baseline and candidate alternate as B C B C B C. Three successful runs are the
default and the median is authoritative. Increase to five when spread or
standard deviation exceeds 5%.

All concurrency is explicit, including data workers, video workers, Arrow
pools, Rayon pools, and codec threads. Jobs use one Slurm task, core binding,
and no more than half-node CPU and memory. Every run uses a unique destination.
Cache state is recorded but is not called cold unless eviction is controlled.

### 6.1 Half-node high-resource lane

Optimizations may intentionally target saturation of the largest permitted
single-node allocation. For the recorded H800 node class, Slurm reports 192
CPUs and 1998485 MiB, so the hard request ceiling is 96 CPUs and 999242 MiB.
The ceiling is recalculated as `floor(CPUTot / 2)` and
`floor(RealMemory / 2)` when the node class changes.

Before optimizing at the ceiling, characterize current-main scaling with a
resource ladder such as 8, 16, 32, 64, and 96 CPUs. Memory should increase only
when measured demand requires it; the 50 percent allowance is a ceiling, not a
requirement to reserve unused memory. Record the worker settings, CPU affinity,
NUMA placement, CPU seconds, mean cores, peak RSS, peak threads, I/O wait, and
throughput at every point. The curve identifies the saturation point and
whether CPU, memory bandwidth, local storage, or JuiceFS is limiting.

Two experiment types are permitted:

1. For an algorithm or implementation change, baseline and candidate both run
   with the same half-node Slurm request and the same explicit concurrency.
2. For concurrency or resource tuning, baseline and candidate use the same
   half-node Slurm request but may use different explicit worker settings. The
   acceptance ratios use measured process-tree CPU seconds, RSS, and peak
   threads rather than requested resources.

The authoritative high-resource Full benchmark uses 96 CPUs and at most 999242
MiB, one Slurm task, core binding, unique destinations, and serial B/C runs. Do
not overlap acceptance runs: shared JuiceFS traffic would invalidate the
comparison. Run B C B C B C by default and increase to five pairs when spread
exceeds 5 percent.

An optimization accepted in the high-resource lane must also pass a low-resource
regression check at the established production allocation. A high-resource
speedup may not silently make the ordinary path slower beyond observed noise.
The default configuration is not automatically raised to 96 workers; defaults
follow the measured Pareto point for throughput, CPU efficiency, memory, and
I/O pressure.

## 7. Correctness Gate

All conditions must pass:

1. letools validate --deep reports no errors or warnings.
2. v3 output loads with official LeRobotDatasetMetadata.
3. v3 output constructs with official LeRobotDataset and reads samples from
   the first, middle, and final episodes.
4. Episode indices, lengths, tasks, and statistics are equivalent.
5. Every Parquet column preserves logical dtype, shape, and values.
6. Video boundaries and encoded packet payload digests are equivalent.
7. Both roundtrip directions are semantically equivalent to their source.
8. Failures never publish a partial destination.

Physical Parquet layout and MP4 container metadata may differ. Encoded packet
payloads and dataset semantics may not.

## 8. Acceptance Policy

For candidate versus current-main baseline:

    T = candidate primary throughput / baseline primary throughput
    M = candidate process-tree peak RSS / baseline peak RSS
    C = candidate CPU seconds / baseline CPU seconds
    W = candidate peak threads / baseline peak threads

A performance candidate is measurable when its target workload improves by at
least max(3%, 2 * observed_noise).

It is accepted only when correctness passes, M and W are no larger than
max(T, 1.10), it remains below half-node resources, non-target directions have
no regression beyond noise, resource costs are proportionate, and complexity
is justified. Linear speedup from linear worker growth is classified as a
scaling improvement rather than an algorithmic efficiency improvement.

For half-node experiments, requested capacity is not evidence of resource cost.
Use cgroup/process-tree measurements. A candidate that reserves 96 CPUs but
only uses a few cores must not claim 96-core scaling, and a candidate that
increases workers without proportional throughput or efficiency is rejected.

Simplification is independently valuable. Deleting code with equal or better
performance is accepted. Near-zero improvement with substantial complexity is
rejected. Performance-specific complexity stays inside a backend or primitive
and does not leak into the episode/plugin API.

## 9. Iteration Lifecycle

1. Verify clean letools/main.
2. Run or refresh the current-main baseline.
3. When targeting the high-resource lane, refresh the current-main resource
   scaling curve and choose the best measured baseline point.
4. Inspect stage metrics and profiles; choose the largest actionable bottleneck.
5. Archive draft.md with evidence, hypothesis, proposed change, expected
   benefit, risks, correctness impact, and rollback.
6. Create opt/NNNN-short-name from main in a disposable branch or worktree.
7. Implement the smallest candidate and run unit and Micro tests.
8. Run Medium profiling; reject early if the hypothesis fails.
9. Run Full bidirectional conversions and the complete correctness gate.
10. Alternate Full baseline and candidate timing when a fresh comparison is
   needed.
11. Run the low-resource regression check for high-resource candidates.
12. Archive raw results, candidate.diff, and report.md.
13. If accepted, write the commit message according to the standard below,
    commit, and fast-forward merge into main.
14. If rejected, preserve the diff, remove its branch/worktree, and return to
    clean main.
15. The accepted main becomes the next iteration's baseline.

### 9.1 Accepted Commit Message Standard

Every accepted optimization commit uses a short, imperative subject followed
by a detailed body. The subject identifies the affected component and behavior;
the body makes the acceptance decision understandable without opening the local
self-improve archive. A subject-only performance commit is not acceptable.

The body must contain these sections:

1. `Change`: what changed, where it changed, the bottleneck it addresses, and
   why the implementation is preferable to the previous one.
2. `Profile`: dataset and conversion direction, baseline and candidate commits,
   Slurm allocation, effective process/thread/worker counts, number of measured
   runs, aggregation method, and the profile evidence that motivated the work.
3. `Result`: baseline and candidate wall time and throughput with both raw
   values and percentage change; process-tree peak RSS, CPU seconds, and peak
   process/thread changes when relevant. Include the low-resource regression
   result for a high-resource candidate.
4. `Validation`: the correctness gates that ran and their outcome, including
   deep validation, official metadata/dataset loader checks, and bidirectional
   or roundtrip equivalence as applicable.

Use measured medians for the primary comparison and state the sample count.
Distinguish requested Slurm capacity from observed resource use. Do not claim a
cause that the profile does not support, do not report only relative percentages,
and do not multiply speedups from successive commits to claim an unmeasured
cumulative result. If a simplification is accepted without a measurable
throughput gain, state that explicitly and report the unchanged performance,
deleted complexity, and resource/correctness results.

Template:

    perf(<scope>): <imperative summary>

    Change:
    - <implementation change and bottleneck addressed>

    Profile:
    - Workload: <dataset>, <direction>, <episode/frame counts>
    - Environment: <node>, Slurm <CPUs/RAM>, observed <workers/threads>
    - Samples: <N baseline / N candidate>, median, <alternation method>
    - Evidence: <profile observation>

    Result:
    - Wall time: <baseline> -> <candidate> s (<percent>)
    - Throughput: <baseline> -> <candidate> episodes/s (<percent>)
    - Peak RSS: <baseline> -> <candidate>; CPU: <baseline> -> <candidate>
    - Low-resource check: <allocation and result, or not applicable>

    Validation:
    - <validators, official loaders, and equivalence checks>: PASS

The iteration number and archive path may be included for traceability, but
the ignored archive is not a substitute for the required evidence in the
commit message.

## 10. Archive Layout

    /workspace/shrelic/letools/self-improve/
      PROTOCOL.md
      environment/
      datasets/manifests/
      baselines/<letools-main-commit>/
      iterations/
        0001-short-name/
          draft.md
          candidate.diff
          baseline-runs.jsonl
          candidate-runs.jsonl
          correctness.json
          report.md
          profiles/

Each report states accepted or rejected with evidence. An iteration counts
toward an optimization target only after its accepted commit is present in
letools/main.
