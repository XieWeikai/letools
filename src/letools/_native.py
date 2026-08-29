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


def build_info() -> tuple[str, str] | None:
    return _native.build_info() if _native is not None else None


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
