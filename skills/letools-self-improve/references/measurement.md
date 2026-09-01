# Measurement Discipline

## Comparable samples

Baseline and candidate must use the same physical workload, node class, Slurm
request, dependency lock, explicit concurrency, source/destination storage,
NUMA/core binding, and cache classification except when worker tuning itself is
the declared variable. Run serially in the protocol's alternating order.

Use the prescribed sample count and median. Increase samples when spread
exceeds the protocol threshold. Preserve every sample and distinguish failed
runs from outliers; do not silently discard either.

## Record the process tree

Collect the protocol's stage spans, external wall time, throughput units, peak
cgroup/process-tree RSS, CPU seconds, mean utilized cores, peak and weighted
process/thread counts, I/O bytes and wait, context switches, and page faults.
Requested CPU and memory are capacity limits, not consumption metrics.

Report source bytes and frames consistently. For HDF5 with encoded cameras,
distinguish trajectory frames from media frames. For video remux, include
encoded video seconds or packet counts when that better explains work.

## Profile separately

Use stage telemetry to choose where to profile. Prefer mixed Python/native
flame graphs and `perf stat` when available; otherwise record the limitation and
use cgroup, `/proc`, stage spans, and an available language profiler. Never mix
profiling overhead into acceptance timings.

Classify bottlenecks from multiple signals. Low CPU utilization plus high I/O
wait and flat throughput across a worker/resource ladder supports an I/O-bound
conclusion. A flame graph alone does not prove end-to-end benefit, and low
Python samples do not prove that Rust will help if native I/O dominates.

## Validate outside timing

Run the complete protocol correctness gate on retained outputs outside the
timed region. Validation cost may be reported separately but must not be
removed from the acceptance record. Preserve failed staging/publication
evidence long enough to verify transactional behavior.
