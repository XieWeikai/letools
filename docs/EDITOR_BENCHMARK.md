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
- Middle-episode deletion from an inter-frame video uses packet-preserving
  remux only after the bounded H.264 safety proof described below; inputs that
  fail that proof take the explicit CRF-0 re-encoding fallback. Removed MJPEG
  frames are physically absent. Missing/extra decoded frames are rejected.
  Encoder and publication failures preserve the previous destination, remove
  staging, and do not alter the source.
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

## H.264 deletion optimization (2026-09-30)

The initial implementation transcoded every retained physical file of a camera
if any file had a partial deletion. The editor now transcodes only the affected
files; whole retained files preserve their original packets and statistics.
This was measured with the same 12-episode, three-camera 640x480 fixture after
H.264 encoding (174 MiB, 23 video files). Episodes 1, 5, and 9 were deleted,
leaving 9 episodes and 8,678 frames. The editor changed six partial video files
and reused 14 whole ones, rather than transcoding 12 retained files.

Alternating baseline/candidate runs (B C B C B C) used Slurm job 4555 on
`H800-node11`, 16 CPUs, 64 GiB, four editor workers, local XFS, and warm,
uncontrolled OS cache. Median end-to-end CLI wall time was **68.788 s versus
32.237 s**, or **2.13x throughput** (126.2 versus 269.2 output frames/s).
Median CPU time fell from 319.6 to 127.9 CPU-seconds; peak aggregate RSS
was below 390 MiB in all six runs and peak thread count remained 65. This is a
specific H.264 deletion workload, not a general editor speedup. The earlier
official-tool comparison used a different output encoding policy: official
deletion retained source-like settings, while this fallback uses CRF 0 and
recomputes output pixel statistics. Its previously measured 29.436 s median is
therefore a reference point, not an identical-workload speed comparison.

In a fresh, same-allocation four-worker comparison (Slurm 4559), official
LeRobot deletion took 31.033 s median versus LeTools' 32.457 s, and official
re-encoding took 62.216 s versus LeTools' 65.247 s. At eight workers
(Slurm 4560), re-encoding took 38.942 s official versus 46.023 s LeTools.
Thus the improvement over the previous editor is accepted, but the editor
does **not** yet exceed official throughput on either H.264 operation. Output
policies differ as described above, so these times are operational rather
than bit-exact-equivalent comparisons.

The candidate output passed deep dataset validation, row/episode/task checks,
byte identity of the 14 reused video files, and official v3 metadata and
dataset loader checks at all 18 retained episode boundaries. The complete
bidirectional dagger conversion gate is tracked in the local self-improve
iteration report.

## Safe H.264 packet remux (2026-09-30)

Iteration `0056` added a fail-closed packet audit for partial H.264 deletion.
On the same 12-episode fixture, the six affected H.264 files were proven
IDR-aligned and remuxed without decoding; zero files were transcoded. The
checker compared **26,034 retained packet payloads and 26,034 decoded RGB
frames** against the immutable source, and LeTools deep validation passed.
The editor suite passed 22/22 tests, including a non-IDR boundary that is
required to reject the fast path.

The formal B/C/B/C/B/C run used Slurm job 4569 on `H800-node11` (16 CPUs,
64 GiB, four workers, local XFS, warm cache). Candidate deletion had a median
wall time of **0.91 s** (9,536 output frames/s); the accepted 0055 baseline had
a median of **31.90 s** (273 output frames/s), a **35.0x** wall-throughput
improvement. A fresh same-allocation official comparison (job 4570) measured
official deletion at **30.04 s** median (289 frames/s), so the candidate is
**33.0x faster** on this workload. Candidate peak aggregate RSS was about
216 MiB; official peak RSS was about 580 MiB. These numbers cover deletion
without an explicit video transform; requested resize/codec changes still
take the transcode path.

## Automatic codec-thread tuning (2026-10-01)

Iteration `0067` tuned only the omitted-concurrency transcode path. The
accepted baseline used eight media workers with one FFmpeg codec thread. The
candidate keeps eight workers and selects two codec threads. A later audit
found that an explicit `codec_threads` value was also overridden if `workers`
was omitted; the contract correction is documented below. The worker count
avoids the rejected 0066 policy whose measured RSS increase exceeded the
protocol's proportional-resource allowance.

The B/C/B/C/B/C run used Slurm job `4640` on `H800-node11`, one task, 16 CPUs,
64 GiB, the frozen 12-episode H.264 fixture, and warm uncontrolled cache.
Median measurements were:

| implementation | workers | codec threads | wall s | episodes/s | frames/s | mean CPU cores | peak RSS MiB | peak threads |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| accepted baseline | 8 | 1 | 42.414 | 0.2829 | 291.6 | 10.31 | 595.6 | 95 |
| candidate | 8 | 2 | 35.245 | 0.3405 | 351.0 | 14.11 | 630.3 | 114 |

The candidate is **1.203x faster** (+20.3%). RSS is 1.058x and peak threads
1.20x, both within the protocol allowance derived from the throughput factor;
CPU occupancy rises from 64% to 88% of the 16-CPU allocation. Median CPU time
is 496.2 seconds versus 437.3 seconds: this is an elapsed-time improvement with
increased CPU cost, not improved CPU efficiency. These measurements alone do
not isolate the cause of the additional work; no CPU or memory allocation was
exceeded.

The deletion regression check used five alternating baseline/candidate runs in
Slurm job `4649` with four explicit workers. Medians were 6.123 s versus 6.380
s (4.2% difference), within the warm-cache/run-to-run noise observed in this
remux workload; the code path and effective plan are unchanged for deletion. A
separate three-run comparison in job `4647` measured official deletion at
36.247 s median versus 4.871 s for the candidate (7.4x wall-throughput
advantage), with the expected cache variance in the editor lanes.

The candidate output passed the editor's built-in validation and the official
LeRobot metadata/dataset loader boundary checks in job `4648` for both a
transcode output (12,370 frames) and a deletion output (8,678 frames). The
focused candidate suite had 38 passes and eight v3 fixture failures caused by
the existing PyAV/FFmpeg MJPEG frame-count probe; the accepted baseline showed
the identical eight failures in job `4646`, so this is not a candidate
regression. The real H.264 acceptance fixture and loader checks were clean.

## Concurrency contract and official comparison (2026-10-01)

The automatic policy now distinguishes omitted codec threads (`None`) from an
explicit value such as `1`. Specifying either concurrency option preserves its
explicit value and uses the conservative resource caps. Automatic deletion no
longer warns merely because the dataset has fewer files than the default job
limit. These are correctness fixes, not new performance improvements.

Job `4691` compared accepted `1098f7d`, the corrected source, and unmodified
official LeRobot `fb5cfec7` in three serial alternating rounds. It used the
same 12-episode / 12,370-frame / three-camera 640x480 H.264 fixture, 16 CPUs,
64 GiB, local scratch XFS, warm/uncontrolled cache, BLAS/OpenMP pools of one
thread and a Rayon pool of 16. LeTools auto selected eight jobs and two encoder
threads; official reencode used eight jobs and one encoder thread.

| Reencode implementation | Wall s | Episodes/s | Frames/s | CPU s | Peak RSS MiB | Peak threads |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| LeTools before contract fix | 35.077 | 0.3421 | 352.65 | 494.79 | 730.98 | 84 |
| LeTools after contract fix | 35.167 | 0.3412 | 351.75 | 494.38 | 695.16 | 84 |
| Official LeRobot | 35.202 | 0.3409 | 351.40 | 225.65 | 3113.09 | 136 |

Reencoding is effectively tied with official in this run. The contract fix's
0.26% wall-time change is within measured variation. Both tools use H.264
CRF 23/veryfast at the original resolution, but official uses GOP 2 while
LeTools forces episode IDRs without B-frames. LeTools additionally recomputes
all-pixel statistics from decoded output; the inspected official reencoder
retains existing episode statistics. This is an operational comparison, not
an equal-bitstream or equal-quality benchmark. Official's initial copy is
included to give both tools a fresh-output contract.

The full Python suite passed 119 tests in jobs `4690` and `4697`. The medium
outputs passed deep validation, numeric/task-row comparison, deletion packet
digests, and official metadata/dataset boundary reads in job `4694`. A stricter
full-conversion loader check also exposed missing pandas task-index metadata
in the existing conversion and merge writers. These writers now retain task
text as both a physical Arrow column and the pandas row index, matching the
already-correct editor writer and the official task lookup contract.

Job `4695` completed five total deletion samples per implementation. Medians
were 0.854 s before the correction, 0.805 s after it, and 29.924 s official
(10,786 versus 290 output frames/s, approximately 37x on this fixture).
The deletion fast path is unchanged; its short timings remain noisy.

The full dagger gate in job `4694` covered both directions and both roundtrips,
each with 3,457 episodes, 2,415,341 frames, and 10,371 video-slice comparisons.
After the task-index fix, job `4698` regenerated both full v3 outputs, passed
deep/semantic/payload comparisons, and passed official metadata and dataset
loaders with text tasks. Earlier outputs were left intact for traceability.

Five paired core-conversion checks (`4699`) passed semantic/payload comparison
for every output, but timings were too variable for a precise regression
bound: forward medians were 2.863/4.067 s and reverse 15.693/13.400 s. Baseline
forward samples alone ranged from 1.257 to 25.733 s. This metadata correction
is retained for correctness; these noisy measurements are not presented as a
conversion performance improvement or a guarantee of unchanged throughput.
