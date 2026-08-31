# Specialized LeRobot merge engine

## Scope

Merge is a fixed physical operation, not an extension of dataset conversion. It
supports exactly these cases:

```text
v2.1 + v2.1 + ... -> v2.1
v3.0 + v3.0 + ... -> v3.0
```

At least two unique local paths are required. All inputs must agree on version,
FPS, robot type, feature metadata, video keys, and the name of one split that
covers every episode. Mixed versions, feature union, missing-field filling,
camera renaming, FPS resampling, episode deduplication, and partial split
composition are rejected before destination creation.

Input order determines output episode order. Task strings are deduplicated in
first-appearance order. Local task indices from each source are mapped into that
global table.

## CLI

```bash
letools merge SOURCE_A SOURCE_B [SOURCE_C ...] --output DESTINATION --auto
```

| Option | Behavior |
| --- | --- |
| `--auto` | Calibrate real work when no matching plan is cached |
| `--plan-only` | Print a plan without publishing a dataset |
| `--data-workers N` | Fix concurrent Parquet resource rewrites |
| `--file-workers N` | Fix concurrent complete media file operations |
| `--calibration-seconds N` | Stop starting samples after this wall budget; default 10 |
| `--calibration-mb N` | Total candidate sample byte budget; default 1024 MiB |
| `--no-cache` | Ignore and do not save plans |
| `--overwrite` | Replace a destination after staging validates |
| `--no-validate` | Skip built-in deep validation |

`--data-workers` and `--file-workers` are hard overrides. The phases run
sequentially, so each may use the available CPU allocation without their counts
being added. Output is JSON and includes `MergePlan`, source contributions,
clone/copy counts, logical bytes, and stage metrics.

The Python API is:

```python
from letools import merge_datasets, plan_merge

plan = plan_merge(["part-a", "part-b"], "combined", calibrate=True)
result = merge_datasets(["part-a", "part-b"], "combined", auto=True)
```

## Physical algorithm

The initial manifest opens only physical LeRobot readers, validates compatibility,
and assigns every output episode, global frame offset, and task mapping.

Parquet resources are read as bounded Arrow RecordBatches. Three system columns
are replaced:

- `episode_index`: new globally contiguous episode index;
- `index`: new globally contiguous frame index derived from episode offset and
  unchanged `frame_index`;
- `task_index`: lookup through the source-specific task mapping.

All other Arrow arrays remain in the batch without conversion to Python rows.
Index statistics are recomputed exactly; other episode statistics are retained.
v2.1 writes one Parquet resource per episode. v3.0 preserves input shard
boundaries and updates every data/video resource reference and global offset.

Videos are never opened by FFmpeg. Python submits all `(source, destination)`
pairs in one PyO3 call. Rust releases the GIL, builds a Rayon pool capped at
`file_workers`, attempts Linux `FICLONE`, and falls back to `std::fs::copy`.
Consequently encoded packet payloads remain byte-identical.

The complete result is written to a hidden sibling staging directory, deep
validated, and atomically renamed. A failed run removes staging. Existing output
is retained unless `--overwrite` is specified, and source/destination directory
containment is rejected.

## Autotune

The static merge planner reads CPU affinity, cgroup and Slurm limits, effective
memory, all source mount identities, destination storage, Parquet logical and
physical sizes, media sizes, and resource counts. Its heuristic caps each phase
at available tasks and CPUs, 16 candidate workers, and a half-allocation memory
budget. Batch rows are derived from logical bytes per row and tightened again if
calibration selects greater concurrency.

On a cache miss, powers-of-two candidates plus the feasible limit execute real
Parquet rewrites and real destination-side clone/copy operations. Sampling is
bounded by the byte budget and no new candidate starts after the wall budget.
The least worker count within 3% of measured peak throughput is retained.

Plans are cached at `~/.cache/letools/merge-plans/<fingerprint>.json`. The
fingerprint includes the merge algorithm, effective CPUs/memory, input and
output storage identities/classes, version, workload totals, and complete data
and media size distributions. Cache hits still rescan and validate metadata.
Delete `~/.cache/letools/merge-plans` to clear all merge plans.

## XVLA preparation

Slurm array job `916` converted every HDF5 directory immediately under
`/data/share/datasets/XVLA-Soft-Fold` using the stored XVLA preset. The input was
20 datasets, 1,542 episodes, and 439.962 GiB of HDF5. For every directory it
created sibling `_lerobot_v2_1` and `_lerobot_v3_0` outputs. All 20 array tasks
completed with exit code zero after both outputs passed conversion validation
and a separate deep validation. The array used at most four concurrent tasks,
24 CPUs and 128 GiB each: at most 96 CPUs and 512 GiB in total.

## Real merge acceptance

The merge workload combined these two prepared datasets in input order:

- `0706_17pm_stage_1_stage2new_new_cam_very_slow`: 24 episodes, 60,828 frames;
- `0712_8pm_stage_1_stage2new_new_cam_very_slow_grasp_corner`: 104 episodes,
  49,958 frames.

The output contains 128 episodes, 110,786 trajectory frames, three cameras, 384
episode-camera streams, and about 17.8 GB of encoded media. Slurm job `938` used
48 CPUs and 128 GiB for each target, with two targets concurrent, staying within
the half-node limit.

| Target | Auto plan data/file | Engine wall | External CLI wall | Process peak RSS |
| --- | ---: | ---: | ---: | ---: |
| v2.1 | 16 / 1 | 3.234 s | 3.76 s | 618,220 KiB |
| v3.0 | 2 / 8 | 1.868 s | 2.42 s | 578,140 KiB |

These auto timings include manifest scan, calibration, complete merge, deep
validation, and publication. The v2.1 media stage took 0.769 s for 384 files;
the v3.0 stage took 0.223 s for 81 files. JuiceFS did not report successful
`FICLONE`, so results classify them as copies; the very high logical throughput
and low process filesystem-block accounting indicate filesystem/server-side
copy optimization rather than physical client-side transfer. This is an
inference, not a reflink claim.

Slurm job `940` ran 60 complete no-validation oracle merges: three repeats for
each fixed worker candidate. Medians were:

| Target/axis | Auto choice | Oracle best | Auto-choice median | Distance |
| --- | ---: | ---: | ---: | ---: |
| v2.1 data, file=1 | 16 | 16 | 1.0267 s | 0% |
| v2.1 file, data=16 | 1 | 1 | 1.0225 s | 0% |
| v3.0 data, file=8 | 2 | 2 | 0.3892 s | 0% |
| v3.0 file, data=2 | 8 | 1 | 0.4220 vs 0.4174 s | 1.1% |

Every choice is within the required 5% of the measured environmental optimum.
The v3 external wall median was about 0.889 s for both file=1 and file=8.

## Correctness evidence

Slurm job `941` independently checked both outputs. It compared every non-system
Arrow value for all 128 episodes, verified global episode/frame/task mappings,
and compared encoded packet digests for all 384 episode-camera streams. Both
targets were equal. Both also passed built-in deep validation twice with no
errors or warnings.

The current official LeRobot `LeRobotDatasetMetadata` and `LeRobotDataset`
opened the v3.0 output, reported 128 episodes and 110,786 frames, and decoded
indices 0, 60,827, 60,828, and 110,785 as `(3, 480, 640)` images. Current
official LeRobot intentionally rejects direct v2.1 loading and asks users to
convert to v3.0, so v2.1 acceptance uses deep structural and full semantic
comparison. The official loader fell back from an unavailable torchcodec shared
FFmpeg library to PyAV and completed successfully.

The complete Python suite contains 40 passing tests, including same-version
v2.1/v3.0 merge, conflicting local task maps, byte-identical video files,
calibration/cache behavior, CLI operation, incompatible metadata, path safety,
deep validation, and transactional failure. Rust tests, formatting, and Clippy
with warnings denied also pass.
