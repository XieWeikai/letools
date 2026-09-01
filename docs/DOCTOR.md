# Dataset Doctor

`letools doctor` integrates the complete pinned `lerobot-doctor` command set.
LeTools does not copy or reimplement individual checks: dataset arguments are
delegated to the pinned upstream submodule package, so reports, repair behavior, and
exit codes remain upstream behavior.

## Command boundary

The no-argument command remains the letools environment diagnostic:

```bash
letools doctor
letools doctor environment
```

It reports Python, PyAV/FFmpeg, the native provider, and the exact Doctor and
Visualizer upstream commits. Every other argument is passed to Doctor:

```bash
letools doctor DATASET [check options]       # legacy check shorthand
letools doctor check DATASET [options]
letools doctor fix DATASET [options]
letools doctor trim DATASET [options]
letools doctor score DATASET [options]
letools doctor gate DATASET [options]
letools doctor merge-check DATASET... [options]
```

`DATASET` may be a local LeRobot v2/v3 directory, a local zip archive, or a
Hugging Face `org/repo` ID where the selected upstream operation permits it.
Use `--max-episodes` for large Hub datasets to limit downloads.

## Quality checks

Run all 12 checks:

```bash
letools doctor check /data/dataset
```

Select checks, emit JSON, or write Markdown:

```bash
letools doctor check /data/dataset \
  --checks metadata,temporal,actions,videos \
  --max-episodes 20 --json

letools doctor check /data/dataset --markdown doctor-report.md
```

The check IDs and their scope are:

| ID | Detects |
| --- | --- |
| `metadata` | Invalid metadata, counts, files, tasks, and format compliance |
| `temporal` | Timestamp, FPS, frame-index, and episode-index discontinuities |
| `actions` | NaN/Inf, clipping, frozen actions, and large jumps |
| `videos` | Missing/undecodable media and FPS, resolution, or frame-count mismatch |
| `statistics` | Invalid observations, zero variance, outliers, and stats drift |
| `episodes` | Empty/short episodes, imbalance, lengths, and policy windows |
| `consistency` | Cross-episode schemas, dtypes, and shapes |
| `training` | Normalization, action-space, and policy readiness |
| `anomalies` | Stuck actuators, duplicate episodes, drift, and constant sensors |
| `portability` | Absolute paths, symlinks, large/nonstandard files, and Hub issues |
| `per_episode` | Episode-level reasons for bad samples |
| `kinematics` | Actions outside URDF position or implied velocity limits |

Kinematics can use the upstream built-in SO-100/SO-101 registry or an explicit
URDF:

```bash
letools doctor check /data/dataset --checks kinematics --urdf robot.urdf
```

Doctor cannot infer action units from current LeRobot metadata. Review
kinematics unit-mismatch warnings before treating a violation as physical.

## Automation and exit codes

Normal checks return `1` only when the overall severity is `FAIL`. CI mode emits
JSON to stdout and a summary to stderr:

```bash
letools doctor check /data/dataset --ci
letools doctor check /data/dataset --ci --fail-on warn
```

The first command fails on `FAIL`; the second fails on `WARN` or `FAIL`.
`--json` changes output representation without changing the normal threshold.

## Repair and curation

Always preview mutating commands:

```bash
letools doctor fix /data/dataset --dry-run
letools doctor fix /data/dataset --fixes reindex,timestamps,nan,metadata,episodes

letools doctor trim /data/dataset --dry-run
letools doctor trim /data/dataset --threshold 0.01 --min-active 10
```

`fix` creates a backup by default; `--no-backup` disables that protection.
`trim` rewrites episodes to remove static leading/trailing frames and does not
provide the same backup option. Keep an independent copy and validate after any
mutation. `--keep-static` preserves fully static episodes.

Rank episodes without rewriting them:

```bash
letools doctor score /data/dataset --drop-threshold 30
letools doctor score /data/dataset --max-episodes 100 --json
```

Check policy compatibility:

```bash
letools doctor gate /data/dataset --policy act
letools doctor gate /data/dataset --policy diffusion --chunk-size 100
letools doctor gate /data/dataset --policy smolvla
letools doctor gate /data/dataset --policy pi0
```

Check datasets before or after a merge:

```bash
letools doctor merge-check /data/a /data/b
letools doctor merge-check /data/merged --post-merge
```

`merge-check` is Doctor's compatibility diagnostic. It does not execute the
specialized `letools merge` engine.

## Visualizer integration

For local visualizer targets, letools exposes the same 12-check report at
`/doctor/` and `/doctor/report.json` on the local data service. The upstream
Doctor tab is pointed at the local HTML endpoint. Reports are computed lazily
and cached for that server lifetime; `--doctor-max-episodes` controls the sample.

For Hub targets, the Visualizer retains the public Doctor Space by default.
Command-line Doctor and embedded Doctor are therefore two views of the same
pinned implementation only for local targets.

## Ownership

`src/letools/doctor_external.py` is intentionally a thin CLI adapter.
`src/letools/visualizer_server.py` owns only local HTTP presentation and calls
the pinned public loader/runner/report APIs. Upstream algorithms live under
`third_party/external/lerobot-doctor` and must not be edited directly.
