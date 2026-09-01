"""Read-only diagnostics for installed Python, native, and FFmpeg providers."""

from __future__ import annotations

import importlib.metadata
import shutil
import subprocess
from pathlib import Path
from typing import Any

import av

from letools import _native
from letools.external import upstream_project


def _distribution_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _system_ffmpeg() -> dict[str, str] | None:
    executable = shutil.which("ffmpeg")
    if executable is None:
        return None
    completed = subprocess.run(
        [executable, "-version"],
        check=False,
        capture_output=True,
        text=True,
    )
    first_line = completed.stdout.splitlines()[0] if completed.stdout else "unknown"
    return {"path": executable, "version": first_line}


def environment_report() -> dict[str, Any]:
    """Return serializable provider versions without modifying the environment."""

    native_path = Path(_native._native.__file__).resolve() if _native.available() else None
    return {
        "letools": _distribution_version("letools"),
        "native": {
            "available": _native.available(),
            "provider": _native.provider(),
            "version": _distribution_version("letools-native"),
            "module": str(native_path) if native_path else None,
            "build": _native.build_info(),
        },
        "pyav": {
            "version": av.__version__,
            "ffmpeg_libraries": {
                name: ".".join(str(part) for part in version)
                for name, version in sorted(av.library_versions.items())
            },
        },
        "system_ffmpeg": _system_ffmpeg(),
        "external": {
            project["name"]: {
                "commit": project["commit"],
                "repository": project["repository"],
                "license": project["license"],
            }
            for project in (
                upstream_project("lerobot-doctor"),
                upstream_project("lerobot-dataset-visualizer"),
            )
        },
    }
