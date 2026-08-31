# letools self-improvement summary

Status: complete

Seventeen accepted optimizations are present on `/workspace/shrelic/letools/main`,
including the six accepted HDF5-source optimizations. All conversions and full
comparisons ran as single-node Slurm jobs within the protocol resource ceiling.
The HDF5 source, preset tooling, documentation, and accepted performance work
were integrated into `main` after completing the feature campaign.

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

Each percentage compares the candidate median with the current-main baseline
for that iteration. Results from different workloads are not multiplied.

## HDF5 source campaign

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

At the final fixed eight-worker setting, v2.1 converts in an 11.70 second
five-sample external-wall median (9.231 episodes/s, 10719 trajectory frames/s,
and 32157 media frames/s). The final v3 target median is 7.15 seconds (15.105
episodes/s, 17540 trajectory frames/s, and 52620 media frames/s). These are
measured end-to-end CLI wall times and are not products of per-commit speedup
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

## Retained outputs

- Final v3.0: `/jfs/tmp/letools/si-0013-roundtrip-v30`
- Final v2.1 roundtrip: `/jfs/tmp/letools/si-0013-low-c`
- Immutable v2.1 source: `/jfs/tmp/letools/dagger_v3_official_old`
- Existing v3.0 oracle: `/jfs/tmp/letools/dagger_v30_letools_fixed`
- Rust-split validated v2.1: `/jfs/tmp/letools/si-0019-c1`
- Rust-split v3.0 roundtrip: `/jfs/tmp/letools/si-0019-roundtrip-v30`

The governing process is [PROTOCOL.md](PROTOCOL.md). Future optimization cycles
should continue numbering from iteration 0046 and use the current accepted
`main` tip as their baseline. Drafts, profiles, diffs, and reports remain under
the ignored `self-improve/` workspace.
