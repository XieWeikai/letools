# MVP benchmark

Date: 2026-08-28

Dataset: `dagger`, 3,457 episodes, 2,415,341 frames, 10,371 episode videos,
approximately 39 GiB in LeRobot v2.1 format.

Both forward conversions used one Slurm node with 8 CPUs and 48 GiB RAM. The target
sizes were 100 MiB for Parquet and 200 MiB for video. Post-conversion validation was
excluded from both timings. The official baseline used the unmodified LeRobot repository
at commit `bf31dd794ffb4f87380aba3912f64421e8352d3c`.

| Converter | Direction | Wall time | Peak RSS | Relative speed |
|---|---:|---:|---:|---:|
| LeRobot official | v2.1 to v3.0 | 273.06 s | 1,283,400 KiB | 1.00x |
| letools | v2.1 to v3.0 | 156.04 s | 1,128,104 KiB | 1.75x |
| letools | v3.0 to v2.1 | 352.91 s | 1,663,824 KiB | n/a |

The letools forward conversion reduced wall time by 42.9% and peak RSS by 12.1% versus
the official converter in these runs. Video remux uses a node-local temporary MP4 and a
single sequential publish stream by default; concurrent large-file remux was substantially
slower on JuiceFS.

## Correctness checks

- Deep structural validation passed for final v3.0 and v2.1 outputs.
- All 3,457 episode Arrow tables matched the official v3.0 output.
- Tasks, feature schemas, episode statistics, and dataset totals matched.
- Encoded packet payload hashes matched for all 10,371 videos.
- The full v2.1 to v3.0 to v2.1 round trip matched the original dataset.

Validation compares semantics rather than container or Parquet byte identity because both
formats permit equivalent metadata ordering, row-group layout, and MP4 container metadata.

## Source metadata caveat

The source v2.1 dataset declares `observation.state` and `action` with shape `[1, 14]`, while
its Parquet columns contain one-dimensional `list<float>` values of length 14. The official
converter preserves this mismatch, so both the official v3.0 result and the byte-equivalent
letools schema pass `LeRobotDatasetMetadata` but fail the current `LeRobotDataset` loader with
the same Arrow `float to list` cast error. The letools validator reports this inherited issue as
a schema warning. Changing it during conversion would diverge from the designated official
golden result and would make the round trip lossy at the metadata level.
