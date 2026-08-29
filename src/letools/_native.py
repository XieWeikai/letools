from __future__ import annotations

import shutil
from collections.abc import Sequence
from pathlib import Path

try:
    import letools_native as _native
except ImportError:
    _native = None


def available() -> bool:
    return _native is not None


def provider() -> str:
    return "letools-native" if available() else "python"


def build_info() -> tuple[str, list[str]] | None:
    return _native.build_info() if _native is not None else None


def video_packet_digests_available() -> bool:
    return _native is not None and hasattr(_native, "packet_digests")


def video_concat_available() -> bool:
    return _native is not None and hasattr(_native, "concatenate_videos")


def video_split_available() -> bool:
    return _native is not None and hasattr(_native, "split_video")


def file_sizes(paths: Sequence[Path]) -> list[int]:
    if _native is not None:
        return _native.file_sizes(list(paths))
    return [path.stat().st_size for path in paths]


def copy_files(files: Sequence[tuple[Path, Path]]) -> list[int]:
    if _native is not None:
        return _native.copy_files(list(files))
    sizes = []
    for source, destination in files:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        sizes.append(destination.stat().st_size)
    return sizes


def packet_digests(path: Path, slices: Sequence[tuple[float, float]]) -> list[str]:
    if not video_packet_digests_available():
        raise RuntimeError("native packet digest capability is unavailable")
    return _native.packet_digests(path, list(slices))


def concatenate_videos(inputs: Sequence[Path], output: Path) -> None:
    if not video_concat_available():
        raise RuntimeError("native video concat capability is unavailable")
    _native.concatenate_videos(list(inputs), output)


def split_video(source: Path, outputs: Sequence[tuple[float, float, Path]]) -> None:
    if not video_split_available():
        raise RuntimeError("native video split capability is unavailable")
    _native.split_video(source, list(outputs))
