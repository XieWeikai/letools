from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict
from pathlib import Path

from letools.planner.heuristic import choose_heuristic
from letools.planner.inspect import inspect_dataset, inspect_resources, inspect_storage
from letools.planner.types import ConversionPlan, PerformanceOverrides
from letools.plugins import DatasetSource, open_dataset


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
) -> str:
    resource = asdict(resources)
    data = asdict(dataset)
    source = asdict(source_storage)
    destination = asdict(destination_storage)
    payload = {
        "schema": 1,
        "target": target_version,
        "resources": {
            "cpus": resource["effective_cpus"],
            "memory_gib": resource["effective_memory_bytes"] // (1024**3),
            "cpu_model": resource["cpu_model"],
        },
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
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def plan_conversion(
    source: str | Path | DatasetSource,
    destination: str | Path,
    target_version: str,
    *,
    overrides: PerformanceOverrides | None = None,
) -> ConversionPlan:
    started = time.perf_counter()
    dataset_source = open_dataset(source) if isinstance(source, (str, Path)) else source
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
        overrides or PerformanceOverrides(),
    )
    fingerprint = _fingerprint_payload(
        target, resources, dataset, source_storage, destination_storage
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
        confidence="heuristic",
        planning_seconds=time.perf_counter() - started,
        estimated_peak_memory_bytes=choice.estimated_peak_memory_bytes,
        estimated_data_tasks=choice.estimated_data_tasks,
        estimated_video_tasks=choice.estimated_video_tasks,
        reasons=choice.reasons,
    )
