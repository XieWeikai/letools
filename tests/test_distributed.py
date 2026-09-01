from __future__ import annotations

import json
from pathlib import Path

import pytest

from letools import (
    ConversionConfig,
    HDF5Source,
    compare_datasets,
    convert,
    validate_dataset,
)
from letools.distributed import (
    JobStore,
    KubernetesScheduler,
    LocalScheduler,
    SlurmScheduler,
    SourceSpec,
    WorkerConfig,
    distributed_status,
    hdf5_source_spec,
    plan_distributed_conversion,
    run_distributed_task,
    try_finalize_distributed_job,
)
from test_roundtrip import make_v21
from test_hdf5 import make_hdf5


def _plan(source: Path, destination: Path, job: Path):
    return plan_distributed_conversion(
        SourceSpec("lerobot", str(source)),
        destination,
        "v3.0" if source.name == "v21" else "v2.1",
        job,
        task_count=2,
        worker=WorkerConfig(workers=2, video_workers=1, data_file_size_mb=1),
    )


@pytest.mark.parametrize("direction", ["v21-v30", "v30-v21"])
def test_local_distributed_conversion_is_semantically_equal(
    tmp_path: Path, direction: str
) -> None:
    v21 = make_v21(tmp_path / "v21")
    if direction == "v21-v30":
        source = v21
        destination = tmp_path / "distributed-v30"
    else:
        source = tmp_path / "v30"
        convert(v21, source, "v3.0", config=ConversionConfig(workers=2))
        destination = tmp_path / "distributed-v21"
    plan = _plan(source, destination, tmp_path / "job")
    assert [(task.episode_start, task.episode_stop) for task in plan.tasks] == [
        (0, 2),
        (2, 3),
    ]

    LocalScheduler(max_parallel=2).submit(tmp_path / "job")

    status = distributed_status(tmp_path / "job")
    assert status.state == "published"
    assert (status.completed_tasks, status.completed_episodes) == (2, 3)
    assert validate_dataset(destination, deep=True).valid
    assert compare_datasets(source, destination).equal


def test_worker_retry_reuses_valid_commit_record(tmp_path: Path) -> None:
    source = make_v21(tmp_path / "v21")
    job = tmp_path / "job"
    _plan(source, tmp_path / "output", job)

    first = run_distributed_task(job, 0, finalize=False)
    marker_mtime = JobStore(job).result_path(0).stat().st_mtime_ns
    second = run_distributed_task(job, 0, finalize=False)

    assert second == first
    assert JobStore(job).result_path(0).stat().st_mtime_ns == marker_mtime
    assert distributed_status(job).completed_tasks == 1


def test_finalizer_recovers_a_missing_shared_publication_marker(tmp_path: Path) -> None:
    source = make_v21(tmp_path / "v21")
    destination = tmp_path / "output"
    job = tmp_path / "job"
    _plan(source, destination, job)
    LocalScheduler(max_parallel=2).submit(job)
    marker = job / "published.json"
    marker.unlink()

    status = try_finalize_distributed_job(job)

    assert status.state == "published"
    assert marker.is_file()
    provenance = json.loads(
        (destination / ".letools-distributed.json").read_text()
    )
    assert provenance["job_id"] == JobStore(job).load_plan().job_id


def test_hdf5_distributed_conversion_embeds_mapping_and_preserves_media(
    tmp_path: Path,
) -> None:
    root, mapping = make_hdf5(tmp_path / "hdf5")
    destination = tmp_path / "output"
    job = tmp_path / "job"
    plan_distributed_conversion(
        hdf5_source_spec(root, mapping),
        destination,
        "v3.0",
        job,
        task_count=2,
        worker=WorkerConfig(workers=1, video_workers=1, data_file_size_mb=1),
    )

    LocalScheduler(max_parallel=1).submit(job)

    assert validate_dataset(destination, deep=True).valid
    assert compare_datasets(
        HDF5Source(root, mapping), destination, check_videos=True
    ).equal


def test_requested_task_count_is_exact_and_capped_by_episodes(tmp_path: Path) -> None:
    source = make_v21(tmp_path / "v21")
    plan = plan_distributed_conversion(
        SourceSpec("lerobot", str(source)),
        tmp_path / "output",
        "v3.0",
        tmp_path / "job",
        task_count=2,
        worker=WorkerConfig(workers=1, video_workers=1),
    )
    assert len(plan.tasks) == 2
    assert [(task.episode_start, task.episode_stop) for task in plan.tasks] == [
        (0, 2),
        (2, 3),
    ]


def test_scheduler_adapters_render_the_same_worker_protocol(tmp_path: Path) -> None:
    source = make_v21(tmp_path / "v21")
    job = tmp_path / "job"
    _plan(source, tmp_path / "output", job)

    slurm = SlurmScheduler(
        max_parallel=2,
        cpus_per_task=4,
        memory="16G",
        partition="batch",
        submit=False,
    ).submit(job)
    script = slurm.artifacts[0].read_text()
    command = json.loads((job / "scheduler/slurm-command.json").read_text())
    assert "-m letools.cli dist worker" in script
    assert "SLURM_ARRAY_TASK_ID" in script
    assert "--array=0-1%2" in command
    assert "--cpus-per-task=4" in command

    kubernetes = KubernetesScheduler(
        "ghcr.io/example/letools:test",
        namespace="datasets",
        max_parallel=2,
        cpu="4",
        memory="16Gi",
        pvc_claim="shared-data",
        mount_path=str(tmp_path),
        submit=False,
    ).submit(job)
    manifest = json.loads(kubernetes.artifacts[0].read_text())
    assert manifest["spec"]["completionMode"] == "Indexed"
    assert manifest["spec"]["completions"] == 2
    command = manifest["spec"]["template"]["spec"]["containers"][0]["command"]
    assert command[:3] == ["letools", "dist", "worker"]
    assert command[-1] == "JOB_COMPLETION_INDEX"
    assert manifest["spec"]["template"]["spec"]["volumes"][0][
        "persistentVolumeClaim"
    ]["claimName"] == "shared-data"


def test_scheduler_resource_guards_reject_oversubscription(tmp_path: Path) -> None:
    source = make_v21(tmp_path / "v21")
    job = tmp_path / "job"
    _plan(source, tmp_path / "output", job)

    with pytest.raises(ValueError, match="node-local concurrency"):
        SlurmScheduler(cpus_per_task=1, submit=False).submit(job)
    with pytest.raises(ValueError, match="node-local concurrency"):
        KubernetesScheduler("image", cpu="1", submit=False).submit(job)
