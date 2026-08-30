"""Transactional coordinator shared by every source plugin and target backend.

This module owns lifecycle policy: source dispatch, target selection, staging,
validation, atomic publication, failure cleanup, and top-level stage timing. It
does not parse source formats or decide physical target layout.
"""

from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path

from letools.backends import LeRobotV21Backend, LeRobotV30Backend
from letools.conversion_types import ConversionConfig, ConversionResult
from letools.plugins import DatasetSource, open_dataset
from letools.telemetry import StageRecorder


def _normalize_version(version: str) -> str:
    value = version.lower().removeprefix("lerobot-").removeprefix("v")
    if value in {"2.1", "21"}:
        return "v2.1"
    if value in {"3.0", "30", "3"}:
        return "v3.0"
    raise ValueError(f"Unsupported target version: {version}")


def convert(
    source: str | Path | DatasetSource,
    destination: str | Path,
    target_version: str,
    *,
    config: ConversionConfig | None = None,
) -> ConversionResult:
    """Convert a source into one supported LeRobot target transactionally.

    A complete dataset is written to a unique sibling staging directory. The
    destination is published only after backend completion and optional shallow
    validation; any failure removes staging and leaves an existing destination
    untouched. DatasetSource objects bypass path auto-detection, which is how
    explicit HDF5 and third-party plugins enter the pipeline.
    """

    config = config or ConversionConfig()
    started = time.perf_counter()
    recorder = StageRecorder()
    with recorder.measure("source_open"):
        dataset = open_dataset(source) if isinstance(source, (str, Path)) else source
    with recorder.measure("staging_prepare"):
        destination = Path(destination).resolve()
        target_version = _normalize_version(target_version)
        if dataset.metadata.version == target_version:
            raise ValueError(f"Source is already {target_version}")
        if destination.exists() and not config.overwrite:
            raise FileExistsError(f"Destination already exists: {destination}")
        backend = LeRobotV21Backend() if target_version == "v2.1" else LeRobotV30Backend()
        staging = destination.with_name(f".{destination.name}.letools-{uuid.uuid4().hex}")
    try:
        backend.write(dataset, staging, config, recorder)
        if config.validate:
            from letools.validation import validate_dataset

            with recorder.measure("conversion_validate"):
                report = validate_dataset(staging, deep=False)
                if not report.valid:
                    raise ValueError("Converted dataset is invalid: " + "; ".join(report.errors))
        with recorder.measure("publish_cleanup"):
            if destination.exists():
                shutil.rmtree(destination)
            staging.replace(destination)
    except Exception:
        with recorder.measure("publish_cleanup"):
            shutil.rmtree(staging, ignore_errors=True)
        raise
    return ConversionResult(
        source=dataset.root,
        destination=destination,
        source_version=dataset.metadata.version,
        target_version=target_version,
        episodes=dataset.metadata.total_episodes,
        frames=dataset.metadata.total_frames,
        elapsed_seconds=time.perf_counter() - started,
        stages=recorder.snapshot(),
    )


__all__ = ["ConversionConfig", "ConversionResult", "convert"]
