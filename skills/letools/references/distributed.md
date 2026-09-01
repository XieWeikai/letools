# Distributed Conversion

Distributed execution partitions conversion work. It does not distribute the
specialized merge engine. All workers and the coordinator require the same
POSIX paths to source, destination staging, and job state on shared storage.

Resolve source-specific semantics before planning: HDF5 needs a portable preset
embedded or referenced by the job specification, and AgileX needs its explicit
instruction. Do not depend on a user-local preset that workers cannot read.

Keep these controls separate:

- task count controls dataset partitioning;
- workers per task control local conversion concurrency;
- maximum parallel tasks control scheduler concurrency and shared-I/O pressure.

Start with `letools dist plan`, then inspect its durable job state. Use render
mode when the user wants manifests or scripts for review. Submit to Slurm or
Kubernetes only when authorized; choose per-task CPU and memory consistently
with planner output. Kubernetes also requires an executable image and shared
PVC mapping.

After submission, monitor both the scheduler and `letools dist status`. Retry
only failed tasks. Keep job state and completed shards so retries remain
idempotent. Use manual finalize only when every task succeeded but publication
did not complete. Finally validate the published dataset and report scheduler,
job ID, allocation, task outcomes, timings, and retained recovery state.
