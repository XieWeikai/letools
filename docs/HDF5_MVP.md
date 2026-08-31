# HDF5 source MVP acceptance

## Scope

The MVP adds an explicit `HDF5Mapping` and `HDF5Source` that can write either
LeRobot v2.1 or v3.0 through the existing backends. It supports one HDF5 file
per episode, frame-aligned numeric arrays, variable-length encoded image arrays,
a task dataset or fixed task, canonical LeRobot index/time columns, numeric
statistics, static planning, and batched image-to-video encoding.

It deliberately does not define an XVLA/Soft-Fold semantic preset, infer joint
names, choose optional robot fields, use irregular source timestamps as canonical
LeRobot timestamps, or compute camera pixel statistics. A later CLI tools layer
can now scan a representative episode, decode encoded-image dimensions, and save
the user's explicit choices as a JSON preset; see
[HDF5_PRESETS.md](HDF5_PRESETS.md). It does not change these semantic boundaries.

## Synthetic correctness

The unit fixture contains two HDF5 episodes, seven total frames, two tasks, two
mapped numeric vectors, and one JPEG camera. The suite checks:

- natural episode ordering and global/canonical generated columns;
- mapping validation and mapping-specific planner fingerprints;
- HDF5 to v2.1 and HDF5 to v3.0 conversion;
- deep validation of both targets;
- numeric/stat/task equality between both targets;
- complete video decode and batched frame reads.

The initial implementation at commit `54c7631` reported 19 passing Python
tests. The subsequent preset/TUI layer adds JSON round-trip, representative HDF5
inspection, scripted terminal interaction, stored-preset selection, CLI parsing,
and an end-to-end preset-driven HDF5 to v3 conversion with deep validation.
Slurm job 784 ran the complete suite with 4 CPUs and 8 GiB and reported 23
passed tests. The authoring and lookup layer is outside backend execution, and
the conversion coordinator, backends, Arrow primitives, and video primitives
are unchanged.

The release integration suite reports 28 passing Python tests. It additionally
checks direct JPEG packet muxing, buffer-backed HDF5 frame batches, bounded
streaming metadata caches, target-specific output placement, and safe spawned
media processes. Rust tests and Clippy with warnings denied also pass.

## Real HDF5 smoke test

The real smoke source was
`/data/share/datasets/XVLA-Soft-Fold/0930_10am_new/episode_0.hdf5`: one episode,
1,460 frames, and three 640 x 480 JPEG camera arrays. The temporary test mapping
exported qpos, action, source timestamp, three cameras, and language instruction;
it is test input and is not a repository preset.

Both conversions ran through Slurm with 8 CPUs, 16 GiB, two data workers, and
three video workers:

| Target | Job | Conversion | Video stage | Process MaxRSS |
| --- | ---: | ---: | ---: | ---: |
| v3.0 | 765 | 11.870 s | 11.725 s | 327,068 KiB |
| v2.1 | 766 | 4.202 s | 4.146 s | 23,424 KiB |

Both outputs deep-validated with zero errors and warnings. Their 1,460 numeric
frames compare equal. Timing differs because cache state was not controlled and
is smoke evidence, not a target-format performance claim.

The unmodified official LeRobot v3 metadata and dataset loaders opened the v3
output and decoded first, middle, and final camera samples as `(3, 480, 640)`.
The current official main loader intentionally rejects every v2.1 dataset with
its backward-compatibility error, so v2.1 was checked with letools deep
validation and semantic comparison.

## Full XVLA throughput

The fixed XVLA Soft Fold workload contains 108 episodes, 125,412 trajectory
frames, three JPEG cameras, 376,236 encoded media frames, and 20.739 GiB of HDF5
input. Five alternating Full samples on one H800 node used a 96-CPU/128-GiB
Slurm allocation but fixed execution at eight data and eight video workers.
Replacing the eight media threads with eight safe spawned processes retained
the same logical worker count while allowing independent h5py readers to run
outside its process-wide HDF5 lock.

| Target | Baseline wall | Isolated wall | Trajectory frames/s | Media frames/s | Speedup |
| --- | ---: | ---: | ---: | ---: | ---: |
| v2.1 | 20.41 s | 11.70 s | 10,719 | 32,157 | 1.74x |
| v3.0 | 19.18 s | 7.15 s | 17,540 | 52,620 | 2.68x |

Values are external CLI wall medians and include source construction, process
startup, serialization, conversion, and publication. The candidate increased
median CPU seconds from 61.33 to 85.36 for v2.1 and from 36.34 to 58.82 for
v3.0, both proportionally below throughput. Slurm step MaxRSS stayed near the
same 21.7 GiB accounting value. Deep validation, complete Arrow/statistics and
324-video packet comparisons, and official v3 loader samples passed.

Five subsequent iterations tested larger frame batches, larger v3 video shards,
direct v2.1 JuiceFS output, process-local HDF5 handle caching, and coarser process
dispatch. None met the cross-target acceptance policy, so the table above is the
final accepted implementation and throughput. Their reports are retained under
the ignored `self-improve/iterations/0036` through `0040` archive directories.

## Existing conversion regression

The frozen dagger medium fixture has 300 episodes, 239,314 frames, and 900
episode-camera videos. Baseline `main` commit `198c771` and candidate `54c7631`
ran B/C/B/C/B/C plus two additional pairs because initial spread exceeded 5%.
All timed runs used one Slurm node, 8 CPUs, 16 GiB, one data worker, eight video
workers, 100 MiB data groups, 256 MiB video groups, JuiceFS source/destination,
and no validation inside the timed command.

| Direction | Baseline samples (s) | Candidate samples (s) | Median change | Throughput change |
| --- | --- | --- | ---: | ---: |
| v2.1 to v3.0 | 7.09, 2.83, 2.86, 3.17, 2.94 | 2.88, 2.77, 2.82, 2.82, 2.98 | 2.94 to 2.82 s (-4.1%) | +4.3% |
| v3.0 to v2.1 | 7.32, 6.81, 6.51, 6.72, 6.99 | 6.21, 6.30, 7.14, 6.49, 6.46 | 6.81 to 6.46 s (-5.1%) | +5.4% |

Median process RSS changed from 368,960 to 358,916 KiB forward and from
371,600 to 349,300 KiB reverse. The first forward baseline was visibly cold and
is retained in the raw evidence rather than discarded. The median result shows
no measurable regression from source/media abstraction dispatch.

Slurm job 772 separately ran conversion validation outside timing. v3 output
and its v2.1 roundtrip both deep-validated with zero errors/warnings. Full
semantic comparisons checked all 300 episodes, 239,314 frames, and 900 encoded
packet streams in each direction and reported equality. Timed and correctness
outputs created by jobs 768, 769, and 772 were removed after each run; CSV and
Slurm logs remain under `/jfs/tmp/letools/` as local raw evidence.
