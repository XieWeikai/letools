# Static planner Slurm acceptance

## Scope

These results were collected on 2026-08-30 from branch
`feat/static-conversion-planner`. Every conversion, calibration, and oracle
candidate ran through Slurm on an Intel Xeon Platinum 8468V node. Local
synthetic unit tests are the only work not submitted through Slurm.

The final oracle schema executes all cameras in the same global worker pool as
the backend. It factorizes serial data and video stages, exhausts their finite
candidate domains independently, randomizes candidate order per round, and
uses three-run medians. Equivalent target sizes that produce identical task
boundaries are measured once.

## Frozen workloads

| Workload | Episodes | Frames | Data | Videos |
| --- | ---: | ---: | ---: | ---: |
| dagger medium v2.1 | 300 | 239,314 | 300 files, 30.6 MB uncompressed | 900 files, 3.74 GB |
| dagger medium v3.0 | 300 | 239,314 | 1 file | 59 shards, 3.74 GB |
| dagger data-only | 3,457 | 2,415,341 | 3,457 files, 316.8 MB uncompressed | none |
| dagger full | 3,457 | 2,415,341 | 3,457 v2.1 files | 10,371 files, 40.5 GB |

Node-local scenarios copy the frozen source into `/tmp` before timing and
write candidate outputs below `/tmp`. Setup copies are excluded. Network
scenarios use JuiceFS for source and destination.

## Oracle results

The plan column is `data workers / video workers / data MiB / video MiB`.
Dashes mean that v2.1 output has no target-size parameters.

| Direction and environment | Slurm allocation | Final plan | Oracle | Execution regret |
| --- | --- | --- | --- | ---: |
| v2.1 to v3, JuiceFS | 8 CPU / 16 GiB | 1 / 8 / 100 / 256 | 1 / 8 / equivalent / 256 | 1.000 |
| v2.1 to v3, node-local | 8 CPU / 16 GiB | 1 / 8 / 100 / 100 | 1 / 8 / equivalent / 100 | 1.000 |
| v2.1 to v3, JuiceFS | 16 CPU / 16 GiB | 1 / 16 / 100 / 256 | 1 / 16 / equivalent / 256 | 1.000 |
| v2.1 to v3, JuiceFS | 32 CPU / 48 GiB | 1 / 16 / 100 / 256 | 1 / 16 / equivalent / 256 | 1.000 |
| v2.1 to v3, data-only | 2 CPU / 4 GiB | 2 / 1 / 128 / 200 | 2 / 1 / 128 / equivalent | 1.000 |
| v2.1 to v3, data-only | 8 CPU / 4 GiB | 5 / 1 / 64 / 200 | 5 / 1 / 64 / equivalent | 1.000 |
| v2.1 to v3, data-only | 8 CPU / 1 GiB | 3 / 1 / 100 / 200 | 3 / 1 / 100 / equivalent | 1.000 |
| v3 to v2.1, JuiceFS | 8 CPU / 16 GiB | 1 / 8 / - / - | 1 / 8 / - / - | 1.000 |

The geometric-mean and worst final execution regret in the primary matrix are
both 1.000. Before the final rules, the measured failures were useful tuning
evidence:

- data-only 2 CPU: 1.357 with one unmeasured worker, then 1.108 with excessive
  32 MiB groups
- reverse medium: 1.753 when the 8-worker calibration anchor was filtered out
- 32 CPU JuiceFS: 1.070 after extrapolating to 32 workers past the 16-worker
  storage plateau
- node-local: 1.175 when applying the network-optimal 256 MiB target instead
  of the local 100 MiB target

Preliminary heterogeneous local/JuiceFS and JuiceFS/local probes measured
1.000 and 1.002 regret respectively. They predate the all-camera oracle schema,
so they are diagnostic evidence rather than entries in the primary table. The
corrected primary matrix covers the two storage endpoints; both heterogeneous
paths select the network policy because one side is JuiceFS.

## Full conversion

The final current-code v2.1 to v3.0 run used 8 CPU / 48 GiB and selected:

```text
data workers       5
video workers      8
data target       64 MiB
video target     800 MiB
planning         4.208 s
conversion      35.662 s
external wall   40.42 s
peak process RSS 1,195,564 KiB
```

Conversion-only throughput was 96.94 episodes/s and 67,728 frames/s. Cold
end-to-end throughput, including planning and process startup, was 85.53
episodes/s and 59,756 frames/s. Compared with the earlier fixed benchmark, the
40.42 second cold result reduces the 52.09 second median by 22.4 percent
(1.289x throughput) and improves on the earlier 41.52 second best single run
by 2.7 percent throughput.

The full output at `/jfs/tmp/letools/planner-final-v30` deep-validates with no
warnings. Semantic comparison against the authoritative v2.1 source checks
3,457 episodes, 2,415,341 frames, and 10,371 videos and reports equality.

A full v3 to v2.1 round trip also deep-validates and semantically matches all
episodes, frames, and videos. The earlier full execution used three video
workers and took 116.68 seconds; a controlled full-stage oracle measured
10.95 seconds per camera at eight workers versus 28.35 seconds at three.
The final planner selects eight. A medium final-code reverse conversion took
6.113 seconds after 1.893 seconds of planning and used 371,340 KiB peak process
RSS.

## Resource accounting

`/usr/bin/time -v` is authoritative for process RSS. Slurm `sacct MaxRSS` on
this cluster includes cgroup file-page cache: both a 40 GB semantic comparison
and conversion were reported near the entire 48 GiB allocation, while process
RSS was about 1.14 GiB and the jobs completed without swaps. Oracle jobs used
at most half a node and no accepted scenario OOMed.

## Residual costs

Execution selection meets the written regret thresholds. Two cold-planning
costs remain visible rather than hidden:

- the 300-episode stage oracle is deliberately too small for its planning time,
  so its cold-stage regret is not a large-conversion acceptance metric
- a 3,457-file, 291 MB data-only source skips calibration but still needs
  2.32 seconds to read metadata and Parquet footers; execution parameters match
  the oracle, but metadata profiling is not recoverable for such a small
  conversion

Raw JSON reports are retained outside Git under
`/jfs/tmp/letools/planner-results`. The reproducible drivers are
`scripts/make_planner_fixture.py`, `scripts/planner_oracle.py`, and
`scripts/run_planner_scenario.py`.
