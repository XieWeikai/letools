# Benchmark record

Updated: 2026-10-01

Results below are tied to their recorded revisions and environments, not an
automatic benchmark of every new main tip. See [Performance](docs/PERFORMANCE.md)
for the public conversion comparison and [Editor benchmarks](docs/EDITOR_BENCHMARK.md)
for the optional editor's latest accepted results and limitations.

## Historical full-dagger Rust campaign

Dataset: `dagger`, 3,457 episodes, 2,415,341 frames, 10,371 episode videos,
approximately 39 GiB in LeRobot v2.1 format.

Acceptance runs used one H800 Slurm node with 8 CPUs, 48 GiB RAM, eight
Parquet workers, and three video workers. The target sizes were 100 MiB for
Parquet and 200 MiB for video. Every run wrote a unique destination. Medians
come from three alternating accepted-baseline/candidate pairs per iteration.

| Direction/workload | Before Rust primitive | Accepted candidate | Speedup | Candidate peak RSS |
|---|---:|---:|---:|---:|
| v2.1 to v3.0 concat | 106.92 s | 52.09 s | 2.05x | 1,108,484 KiB |
| v3.0 to v2.1 split | 143.59 s | 109.01 s | 1.32x | 1,780,984 KiB |
| full video payload compare | 147.90 s | 51.76 s | 2.86x | 848,116 KiB |

Rust concat reduced median CPU from 221.98 to 115.08 seconds. Rust split
reduced median CPU from 155.03 to 57.07 seconds and voluntary context switches
from 5,253,000 to 820,704. Split wall time improved less than CPU because the
remaining work waits on roughly 39 GiB of JuiceFS output. Larger CPU allocations
did not improve the established I/O-limited resource curve.

The original official LeRobot v2.1-to-v3.0 reference run was 273.06 seconds at
commit `bf31dd794ffb4f87380aba3912f64421e8352d3c`. It predates the current paired
series and is retained as historical context, not multiplied into the per-
iteration speedups above.

## Video encoding campaign: aed74b9 versus ca2235e

The feature revision `aed74b9` was compared directly with `ca2235e`
after adding configurable frame-source encoding and native batched MJPEG mux.
The complete dagger sources were copied outside timing to allocation-local XFS.
Slurm job 3555 used H800-node11, 16 CPUs, 64 GiB, 16 data workers, and 16 video
workers. Five baseline/candidate pairs produced these medians:

| Direction | ca2235e | aed74b9 | Throughput change | CPU seconds | Peak RSS |
| --- | ---: | ---: | ---: | ---: | ---: |
| v2.1 to v3.0 | 21.543 s, 160.47 ep/s | 21.398 s, 161.56 ep/s | +0.68%, within noise | 133.37 -> 130.81 | 1134 -> 1137 MiB |
| v3.0 to v2.1 | 21.776 s, 158.75 ep/s | 21.327 s, 162.10 ep/s | +2.11%, within noise | 111.91 -> 111.96 | 999 -> 1045 MiB |

Both sub-3% differences showed no measurable remux regression in that experiment,
not a new speedup. Deep validation and complete comparison passed between candidate
and baseline and between candidate and each source: 3,457 episodes, 2,415,341 frames,
and 10,371 encoded video payloads in both directions.

The separate candidate-versus-baseline HDF5 run used the 20.7-GiB XVLA source,
H800-node13, 16 CPUs, 64 GiB, eight data/video workers, and shared JuiceFS
input/output. Five-pair medians were 16.166 -> 12.784 seconds for v2.1
(+26.45% throughput) and 12.431 -> 12.212 seconds for v3.0 (+1.79%, within
noise). Both layouts deep-validated and matched all 108 episodes, 125,412
frames, and 324 videos. See `docs/VIDEO_ENCODING.md` for raw sample spread and
the isolated native-primitive result.

## Editor integration

Accepted editor revision `265273a` has a separately documented official
comparison: five-pair deletion 0.805/29.924 s and reencoding 34.459/30.984 s
(LeTools/official). Reencoding has not beaten tuned official. The same
16-CPU/64-GiB allocation and warm local XFS fixture contain 12 episodes,
12,370 dataset frames and three 640x480 cameras. See the
[editor report](docs/EDITOR_BENCHMARK.md) for version/codec contracts and
historical versus latest measurements.

The integration also repairs v3 task-table pandas index metadata so conversion
and merge outputs return task strings through official loaders. Full dagger
directions and roundtrips passed semantic/statistics/packet and loader checks.
Five-pair core timing controls were too noisy to establish a tight regression
bound; the earlier video-campaign numbers above do not substitute for one.

## Correctness checks

- Deep structural validation passed for final v3.0 and v2.1 outputs.
- All 3,457 episode Arrow tables matched the official v3.0 output.
- Tasks, feature schemas, episode statistics, and dataset totals matched.
- Encoded packet payload hashes matched for all 10,371 videos.
- Both full roundtrip directions matched their source datasets.
- Official `LeRobotDatasetMetadata` and `LeRobotDataset` loaded the final v3.0
  roundtrip and decoded frames 0, 1,207,670, and 2,415,340.

Validation compares semantics rather than container or Parquet byte identity because both
formats permit equivalent metadata ordering, row-group layout, and MP4 container metadata.

Current LeRobot intentionally rejects loading v2.1 directly and requests a v3.0
conversion. letools therefore validates v2.1 structurally and semantically,
then uses the v3.0 roundtrip for official current-loader acceptance.
