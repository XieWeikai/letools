# Video encoding options

MP4 is the output container. The codec inside it depends on the input and the
requested encoding policy. Both LeRobot backends share the same media writer.

## CLI

`convert`, `plan`, and `dist plan` accept the same four options for HDF5,
AgileX, and external providers returning `FrameSequence` media:

| Option | Default | Meaning |
| --- | --- | --- |
| `--video-codec NAME` | `mjpeg` | PyAV encoder implementation name |
| `--video-pixel-format NAME` | automatic | Preserve JPEG pixels on direct mux; otherwise use `yuvj420p` for MJPEG or `yuv420p` for other encoders |
| `--video-batch-frames N` | `48` | Maximum images requested in a source batch |
| `--video-codec-threads N` | `1` | Threads requested inside each encoder |

All numeric values must be positive. Encoder and pixel-format support depend
on the FFmpeg linked into **PyAV**, not a system `ffmpeg` executable or the Rust
wheel. Unsupported combinations fail during preflight before output staging.

```bash
# Compact MPEG-4 output, with automatic worker planning.
letools convert /data/hdf5 /data/output-v30 \
  --source-format hdf5 --preset xvla --to v3.0 --auto \
  --video-codec mpeg4 --video-pixel-format yuv420p \
  --video-batch-frames 48 --video-codec-threads 2

# Inspect or calibrate that same workload without publishing a dataset.
letools plan /data/hdf5 /data/output-v30 \
  --source-format hdf5 --preset xvla --to v3.0 --calibrate \
  --video-codec mpeg4 --video-pixel-format yuv420p \
  --video-codec-threads 2

# H.264 is available only if this PyAV runtime supplies libx264.
letools convert /data/hdf5 /data/output-h264 \
  --source-format hdf5 --preset xvla --to v2.1 \
  --video-codec libx264 --video-pixel-format yuv420p
```

These options do not transcode an existing LeRobot MP4. Explicit encoding
options on a CLI source with only `VideoSlice` media, or without video, are
rejected rather than silently ignored. Python `convert()` retains its existing
remux behavior when a shared `ConversionConfig` contains encoding settings.
`merge` continues to preserve encoded payloads and has no encoding options.

## Direct JPEG mux versus re-encoding

With no options, JPEG frames are copied as MJPEG packets into MP4, retaining
the source's actual pixel format and compressed payload. There is no pixel
decode/re-encode or additional lossy compression. Output size remains close to
the JPEG input size.

An explicit pixel format is a requirement, not a metadata label. With MJPEG,
the writer probes a representative JPEG in each sequence: matching formats can
still use direct mux; a mismatch sends the group through decode/encode. Other
codecs and non-JPEG images use decode/encode. Plugins must provide consistent
dimensions and image format within each sequence and a consistent video schema
per camera; a first-image probe is not a full source validation pass.

The encoder disables B-frame reordering and requests an intra frame at every
episode start. This keeps newly encoded v3 episode boundaries suitable for
later packet splitting into v2.1. The backend reads each camera's first output
stream to record the actual codec and pixel format in metadata. For example,
the encoder `libx264` produces codec metadata `h264`.

Lossy codecs can change pixels and packet payloads. Validate frame count,
timestamp order, boundaries, and decoded image quality with an explicit
tolerance; `compare --videos` is intended for packet-preserving conversions.
CRF, bitrate, encoder presets, GPU selection, and existing-MP4 transcoding are
not exposed by these four options.

## Python and planner contracts

```python
from letools import ConversionConfig, VideoEncodingConfig, convert, plan_and_convert

encoding = VideoEncodingConfig(
    codec="mpeg4", pixel_format="yuv420p", batch_frames=48, codec_threads=2,
)
convert(source, "/data/fixed", "v3.0",
        config=ConversionConfig(video_encoding=encoding))
result = plan_and_convert(source, "/data/auto", "v3.0", video_encoding=encoding)
assert result.plan.conversion_config().video_encoding == encoding
```

`VideoEncodingConfig.pixel_format` now defaults to `None` (automatic). Existing
explicit strings remain accepted. Default JPEG output preserves its original
payload, including images that are not 4:2:0.

Encoding policy is separate from `PerformanceOverrides`. The planner keeps it
fixed, includes it in `ConversionPlan` JSON, and uses it for both calibration
and final execution. All four fields and the PyAV/FFmpeg runtime versions enter
the cache fingerprint for image workloads. Planner algorithm version 8 makes
previous choices ineligible for reuse.

For actual encoding, worker selection accounts for requested encoder threads
and an estimate of decoded-frame and encoder-buffer memory. Eight CPUs with two
threads per encoder permit at most four video workers. Direct JPEG mux has no
active encoder, so its worker count is not divided by `codec_threads`.
These are planning estimates, not OS thread or memory enforcement; dependency
helper threads and encoder-specific buffers can vary. Fixed `convert` leaves
explicit worker scheduling to the caller.

Nondefault encoding calibration uses at most 48 frames per episode/camera
sample and preserves process isolation when the source requires it. It checks
the time deadline between batches, excludes partial samples from choices, and
cleans temporary output. The deadline stops new work; an in-progress native
encode, flush, source read, or process startup can overrun it. Byte admission
conservatively charges original resource bytes; it is not a filesystem quota.
Calibration results measure short samples, not a proof of globally optimal
long-video encoder settings.

## Distributed conversion

```bash
letools dist plan /shared/hdf5 /shared/output --job-dir /shared/job \
  --source-format hdf5 --preset xvla --to v3.0 --tasks 8 \
  --workers 4 --video-workers 4 \
  --video-codec mpeg4 --video-pixel-format yuv420p --video-codec-threads 2
letools dist submit /shared/job --scheduler slurm \
  --cpus-per-task 8 --memory 48G
```

`WorkerConfig.video_encoding` stores the configuration in the immutable
manifest. The coordinator records the estimated CPU cost per media worker;
Slurm and Kubernetes CPU guards use the larger of data concurrency and total
video encoder concurrency because the phases run sequentially. Workers repeat
encoder preflight in their own runtime before converting. All nodes must supply
the requested PyAV encoder. Old manifests without the encoding fields use the
previous default JPEG-mux behavior. Worker retry and final merge remain packet
preserving.

## Validation evidence

The September 15, 2026 implementation was exercised under Slurm. The complete
suite passed **89 tests**, with one libx264 test skipped because the project's
source-built PyAV runtime does not supply that encoder. An isolated PyAV 16.1.0
wheel environment passed all **30 video-focused tests**, including libx264.
Repeating those 30 tests with the parent-process native dispatcher disabled
also passed, exercising Python video remux and packet comparison fallbacks.
Official `LeRobotDatasetMetadata` and `LeRobotDataset` loaders also opened
MJPEG, MPEG-4, and H.264 v3 fixtures and read samples across episode boundaries.

Coverage includes both output versions, fixed and automatic CLI conversion,
plan JSON and cache invalidation, distributed manifest roundtrip and task
execution, encoder CPU accounting, process-isolated calibration, invalid-option
cleanup, explicit JPEG pixel conversion, actual codec metadata, and subsequent
v3-to-v2.1 packet splitting. Lossy fixture checks use decoded pixels with a
stated tolerance; the default JPEG path is checked by packet equality.

Large default-configuration regression runs compare against commit `ca2235e`:
811 episodes / 693,669 frames for each LeRobot conversion direction, and the
full XVLA `0930_10am_new` source (108 episodes / 125,412 frames) for HDF5 to both
versions. Timed runs exclude validation; retained outputs undergo deep
validation and full semantic/packet comparison afterward. Slurm jobs 3016 and
3017 each request one node, 8 CPUs and 48 GiB, with 8 data workers and 3 video
workers. Timing uses external CLI wall time, including startup, with one warmup
per workload and alternating baseline/candidate runs. Caches are uncontrolled;
these are not cold-cache storage benchmarks or codec compression benchmarks.

Node-local `/scratch` input/output on H800-node14, five pairs, median results:

| Direction | Baseline seconds | Candidate seconds | Baseline episodes/s | Candidate episodes/s | Throughput change |
| --- | ---: | ---: | ---: | ---: | ---: |
| LeRobot v2.1 → v3.0 | 10.356 | 10.589 | 78.316 | 76.590 | -2.20% |
| LeRobot v3.0 → v2.1 | 4.827 | 4.794 | 168.008 | 169.158 | +0.68% |
| HDF5 → v2.1 | 23.975 | 24.131 | 4.505 | 4.476 | -0.65% |
| HDF5 → v3.0 | 15.773 | 14.904 | 6.847 | 7.247 | +5.83% |

Candidate median process-tree peak RSS was 434, 515, 629, and 605 MiB,
respectively. Peak process/thread counts matched the baseline: 1/7, 1/13,
5/35, and 5/35. CPU-time medians were 21.73, 10.88, 65.91, and 37.69 seconds,
versus baseline 17.98, 10.83, 60.29, and 37.17. In particular, the forward
LeRobot and HDF5-to-v2.1 runs do **not** establish unchanged CPU efficiency.

The preceding shared-filesystem run used `/jfs` input/output for LeRobot and
`/data/share` input with `/jfs` output for HDF5. Its three-pair medians were:

| Direction | Baseline seconds | Candidate seconds |
| --- | ---: | ---: |
| LeRobot v2.1 → v3.0 | 11.527 | 21.220 |
| LeRobot v3.0 → v2.1 | 28.871 | 31.036 |
| HDF5 → v2.1 | 32.535 | 33.639 |
| HDF5 → v3.0 | 20.755 | 21.150 |

Shared forward samples ranged from 10.748–19.516 seconds for baseline and
10.549–27.348 seconds for candidate. Local forward samples also varied from
7.717–13.819 and 9.480–15.006 seconds. Local replication did not reproduce the
large shared forward slowdown; the added preflight span there was about
31 microseconds. This is evidence against attributing that slowdown solely to
preflight, not proof that storage contention caused it. These measurements
support a small default-path wall-time difference locally, but neither a
general speedup claim nor a guarantee of no regression on shared storage.

Raw commands, per-run stages, process-tree metrics, validation and comparison
JSON are archived locally in the ignored
`self-improve/experiments/video-encoding-options/` directory (`local/` for the
five-pair run). Feature acceptance is based on the correctness coverage above;
this change is not presented as an accepted self-improvement optimization.
