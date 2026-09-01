---
name: letools
description: Operate robot datasets with LeTools. Use when the user asks to inspect, plan, convert, merge, validate, compare, repair, score, or visualize LeRobot data; import HDF5 or AgileX recordings; or run LeTools conversion through Local, Slurm, or Kubernetes execution. Do not use for model training or robot control.
---

# LeTools Dataset Operations

Turn the user's dataset intent into a complete, verified LeTools operation. Use
the installed CLI as the execution boundary; do not reimplement its conversion,
planning, merge, validation, Doctor, or Visualizer logic.

## Start with evidence

1. Locate the executable with `command -v letools`. Inside a source checkout,
   use `.venv/bin/letools` only when the user has not installed the command.
2. Run `letools doctor` when environment readiness or native video support is
   relevant. If LeTools is unavailable, read
   [runtime setup](references/runtime.md) before changing the environment.
3. Run `letools <command> --help` for the selected operation and source provider.
   Treat current help and JSON output as authoritative over remembered flags.
4. Resolve source, destination, preset, and job paths to unambiguous absolute
   paths before launching long work. Inspect existing paths without modifying
   them.

## Route the request

- For conversion, resource planning, HDF5, or AgileX, read
  [conversion and sources](references/conversion.md).
- For same-version LeRobot merge, read [merge](references/merge.md).
- For multi-node conversion or explicit Slurm/Kubernetes execution, read
  [distributed execution](references/distributed.md).
- For validation, semantic comparison, Doctor, or Visualizer, read
  [quality and visualization](references/quality.md).

Read only the references needed for the request.

## Execute the whole requested workflow

Distinguish an action request from a guidance request. "Convert this dataset"
authorizes the scoped conversion and its normal validation; "show me how" or
"make a plan" is read-only. For an action request, continue through execution,
monitoring, validation, and a concise result instead of stopping after printing
a command or scheduler job ID.

Prefer `--auto` for substantial single-node conversion and merge unless the
user supplies fixed performance parameters. Run planning in the allocation that
will execute the work. Respect explicit repository, operator, and scheduler
requirements; do not run heavy data work on a login node when it is required to
use Slurm or another scheduler.

Parse result JSON rather than progress text. Preserve exact commands and job
identifiers needed to reproduce or monitor the operation.

## Preserve data and meaning

- Never infer permission to add `--overwrite`. If the destination exists,
  report it and obtain a new destination or explicit replacement authority.
- Keep built-in validation enabled. Use `--no-validate` only for an explicitly
  scoped benchmark, and validate the retained output outside the timed region.
- Never invent an HDF5 field mapping, task text, an AgileX instruction, camera
  semantics, or feature names. Ask for the missing semantic decision or invoke
  the mapping workflow when interactive use is possible.
- Do not upload data, expose a public Visualizer, submit scheduler work, repair
  files, or trim episodes unless the user's requested outcome authorizes that
  external or mutating action.
- Prefer a new destination. Conversion and merge are transactional, but an old
  destination and staging output can coexist, so check destination capacity for
  large operations.
- Do not call `letools dist worker` directly except for an explicit task retry
  or distributed-job diagnosis.

## Verify and report

For a completed write, retain the built-in validation result. Run
`letools validate DATASET --deep` when the user requires final correctness,
when accepting a new source mapping, or when the additional full read is
proportionate to the task. Use semantic comparison, including `--videos` when
encoded media equality matters, when an oracle or round-trip source exists.
State any validation intentionally omitted because of cost or scope.

Report:

- operation, source format/version, target version, and absolute output path;
- episode, frame, video, and byte counts available from results;
- Local/Slurm/Kubernetes execution context, allocation, and job identifier;
- selected data/video workers and v3 shard targets when applicable;
- wall time and episode/frame throughput when measured;
- shallow, deep, semantic, packet-payload, or Doctor checks actually completed;
- failures, retained staging/job state, and the exact safe next action.
