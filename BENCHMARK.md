# Current benchmark

Updated: 2026-08-29

Dataset: `dagger`, 3,457 episodes, 2,415,341 frames, 10,371 episode videos,
approximately 39 GiB in LeRobot v2.1 format.

Acceptance runs used one H800 Slurm node with 8 CPUs, 48 GiB RAM, eight
Parquet workers, and three video workers. The target sizes were 100 MiB for
Parquet and 200 MiB for video. Every run wrote a unique destination. Medians
come from three alternating current-main/candidate pairs.

| Direction/workload | Before Rust primitive | Current | Speedup | Current peak RSS |
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
