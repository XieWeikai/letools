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

All numeric values must be positive. Re-encoding support depends on the FFmpeg
linked into **PyAV**, not merely a system `ffmpeg` executable or the Rust wheel.
The default direct-JPEG path uses the native wheel when it advertises
`mjpeg-batch-mux` and otherwise falls back to PyAV. Unsupported re-encoding
combinations fail during preflight before output staging.

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

For existing MP4 re-encoding, CRF/preset control, or exact resizing, install the
optional [letools-editor](EDITOR.md) and use `letools editor apply ... --resize
224x224 --video-codec libx264 --crf 23`. That separate same-version engine
rebuilds actual output pixel statistics and episode-boundary keyframes.

The editor's executable-based encoding has a separate option vocabulary:

| Frame-source conversion | Existing-MP4 editor | Meaning |
| --- | --- | --- |
| `--video-codec` | `--video-codec` | PyAV encoder availability vs editor's libx264/MJPEG executable support |
| `--video-pixel-format` | `--pixel-format` | Requested encoded pixel format |
| `--video-codec-threads` | `--codec-threads` | Encoder threads per job |
| `--video-workers` | `--workers` | Concurrent media jobs vs editor physical file jobs |
| `--auto` | Omit both concurrency options | Calibrated conversion planner vs bounded editor heuristic |
| Not exposed | `--resize`, `--crf`, `--preset`, `--quality` | Existing-video dimensions and codec-specific quality |

`--preset` is an HDF5 mapping name in conversion and an x264 speed preset in
the editor. Editor FFmpeg is selected by `--ffmpeg`, `LETOOLS_EDITOR_FFMPEG`,
`PATH`, then the packaged executable. Its availability is independent of
PyAV's encoders; no shared FFmpeg ABI is required. See [Editor](EDITOR.md).

H.264 concat disables automatic Annex-B injection to preserve MP4 packet
payloads; installations with an older minimal native wheel missing the required
bitstream filter retry through PyAV for that specific capability error.

## Direct JPEG mux versus re-encoding

With no options, JPEG frames are copied as MJPEG packets into MP4, retaining
the source's actual pixel format and compressed payload. There is no pixel
decode/re-encode or additional lossy compression. Output size remains close to
the JPEG input size.

FFmpeg-enabled native wheels batch this packet mux in Rust. The source reader
and `--video-batch-frames` boundary remain in Python, so plugin behavior does
not change; the optimization removes per-frame Python/PyAV mux calls. Portable
wheels retain the PyAV fallback. Both paths preserve the original JPEG packet
payloads.

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

Two September 19 follow-up jobs compare the current branch tip `aed74b9`
directly with `main@ca2235e`. Timing uses external CLI wall time and excludes
validation; retained outputs undergo deep validation and complete semantic and
packet-payload comparison afterward. Caches are uncontrolled, so medians and
raw spread are reported rather than presenting a cold-cache storage claim.

Full HDF5 conversion used `/data` input and `/jfs` output on the same JuiceFS
mount, one H800-node13 task, 16 CPUs, 64 GiB, eight data workers, and eight
video workers. The source has 108 episodes, 125,412 trajectory frames, three
cameras, 376,236 encoded JPEG frames, and 20.7 GiB of HDF5 input. Five
main/current pairs produced:

| Target | Main median | Current median | Throughput change | CPU seconds | Peak RSS | Peak threads |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| v2.1 | 16.166 s, 6.681 ep/s | 12.784 s, 8.448 ep/s | **+26.45%** | 74.58 → 47.78 | 1265 → 1231 MiB | 141 → 21 |
| v3.0 | 12.431 s, 8.688 ep/s | 12.212 s, 8.844 ep/s | +1.79%, within noise | 49.71 → 48.73 | 1207 → 1189 MiB | 141 → 21 |

The five raw wall samples were `15.925, 15.142, 16.330, 22.407, 16.166`
seconds for main and `11.076, 16.022, 11.827, 18.449, 12.784` for current when
targeting v2.1. For v3.0 they were `12.431, 15.657, 12.172, 17.833, 11.508`
and `11.861, 12.968, 12.212, 13.066, 11.317`. Both main/current output pairs
passed deep validation and matched all 108 episodes, 125,412 frames, and 324
encoded video payloads.

The Rust primitive itself was accepted separately against the immediately
preceding feature tip `8212e1f`, using the same HDF5 source and resource shape.
Five JuiceFS pairs improved v2.1 from 12.444 to 8.217 seconds (+51.44%
throughput) and v3.0 from 8.427 to 8.011 seconds (+5.19%). Those isolated
iteration gains must not be multiplied by the cumulative current-versus-main
numbers above.

The LeRobot remux regression used the complete dagger datasets on
allocation-local XFS: 3,457 episodes, 2,415,341 frames, and 10,371 videos. Job
3555 ran on H800-node11 with 16 CPUs, 64 GiB, and 16 data/video workers:

| Direction | Main median | Current median | Throughput change | CPU seconds | Peak RSS |
| --- | ---: | ---: | ---: | ---: | ---: |
| v2.1 → v3.0 | 21.543 s, 160.472 ep/s | 21.398 s, 161.556 ep/s | +0.68%, within noise | 133.37 → 130.81 | 1134 → 1137 MiB |
| v3.0 → v2.1 | 21.776 s, 158.750 ep/s | 21.327 s, 162.096 ep/s | +2.11%, within noise | 111.91 → 111.96 | 999 → 1045 MiB |

Both current outputs deep-validated and compared equal to main and their
opposite-version sources for every episode, Arrow frame, and video payload.
The sub-3% timing differences establish no measurable remux regression, not a
new remux optimization. The MJPEG primitive cannot run on this path because
LeRobot sources provide `VideoSlice` inputs to the existing native concat/split
operations.

Raw records are archived locally under the ignored
`self-improve/experiments/20260919-docs-main-hdf/` and
`20260919-main-remux-regression/` directories. The accepted optimization report
is summarized in `self-improve/SUMMARY.md`.
