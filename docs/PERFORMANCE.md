# Performance

LeTools performance claims use immutable datasets, explicit resource limits,
external wall-clock and peak-RSS measurement, repeated runs, and semantic
correctness gates. This page records the current public comparison and links to
the longer optimization history.

## Official LeRobot comparison

The directly shared operation is LeRobot v2.1 to v3.0 conversion. On 2026-09-01
we ran the unmodified official converter from LeRobot commit `d36d404b6531` and
LeTools commit `0e417d98c941` on the same Slurm allocation. Each implementation
received a fresh copy of the same source before every run; preparation copies
were excluded from timing. Runs were interleaved to reduce ordering bias.

Both converters used 100 MiB Parquet and 256 MiB video targets. LeTools used one
data worker and eight video workers, the plan selected for this 8-CPU JuiceFS
environment. The official script has no worker flags and ran unmodified. Its
built-in conversion checks and LeTools' default validation were included in
the timed CLI wall. Full deep validation and cross-output comparison ran after
timing.

| Measurement | Official LeRobot | LeTools | Difference |
| --- | ---: | ---: | ---: |
| Median wall time | 33.22 s | 7.36 s | **4.51x throughput** |
| Throughput | 9.03 episodes/s | 40.76 episodes/s | **4.51x** |
| Frame throughput | 7,204 frames/s | 32,516 frames/s | **4.51x** |
| Median peak process RSS | 670 MiB | 336 MiB | **49.9% lower** |

Raw wall samples were `33.27`, `32.41`, and `33.22` seconds for LeRobot and
`9.66`, `7.36`, and `7.18` seconds for LeTools. The first LeTools sample was
retained in the median rather than discarded as warm-up.

![LeTools and official LeRobot conversion benchmark](assets/images/convert-benchmark.svg)

### Workload and machine

| Resource | Value |
| --- | --- |
| Dataset | `dagger-v21-300`: 300 episodes, 239,314 frames, 900 videos |
| Physical source size | 3,766,328,098 bytes (3.51 GiB) |
| Scheduler | Slurm job `963`, one node |
| CPU allocation | 8 CPUs, Intel Xeon Platinum 8468V |
| Memory allocation | 48 GiB |
| Source and destination | JuiceFS shared network filesystem (`fuse.juicefs`) |
| Bounded write probe | 1 GiB in 0.97 s, 1,056 MiB/s effective |
| Warm sequential read ceiling | 3.51 GiB in 0.14 s, 25.05 GiB/s effective |

The read number is explicitly a warm page-cache ceiling, not an advertised
JuiceFS backend limit. The conversion workload itself is more representative:
it opens hundreds of small Parquet and MP4 inputs, remuxes media, and writes the
new layout. The storage probe is reported so the cache state and write ceiling
are visible rather than implied.

### Correctness gate

Both outputs passed deep validation with no errors or warnings. Cross-output
semantic comparison reported equality for all 300 episodes, 239,314 Arrow
frames, and 900 encoded video packet payloads. Equivalent MP4 container bytes
and Parquet row-group layouts are not required when their dataset semantics and
encoded packet payloads agree.

The official script only provides v2.1 to v3.0 conversion, so there is no
official reverse-converter bar. LeTools also supports v3.0 to v2.1; the frozen
full 3,457-episode benchmark and all historical Rust optimization measurements
remain in the [benchmark record](https://github.com/XieWeikai/letools/blob/main/BENCHMARK.md)
and [self-improvement summary](https://github.com/XieWeikai/letools/blob/main/self-improve/SUMMARY.md).

## Other accepted workloads

### Iteration 0046: staged v2.1 media output

The accepted `bd3f77b` to candidate `opt/0046-staging-direct-video` comparison
used the frozen 811-episode/693,669-frame 10 GiB v3.0 source on H800-node11,
with one Slurm task, 16 CPUs, 48 GiB, 16 data workers, and 16 video workers.
Each filesystem used five alternating baseline/candidate samples after the
initial three-sample spread exceeded 5%. The candidate writes split MP4s
directly below the hidden dataset staging root, removing one temporary rename
per output while preserving standalone atomic writes.

| Destination filesystem | Baseline median | Candidate median | Throughput change | RSS ratio | CPU ratio |
| --- | ---: | ---: | ---: | ---: | ---: |
| NFS `/home` | 20.733 s (39.12 ep/s) | 19.064 s (42.54 ep/s) | **+8.76%** | 0.980 | 1.012 |
| JuiceFS `/jfs` | 18.180 s (44.61 ep/s) | 16.823 s (48.21 ep/s) | **+8.07%** | 0.995 | 1.035 |

On the full 3,457-episode dagger workload at 8 CPUs/48 GiB, five-sample medians
were 39.244 -> 38.613 s for v2.1 to v3.0 (+1.64%, within run noise) and
79.725 -> 72.630 s for v3.0 to v2.1 (+9.77%). Peak RSS and peak threads
remained within the protocol ratios. HDF5 five-sample regression medians showed
no slowdown: v2.1 17.881 -> 16.518 s (-7.62%), v3.0 10.790 -> 10.852 s
(+0.58%). Full deep
semantic and packet-payload checks passed for both LeRobot directions and both
HDF5 targets; see the ignored iteration archive for raw metrics.

These numbers demonstrate other code paths and must not be compared directly
with the 300-episode chart because their datasets and allocations differ.

| Operation | Workload | Allocation | Result |
| --- | --- | --- | ---: |
| HDF5 to v3.0 | 108 episodes, 125,412 trajectory frames, 3 cameras | 96 CPU / 128 GiB | 7.15 s, 15.11 episodes/s |
| HDF5 to v2.1 | same | 96 CPU / 128 GiB | 11.70 s, 9.23 episodes/s |
| Merge v3.0 | 128 episodes, 110,786 frames, about 17.8 GB video | 48 CPU / 128 GiB | 2.42 s |
| Merge v2.1 | same | 48 CPU / 128 GiB | 3.76 s |

See [HDF5 acceptance](HDF5_MVP.md), [merge acceptance](MERGE.md), and
[planner benchmarks](PLANNER_BENCHMARK.md) for methodology, detailed resource
curves, and rejected candidates.

## Reproducing claims

Conversion benchmarks must run through the scheduler on cluster deployments.
Use unique destinations, retain all samples, report external `/usr/bin/time -v`
RSS, and validate outside the measured region when comparing engine-only work.
The governing acceptance and complexity policy is the
[self-improvement protocol](https://github.com/XieWeikai/letools/blob/main/self-improve/PROTOCOL.md).
