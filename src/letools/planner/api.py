"""Planner orchestration: inspect, choose, fingerprint, calibrate, and cache.

Planning always returns a static ConversionPlan. This module never requests
resources or adjusts a conversion after execution begins.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, replace
from pathlib import Path

from letools.conversion import convert
from letools.conversion_types import VideoEncodingConfig
from letools._video import validate_source_encoding
from letools.planner.cache import load_cached_choice, save_cached_choice
from letools.planner.calibrate import calibrate_workers
from letools.planner.heuristic import choose_heuristic
from letools.planner.inspect import inspect_dataset, inspect_resources, inspect_storage
from letools.planner.types import (
    CalibrationMeasurement,
    CalibrationOptions,
    ConversionPlan,
    PerformanceOverrides,
    PlannedConversionResult,
)
from letools.plugins import DatasetSource, open_dataset


_PLANNER_ALGORITHM_VERSION = 8


def _normalize_version(version: str) -> str:
    value = version.lower().removeprefix("lerobot-").removeprefix("v")
    if value in {"2.1", "21"}:
        return "v2.1"
    if value in {"3.0", "30", "3"}:
        return "v3.0"
    raise ValueError(f"Unsupported target version: {version}")


def _fingerprint_payload(
    target_version: str,
    resources: object,
    dataset: object,
    source_storage: object,
    destination_storage: object,
    overrides: PerformanceOverrides,
    video_encoding: VideoEncodingConfig,
) -> str:
    resource = asdict(resources)
    data = asdict(dataset)
    source = asdict(source_storage)
    destination = asdict(destination_storage)
    payload = {
        "schema": 1,
        "planner_algorithm": _PLANNER_ALGORITHM_VERSION,
        "target": target_version,
        "resources": {
            "cpus": resource["effective_cpus"],
            "memory_gib": resource["effective_memory_bytes"] // (1024**3),
            "cpu_model": resource["cpu_model"],
        },
        "overrides": asdict(overrides),
        "video_encoding": asdict(video_encoding),
        "dataset": data,
        "source_storage": {
            key: str(source[key])
            for key in ("mount_point", "filesystem", "storage_class", "device")
        },
        "destination_storage": {
            key: str(destination[key])
            for key in ("mount_point", "filesystem", "storage_class", "device")
        },
    }
    if data["encoding_media_inputs"]:
        import av

        payload["encoder_runtime"] = {
            "pyav": av.__version__,
            "ffmpeg": av.library_versions,
        }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def plan_conversion(
    source: str | Path | DatasetSource,
    destination: str | Path,
    target_version: str,
    *,
    video_encoding: VideoEncodingConfig | None = None,
    overrides: PerformanceOverrides | None = None,
    calibration: CalibrationOptions | None = None,
    use_cache: bool = True,
    cache_directory: Path | None = None,
    cache_ttl_seconds: float = 7 * 24 * 60 * 60,
) -> ConversionPlan:
    """Build a static plan for the resources visible to this process.

    Read-only resource/dataset/storage inspection and heuristic selection always
    run. A matching cache entry can replace worker choices; otherwise optional
    bounded calibration executes representative real conversion jobs before the
    immutable plan is returned.
    """

    started = time.perf_counter()
    overrides = overrides or PerformanceOverrides()
    dataset_source = open_dataset(source) if isinstance(source, (str, Path)) else source
    explicit_encoding = video_encoding is not None
    video_encoding = video_encoding or VideoEncodingConfig()
    video_cpu, video_memory = validate_source_encoding(
        dataset_source, video_encoding, explicit=explicit_encoding
    )
    target = _normalize_version(target_version)
    if dataset_source.metadata.version == target:
        raise ValueError(f"Source is already {target}")
    destination_path = Path(destination).resolve(strict=False)
    resources = inspect_resources()
    dataset = inspect_dataset(dataset_source)
    source_storage = inspect_storage(dataset_source.root)
    destination_storage = inspect_storage(destination_path)
    choice = choose_heuristic(
        target,
        resources,
        dataset,
        source_storage,
        destination_storage,
        overrides,
    )
    video_limit = resources.effective_cpus // video_cpu
    if video_memory:
        video_limit = min(
            video_limit, int(resources.effective_memory_bytes * 0.85) // video_memory
        )
    if video_limit < 1 or (
        overrides.video_workers and overrides.video_workers > video_limit
    ):
        raise ValueError(
            "Video workers/codec threads exceed the effective CPU or memory allocation"
        )
    choice = replace(
        choice,
        video_workers=min(choice.video_workers, video_limit),
        reasons=(
            *choice.reasons,
            *(
                (
                    f"image media costs {video_cpu} requested CPU(s) and approximately {video_memory // 1024**2} MiB per worker; encoding concurrency ceiling is {video_limit}",
                )
                if dataset.encoding_media_inputs
                else ()
            ),
        ),
    )
    fingerprint = _fingerprint_payload(
        target,
        resources,
        dataset,
        source_storage,
        destination_storage,
        overrides,
        video_encoding,
    )
    cached = load_cached_choice(fingerprint, cache_directory) if use_cache else None
    cache_hit = False
    if cached is not None:
        try:
            measurements = tuple(
                CalibrationMeasurement(**measurement)
                for measurement in cached.get("measurements", ())
            )
            choice = replace(
                choice,
                workers=int(cached["workers"]),
                video_workers=int(cached["video_workers"]),
                data_file_size_mb=cached.get("data_file_size_mb"),
                video_file_size_mb=cached.get("video_file_size_mb"),
                reasons=(
                    *choice.reasons,
                    "loaded a calibrated plan from the environment cache",
                ),
            )
            cache_hit = True
        except (KeyError, TypeError, ValueError):
            cached = None
    if cached is None:
        choice, measurements = calibrate_workers(
            dataset_source,
            target,
            destination_storage.existing_path,
            choice,
            resources.effective_cpus,
            calibration or CalibrationOptions(),
            video_encoding=video_encoding,
            video_worker_limit=video_limit,
            fixed_data_workers=overrides.workers is not None,
            fixed_video_workers=overrides.video_workers is not None,
            network_io=(
                source_storage.storage_class == "network"
                or destination_storage.storage_class == "network"
            ),
        )
        if use_cache and measurements:
            try:
                save_cached_choice(
                    fingerprint,
                    {
                        "workers": choice.workers,
                        "video_workers": choice.video_workers,
                        "data_file_size_mb": choice.data_file_size_mb,
                        "video_file_size_mb": choice.video_file_size_mb,
                        "measurements": [
                            asdict(measurement) for measurement in measurements
                        ],
                    },
                    ttl_seconds=cache_ttl_seconds,
                    cache_directory=cache_directory,
                )
            except OSError:
                pass
    # Recheck cached/calibrated choices; execution must obey encoder budgets too.
    choice = replace(
        choice,
        video_workers=min(choice.video_workers, video_limit),
        estimated_peak_memory_bytes=max(
            choice.estimated_peak_memory_bytes,
            min(choice.video_workers, video_limit) * video_memory,
        ),
    )
    return ConversionPlan(
        schema_version=1,
        source=dataset_source.root,
        destination=destination_path,
        source_version=dataset_source.metadata.version,
        target_version=target,
        workers=choice.workers,
        video_workers=choice.video_workers,
        data_file_size_mb=choice.data_file_size_mb,
        video_file_size_mb=choice.video_file_size_mb,
        resources=resources,
        dataset=dataset,
        source_storage=source_storage,
        destination_storage=destination_storage,
        fingerprint=fingerprint,
        confidence="calibrated" if measurements else "heuristic",
        planning_seconds=time.perf_counter() - started,
        estimated_peak_memory_bytes=choice.estimated_peak_memory_bytes,
        estimated_data_tasks=choice.estimated_data_tasks,
        estimated_video_tasks=choice.estimated_video_tasks,
        reasons=choice.reasons,
        measurements=measurements,
        cache_hit=cache_hit,
        video_encoding=video_encoding,
    )


def plan_and_convert(
    source: str | Path | DatasetSource,
    destination: str | Path,
    target_version: str,
    *,
    video_encoding: VideoEncodingConfig | None = None,
    overrides: PerformanceOverrides | None = None,
    calibration: CalibrationOptions | None = None,
    use_cache: bool = True,
    cache_directory: Path | None = None,
    overwrite: bool = False,
    validate: bool = True,
) -> PlannedConversionResult:
    """Plan once, translate the plan to ConversionConfig, and convert."""

    plan = plan_conversion(
        source,
        destination,
        target_version,
        video_encoding=video_encoding,
        overrides=overrides,
        calibration=calibration or CalibrationOptions(enabled=True),
        use_cache=use_cache,
        cache_directory=cache_directory,
    )
    result = convert(
        source,
        destination,
        target_version,
        config=plan.conversion_config(overwrite=overwrite, validate=validate),
    )
    return PlannedConversionResult(plan=plan, conversion=result)
