# Static conversion planner

## 1. Objective and scope

The planner chooses a fixed performance configuration before a conversion
starts. Its objective is to minimize planning time plus predicted end-to-end
conversion time within the CPU and memory allocation visible to the current
process. Runtime adaptation and changing external load are explicitly out of
scope.

The planner owns performance choices only:

- concurrent Parquet groups
- concurrent video remux jobs
- v3 Parquet target size
- v3 video target size

It does not request Slurm resources, overwrite data, disable validation, change
format semantics, or execute conversion tasks. Explicit user values are hard
constraints; the planner fills only unspecified performance fields.

## 2. Architecture

```text
CLI / Python API
       |
       v
resource inspector ---- dataset inspector ---- storage inspector
       |                        |                       |
       +------------------------+-----------------------+
                                |
                                v
                    feasible candidate generator
                                |
                                v
                 bounded workload-native calibration
                                |
                                v
                       static cost optimizer
                                |
                                v
              ConversionPlan + evidence + fingerprint
                                |
                                v
                    existing conversion executor
```

`ConversionPlan` is serializable and records the selected parameters, resource
limits, dataset/storage summaries, planning mode, evidence, confidence, and a
schema version. Conversion code consumes the selected parameters but remains
independent from planner policy.

## 3. Inputs

### 3.1 Effective resources

Effective CPU capacity is the narrowest valid limit reported by process CPU
affinity, cgroup cpusets, and Slurm. Effective memory is the narrowest valid
Slurm, cgroup, resource-limit, and host-availability bound. Planning reserves
15 percent memory headroom by default.

### 3.2 Dataset profile

Profiling reads metadata, file statistics, and Parquet footers, not frame data
or video packets. It records episode/frame/camera counts, source file counts,
Parquet uncompressed-size distributions, physical video-size distributions,
episodes per source file, and estimated total bytes.

### 3.3 Storage profile

The source and destination are described separately. The profile records mount
point, filesystem type, device identity, free space, whether both paths share a
mount, and optional calibrated throughput and metadata latency.

## 4. Planning algorithm

### 4.1 Candidate domain

Worker candidates are selected from `1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48,
64, 96`, clipped by the effective CPU capacity and available task count. The
effective CPU capacity itself is included when it is absent from the lattice.

V3 data target candidates are `32, 64, 100, 128, 200, 256, 512` MiB. V3 video
target candidates are `64, 100, 200, 256, 400, 800` MiB. Targets are absent for
v2.1 output. Future candidates require a planner schema change or evidence from
the acceptance suite.

Candidates are rejected before calibration when they exceed CPU capacity, have
insufficient tasks to use the requested concurrency, predict more than 85
percent of effective memory, or create pathologically few or many output files.

### 4.2 Static heuristic

The zero-write mode uses dataset shape and filesystem class to choose a safe
point. It prefers enough groups to keep workers occupied, caps video concurrency
conservatively on network/FUSE storage, and reduces concurrency or target size
under memory pressure. It must complete quickly enough for small datasets.

### 4.3 Bounded calibration

Calibration uses deterministic, stratified real Parquet and video samples so
that PyArrow, FFmpeg, compression, small-file, source-read, and destination-write
costs are represented. Data and video stages are calibrated independently
because they execute serially and have disjoint parameters.

Default hard limits are 10 seconds, 1 GiB read, 1 GiB write, and 10 percent of
predicted conversion time. Temporary outputs are created under the destination
parent and always removed. Small conversions skip calibration when its expected
cost cannot be recovered.

### 4.4 Selection

The optimizer minimizes:

```text
planning_time + predicted_data_time + predicted_video_time
+ predicted_metadata_validation_publish_time
```

subject to the feasibility constraints. It reports predicted wall time, peak
memory, task/file counts, confidence, and the measurements or rules supporting
the choice.

## 5. Stage telemetry

Conversions expose low-overhead timings for source open, staging preparation,
metadata preparation, data planning, data execution, video planning, video
execution, metadata finalization, shallow validation, and publication/cleanup.
Stages also report task and useful-byte counts when available. External CLI
wall time remains authoritative for acceptance.

## 6. Fingerprint and cache

The cache is stored outside the repository under the user's platform cache
directory. Its key includes planner/letools versions, target direction,
effective resource bucket, CPU model, source and destination mount identities,
dataset size/file-distribution buckets, camera count, and codecs when known.
Entries contain evidence and expiry metadata. An incompatible schema or changed
fingerprint is a miss. Cache failures never prevent planning.

## 7. Performance acceptance

### 7.1 Offline oracle

For each mandatory static scenario, an offline oracle exhausts the planner's
feasible candidate domain. Data `(workers, target-size)` and video
`(video-workers, target-size)` pairs are searched separately, then their best
pair is confirmed in a full conversion. V2.1 targets omit size dimensions.

Each candidate has at least three successful samples; use five when dispersion
exceeds 5 percent. Final oracle/planner comparison alternates `O P O P O P` and
uses medians. All conversion and full benchmark jobs run through Slurm with
unique destinations and recorded cache state.

Define:

```text
execution_regret = planner_conversion_median / oracle_conversion_median
cold_e2e_regret  = (planning_time + planner_conversion_time) / oracle_conversion_median
```

Acceptance requires:

- deep validation and semantic roundtrip comparison pass for every output
- no OOM and process-tree peak RSS stays below 85 percent of the allocation
- per-scenario execution regret is at most `max(1.05, 1 + 2 * noise)`
- no scenario exceeds 1.10 execution regret
- geometric-mean execution regret is at most 1.03
- cold end-to-end regret is at most 1.10 for large conversions
- planning takes at most `min(10 seconds, 10 percent of predicted conversion)`
- skipped-calibration small-dataset planning takes at most one second
- four of five repeated static plans perform within 5 percent of the oracle
- no scenario regresses against fixed defaults beyond measured noise
- at least one read-bound, one write-bound, and one CPU-bound scenario improves
  materially over fixed defaults

### 7.2 Scenario matrix

Real storage scenarios cross node-local scratch and JuiceFS:

- local to local
- JuiceFS to local
- local to JuiceFS
- JuiceFS to JuiceFS

CPU/memory Slurm anchors are 2 CPU/4 GiB, 8 CPU/4 GiB, 8 CPU/16 GiB,
16 CPU/16 GiB, and 32 CPU/48 GiB, within the repository's half-node ceiling.
Synthetic datasets cover many short episodes, few long episodes, wide Parquet,
and video-heavy one- and multi-camera cases. Frozen dagger medium and full
workloads anchor real behavior in both conversion directions.

A test-only user-space filesystem throttle may add independently controlled
source bandwidth, destination bandwidth, and metadata latency. It supplements
but never replaces real-storage acceptance. If FUSE is unavailable, the suite
records that limitation and uses the real storage cross-product.

## 8. Implementation order

1. Add stage telemetry and a test-only data/video stage runner.
2. Add resource, dataset, and storage inspectors plus the read-only heuristic.
3. Add bounded workload calibration and static selection.
4. Add environment fingerprints, cache, CLI, and Python API integration.
5. Add Slurm scenario/oracle infrastructure and execute the acceptance matrix.

Each boundary receives focused unit tests before full Slurm conversion tests.
Performance results and any justified protocol adjustment are recorded before
the feature branch is considered complete.
