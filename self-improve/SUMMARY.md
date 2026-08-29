# letools self-improvement summary

Status: complete

Eight accepted optimizations are present on `/workspace/shrelic/letools/main`.
All conversions and full comparisons ran as single-node Slurm jobs within the
protocol resource ceiling.

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

Each percentage compares the candidate median with the current-main baseline
for that iteration. Results from different workloads are not multiplied.

## Rejected experiments

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

## Final correctness

- Python tests: 3 passed.
- Rust build/tests: passed.
- v2.1 to v3.0 and v3.0 to v2.1 outputs passed deep validation with no errors
  or warnings.
- Both roundtrip directions compared equal for 3457 episodes, 2415341 frames,
  and 10371 episode/video packet payload digests.
- Official `LeRobotDatasetMetadata` and `LeRobotDataset` loaded the final v3.0
  output and decoded samples from episodes 0, 1728, and 3456.
- Final letools Git branch: clean `main` at `b7b2b89`.

## Retained outputs

- Final v3.0: `/jfs/tmp/letools/si-0013-roundtrip-v30`
- Final v2.1 roundtrip: `/jfs/tmp/letools/si-0013-low-c`
- Immutable v2.1 source: `/jfs/tmp/letools/dagger_v3_official_old`
- Existing v3.0 oracle: `/jfs/tmp/letools/dagger_v30_letools_fixed`

The governing process is
`/workspace/shrelic/letools/self-improve/PROTOCOL.md`; future optimization
cycles should continue numbering from iteration 0017 and use final `main` as
their baseline. All drafts, profiles, diffs, and reports are rooted at
`/workspace/shrelic/letools/self-improve/`.
