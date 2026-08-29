from pathlib import Path

import av
import numpy as np

from letools import _native

from letools._video import concatenate_videos, packet_digests, split_video, video_duration
from letools.model import VideoSlice


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
