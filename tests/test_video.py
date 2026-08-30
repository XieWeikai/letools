import hashlib
from fractions import Fraction
from pathlib import Path

import av
import numpy as np

from letools import _native

from letools._video import (
    concatenate_videos,
    packet_digests,
    split_video,
    video_duration,
    write_episode_media,
    write_media_group,
)
from letools.conversion_types import VideoEncodingConfig
from letools.model import FrameSequence, VideoSlice


class BytesFrameSequence(FrameSequence):
    """In-memory JPEG source used to exercise the batch media contract."""

    def __init__(self, frames: tuple[bytes, ...], width: int, height: int) -> None:
        self._frames = frames
        self.frame_count = len(frames)
        self.width = width
        self.height = height
        self.encoded_format = "jpeg"
        self.estimated_size_bytes = sum(map(len, frames))
        self.requests: list[tuple[int, int]] = []

    def read_batch(self, start: int, stop: int) -> tuple[bytes, ...]:
        self.requests.append((start, stop))
        return self._frames[start:stop]


def test_default_frame_batch_is_profiled_value() -> None:
    assert VideoEncodingConfig().batch_frames == 48


def _make_video(path: Path, value: int) -> None:
    container = av.open(str(path), "w")
    stream = container.add_stream("mpeg4", rate=10)
    stream.width = 32
    stream.height = 24
    stream.pix_fmt = "yuv420p"
    for index in range(5):
        array = np.full((24, 32, 3), value + index, dtype=np.uint8)
        frame = av.VideoFrame.from_ndarray(array, format="rgb24")
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()


def _make_jpegs(value: int, count: int = 5) -> tuple[bytes, ...]:
    codec = av.CodecContext.create("mjpeg", "w")
    codec.width = 32
    codec.height = 24
    codec.pix_fmt = "yuvj420p"
    codec.time_base = Fraction(1, 10)
    frames = []
    for index in range(count):
        array = np.full((24, 32, 3), value + index, dtype=np.uint8)
        packet = codec.encode(av.VideoFrame.from_ndarray(array, format="rgb24"))
        assert len(packet) == 1
        frames.append(bytes(packet[0]))
    assert not codec.encode(None)
    return tuple(frames)


def test_concat_and_split_preserve_packet_payloads(tmp_path: Path, monkeypatch) -> None:
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    combined = tmp_path / "combined.mp4"
    split_first = tmp_path / "split-first.mp4"
    split_second = tmp_path / "split-second.mp4"
    _make_video(first, 10)
    _make_video(second, 30)
    first_duration = video_duration(first)
    second_duration = video_duration(second)
    concatenate_videos([first, second], combined)
    slices = [
        VideoSlice(combined, 0.0, first_duration),
        VideoSlice(combined, first_duration, first_duration + second_duration),
    ]
    if _native.video_packet_digests_available():
        monkeypatch.setattr(_native, "video_packet_digests_available", lambda: False)
        pyav_digests = packet_digests(slices)
        monkeypatch.undo()
        assert packet_digests(slices) == pyav_digests
    split_video(combined, [(slices[0], split_first), (slices[1], split_second)])
    expected = [
        packet_digests([VideoSlice(first, 0.0, first_duration)])[0],
        packet_digests([VideoSlice(second, 0.0, second_duration)])[0],
    ]
    actual = [
        packet_digests([VideoSlice(split_first, 0.0, first_duration)])[0],
        packet_digests([VideoSlice(split_second, 0.0, second_duration)])[0],
    ]
    assert actual == expected


def test_frame_sequences_encode_in_batches_for_both_layouts(tmp_path: Path) -> None:
    first = BytesFrameSequence(_make_jpegs(10), 32, 24)
    second = BytesFrameSequence(_make_jpegs(30, count=3), 32, 24)
    encoding = VideoEncodingConfig(batch_frames=2)
    grouped = tmp_path / "grouped.mp4"
    write_media_group([first, second], grouped, 10, encoding)

    with av.open(str(grouped)) as container:
        decoded = list(container.decode(video=0))
        assert container.streams.video[0].codec_context.name == "mjpeg"
    assert len(decoded) == 8
    assert first.requests == [(0, 2), (2, 4), (4, 5)]
    assert second.requests == [(0, 2), (2, 3)]

    first_output = tmp_path / "episode-0.mp4"
    second_output = tmp_path / "episode-1.mp4"
    write_episode_media(
        [(first, first_output), (second, second_output)], 10, encoding
    )
    with av.open(str(first_output)) as container:
        assert len(list(container.decode(video=0))) == 5
    with av.open(str(second_output)) as container:
        assert len(list(container.decode(video=0))) == 3

    expected = hashlib.sha256(b"".join((*first._frames, *second._frames))).hexdigest()
    actual = packet_digests([VideoSlice(grouped, 0.0, 0.8)])[0]
    assert actual == expected


def test_frame_sequences_allow_explicit_mpeg4_transcoding(tmp_path: Path) -> None:
    source = BytesFrameSequence(_make_jpegs(10), 32, 24)
    output = tmp_path / "transcoded.mp4"
    write_media_group(
        [source],
        output,
        10,
        VideoEncodingConfig(codec="mpeg4", pixel_format="yuv420p", batch_frames=2),
    )

    with av.open(str(output)) as container:
        assert container.streams.video[0].codec_context.name == "mpeg4"
        assert len(list(container.decode(video=0))) == 5
