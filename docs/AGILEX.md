# AgileX source acceptance

## 1. Implemented source contract

`AgileXSource` converts one directory containing `episodeN` subdirectories.
Every episode must provide these timestamp-named streams:

```text
arm/jointState/{puppetLeft,puppetRight,masterLeft,masterRight}/*.json
camera/color/{left,front,right}/*.jpg
```

Each JSON object supplies a seven-value `position`. Puppet left/right positions
form the 14-value `observation.state`; master left/right positions form the
14-value `action`. The newest common camera frame count is retained. Left-camera
timestamps are the clock, and each joint stream uses the latest message at or
before a frame timestamp. LeRobot timestamp, frame, episode, global, and task
indices are generated deterministically.

The caller must provide one non-empty instruction. It is written as task 0,
assigned to every episode, and referenced by every frame. The baseline source
does not infer language from directory names or support different instructions
within one episode.

## 2. Accepted command

The same source object feeds either existing backend:

```bash
letools convert /data/agilex /data/agilex-v21 \
  --source-format agilex \
  --instruction "do something" \
  --to v2.1 --auto

letools convert /data/agilex /data/agilex-v30 \
  --source-format agilex \
  --instruction "do something" \
  --to v3.0 --auto
```

Defaults are 30 FPS and robot type `cobot_magic`. Both are explicit CLI
overrides. Raw-source auto-detection remains intentionally disabled; path-only
`open_dataset()` continues to recognize physical LeRobot v2.1 and v3.0 layouts.

## 3. Real workload

Acceptance used `/home/agilex/data_episode/do_something` on an Intel i7-13700
with 24 effective CPUs, 31.1 GiB available memory, and local ext4/NVMe storage.
No Slurm allocation was available, so these measurements are a documented local
substitute lane rather than results comparable to the repository's Slurm/H800
benchmarks.

| Property | Value |
| --- | ---: |
| Episodes | 50 |
| Retained trajectory frames | 42,664 |
| Cameras | 3 |
| Retained media inputs | 150 episode-camera streams |
| JPEG files in the raw recording | 128,043 |
| Retained JPEG input bytes | 4,559,782,899 bytes (4.25 GiB) |
| Default output encoding | MJPEG/yuvj420p packet mux |
| v2.1 output size | 4.3 GiB |
| v3.0 output size | 4.3 GiB |

The older reference metadata reports 42,660 frames. The current raw directory
contains one additional usable common-camera frame in each of episodes 17, 19,
24, and 33. The source follows current physical stream counts rather than
hard-coding those four historical trims.

## 4. Performance

External wall time is authoritative because CLI source construction occurs
before the conversion coordinator's recorded `ConversionResult`. Input
throughput below uses the 4,559,782,899 retained JPEG bytes; it does not count
JSON bytes or claim storage-device bandwidth.

| Target/configuration | External wall | Frames/s | Input MiB/s | Recorded conversion |
| --- | ---: | ---: | ---: | ---: |
| v2.1, fixed 8 data / 3 video workers | 18.64 s | 2,289 | 233 | 9.49 s |
| v3.0, fixed 8 data / 3 video workers, 200 MiB video groups | 15.95 s | 2,675 | 273 | 3.09 s |
| v2.1, first `--auto --no-cache` | 22.42 s | 1,903 | 194 | 6.93 s |
| v3.0, first `--auto --no-cache` | 18.45 s | 2,312 | 236 | 6.16 s |

The first v2.1 auto plan selected 8 data and 8 video workers. The first v3.0
auto plan selected 1 data worker, 8 video workers, and 100 MiB video groups.
Peak RSS across these runs was 249-316 MiB. Direct MJPEG mux avoids pixel
decoding and lossy re-encoding; compact AV1, H.264, or MPEG-4 output has a
different CPU, quality, size, and throughput profile and is not represented by
these numbers.

Repeated warm-cache v3 auto baselines during self-improvement had a 12.03 second
five-sample median. A 200 MiB planner candidate measured 11.93 seconds, only
0.8% faster, and was rejected under the 3% acceptance rule. Cold/warm cache
classification therefore matters more than the apparent difference between
single samples.

## 5. Correctness evidence

- The complete Python suite passes: 31 tests.
- Generated v2.1 and v3.0 datasets pass `letools validate --deep` without
  errors or warnings.
- Cross-layout comparison is equal for 50 episodes and 42,664 Arrow rows.
- Packet comparison is equal for all 150 episode-camera videos.
- Task tables contain the configured instruction and every frame has
  `task_index = 0`.
- The Git worktree was clean after reverting every rejected optimization.

## 6. Five-iteration optimization audit

Iterations 0041-0045 followed the repository protocol. None produced a stable
improvement at or above the 3% measurable-performance threshold:

| Iteration | Candidate | Decision |
| --- | --- | --- |
| 0041 | Rust/Rayon bulk file-size inspection | Rejected: +2.0% wall, +4.8% RSS |
| 0042 | Single-read JSON and size accounting | Rejected: +2.3% wall |
| 0043 | Eight-thread episode scan | Rejected: 2.58x slower |
| 0044 | Retained timestamps and NumPy search | Rejected: 4.5% slower |
| 0045 | 200 MiB local thread-frame auto groups | Rejected: +0.8% full wall |

The dominant remaining cost is traversal, opening, and parsing of many small
JSON files. Meaningful further work likely requires a coarse native batch
reader or a recording-side manifest/index. That is a larger source-format and
native-boundary change and should begin with a new protocol iteration.

## 7. Current limitations

- One fixed instruction applies to the complete source dataset.
- Only the documented dual-arm position fields and three color cameras are
  exported; depth, point clouds, localization, IMU, lidar, lift, and base data
  are ignored by this baseline.
- Numeric episode statistics are generated, but camera pixels are not decoded
  to calculate image statistics.
- Default MJPEG output is close to source JPEG size. The older AV1 reference is
  much smaller, but is lossy and requires substantially more encoding work.
- Timestamp continuity and source-message latency are validated structurally;
  the baseline does not interpolate joint positions.
