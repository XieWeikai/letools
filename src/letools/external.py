"""Locations and provenance for immutable external source snapshots.

The repository checkout and installed wheel have different layouts. Adapters
use this module instead of relying on the current working directory, keeping
both layouts deterministic and testable.
"""

from __future__ import annotations

import tomllib
from importlib.resources import files
from pathlib import Path
from typing import Any


def _checkout_third_party() -> Path | None:
    """Return the repository third_party directory when running from source."""

    candidate = Path(__file__).resolve().parents[2] / "third_party"
    return candidate if (candidate / "UPSTREAM.toml").is_file() else None


def external_file(relative: str) -> Path:
    """Resolve a bundled external file in a checkout or an installed wheel."""

    checkout = _checkout_third_party()
    if checkout is not None:
        return checkout / relative
    resource = files("letools").joinpath("_external", relative)
    # Hatch installs these resources as ordinary wheel files. Converting the
    # Traversable is therefore valid and avoids temporary extraction.
    return Path(str(resource))


def visualizer_source() -> Path:
    """Return the pristine pinned Visualizer application directory."""

    checkout = _checkout_third_party()
    if checkout is not None:
        return checkout / "external/lerobot-dataset-visualizer"
    return external_file("lerobot-dataset-visualizer")


def visualizer_patches() -> tuple[Path, ...]:
    """Return integration patches in deterministic application order."""

    checkout = _checkout_third_party()
    root = (
        checkout / "patches/lerobot-dataset-visualizer"
        if checkout is not None
        else external_file("patches/lerobot-dataset-visualizer")
    )
    return tuple(sorted(root.glob("*.patch")))


def upstream_manifest() -> dict[str, Any]:
    """Load the machine-readable provenance manifest bundled with letools."""

    path = (
        _checkout_third_party() / "UPSTREAM.toml"
        if _checkout_third_party() is not None
        else external_file("UPSTREAM.toml")
    )
    return tomllib.loads(path.read_text(encoding="utf-8"))


def upstream_project(name: str) -> dict[str, Any]:
    """Return one named upstream entry or fail on a packaging inconsistency."""

    for project in upstream_manifest()["project"]:
        if project["name"] == name:
            return project
    raise KeyError(f"Unknown external project: {name}")


__all__ = [
    "external_file",
    "upstream_manifest",
    "upstream_project",
    "visualizer_patches",
    "visualizer_source",
]
