# letools self-improvement summary

Status: editor campaign integrated; future optimization requires a new measured iteration.

## Editor campaign

The editor campaign used `feat/dataset-editor` as its acceptance branch before
integration into main. Accepted optimization history includes `17f699e`
(limit H264 deletion reencoding to affected files), `0e3a155` (prove IDR-aligned
packet remux), and `1098f7d` (automatic eight-job/two-codec-thread plan).
The editor's measurements and contracts are in [the benchmark report](../docs/EDITOR_BENCHMARK.md)
and [editor guide](../docs/EDITOR.md).

Iterations 0068 and 0069 were rejected; Rust packet auditing was slower and a
combined probe did not establish an end-to-end gain. Iteration 0070 is a
correctness correction, not an additional performance optimization: explicit
codec-thread choices survive auto planning, and conversion/merge v3 task
tables now expose text labels through the official loader's pandas index.
119 tests pass, full dagger directions/roundtrips pass deep and packet checks,
and regenerated full v3 outputs pass official metadata/dataset loaders.

Iteration 0071 accepts larger-transcode-first submission, with results still
applied in manifest order. Its three-pair reencode median improves from
35.436 to 33.917 s (+4.48% throughput), with slightly lower RSS and unchanged
84 peak threads under 16 CPUs/64 GiB. Independent output comparisons, official
loaders, 119 tests and a four-CPU v2.1 control pass. Deletion controls fluctuate
in the unchanged Parquet stage; their media work remains about 0.02 s, so a
tight end-to-end deletion regression bound is not established.

A fresh five-pair comparison against **tuned** official reencoding (eight jobs,
two encoder threads) measures 34.459 s LeTools / 30.984 s official. Reencoding
has not beaten this official lane. The earlier five-sample deletion comparison
was 0.805 s / 29.924 s on the frozen 12-episode fixture. GOP and output-statistics
policies differ and are documented; this is not equal-bitstream performance.
The core conversion check also had excessive timing noise and does not
establish a tight no-regression bound. Drafts, complete samples and diagnostic
spans are archived outside Git.

The accepted scheduling commit is `265273a`. Follow-ups 0072 and 0073 were
rejected and restored: bounded RGB integer reductions improved the median
8.49% but failed the twice-noise gate (baseline CV 12.78%); enlarging the decode
pipe improved only 0.64% and raised peak RSS 14.55%. Both passed their Python/
Rust tests but failed before a full acceptance gate, and neither produced a
performance commit. The three-pair 0073 unchanged baseline measured 33.654 s
versus 31.828 s tuned official, so no reencoding lead is claimed. Raw records
remain in ignored iteration directories. Future work starts from the integrated
main tip, with a fresh profile and the next unused iteration number.

## Prior conversion campaigns

Twenty accepted optimizations are present on the current acceptance history,
including the six accepted HDF5-source optimizations. All conversions and full
comparisons ran as single-node Slurm jobs within the protocol resource ceiling.
The HDF5 source, preset tooling, documentation, and accepted performance work
were integrated into `main` after completing the feature campaign.

Iteration 0046 is accepted below. It removes redundant per-video temporary
renames inside the conversion coordinator's hidden staging transaction while
preserving atomic behavior for standalone split callers.

Iteration 0047 was rejected because episode fan-out multiplied the v3 Arrow
table cache by worker count (about 6x RSS), despite a faster data stage.
Iteration 0048 fixes that specific issue with a shared current-shard cache and
is accepted below.

Iteration 0050 is accepted below. It moves the remaining direct-JPEG packet
mux loop into a capability-gated Rust/FFmpeg primitive while retaining the
portable PyAV implementation. Iterations 0051 and 0052 found no acceptable
batch-size or encoder-thread policy change.

## Accepted optimizations

| Commit | Change | Target result |
| --- | --- | --- |
| `e7be015` | Precompute video split timestamps | reverse conversion throughput +6.26% |
| `12992b9` | Avoid `Fraction` work in packet digests | full comparison throughput +14.16% |
| `0ddbda6` | Reduce digest workers from eight to four | full comparison throughput +11.58% |
| `bbd4784` | Use up to three video workers by default | default conversion throughput +91.55% |
| `8b92b01` | Skip local concat `faststart` relocation | forward conversion throughput +8.47% |
| `a921bcc` | Skip local split `faststart` relocation | CPU seconds -18%; low-resource throughput +3.69% |
| `467f467` | Reduce digest workers from four to three | full comparison throughput +32.79% |
| `b7b2b89` | Reduce digest workers from three to two | full comparison throughput +41.56% |
| `6f95db8` | Move packet payload digests into Rust | full comparison throughput 2.86x |
| `167c06f` | Move FFmpeg concatenation into Rust | forward conversion throughput 2.05x |
| `531432b` | Move episode video splitting into Rust | reverse conversion throughput 1.32x |
| `aed74b9` | Batch direct MJPEG mux in Rust | HDF5 throughput +51.44% to v2.1, +5.19% to v3.0 on JuiceFS |

Each percentage compares the candidate median with the current-main baseline
for that iteration. Results from different workloads are not multiplied.

## Iteration 0050: native batched MJPEG mux

The accepted primitive retains one FFmpeg MP4 context in Rust and receives a
batch of encoded JPEG `bytes` per Python call. This removes per-frame PyAV
packet creation and Python/FFmpeg crossings without changing the generic
`FrameSequence`, source-provider, planner, or backend contracts. Old native
wheels automatically use the original PyAV fallback; transcoding is unchanged.

Five alternating baseline/candidate pairs ran on H800-node14 with 16 CPUs,
64 GiB, eight data workers, eight video workers, `/data` input, and `/jfs`
output. Both paths are the same JuiceFS mount.

| Target | Baseline median | Candidate median | Throughput | CPU seconds | RSS | Threads |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| HDF5 -> v2.1 | 12.444 s | 8.217 s | +51.44% | 61.15 -> 32.41 | 1274 -> 1247 MiB | 141 -> 21 |
| HDF5 -> v3.0 | 8.427 s | 8.011 s | +5.19% | 36.96 -> 34.75 | 1213 -> 1204 MiB | 141 -> 21 |

Both outputs passed deep validation and complete 324-video payload comparison.
The official LeRobot metadata and dataset loaders opened the v3.0 result and
read its first, middle, and final samples. Full dagger remux and MPEG-4 controls
were also deep-validated; their execution paths do not enter the new primitive.

Iteration 0051 retained the 48-frame default: the best v2.1 alternative was
only 2.55% faster with 13.0% more RSS, and v3.0 samples were order-confounded.
Iteration 0052 rejected `4 video workers x 2 codec threads` after its first
full HDF5 sample took 205.057 seconds versus 121.134 seconds for `8 x 1` and
reduced observed CPU use from 7.74 to 4.65 cores. No planner policy changed.

## HDF5 source campaign

## Iteration 0048: shared v3 shard cache

The v3.0 -> v2.1 target direction improved by 12.77% on the five-sample Full
dagger median (70.226 -> 62.274 s) while RSS fell to 0.987x and CPU seconds
fell. The non-target forward direction remained within noise (-1.71%), and
HDF5 v2.1/v3.0 five-sample wall times also fell (-9.32% and -1.70%). Deep validation,
official loaders, packet payloads, both roundtrips, and failure publication
checks passed. Raw evidence is in the ignored
`iterations/0048-shared-shard-cache/` archive.

## Iteration 0046: staged v2.1 media output

The accepted commit is the campaign commit that contains this summary update.

The candidate passed five-sample Medium comparisons on both NFS `/home`
(20.733 -> 19.064 s, +8.76%) and JuiceFS `/jfs` (18.180 -> 16.823 s,
+8.07%) with 16 CPUs and 48 GiB. On the complete dagger workload the final
five-sample medians improved v2.1 -> v3.0 by 1.64% (within noise) and v3.0
-> v2.1 by 9.77%, with no resource-ratio or HDF5 regression. Deep validation, official loaders, packet payloads, both
roundtrips, and failure-publication checks all passed. The detailed raw
evidence remains in the ignored `iterations/0046-staging-direct-video/` archive.

The fixed XVLA Soft Fold workload contains 108 episodes, 125412 trajectory
frames, three JPEG cameras, 376236 encoded frames, and 20.7 GiB of HDF5 input.

| Commit | Change | V2.1 result | V3.0 result |
| --- | --- | ---: | ---: |
| `e200c49` | Retain HDF5 frame readers across batches | +9.5% | +7.2% |
| `1c59ca7` | Packet-mux JPEG values without transcoding | 2.70x | 3.07x |
| `2f37e36` | Pass HDF5 buffers without a bytes copy | +16.6% | +11.7% |
| `720b66e` | Balance frame batches at 48 | +5.3% | +4.2% |
| `6716c7e` | Write grouped v3 shards directly to staging | no regression | +5.6% |
| `d7e4469` | Isolate h5py media workers with safe spawn processes | 1.74x | 2.68x |

At the process-isolation campaign's fixed eight-worker setting, v2.1 converted
in an 11.70 second five-sample external-wall median (9.231 episodes/s, 10719
trajectory frames/s, and 32157 media frames/s). Its v3 target median was 7.15
seconds (15.105 episodes/s, 17540 trajectory frames/s, and 52620 media
frames/s). These historical measurements predate iteration 0050, use a
different allocation/cache state, and are not products of per-commit speedup
ratios.

Fourteen later HDF5 candidates were rejected: PyAV batch mux, batch 64 from the
older baseline, packet-mux planner 6/64, direct staging for both layouts,
sequential Rust cross-device copy, planner 7/64, PyAV `mux_one`, and explicit
FFmpeg packet buffering, plus a forkserver follow-up that improved v2.1 but
missed the v3.0 acceptance threshold. Copy concurrency limits and source/chunk
profiles were also retained as read-only baseline evidence.

Iterations 0036-0040 tested five further directions without changing the
accepted implementation. Larger HDF5 frame batches (96 and 192) improved the
best v3 sample by only 1-2% and did not clear the threshold. Raising the v3
video shard target from 400 to 800 MiB regressed the median by 4.1%. Direct
v2.1 process output to JuiceFS regressed the median by 4.9%. A process-local
HDF5 handle LRU was flat to slightly slower for both targets while increasing
resident memory. Finally, process-map chunks of three improved v3.0 by 4.0%
but regressed v2.1 by 10.5%, so the global scheduling change was rejected.

## Rejected experiments

### AgileX source campaign

Iterations 0041-0045 evaluated five optimization directions on the 50-episode,
42664-frame, 4.25 GiB `do_something` workload without changing the accepted
AgileX implementation:

| Iteration | Candidate | Result |
| --- | --- | --- |
| 0041 | Rust/Rayon bulk file-size inspection | source open +2.0%, below threshold; RSS +4.8% |
| 0042 | Single-read JSON parsing and size accounting | source open +2.3%, below threshold |
| 0043 | Eight-thread episode scan | 2.58x slower; system CPU and RSS increased |
| 0044 | Retained timestamps and NumPy search alignment | 4.5% slower |
| 0045 | 200 MiB local thread-frame auto groups | full auto wall +0.8%, below threshold |

The local substitute lane used an Intel i7-13700 with 24 effective CPUs,
31.1 GiB memory, and ext4 on NVMe because no Slurm allocation was available.
Runs were serial with explicit cache classification. The fifth iteration used
five alternating full B/C pairs after the initial spread exceeded 5%: baseline
median 12.03 seconds and candidate median 11.93 seconds. All candidates were
reverted under the protocol's 3% measurable-improvement gate.

Eight candidates were measured and rejected: target-local concat staging, concat
copyfile publication, split copyfile publication, split parent-directory
deduplication, one split temporary directory per job, and concurrent left/right
dataset digest pools, a global cross-camera video pool, and zero-copy packet
hashing. Their drafts, diffs, and reports remain in `iterations/`.

The half-node resource curve used 8/16/32/64/96 CPUs and reached the permitted
96 CPU / 999242 MiB ceiling. Forward conversion saturated at about 3.3 utilized
cores because JuiceFS I/O, not CPU or memory capacity, was limiting. Larger
video groups and tmpfs staging were profiled but did not clear the acceptance
threshold.

Iteration 0020 audited the remaining Python/Rust boundary and stopped further
lowering. All packet-count-proportional loops are now Rust/FFmpeg; Parquet and
Arrow work already runs in PyArrow C++, statistics are vectorized NumPy, and
the remaining Python work is bounded episode planning and plugin policy. The
final reverse profile used only 57.07 CPU seconds over 109.01 wall seconds while
writing about 39 GiB, leaving I/O wait rather than a Python hot loop.

## Final correctness

- Python tests: 4 passed with the released native wheel and 4 passed with the
  native package removed and uv auto-sync disabled.
- Rust build/tests: passed.
- v2.1 to v3.0 and v3.0 to v2.1 outputs passed deep validation with no errors
  or warnings.
- Both roundtrip directions compared equal for 3457 episodes, 2415341 frames,
  and 10371 episode/video packet payload digests.
- Official `LeRobotDatasetMetadata` and `LeRobotDataset` loaded the final v3.0
  output and decoded frames 0, 1207670, and 2415340.
- Official current LeRobot intentionally rejects direct v2.1 loading; it loaded
  the equivalent v3.0 roundtrip for acceptance.

## Historical output locations

These paths record earlier campaigns, not a promise that generated datasets
remain available; temporary benchmark storage may have been cleaned. Verify
existence before reuse and keep original dataset sources immutable.

- Final v3.0: `/jfs/tmp/letools/si-0013-roundtrip-v30`
- Final v2.1 roundtrip: `/jfs/tmp/letools/si-0013-low-c`
- Immutable v2.1 source: `/jfs/tmp/letools/dagger_v3_official_old`
- Existing v3.0 oracle: `/jfs/tmp/letools/dagger_v30_letools_fixed`
- Rust-split validated v2.1: `/jfs/tmp/letools/si-0019-c1`
- Rust-split v3.0 roundtrip: `/jfs/tmp/letools/si-0019-roundtrip-v30`

The governing process is [PROTOCOL.md](PROTOCOL.md). Future cycles use the next
unused number after the largest archived iteration and the current tip of the
named acceptance branch (`main` unless a feature campaign explicitly names
another branch). Drafts, profiles, diffs, and reports remain under the ignored
`self-improve/` workspace.
