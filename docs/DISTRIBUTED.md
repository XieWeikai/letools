# Distributed conversion MVP

## 1. Scope

Distributed conversion runs one immutable LeTools plan through interchangeable
scheduler adapters. The MVP supports:

- LeRobot v2.1, LeRobot v3.0, mapped HDF5, and AgileX sources;
- v2.1 or v3.0 targets, subject to the normal no-op conversion restriction;
- local execution for testing, Slurm arrays, and Kubernetes Indexed Jobs;
- durable progress, idempotent task retries, final validation, and transactional
  publication;
- one shared POSIX namespace visible at the same absolute paths on every worker.

It does not yet support S3/object-store state, cross-machine path translation,
runtime worker adjustment, cluster-wide storage calibration, heterogeneous
worker shapes, or native backend shard writers. These are deliberate MVP
limits, not properties hidden behind scheduler-specific code.

## 2. Architecture

```text
                     letools dist plan
                            |
        SourceProvider -> portable SourceSpec
                            |
                   distributed planner
         metadata scan -> contiguous episode tasks
                            |
                <job-dir>/plan.json
                            |
                    SchedulerAdapter
             +--------------+---------------+
             |              |               |
        LocalScheduler  SlurmScheduler  KubernetesScheduler
        thread launcher   job array       Indexed Job
             |              |               |
             +--------------+---------------+
                            |
          letools dist worker JOB --task-id INDEX
                            |
       reopen source -> EpisodeSubsetSource -> convert()
                            |
        staging/parts/task-N + results/task-N.json
                            |
           last successful worker takes POSIX lock
                            |
           existing specialized merge_datasets()
                            |
              validate -> atomic destination
```

Core conversion, source plugins, backends, and merge know nothing about Slurm
or Kubernetes. Adapters submit the same worker command and never interpret
dataset semantics. Adding another scheduler requires implementing
`SchedulerAdapter.submit()`; it does not require a new backend or source branch.

### Portable source boundary

A live `DatasetSource` is not serialized or sent between nodes. Planning stores
a versioned `SourceSpec` containing an absolute root, source kind, and portable
options. HDF5 plans embed the complete versioned preset rather than a reference
to `~/.config`, so workers do not depend on the submitting user's preset store.
AgileX plans store instruction, FPS, and robot type. Each worker reconstructs
the source from this JSON contract.

### Episode subset boundary

Each task owns a nonempty contiguous half-open interval `[start, stop)`. The
internal `EpisodeSubsetSource` delegates data/media reads to the original
source, exposes zero-based local episodes, and rewrites only generated
`episode_index` and global `index` columns. Source episode statistics are
preserved and restored after final merge. Existing backends
therefore write each task as an independently valid LeRobot dataset without
distributed branches in their hot paths.

### Finalization boundary

Parts are ordered by task id and passed to the specialized same-version merge
engine. Merge remaps global episode, frame, and task indices and reuses complete
video files. This compositional implementation prioritizes correctness and
reuse for the MVP. A future native distributed backend may write final shards
directly if measurement shows part metadata or final merge is material.

## 3. Durable job layout

The job directory must be on shared POSIX storage:

```text
JOB/
  plan.json                         immutable protocol document
  .finalize.lock                    POSIX advisory publication lock
  staging/parts/task-000000/        valid temporary LeRobot dataset
  results/task-000000.json          atomic task commit marker
  errors/task-000000.json           latest failed attempt, if any
  scheduler/slurm-worker.sh         generated adapter artifact
  scheduler/slurm-command.json      exact sbatch argv
  scheduler/kubernetes-job.json     generated Indexed Job
  published.json                    final publication commit marker
```

The scheduler database is not the source of truth. `dist status` derives
progress from the plan and atomic result files, so it still works after a login
session, Slurm controller, Pod, or submitting process disappears.

## 4. Plan and run

Create a plan. `--tasks` is capped at the episode count and produces that exact
number of balanced-by-episode-count intervals. `--episodes-per-task` is an
alternative hard bundle size.

```bash
letools dist plan /shared/source-v21 /shared/output-v30 \
  --to v3.0 \
  --job-dir /shared/letools-jobs/fold-v30 \
  --tasks 64 \
  --workers 8 \
  --video-workers 3
```

The worker counts are per concurrently scheduled task, not cluster totals. If
64 tasks run with `--max-parallel 8`, at most eight task processes run and each
may use eight data workers and three video workers. Size flags have the same
meaning as normal conversion and affect v3 part grouping only.

Inspect progress or manually retry/finalize:

```bash
letools dist status /shared/letools-jobs/fold-v30
letools dist worker /shared/letools-jobs/fold-v30 --task-id 17
letools dist finalize /shared/letools-jobs/fold-v30
```

Normally the adapter invokes `worker`. Every successful worker attempts
finalization; only the last one finds all result records, and the POSIX lock
ensures that only one process publishes.

### Local adapter

Use local mode to test a plan before consuming cluster resources:

```bash
letools dist submit /shared/letools-jobs/fold-v30 \
  --scheduler local --max-parallel 2
```

This is synchronous. `max-parallel` controls task processes at the adapter
level; node-local worker settings remain those recorded in the plan.

### Slurm adapter

```bash
letools dist submit /shared/letools-jobs/fold-v30 \
  --scheduler slurm \
  --max-parallel 8 \
  --cpus-per-task 16 \
  --memory 64G \
  --partition batch \
  --time-limit 04:00:00
```

LeTools writes a worker script and exact `sbatch` argv below `JOB/scheduler`,
then submits an array indexed from zero. The script records the submitting
LeTools environment's absolute Python executable and invokes `-m letools.cli`,
preventing a different `letools` in compute-node `PATH` from being selected.
`--account` is also available. `--render-only` writes both artifacts without
invoking `sbatch`.

The compute environment must expose the `letools` command and all source,
destination, and job paths. Resource requests should cover all node-local data
and video workers recorded in the plan. When CPU is omitted, the adapter
requests the larger of the plan's data-worker and video-worker counts; an
explicit smaller CPU request is rejected.

### Kubernetes adapter

```bash
letools dist submit /shared/letools-jobs/fold-v30 \
  --scheduler kubernetes \
  --image ghcr.io/xieweikai/letools:VERSION \
  --namespace datasets \
  --max-parallel 8 \
  --cpus-per-task 16 \
  --memory 64Gi \
  --pvc-claim shared-datasets \
  --mount-path /shared
```

The adapter writes `kubernetes-job.json` and applies it with `kubectl`. The Job
uses `completionMode: Indexed`; each Pod obtains its task id from
`JOB_COMPLETION_INDEX`. The image must contain the same LeTools version as the
planner. The PVC must expose the job, source, and destination at the absolute
paths stored in `plan.json`. More complex clusters can use `--render-only`,
modify the generated Pod spec for site-specific mounts, service accounts,
node selectors, or secrets, and apply it through their deployment system.
As with Slurm, an omitted CPU request is derived from node-local concurrency.

## 5. Failure and publication semantics

`results/task-N.json` is written only after the task part has been staged,
validated (unless disabled), and published inside the job directory. A retry:

1. reads the immutable plan;
2. checks whether its commit record and part are valid;
3. returns the existing result without rewriting payloads when valid;
4. otherwise transactionally replaces the incomplete part;
5. atomically replaces the result record.

Failed attempts write a diagnostic under `errors/` and return nonzero so Slurm
or Kubernetes applies its normal retry policy. Kubernetes uses three retries
per index. A successful retry removes that task's old error record.

The formal destination is created only after all episode/frame totals match,
all parts are available, merge succeeds, and validation succeeds. Existing
destinations are rejected unless `--overwrite` was recorded during planning.
Temporary parts remain after publication for audit and explicit cleanup; the
MVP never deletes the shared job directory automatically.

Final merge output is built at a deterministic hidden sibling of the formal
destination, so its rename is atomic on the destination filesystem. Before the
rename, LeTools writes `.letools-distributed.json` with the protocol and job
identity. If a worker exits after the rename but before `published.json` is
committed, any retry recognizes and validates that provenance and completes the
shared state instead of treating the destination as unrelated.

## 6. Correctness and performance model

Correctness requires deterministic contiguous task coverage, valid zero-based
parts, ordered merge by task id, exact episode/frame totals, and normal LeTools
validation. Automated acceptance compares both conversion directions with the
equivalent source semantically.

Performance has two levels. `--max-parallel` controls cluster task concurrency;
the plan worker fields control concurrency inside one task. More nodes do not
guarantee more throughput on shared storage. The MVP leaves cluster-level I/O
saturation measurement to the operator and makes all limits explicit.

### Single-node versus two-node measurement

On 2026-09-03 we compared the same 811-episode, 693,669-frame, 10 GiB source
with 16 total CPUs on the same shared filesystem. The non-distributed run used
one task with 16 data/video workers. The distributed run used two Slurm nodes,
one task and 8 CPUs per node, two contiguous episode tasks, and the same worker
settings per task. Each result is the median of three sequential samples with
source cache eviction between runs; distributed wall time includes final part
merge, while `task phase` stops before merge.

| Filesystem / direction | Single wall | Distributed task phase | Distributed final merge | Distributed wall | End-to-end change |
| --- | ---: | ---: | ---: | ---: | ---: |
| `/home`, v3.0 -> v2.1 | 16.299 s | 10.719 s | 20.374 s | 31.164 s | -47.70% |
| `/home`, v2.1 -> v3.0 | 17.133 s | 10.228 s | 10.321 s | 20.549 s | -16.62% |
| `/jfs`, v3.0 -> v2.1 | 9.179 s | 9.673 s | 8.384 s | 18.025 s | -49.08% |
| `/jfs`, v2.1 -> v3.0 | 7.613 s | 6.114 s | 3.107 s | 9.221 s | -17.44% |

The corresponding end-to-end throughputs were 49.76/26.02 episodes/s on
`/home` reverse, 47.33/39.47 on `/home` forward, 88.35/44.99 on `/jfs`
reverse, and 106.53/87.95 on `/jfs` forward. Distributed peak RSS was about
1.29 GiB for reverse and 0.82--0.84 GiB for forward, versus 0.52--0.66 GiB
single-node RSS; peak threads rose from 41/51 to 51/54 because each task owns
its own conversion workers.

This demonstrates that task fan-out can shorten the conversion phase, but the
current MVP writes complete parts and then rewrites them during final merge.
On `/home` reverse, merge alone exceeded the single-node conversion time. On a
shared JuiceFS, adding nodes therefore does not guarantee higher end-to-end
throughput and can increase aggregate I/O contention. A future optimization
should publish compatible final shards directly or perform a streaming merge,
and should autotune cluster `max_parallel` against aggregate storage bandwidth.

The benchmark also found and fixed a correctness issue: the subset adapter now
preserves source episode statistics while rewriting only physical row indices;
the finalizer restores those statistics after part merge. The corrected retained
distributed output passed deep validation and semantic packet comparison for
all 811 episodes, 693,669 frames, and 2,433 videos.

The next planner iteration should calibrate 1/2/4/... concurrent tasks under a
strict read/write budget, retain the smallest concurrency near peak aggregate
throughput, and include the storage and cluster fingerprint in the plan. That
policy belongs in the distributed planner, not in scheduler adapters.

## 7. MVP acceptance evidence

Slurm array job `959` exercised the installed adapter against shared `/jfs`
storage. The plan split a three-episode, nine-frame v2.1 fixture into two tasks,
requested one CPU and 2 GiB per task, and converted to v3.0 on `H800-node11`.

| Task | Episodes | Frames | Slurm state | Elapsed |
| --- | ---: | ---: | --- | ---: |
| 0 | 2 | 7 | `COMPLETED` | 3 s |
| 1 | 1 | 2 | `COMPLETED` | 2 s |

The shared state reached `published`; deep validation reported three episodes,
nine frames, no errors, and no warnings. Full Arrow semantic comparison against
the v2.1 source reported equality for all nine frames. This is a deployment and
correctness smoke test, not a throughput benchmark.

Automated tests additionally cover both LeRobot conversion directions, mapped
HDF5 with encoded video packet comparison, retries, publication crash recovery,
and Slurm/Kubernetes artifact rendering. No live Kubernetes cluster was
available for this MVP acceptance, so Kubernetes evidence stops at schema and
command construction rather than claiming a successful Pod execution.

## 8. Extension contracts

Add a scheduler by subclassing `SchedulerAdapter` and submitting one command
per index:

```text
letools dist worker JOB_DIR --task-id INDEX
```

Alternatively provide the index through an environment variable:

```text
letools dist worker JOB_DIR --task-id-env VARIABLE_NAME
```

An adapter owns scheduler resource syntax, submission, and artifacts. It must
not open datasets, repartition episodes, alter worker configuration, or publish
outputs. Future `ArtifactStore` and direct shard-writer abstractions must remain
below the scheduler-neutral task protocol.
