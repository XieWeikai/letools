# Editor acceptance and benchmark

Measured on 2026-09-29 for the initial `feat/dataset-editor` implementation,
based on main `44b71c9`. This is an MVP acceptance report, not a claim of optimal
performance on every codec, filesystem, or dataset.

## Environment and measurement boundary

- All dataset construction, editing, conversion, and loader tests ran through
  Slurm, pinned to `H800-node11` because this machine's `/scratch` is node-local.
- Performance allocation: **16 CPUs, 64 GiB RAM**, no GPU. Host: Intel Xeon
  Platinum 8468V, 192 logical CPUs. Measurements concern this allocation, not the
  entire host's capacity.
- Input and output: `/scratch`, XFS on `/dev/nvme2n1p1`, reflinks available. Warm,
  OS-managed page cache; no `drop_caches`. Physical disk bandwidth was **not**
  independently measured. Reused-byte throughput is not physical I/O throughput.
- Fixture: first 12 episodes of
  `XVLA-Soft-Fold/0930_10am_new_lerobot_v2_1`; **12,370 dataset frames**,
  **37,110 camera images**, three 640×480 RGB MJPEG cameras at 30 FPS.
  v2.1 logical file sizes total **2,186,106,131 bytes (2.036 GiB)**. Its v3
  fixture uses a 128 MiB video-shard target. Original shared datasets were not
  edited; fixtures and all outputs reside on scratch.
- Python 3.12.14, PyAV 16.1.0, editor's bundled FFmpeg 7.0.2 executable. Main and
  branch conversion comparisons used the same locked environment and published
  `letools-native==0.2.0`, changing only the Python source revision. A separately
  built current-branch FFmpeg-8 native wheel was also correctness-tested.
- Three repetitions per configuration, version/worker order alternated.
  Reported numbers are medians. CLI wall time includes Python startup, planning,
  output validation, and publication; fixture setup and acceptance comparisons
  are outside timed runs. Overwrite of the preceding output is included.
- `/proc` sampling every 50 ms covers the full process tree, including children
  spawned by Rust threads. CPU is accumulated child user+system time / wall
  time. Reported memory is the median of sampled per-run peak aggregate RSS,
  **not** a hard memory bound or a cgroup peak; brief spikes can be missed.
  The polling interval also adds up to approximately 50 ms wall-time uncertainty.

## Editor results

`task` changes the last episode's task. `delete + feature` removes episodes
1, 5, and 9 and removes `action`, leaving 9 episodes / 8,678 frames. Resize
transforms **all three cameras** to 224×224 H.264, CRF 23, `veryfast`, one encoder
thread per file, and recomputes statistics from decoded encoded output.
Frames/s means dataset timesteps, not the sum across cameras.

| Version / operation | Workers | Wall s | Output episodes/s | Output frames/s | Mean CPU cores | Peak RSS MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| v2.1 task | 4 requested | 0.602 | 19.92 | 20,537 | 0.84 | 162.3 |
| v3.0 task | 4 requested | 0.653 | 18.39 | 18,956 | 0.83 | 182.9 |
| v2.1 delete + feature | 4 | 0.603 | 14.93 | 14,394 | 0.87 | 188.7 |
| v3.0 delete + feature | 4 | 0.703 | 12.80 | 12,345 | 1.08 | 184.6 |
| v2.1 resize + encode | 1 | 50.630 | 0.237 | 244.3 | 2.01 | 181.7 |
| v2.1 resize + encode | 4 | 15.294 | 0.785 | 808.8 | 7.85 | 275.6 |
| v2.1 resize + encode | auto → 8 | **10.335** | **1.161** | **1,196.9** | **13.64** | 529.8 |
| v3.0 resize + encode | 1 | 50.317 | 0.238 | 245.8 | 2.02 | 192.6 |
| v3.0 resize + encode | 4 | 15.652 | 0.767 | 790.3 | 7.57 | 288.9 |
| v3.0 resize + encode | auto → 8 | **10.436** | **1.150** | **1,185.4** | **13.30** | 414.5 |

Automatic concurrency is 4.90× / 4.82× faster than one worker for v2.1 / v3.0
resize. Mean CPU occupancy is about 85% / 83% of the allocation. Peak sampled
process counts are 9 (editor + up to eight FFmpeg children), and peak sampled
thread counts are 63 / 64. Thread counts include sleeping/control threads and
are not equivalent to simultaneously executing CPU cores. No CPU-saturation or
near-optimality guarantee is inferred from this one workload.

Task edits do not decode video and only rewrite the affected Parquet shard.
v2.1 deletion reuses whole retained videos. v3 deletion compacts affected MJPEG
files through packet split/concat. Consequently these are fundamentally
different workloads from lossy re-encoding. Their sub-second timings are
strongly influenced by Python startup and the local reflink/cache behavior;
they must not be extrapolated to slow-copy network filesystems.

## Existing conversion regression

12 episodes / 12,370 frames, eight data workers and three video workers; same
fixture, environment, and measurement method, three interleaved repetitions.

| Direction | Main wall s | Branch wall s | Main episodes/s | Branch episodes/s |
| --- | ---: | ---: | ---: | ---: |
| v2.1 → v3.0 | 1.155 | 1.105 | 10.385 | 10.860 |
| v3.0 → v2.1 | 1.055 | 1.004 | 11.377 | 11.949 |

**No observed throughput regression in these tests.** The small apparent gains
are similar to sampling/timing variability, so they are not presented as an
optimization claim. Every conversion was compared for metadata, numeric values,
and compressed packet payloads. HDF5, provider, planner, merge, and distributed
contracts retain their existing regression tests; this report does not claim a
new large-scale performance campaign for all those paths.

## Correctness acceptance

- **112 Python tests passed**, including the existing full suite and editor
  suite, against both the published native runtime and a freshly built current
  native FFmpeg runtime. **Two Rust reducer unit tests passed**; Rust formatting
  and clippy checks passed. Optional dependency absence and isolated uv tool
  command installation were also checked.
- Both LeRobot versions: no-op equivalence, task override, multitask preservation,
  episode compaction, frame/global/task indices, named splits, feature removal,
  camera selection, packet preservation, and unchanged source hashes.
- H.264/MJPEG outputs, channel-first/channel-last metadata, resized dimensions,
  all-pixel output statistics, opposite-version remux, and full decoding of
  converted test videos. Statistics are compared with independently decoded
  PyAV pixels within one RGB quantization step, accounting for FFmpeg-version
  color-conversion differences.
- Middle-episode deletion from an inter-frame video takes the explicit safe
  re-encoding path. Removed MJPEG frames are physically absent. Missing/extra
  decoded frames are rejected. Encoder and publication failures preserve the
  previous destination, remove staging, and do not alter the source.
- Real-data outputs for all three operations in both versions were deeply
  validated. Every retained numeric row, task label, episode/frame index, and
  (for non-transcodes) every retained camera packet digest was checked.
- **Official `LeRobotDatasetMetadata` and `LeRobotDataset` passed** for task,
  delete/feature, and resize results in both versions: first and last frame of
  every retained episode, task strings, all camera tensor dimensions, and total
  lengths. v3 additionally includes the synthetic combined-edit output.
  These are boundary-loader checks, not a claim that the official loader read
  every real-data video frame.
- Official v2.1 environment: LeRobot 0.3.3, datasets 3.6.0, torch 2.7.1+cpu,
  torchvision 0.22.1+cpu, PyAV backend. Newer datasets/torchvision APIs are not
  compatible with that historic loader; the isolated acceptance environment
  pins its supported versions, without editing official code. v3 used the
  existing official LeRobot checkout `fb5cfec7ab75aa59e350a15d911270009a4e12b2`.

## Reproduce

Install the addon and test tools as documented in [Editor](EDITOR.md), then on
a Slurm cluster (select a node where the checkout and scratch paths exist):

```bash
sbatch --nodelist=YOUR_NODE --output=/scratch/editor-tests.log scripts/test_editor.sbatch

sbatch --nodelist=YOUR_NODE --cpus-per-task=16 --mem=64G --time=00:30:00 \
  --output=/scratch/editor-benchmark.log \
  --wrap='.venv/bin/python scripts/benchmark_editor.py /datasets/source-v21 /scratch/editor-bench'

# Reuse prepared inputs for the default-concurrency sweep.
sbatch --nodelist=YOUR_NODE --cpus-per-task=16 --mem=64G --time=00:15:00 \
  --output=/scratch/editor-auto.log \
  --wrap='.venv/bin/python scripts/benchmark_editor.py /datasets/source-v21 /scratch/editor-bench --workers auto --cases resize-h264 --report results-auto.json'
```

Set `OMP_NUM_THREADS=1`, `OPENBLAS_NUM_THREADS=1`, `MKL_NUM_THREADS=1`, and
`RAYON_NUM_THREADS=16` in the benchmark job environment to match this run.
`--baseline-src /path/to/main/src` adds the conversion comparison. It deliberately
shares dependencies/native runtime, isolating the Python-source change. The
script records commands, configuration, per-stage time, process resources, and
all measurements as JSON under the supplied work directory. It can overwrite
its own output datasets; use a dedicated benchmark directory.

Run `scripts/check_editor_loaders.py OUTPUT...` using a separate compatible
official LeRobot Python environment, also through Slurm. Official training and
robot dependencies are not added to LeTools or to the optional editor.

This run's local raw records are under
`/scratch/shrelic/letools-editor-benchmark/{results.json,results-auto.json}`.
Benchmark Slurm jobs: 4375 and 4380; semantic real-output check: 4384; final
official loader jobs: 4378 (v3) and 4385 (v2.1); current-native suite: 4381.
The final full Python suite passed again in job 4386; MkDocs strict build passed.
These scratch records are not portable repository assets; the scripts and this
summary are versioned so the experiment can be recreated elsewhere.
