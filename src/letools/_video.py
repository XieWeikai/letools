from __future__ import annotations

import hashlib
import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path

import av

from letools.model import VideoSlice


def video_duration(path: Path) -> float:
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        if stream.duration is not None:
            return float(stream.duration * stream.time_base)
        return float(container.duration / av.time_base)


def concatenate_videos(inputs: Sequence[Path], output: Path) -> None:
    if not inputs:
        raise ValueError("At least one input video is required")
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
        destination = av.open(str(temporary), mode="w", options={"movflags": "faststart"})
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
    if not outputs:
        return
    if len(outputs) == 1 and abs(outputs[0][0].start) < 1e-9:
        duration = video_duration(source_path)
        if abs(duration - outputs[0][0].end) <= 1e-3:
            outputs[0][1].parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, outputs[0][1])
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
                destination = av.open(str(temporary), mode="w", options={"movflags": "faststart"})
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


def packet_digests(slices: Sequence[VideoSlice]) -> list[str]:
    if not slices:
        return []
    source = av.open(str(slices[0].path), mode="r")
    digests = [hashlib.sha256() for _ in slices]
    index = 0
    try:
        for packet in source.demux(video=0):
            if packet.dts is None:
                continue
            value = packet.pts if packet.pts is not None else packet.dts
            timestamp = float(value * packet.time_base)
            while index + 1 < len(slices) and timestamp >= slices[index].end - 1e-7:
                index += 1
            if slices[index].start - 1e-7 <= timestamp < slices[index].end - 1e-7:
                digests[index].update(bytes(packet))
    finally:
        source.close()
    return [digest.hexdigest() for digest in digests]
