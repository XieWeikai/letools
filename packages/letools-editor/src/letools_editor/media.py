"""File-oriented media execution: Rust/FFmpeg own the expensive pixel loops."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import av
import numpy as np

from letools._video import concatenate_videos, split_video
from letools.model import VideoSlice
from letools_editor import _native
from letools_editor.model import EditConfig, MediaJob


def resolve_ffmpeg(explicit: str | None) -> str:
    """Prefer a user/system executable, then the optional package's binary.

    The bundled fallback is a subprocess, not another library loaded into the
    Python process: PyAV and native remux never exchange AVFrame/AVPacket memory
    with it. No LD_LIBRARY_PATH, libclang, pkg-config, or shared FFmpeg ABI is
    required by the editor's own Rust crate.
    """

    selected = explicit or os.environ.get("LETOOLS_EDITOR_FFMPEG")
    if selected:
        path = shutil.which(selected)
        if not path:
            raise ValueError(f"FFmpeg executable not found: {selected}")
        return path
    system = shutil.which("ffmpeg")
    if system:
        return system
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def probe(path: Path) -> dict[str, Any]:
    """Read a container header only; planning never decodes video pixels."""

    with av.open(str(path)) as container:
        if len(container.streams.video) != 1:
            raise ValueError(f"Expected exactly one video stream in {path}")
        stream = container.streams.video[0]
        return {
            "codec": stream.codec_context.name,
            "pixel_format": stream.pix_fmt,
            "width": stream.width,
            "height": stream.height,
            "frames": stream.frames,
            "fps": float(stream.average_rate or 0),
            "audio": bool(container.streams.audio),
        }


def packet_remux_safe(path: Path, ranges: tuple[tuple[int, int], ...]) -> bool:
    """Prove that H.264 frame ranges can be compacted without decoding.

    The native splitter preserves packet payloads but cannot repair an
    inter-frame cut. This bounded demux pass therefore fails closed unless
    every packet is one frame with PTS == DTS, timestamps are contiguous, and
    each retained range starts with an IDR NAL unit. Unsupported layouts simply
    return ``False`` so the caller keeps the CRF-0 fallback.
    """

    try:
        with av.open(str(path)) as container:
            streams = container.streams.video
            if len(streams) != 1:
                return False
            stream = streams[0]
            if (
                stream.codec_context.name != "h264"
                or stream.time_base is None
                or stream.average_rate is None
                or stream.frames <= 0
                or "mp4" not in container.format.name
            ):
                return False
            extra = bytes(stream.codec_context.extradata or b"")
            if len(extra) < 5 or extra[0] != 1:
                return False
            length_size = (extra[4] & 3) + 1
            if length_size not in {1, 2, 4}:
                return False
            starts = {start for start, _ in ranges}
            packets = 0
            frame_ticks = 1 / (stream.average_rate * stream.time_base)
            if frame_ticks.denominator != 1:
                return False
            safe_starts: set[int] = set()
            for packet in container.demux(stream):
                # PyAV emits one terminal flush packet with no timestamps.
                # It carries no encoded frame and is not part of the proof.
                if packet.dts is None and packet.pts is None and packet.size == 0:
                    continue
                if packet.dts is None or packet.pts is None:
                    return False
                if packet.pts != packet.dts or packet.pts != packets * frame_ticks:
                    return False
                payload = bytes(packet)
                types: list[int] = []
                position = 0
                while position + length_size <= len(payload):
                    length = int.from_bytes(
                        payload[position : position + length_size], "big"
                    )
                    position += length_size
                    if length <= 0 or position + length > len(payload):
                        return False
                    types.append(payload[position] & 0x1F)
                    position += length
                if position != len(payload) or not types:
                    return False
                if (
                    packets in starts
                    and packet.is_keyframe
                    and 5 in types
                    and not any(kind in {1, 2, 3, 4} for kind in types)
                ):
                    safe_starts.add(packets)
                packets += 1
            if packets != stream.frames or any(
                start < 0 or end > packets or start >= end for start, end in ranges
            ):
                return False
            return safe_starts == starts
    except (OSError, ValueError, av.error.FFmpegError):
        return False


def _coalesce(ranges: tuple[tuple[int, int], ...]) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    for start, end in ranges:
        if result and result[-1][1] == start:
            result[-1] = result[-1][0], end
        else:
            result.append((start, end))
    return result


def transcode(job: MediaJob, output: Path, executable: str, config: EditConfig) -> None:
    """Select source frames, rebase timestamps, scale, and encode in one pass.

    Arbitrary inter-frame packet cuts are unsafe. FFmpeg decodes from the start
    of each affected physical shard and selects exact frame numbers; the output
    contains no deleted frames. Encoder keyframes are forced at new episode
    boundaries and B-frames are disabled so later v3 -> v2.1 remux remains safe.
    """

    transform = job.transform
    assert transform is not None
    ranges = _coalesce(job.ranges)
    selection = "+".join(f"between(n,{start},{end - 1})" for start, end in ranges)
    filters = [f"select='{selection}'", f"setpts=N/({job.fps}*TB)"]
    if transform.size:
        filters.append(f"scale={transform.size[0]}:{transform.size[1]}:flags=bilinear")
    # A filter file avoids command-line limits for highly fragmented episodes.
    script = output.with_suffix(".filter.txt")
    script.write_text(",".join(filters), encoding="utf-8")
    command = [
        executable,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-threads",
        "1",
        "-i",
        str(job.source),
        "-map",
        "0:v:0",
        "-an",
        "-sn",
        "-dn",
        "-filter_threads",
        "1",
        "-filter_script:v",
        str(script),
        "-c:v",
        transform.codec,
        "-threads",
        str(config.codec_threads),
        "-fps_mode",
        "cfr",
        "-r",
        str(job.fps),
        "-enc_time_base",
        f"1:{job.fps}",
    ]
    pixel_format = transform.pixel_format or (
        "yuvj420p" if transform.codec == "mjpeg" else "yuv420p"
    )
    command += ["-pix_fmt", pixel_format]
    if transform.codec == "libx264":
        # expr with a disjunction would grow with episode count. The timestamp
        # list is short for ordinary shards, and lives in a response-free argv
        # (never a shell command). An all-intra fallback bounds enormous lists.
        offsets = []
        frames = 0
        for episode in job.episodes:
            offsets.append(f"{frames / job.fps:.9f}")
            frames += episode.original.length
        timestamps = ",".join(offsets)
        if len(timestamps) > 100000:
            # All-intra is a safe bounded fallback for extraordinarily many
            # tiny episodes, at the cost of a larger output file.
            command += ["-g", "1"]
        else:
            command += ["-force_key_frames", timestamps, "-forced-idr", "1"]
        command += ["-bf", "0", "-crf", str(transform.crf), "-preset", transform.preset]
    else:
        command += ["-q:v", str(transform.quality)]
    command += ["-map_metadata", "-1", str(output)]
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(
                f"FFmpeg failed for {job.source}: {result.stderr[-16000:]}"
            )
    finally:
        script.unlink(missing_ok=True)


def remux(job: MediaJob, output: Path) -> None:
    """Compact MJPEG or proven IDR-aligned H.264 without recompression.

    Reuse the existing GIL-free native split and concat primitives. Temporary
    slices are confined to staging and removed immediately after each job. A
    single new native gather primitive could remove this extra I/O in a later
    measured optimization; duplicating the proven muxer now is unnecessary.
    """

    with tempfile.TemporaryDirectory(prefix=".remux-", dir=output.parent) as temporary:
        slices = [
            (
                VideoSlice(job.source, start / job.fps, end / job.fps),
                Path(temporary) / f"{index}.mp4",
            )
            for index, (start, end) in enumerate(_coalesce(job.ranges))
        ]
        split_video(job.source, slices, atomic_output=False)
        if len(slices) == 1:
            slices[0][1].replace(output)
        else:
            concatenate_videos([path for _, path in slices], output)


def output_stats(
    job: MediaJob, path: Path, executable: str
) -> dict[int, dict[str, Any]]:
    """Compute exact population moments from the *encoded output*, not input.

    Decoding twice (encode pass then stats pass) is deliberate: lossy codecs
    change pixels. The second pass is streamed once per shard in Rust, with
    O(width * height + episode_count) memory and no Python per-frame callbacks.
    """

    header = probe(path)
    command = [
        executable,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-threads",
        "1",
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-an",
        "-sn",
        "-dn",
        "-filter_threads",
        "1",
        "-threads",
        "1",
        "-fps_mode",
        "passthrough",
        "-pix_fmt",
        "rgb24",
        "-f",
        "rawvideo",
        "pipe:1",
    ]
    rows = _native.video_rgb_moments(
        command,
        header["width"],
        header["height"],
        [episode.original.length for episode in job.episodes],
    )
    result = {}
    for episode, (count, minimum, maximum, sums, squares) in zip(
        job.episodes, rows, strict=True
    ):
        pixels = count * header["width"] * header["height"]
        mean = np.asarray(sums) / pixels / 255.0
        variance = np.maximum(0, np.asarray(squares) / pixels / 255.0**2 - mean**2)
        values = {
            # PyO3 exposes [u8; 3] as a compact Python bytes object.
            "min": np.frombuffer(bytes(minimum), dtype=np.uint8) / 255.0,
            "max": np.frombuffer(bytes(maximum), dtype=np.uint8) / 255.0,
            "mean": mean,
            "std": np.sqrt(variance),
        }
        result[episode.index] = {
            key: value.reshape(3, 1, 1).tolist() for key, value in values.items()
        }
        result[episode.index]["count"] = [count]
    return result


def execute_media(
    job: MediaJob, staging: Path, executable: str | None, config: EditConfig
) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    """Return probed stream metadata and any changed episode statistics."""

    output = staging / job.relative_output
    output.parent.mkdir(parents=True, exist_ok=True)
    stats: dict[int, dict[str, Any]] = {}
    if job.mode == "remux":
        remux(job, output)
    elif job.mode == "transcode":
        assert executable is not None
        transcode(job, output, executable, config)
        stats = output_stats(job, output, executable)
    else:
        raise ValueError(f"Unexpected media job mode: {job.mode}")
    header = probe(output)
    expected = sum(episode.original.length for episode in job.episodes)
    if header["frames"] != expected:
        raise ValueError(
            f"{output}: encoded frame count {header['frames']} != {expected}"
        )
    return header, stats
