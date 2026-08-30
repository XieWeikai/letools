from __future__ import annotations

import hashlib
import shutil
import tempfile
from collections.abc import Sequence
from fractions import Fraction
from pathlib import Path
from typing import Any

import av

from letools import _native
from letools.conversion_types import VideoEncodingConfig
from letools.model import FrameSequence, MediaInput, VideoSlice


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
        _native.concatenate_videos(inputs, output)
        return
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
        source = av.open(str(listing_path), mode="r", format="concat", options={"safe": "0"})
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
) -> None:
    """Remux timestamp slices from one input into per-episode outputs."""

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
        )
        return

    source = av.open(str(source_path), mode="r")
    input_streams = {
        stream.index: stream
        for stream in source.streams
        if stream.type in {"video", "audio", "subtitle"}
    }
    time_bases = {index: float(stream.time_base) for index, stream in input_streams.items()}
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
        shutil.move(temporary, outputs[current_index][1])
        destination = None
        temporary = None

    try:
        for packet in source.demux():
            if packet.dts is None or packet.stream.index not in input_streams:
                continue
            timestamp_value = packet.pts if packet.pts is not None else packet.dts
            timestamp = timestamp_value * time_bases[packet.stream.index]
            while current_index + 1 < len(outputs) and timestamp >= outputs[current_index + 1][0].start - 1e-7:
                close_current()
                current_index += 1
                video_slice, target_path = outputs[current_index]
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(suffix=target_path.suffix, delete=False) as handle:
                    temporary = Path(handle.name)
                destination = av.open(str(temporary), mode="w")
                stream_map = {}
                timestamp_offsets = {}
                for index, stream in input_streams.items():
                    target = destination.add_stream_from_template(stream, opaque=True)
                    target.time_base = stream.time_base
                    stream_map[index] = target
                    timestamp_offsets[index] = int(round(video_slice.start / time_bases[index]))
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


def apply_encoding_metadata(
    feature: dict[str, Any],
    fps: int,
    encoding: VideoEncodingConfig,
    *,
    include_legacy_video_info: bool,
) -> None:
    """Record the actual encoded stream settings in a target video feature."""

    namespaces = [feature.setdefault("info", {})]
    if include_legacy_video_info:
        namespaces.append(feature.setdefault("video_info", {}))
    for metadata in namespaces:
        metadata.update(
            {
                "video.fps": fps,
                "video.codec": encoding.codec,
                "video.pix_fmt": encoding.pixel_format,
                "video.is_depth_map": False,
                "has_audio": False,
            }
        )


def _encode_frame_sequences(
    inputs: Sequence[FrameSequence],
    output: Path,
    fps: int,
    encoding: VideoEncodingConfig,
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
        raise ValueError(f"A video shard cannot mix encoded image formats: {sorted(formats)}")
    if any((sequence.width, sequence.height) != (width, height) for sequence in inputs):
        raise ValueError("A video shard cannot mix frame dimensions")

    decoder_name = {"jpg": "mjpeg", "jpeg": "mjpeg"}.get(next(iter(formats)), next(iter(formats)))
    decoder = av.CodecContext.create(decoder_name, "r")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=output.suffix, delete=False) as handle:
        temporary = Path(handle.name)
    container = None
    try:
        container = av.open(str(temporary), mode="w")
        stream = container.add_stream(encoding.codec, rate=fps)
        stream.width = width
        stream.height = height
        stream.pix_fmt = encoding.pixel_format
        stream.codec_context.thread_count = encoding.codec_threads
        time_base = Fraction(1, fps)
        frame_index = 0
        for sequence in inputs:
            produced = 0
            for start in range(0, sequence.frame_count, encoding.batch_frames):
                stop = min(sequence.frame_count, start + encoding.batch_frames)
                batch = sequence.read_batch(start, stop)
                if len(batch) != stop - start:
                    raise ValueError(
                        f"Frame source returned {len(batch)} frames for requested range "
                        f"[{start}, {stop})"
                    )
                for encoded in batch:
                    decoded = decoder.decode(av.Packet(encoded))
                    if len(decoded) != 1:
                        raise ValueError(
                            f"Expected one image per encoded frame, decoded {len(decoded)}"
                        )
                    frame = decoded[0]
                    if (frame.width, frame.height) != (width, height):
                        raise ValueError("Decoded frame dimensions do not match the media profile")
                    frame.pts = frame_index
                    frame.time_base = time_base
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
        shutil.move(temporary, output)
    finally:
        if container is not None:
            container.close()
        temporary.unlink(missing_ok=True)


def write_media_group(
    inputs: Sequence[MediaInput],
    output: Path,
    fps: int,
    encoding: VideoEncodingConfig,
) -> None:
    """Write one v3 media shard while preserving the encoded-video fast path."""

    if not inputs:
        raise ValueError("At least one media input is required")
    if all(isinstance(media, VideoSlice) for media in inputs):
        concatenate_videos([media.path for media in inputs], output)
        return
    if all(isinstance(media, FrameSequence) for media in inputs):
        _encode_frame_sequences(inputs, output, fps, encoding)
        return
    raise TypeError("A media group cannot mix video slices and frame sequences")


def write_episode_media(
    outputs: Sequence[tuple[MediaInput, Path]],
    fps: int,
    encoding: VideoEncodingConfig,
) -> None:
    """Write one locality group as per-episode v2.1 media files."""

    if not outputs:
        return
    if all(isinstance(media, VideoSlice) for media, _ in outputs):
        paths = {media.path for media, _ in outputs}
        if len(paths) != 1:
            raise ValueError("Video slices in one locality group must share a path")
        split_video(next(iter(paths)), outputs)
        return
    if all(isinstance(media, FrameSequence) for media, _ in outputs):
        for media, output in outputs:
            _encode_frame_sequences([media], output, fps, encoding)
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
