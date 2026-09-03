"""Capability-based dispatch to the optional Rust extension.

Wrappers keep native objects behind a path/value boundary. Callers can select a
portable Python fallback without coupling to which native wheel was installed.
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from pathlib import Path

try:
    import letools_native as _native
except ImportError:
    _native = None


def available() -> bool:
    """Return whether the native extension imported successfully."""

    return _native is not None


def provider() -> str:
    """Return the active low-level provider label for diagnostics."""

    return "letools-native" if available() else "python"


def build_info() -> tuple[str, list[str]] | None:
    """Return native version/capabilities, or None for the Python-only path."""

    return _native.build_info() if _native is not None else None


def video_packet_digests_available() -> bool:
    """Report native encoded-packet hashing support."""

    return _native is not None and hasattr(_native, "packet_digests")


def video_concat_available() -> bool:
    """Report native whole-file video remux support."""

    return _native is not None and hasattr(_native, "concatenate_videos")


def video_split_available() -> bool:
    """Report native timestamp-slice video remux support."""

    return _native is not None and hasattr(_native, "split_video")


def video_staged_output_available() -> bool:
    """Report direct output support for files protected by dataset staging."""

    return _native is not None and hasattr(_native, "split_video_staged")


def file_sizes(paths: Sequence[Path]) -> list[int]:
    """Stat paths in input order, using parallel Rust when available."""

    if _native is not None:
        return _native.file_sizes(list(paths))
    return [path.stat().st_size for path in paths]


def copy_files(files: Sequence[tuple[Path, Path]]) -> list[int]:
    """Copy path pairs and return output sizes in input order."""

    if _native is not None:
        return _native.copy_files(list(files))
    sizes = []
    for source, destination in files:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        sizes.append(destination.stat().st_size)
    return sizes


def clone_or_copy_files(
    files: Sequence[tuple[Path, Path]], workers: int
) -> list[tuple[int, bool]]:
    """Clone files when possible, otherwise copy them with bounded concurrency."""

    if workers <= 0:
        raise ValueError("Copy worker count must be positive")
    if _native is not None and hasattr(_native, "clone_or_copy_files"):
        return _native.clone_or_copy_files(list(files), workers)

    from concurrent.futures import ThreadPoolExecutor

    def copy_one(item: tuple[Path, Path]) -> tuple[int, bool]:
        source, destination = item
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        return destination.stat().st_size, False

    with ThreadPoolExecutor(max_workers=min(workers, len(files) or 1)) as pool:
        return list(pool.map(copy_one, files))


def packet_digests(path: Path, slices: Sequence[tuple[float, float]]) -> list[str]:
    """Hash encoded video packet payloads for ordered timestamp slices."""

    if not video_packet_digests_available():
        raise RuntimeError("native packet digest capability is unavailable")
    return _native.packet_digests(path, list(slices))


def concatenate_videos(inputs: Sequence[Path], output: Path) -> None:
    """Invoke native packet-preserving concatenation or fail explicitly."""

    if not video_concat_available():
        raise RuntimeError("native video concat capability is unavailable")
    _native.concatenate_videos(list(inputs), output)


def split_video(
    source: Path,
    outputs: Sequence[tuple[float, float, Path]],
    *,
    atomic_output: bool = True,
) -> None:
    """Invoke native packet-preserving slicing with explicit publication scope."""

    if not video_split_available():
        raise RuntimeError("native video split capability is unavailable")
    if not atomic_output and video_staged_output_available():
        _native.split_video_staged(source, list(outputs))
        return
    _native.split_video(source, list(outputs))
