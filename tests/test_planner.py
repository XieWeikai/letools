from __future__ import annotations

from dataclasses import replace

import pytest

from letools.planner import PerformanceOverrides, plan_conversion
from letools.planner.heuristic import choose_heuristic, worker_candidates
from letools.planner.inspect import _parse_cpu_set, inspect_dataset, inspect_storage
from letools.planner.types import ResourceProfile
from test_roundtrip import make_v21


def test_cpu_set_and_worker_candidates() -> None:
    assert _parse_cpu_set("0-3,8,10-11") == 7
    assert _parse_cpu_set("") is None
    assert worker_candidates(8, 3) == (1, 2, 3)
    assert worker_candidates(5, 100) == (1, 2, 3, 4, 5)


def test_read_only_plan_profiles_dataset_and_storage(tmp_path) -> None:
    source = make_v21(tmp_path / "v21")
    destination = tmp_path / "missing" / "v30"
    plan = plan_conversion(source, destination, "v3.0")
    assert plan.confidence == "heuristic"
    assert plan.dataset.episodes == 3
    assert plan.dataset.frames == 9
    assert plan.dataset.parquet_uncompressed_bytes.total > 0
    assert plan.source_storage.filesystem == inspect_storage(source).filesystem
    assert 1 <= plan.workers <= plan.resources.effective_cpus
    assert plan.data_file_size_mb is not None
    assert plan.video_file_size_mb is not None
    assert plan.fingerprint == plan_conversion(source, destination, "v3.0").fingerprint


def test_explicit_overrides_are_constraints(tmp_path) -> None:
    source_path = make_v21(tmp_path / "v21")
    plan = plan_conversion(
        source_path,
        tmp_path / "v30",
        "v3.0",
        overrides=PerformanceOverrides(
            workers=1,
            video_workers=1,
            data_file_size_mb=64,
            video_file_size_mb=400,
        ),
    )
    assert (plan.workers, plan.video_workers) == (1, 1)
    assert (plan.data_file_size_mb, plan.video_file_size_mb) == (64, 400)


def test_heuristic_rejects_infeasible_override(tmp_path) -> None:
    source_path = make_v21(tmp_path / "v21")
    source_plan = plan_conversion(source_path, tmp_path / "v30", "v3.0")
    resources: ResourceProfile = replace(
        source_plan.resources,
        effective_cpus=2,
        effective_memory_bytes=256 * 1024**2,
    )
    dataset = inspect_dataset(__import__("letools").open_dataset(source_path))
    with pytest.raises(ValueError, match="CPU allocation"):
        choose_heuristic(
            "v3.0",
            resources,
            dataset,
            source_plan.source_storage,
            source_plan.destination_storage,
            PerformanceOverrides(workers=3),
        )
    with pytest.raises(ValueError, match="not applicable"):
        choose_heuristic(
            "v2.1",
            resources,
            dataset,
            source_plan.source_storage,
            source_plan.destination_storage,
            PerformanceOverrides(data_file_size_mb=64),
        )
