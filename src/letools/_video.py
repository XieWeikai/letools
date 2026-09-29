"""Coarse media operations with native remux and PyAV encode paths.

The public primitives accept complete files, episode slices, or frame batches.
No packet/frame object escapes this module, which preserves a stable Python/Rust
boundary and avoids per-packet Python callbacks on existing LeRobot videos.
"""

from __future__ import annotations

import hashlib
import io
import shutil
import tempfile
from collections.abc import Sequence
from fractions import Fraction
from pathlib import Path
from typing import Any, TYPE_CHECKING

import av

from letools import _native
from letools.conversion_types import VideoEncodingConfig
from letools.model import FrameSequence, MediaInput, VideoSlice

if TYPE_CHECKING:
    from letools.plugins import DatasetSource


def video_duration(path: Path) -> float:
    """Read the duration of the first video stream in seconds."""

    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        if stream.duration is not None:
            return float(stream.duration * stream.time_base)
        return float(container.duration / av.time_base)


def concatenate_videos(inputs: Sequence[Path], output: Path) -> None:
    """Remux complete encoded files into one output without decoding pixels."""

    if not inputs:
        raise ValueError("At least one input video is required")
    if _native.video_concat_available():
        try:
            _native.concatenate_videos(inputs, output)
            return
        except OSError as error:
            # Minimal published FFmpeg builds may lack h264_mp4toannexb, which
            # the concat demuxer automatically requests for H.264 MP4. The
            # portable PyAV runtime has the full bitstream-filter set. Do not
            # hide unrelated I/O/mux failures behind an expensive retry.
            if "Bitstream filter not found" not in str(error):
                raise
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".ffconcat", delete=False) as listing:
        listing.write("ffconcat version 1.0\n")
        for path in inputs:
            escaped = str(path.resolve()).replace("'", "'\\''")
            listing.write(f"file '{escaped}'\n")
        listing_path = Path(listing.name)
    with tempfile.NamedTemporaryFile(suffix=output.suffix, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        source = av.open(
            # MP4 -> MP4 remux must retain AVCC packet bytes. The concat
            # demuxer's automatic Annex-B filter inserts SPS/PPS NAL units and
            # changes payload hashes even without re-encoding. Input clips in
            # one camera group already have a compatible codec configuration.
            str(listing_path), mode="r", format="concat",
            options={"safe": "0", "auto_convert": "0"},
        )
        destination = av.open(str(temporary), mode="w")
        streams = {}
        for stream in source.streams:
            if stream.type in {"video", "audio", "subtitle"}:
                target = destination.add_stream_from_template(stream, opaque=True)
                target.time_base = stream.time_base
                streams[stream.index] = target
        for packet in source.demux():
            if packet.dts is None or packet.stream.index not in streams:
                continue
            packet.stream = streams[packet.stream.index]
            destination.mux(packet)
        source.close()
        destination.close()
        shutil.move(temporary, output)
    finally:
        listing_path.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)


def split_video(
    source_path: Path,
    outputs: Sequence[tuple[VideoSlice, Path]],
    *,
    atomic_output: bool = True,
) -> None:
    """Remux timestamp slices with optional outer-transaction protection.

    Atomic output remains the default for standalone callers. A conversion
    backend may disable it only while writing below its unpublished dataset
    staging root, whose cleanup and final directory rename provide the same
    externally visible transaction boundary.
    """

    if not outputs:
        return
    if len(outputs) == 1 and abs(outputs[0][0].start) < 1e-9:
        duration = video_duration(source_path)
        if abs(duration - outputs[0][0].end) <= 1e-3:
            outputs[0][1].parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, outputs[0][1])
            return
    if _native.video_split_available():
        _native.split_video(
            source_path,
            [
                (video_slice.start, video_slice.end, target)
                for video_slice, target in outputs
            ],
            atomic_output=atomic_output,
        )
        return

    source = av.open(str(source_path), mode="r")
    input_streams = {
        stream.index: stream
        for stream in source.streams
        if stream.type in {"video", "audio", "subtitle"}
    }
    time_bases = {
        index: float(stream.time_base) for index, stream in input_streams.items()
    }
    current_index = -1
    destination = None
    stream_map = {}
    timestamp_offsets = {}
    temporary: Path | None = None

    def close_current() -> None:
        nonlocal destination, temporary
        if destination is None or temporary is None:
            return
        destination.close()
        if atomic_output:
            shutil.move(temporary, outputs[current_index][1])
        destination = None
        temporary = None

    try:
        for packet in source.demux():
            if packet.dts is None or packet.stream.index not in input_streams:
                continue
            timestamp_value = packet.pts if packet.pts is not None else packet.dts
            timestamp = timestamp_value * time_bases[packet.stream.index]
            while (
                current_index + 1 < len(outputs)
                and timestamp >= outputs[current_index + 1][0].start - 1e-7
            ):
                close_current()
                current_index += 1
                video_slice, target_path = outputs[current_index]
                target_path.parent.mkdir(parents=True, exist_ok=True)
                if atomic_output:
                    with tempfile.NamedTemporaryFile(
                        suffix=target_path.suffix, delete=False
                    ) as handle:
                        temporary = Path(handle.name)
                else:
                    temporary = target_path
                destination = av.open(str(temporary), mode="w")
                stream_map = {}
                timestamp_offsets = {}
                for index, stream in input_streams.items():
                    target = destination.add_stream_from_template(stream, opaque=True)
                    target.time_base = stream.time_base
                    stream_map[index] = target
                    timestamp_offsets[index] = int(
                        round(video_slice.start / time_bases[index])
                    )
            if current_index < 0 or destination is None:
                continue
            stream_index = packet.stream.index
            video_slice = outputs[current_index][0]
            if timestamp >= video_slice.end - 1e-7:
                continue
            if packet.pts is not None:
                packet.pts -= timestamp_offsets[stream_index]
            packet.dts -= timestamp_offsets[stream_index]
            packet.stream = stream_map[stream_index]
            destination.mux(packet)
        close_current()
    finally:
        source.close()
        if destination is not None:
            destination.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def media_duration(media: MediaInput, fps: int) -> float:
    """Return the semantic duration used in LeRobot episode metadata."""

    if isinstance(media, VideoSlice):
        return media.duration
    return media.frame_count / fps


def jpeg_passthrough(media: FrameSequence, encoding: VideoEncodingConfig) -> bool:
    """Explicit pixel conversion must not be silently treated as packet muxing.

    Default JPEG input requires no probe or extra source open. Explicit pixel
    requests inspect one representative image per sequence; plugins must keep
    each sequence's dimensions and image format consistent.
    """
    if encoding.codec != "mjpeg" or media.encoded_format.lower() not in {"jpeg", "jpg"}:
        return False
    if encoding.pixel_format is None:
        return True
    decoder = av.CodecContext.create("mjpeg", "r")
    frames = decoder.decode(av.Packet(media.read_batch(0, 1)[0]))
    return len(frames) == 1 and frames[0].format.name == encoding.pixel_format


def validate_source_encoding(
    source: DatasetSource, encoding: VideoEncodingConfig, *, explicit: bool = False
) -> tuple[int, int]:
    """Preflight frame encoders and return per-job CPU and memory estimates.

    Only representative media are inspected, never the complete payload. The
    conservative decode/encoder buffer estimate supplements the planner's
    compressed-input accounting. Remux-only sources retain their old path.
    """
    cpu, memory, found = 1, 0, False
    if not source.episodes:
        return cpu, memory
    for key in source.metadata.video_keys:
        media = source.media_input(source.episodes[0], key)
        if not isinstance(media, FrameSequence):
            continue
        found = True
        if jpeg_passthrough(media, encoding):
            # Muxing retains the compressed batch, even though no pixel frames
            # or codec threads are active. Large batch overrides still consume
            # memory and must be visible to the planner.
            compressed_batch = (
                media.estimated_size_bytes
                * min(encoding.batch_frames, media.frame_count)
                // max(1, media.frame_count)
            )
            memory = max(memory, 64 * 1024**2 + compressed_batch)
            continue
        try:
            codec = av.Codec(encoding.codec, "w")
            formats = codec.video_formats
            if formats and encoding.encoder_pixel_format not in {
                f.name for f in formats
            }:
                raise ValueError(
                    f"unsupported pixel format {encoding.encoder_pixel_format}"
                )
            # Check MP4 codec/container compatibility without writing a file.
            with av.open(io.BytesIO(), mode="w", format="mp4") as output:
                stream = output.add_stream(encoding.codec, rate=source.metadata.fps)
                stream.width, stream.height = media.width, media.height
                stream.pix_fmt = encoding.encoder_pixel_format
                stream.codec_context.thread_count = encoding.codec_threads
                stream.codec_context.max_b_frames = 0
                output.start_encoding()
        except Exception as error:
            raise ValueError(
                f"Cannot use video encoder {encoding.codec!r} with {encoding.encoder_pixel_format!r}: {error}"
            ) from error
        cpu = max(cpu, encoding.codec_threads)
        memory = max(
            memory,
            media.width
            * media.height
            * 4
            * (encoding.batch_frames + 16 + encoding.codec_threads)
            + 64 * 1024**2,
        )
    if explicit and not found:
        raise ValueError(
            "Video encoding options require image-frame input; they are not applicable to remux-only or video-free sources"
        )
    return cpu, memory


def apply_encoding_metadata(
    feature: dict[str, Any],
    fps: int,
    encoding: VideoEncodingConfig,
    *,
    include_legacy_video_info: bool,
    output: Path | None = None,
) -> None:
    """Record the actual encoded stream settings in a target video feature."""

    codec, pixel_format = encoding.codec, encoding.encoder_pixel_format
    if output is not None:
        # Encoder implementation names (libx264) and payload formats (h264)
        # differ. Read the actual published stream, including untouched JPEGs.
        with av.open(str(output)) as container:
            stream = container.streams.video[0]
            codec, pixel_format = stream.codec_context.name, stream.pix_fmt
    namespaces = [feature.setdefault("info", {})]
    if include_legacy_video_info or "video_info" in feature:
        namespaces.append(feature.setdefault("video_info", {}))
    for metadata in namespaces:
        metadata.update(
            {
                "video.fps": fps,
                "video.codec": codec,
                "video.pix_fmt": pixel_format,
                "video.is_depth_map": False,
                "has_audio": False,
            }
        )


def _encode_frame_sequences(
    inputs: Sequence[FrameSequence],
    output: Path,
    fps: int,
    encoding: VideoEncodingConfig,
    *,
    local_staging: bool,
) -> None:
    """Decode batches of still images and encode one continuous video shard."""

    if not inputs:
        raise ValueError("At least one frame sequence is required")
    if fps <= 0:
        raise ValueError("Video FPS must be positive")
    if encoding.batch_frames <= 0 or encoding.codec_threads <= 0:
        raise ValueError("Video batch size and codec thread count must be positive")
    width, height = inputs[0].width, inputs[0].height
    formats = {sequence.encoded_format.lower() for sequence in inputs}
    if len(formats) != 1:
        raise ValueError(
            f"A video shard cannot mix encoded image formats: {sorted(formats)}"
        )
    if any((sequence.width, sequence.height) != (width, height) for sequence in inputs):
        raise ValueError("A video shard cannot mix frame dimensions")

    decoder_name = {"jpg": "mjpeg", "jpeg": "mjpeg"}.get(
        next(iter(formats)), next(iter(formats))
    )
    decoder = av.CodecContext.create(decoder_name, "r")
    decoder.thread_count = 1
    output.parent.mkdir(parents=True, exist_ok=True)
    if local_staging:
        with tempfile.NamedTemporaryFile(suffix=output.suffix, delete=False) as handle:
            temporary = Path(handle.name)
    else:
        temporary = output
    container = None
    completed = False
    try:
        container = av.open(str(temporary), mode="w")
        stream = container.add_stream(encoding.codec, rate=fps)
        stream.width = width
        stream.height = height
        stream.pix_fmt = encoding.encoder_pixel_format
        stream.codec_context.thread_count = encoding.codec_threads
        # Episode boundaries must remain independently decodable after a later
        # v3 -> v2.1 packet split. Disable reordering and start each episode with
        # an intra frame, while allowing compression within each episode.
        stream.codec_context.max_b_frames = 0
        time_base = Fraction(1, fps)
        frame_index = 0
        for sequence in inputs:
            produced = 0
            for batch in sequence.iter_batches(encoding.batch_frames):
                expected = min(encoding.batch_frames, sequence.frame_count - produced)
                if len(batch) != expected:
                    raise ValueError(
                        f"Frame source returned {len(batch)} frames for a batch of {expected}"
                    )
                for encoded in batch:
                    decoded = decoder.decode(av.Packet(encoded))
                    if len(decoded) != 1:
                        raise ValueError(
                            f"Expected one image per encoded frame, decoded {len(decoded)}"
                        )
                    frame = decoded[0]
                    if (frame.width, frame.height) != (width, height):
                        raise ValueError(
                            "Decoded frame dimensions do not match the media profile"
                        )
                    frame.pts = frame_index
                    frame.time_base = time_base
                    frame.pict_type = (
                        av.video.frame.PictureType.I
                        if produced == 0
                        else av.video.frame.PictureType.NONE
                    )
                    for packet in stream.encode(frame):
                        container.mux(packet)
                    frame_index += 1
                    produced += 1
            if produced != sequence.frame_count:
                raise ValueError("Frame source ended before its declared frame count")
        for packet in stream.encode():
            container.mux(packet)
        container.close()
        container = None
        if local_staging:
            shutil.move(temporary, output)
        completed = True
    finally:
        if container is not None:
            container.close()
        if local_staging:
            temporary.unlink(missing_ok=True)
        elif not completed:
            output.unlink(missing_ok=True)


def _mux_jpeg_sequences(
    inputs: Sequence[FrameSequence],
    output: Path,
    fps: int,
    encoding: VideoEncodingConfig,
    *,
    local_staging: bool,
) -> None:
    """Mux complete JPEG values as timestamped MJPEG packets without decoding."""

    if not inputs:
        raise ValueError("At least one frame sequence is required")
    if fps <= 0:
        raise ValueError("Video FPS must be positive")
    if encoding.batch_frames <= 0:
        raise ValueError("Video batch size must be positive")
    width, height = inputs[0].width, inputs[0].height
    if any((sequence.width, sequence.height) != (width, height) for sequence in inputs):
        raise ValueError("A video shard cannot mix frame dimensions")

    if _native.mjpeg_batch_mux_available():
        muxer = _native.mjpeg_muxer(
            output,
            width,
            height,
            fps,
            encoding.encoder_pixel_format,
            atomic_output=local_staging,
        )
        completed = False
        try:
            for sequence in inputs:
                produced = 0
                for batch in sequence.iter_batches(encoding.batch_frames):
                    expected = min(
                        encoding.batch_frames, sequence.frame_count - produced
                    )
                    if len(batch) != expected:
                        raise ValueError(
                            f"Frame source returned {len(batch)} frames for a batch of {expected}"
                        )
                    # abi3-py39 has no stable buffer-protocol API. Materialize
                    # one owned bytes object per frame, then let Rust borrow it
                    # synchronously without constructing a PyAV Packet.
                    written = muxer.write_batch(tuple(bytes(frame) for frame in batch))
                    if written != expected:
                        raise ValueError(
                            f"Native muxer wrote {written} frames for a batch of {expected}"
                        )
                    produced += written
                if produced != sequence.frame_count:
                    raise ValueError("Frame source ended before its declared frame count")
            muxer.close()
            completed = True
            return
        finally:
            if not completed and not local_staging:
                output.unlink(missing_ok=True)

    output.parent.mkdir(parents=True, exist_ok=True)
    if local_staging:
        with tempfile.NamedTemporaryFile(suffix=output.suffix, delete=False) as handle:
            temporary = Path(handle.name)
    else:
        temporary = output
    container = None
    completed = False
    try:
        container = av.open(str(temporary), mode="w")
        stream = container.add_stream("mjpeg", rate=fps)
        stream.width = width
        stream.height = height
        stream.pix_fmt = encoding.encoder_pixel_format
        time_base = Fraction(1, fps)
        stream.time_base = time_base
        frame_index = 0
        for sequence in inputs:
            produced = 0
            for batch in sequence.iter_batches(encoding.batch_frames):
                expected = min(encoding.batch_frames, sequence.frame_count - produced)
                if len(batch) != expected:
                    raise ValueError(
                        f"Frame source returned {len(batch)} frames for a batch of {expected}"
                    )
                for encoded in batch:
                    packet = av.Packet(encoded)
                    packet.stream = stream
                    packet.pts = frame_index
                    packet.dts = frame_index
                    packet.duration = 1
                    packet.time_base = time_base
                    packet.is_keyframe = True
                    container.mux(packet)
                    frame_index += 1
                    produced += 1
            if produced != sequence.frame_count:
                raise ValueError("Frame source ended before its declared frame count")
        container.close()
        container = None
        if local_staging:
            shutil.move(temporary, output)
        completed = True
    finally:
        if container is not None:
            container.close()
        if local_staging:
            temporary.unlink(missing_ok=True)
        elif not completed:
            output.unlink(missing_ok=True)


def write_media_group(
    inputs: Sequence[MediaInput],
    output: Path,
    fps: int,
    encoding: VideoEncodingConfig,
    *,
    local_staging: bool = True,
) -> None:
    """Write one v3 media shard while preserving the encoded-video fast path."""

    if not inputs:
        raise ValueError("At least one media input is required")
    if all(isinstance(media, VideoSlice) for media in inputs):
        concatenate_videos([media.path for media in inputs], output)
        return
    if all(isinstance(media, FrameSequence) for media in inputs):
        formats = {media.encoded_format.lower() for media in inputs}
        if (
            encoding.codec == "mjpeg"
            and formats <= {"jpg", "jpeg"}
            and all(jpeg_passthrough(media, encoding) for media in inputs)
        ):
            _mux_jpeg_sequences(
                inputs, output, fps, encoding, local_staging=local_staging
            )
            return
        _encode_frame_sequences(
            inputs, output, fps, encoding, local_staging=local_staging
        )
        return
    raise TypeError("A media group cannot mix video slices and frame sequences")


def write_episode_media(
    outputs: Sequence[tuple[MediaInput, Path]],
    fps: int,
    encoding: VideoEncodingConfig,
    *,
    atomic_output: bool = True,
) -> None:
    """Write one locality group with an explicit file publication boundary."""

    if not outputs:
        return
    if all(isinstance(media, VideoSlice) for media, _ in outputs):
        paths = {media.path for media, _ in outputs}
        if len(paths) != 1:
            raise ValueError("Video slices in one locality group must share a path")
        split_video(next(iter(paths)), outputs, atomic_output=atomic_output)
        return
    if all(isinstance(media, FrameSequence) for media, _ in outputs):
        for media, output in outputs:
            write_media_group([media], output, fps, encoding)
        return
    raise TypeError("A media group cannot mix video slices and frame sequences")


def packet_digests(slices: Sequence[VideoSlice]) -> list[str]:
    """Hash encoded packet payloads for semantic video comparison."""

    if not slices:
        return []
    if _native.video_packet_digests_available():
        return _native.packet_digests(
            slices[0].path,
            [(video_slice.start, video_slice.end) for video_slice in slices],
        )

    source = av.open(str(slices[0].path), mode="r")
    time_base = float(source.streams.video[0].time_base)
    digests = [hashlib.sha256() for _ in slices]
    index = 0
    try:
        for packet in source.demux(video=0):
            if packet.dts is None:
                continue
            value = packet.pts if packet.pts is not None else packet.dts
            timestamp = value * time_base
            while index + 1 < len(slices) and timestamp >= slices[index].end - 1e-7:
                index += 1
            if slices[index].start - 1e-7 <= timestamp < slices[index].end - 1e-7:
                digests[index].update(bytes(packet))
    finally:
        source.close()
    return [digest.hexdigest() for digest in digests]
