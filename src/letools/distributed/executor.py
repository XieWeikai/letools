"""Idempotent worker execution and crash-recoverable final publication."""

from __future__ import annotations

import json
import shutil
import socket
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from letools._io import write_json, write_jsonl
from letools._stats import flatten_stats, aggregate_episode_stats
from letools.conversion import convert
from letools.conversion_types import ConversionConfig
from letools.merge import merge_datasets
from letools.validation import validate_dataset

from .source import EpisodeSubsetSource, open_source_spec
from .state import JobStore
from .types import (
    PROTOCOL_VERSION,
    DistributedPlan,
    DistributedStatus,
    DistributedTask,
    TaskResult,
)


def _restore_source_episode_stats(source, destination: Path, target_version: str) -> None:
    """Restore source episode statistics after the part merge remaps row indices.

    The specialized merge engine correctly recomputes system-column statistics
    for a general merge. A distributed conversion, however, must match the
    ordinary converter, which carries source episode statistics through the
    version change (including v2.1 chunk-local index statistics).
    """

    source_stats = {episode.index: episode.stats for episode in source.episodes}
    if target_version == "v2.1":
        rows = [
            {"episode_index": index, "stats": source_stats[index]}
            for index in range(len(source.episodes))
        ]
        write_jsonl(destination / "meta/episodes_stats.jsonl", rows)
        return

    episode_path = destination / "meta/episodes/chunk-000/file-000.parquet"
    table = pq.read_table(episode_path)
    columns = {name: table[name] for name in table.column_names if not name.startswith("stats/")}
    stats_columns: dict[str, list[object]] = {}
    for row_index, episode_index in enumerate(table["episode_index"].to_pylist()):
        flattened = flatten_stats(source_stats[int(episode_index)])
        for name in list(stats_columns):
            stats_columns[name].append(flattened.get(name))
        for name, value in flattened.items():
            if name not in stats_columns:
                stats_columns[name] = [None] * row_index + [value]
    arrays = [columns[name] for name in columns]
    names = list(columns)
    for name, values in stats_columns.items():
        field = table.schema.field(name) if name in table.column_names else None
        arrays.append(pa.array(values, type=field.type if field else None))
        names.append(name)
    pq.write_table(pa.Table.from_arrays(arrays, names=names), episode_path)
    write_json(
        destination / "meta/stats.json",
        aggregate_episode_stats([source_stats[index] for index in range(len(source.episodes))]),
    )


def _part_is_valid(path: Path, validate: bool) -> bool:
    if not path.is_dir() or not (path / "meta" / "info.json").is_file():
        return False
    return not validate or validate_dataset(path, deep=False).valid


def _result_matches(
    plan: DistributedPlan,
    task: DistributedTask,
    result: TaskResult,
    expected_part: Path,
) -> bool:
    """Reject stale, corrupt, or manually misplaced task commit records."""

    return (
        result.protocol_version == PROTOCOL_VERSION
        and result.job_id == plan.job_id
        and result.task_id == task.task_id
        and result.episode_start == task.episode_start
        and result.episode_stop == task.episode_stop
        and result.episodes == task.episodes
        and result.frames == task.expected_frames
        and Path(result.part).resolve() == expected_part.resolve()
    )


def _write_error(store: JobStore, task_id: int, error: BaseException) -> None:
    store._atomic_json(  # noqa: SLF001 - executor and store form one state boundary
        store.root / "errors" / f"task-{task_id:06d}.json",
        {"type": type(error).__name__, "message": str(error), "host": socket.gethostname()},
    )


def run_distributed_task(
    job_dir: str | Path,
    task_id: int,
    *,
    finalize: bool = True,
) -> TaskResult:
    """Run or resume one task and optionally attempt final publication.

    A valid result record is the commit marker. Repeated scheduler attempts
    return it without touching payload files; an incomplete attempt is replaced
    transactionally by the ordinary conversion coordinator.
    """

    store = JobStore(job_dir)
    plan = store.load_plan()
    if not 0 <= task_id < len(plan.tasks):
        raise IndexError(f"Task id {task_id} is outside [0, {len(plan.tasks)})")
    task = plan.tasks[task_id]
    part = store.part_path(task_id)
    existing = store.load_result(task_id)
    if existing is not None:
        if _result_matches(plan, task, existing, part) and _part_is_valid(
            part, plan.validate
        ):
            if finalize:
                try_finalize_distributed_job(store.root)
            return existing

    started = time.perf_counter()
    try:
        source = open_source_spec(plan.source)
        subset = EpisodeSubsetSource(source, task.episode_start, task.episode_stop)
        result = convert(
            subset,
            part,
            plan.target_version,
            config=ConversionConfig(
                workers=plan.worker.workers,
                video_workers=plan.worker.video_workers,
                data_file_size_mb=plan.worker.data_file_size_mb,
                video_file_size_mb=plan.worker.video_file_size_mb,
                overwrite=True,
                validate=plan.validate,
            ),
        )
        record = TaskResult(
            protocol_version=PROTOCOL_VERSION,
            job_id=plan.job_id,
            task_id=task_id,
            episode_start=task.episode_start,
            episode_stop=task.episode_stop,
            episodes=result.episodes,
            frames=result.frames,
            part=str(part),
            elapsed_seconds=time.perf_counter() - started,
            worker_host=socket.gethostname(),
        )
        if record.frames != task.expected_frames:
            raise ValueError(
                f"Task {task_id} wrote {record.frames} frames; expected {task.expected_frames}"
            )
        store.write_result(record)
        (store.root / "errors" / f"task-{task_id:06d}.json").unlink(missing_ok=True)
        if finalize:
            try_finalize_distributed_job(store.root)
        return record
    except Exception as error:
        _write_error(store, task_id, error)
        raise


def _provenance_path(root: Path) -> Path:
    return root / ".letools-distributed.json"


def _has_job_provenance(root: Path, plan: DistributedPlan) -> bool:
    path = _provenance_path(root)
    if not path.is_file():
        return False
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return False
    return (
        value.get("protocol_version") == PROTOCOL_VERSION
        and value.get("job_id") == plan.job_id
    )


def _validate_final_output(root: Path, plan: DistributedPlan) -> None:
    if plan.validate:
        report = validate_dataset(root, deep=True)
        if not report.valid:
            raise ValueError("Distributed output is invalid: " + "; ".join(report.errors))
    info = json.loads((root / "meta" / "info.json").read_text())
    if int(info["total_episodes"]) != plan.total_episodes:
        raise ValueError("Published episode total differs from the plan")
    if int(info["total_frames"]) != plan.total_frames:
        raise ValueError("Published frame total differs from the plan")


def _build_final_output(store: JobStore, parts: list[Path], final: Path) -> None:
    """Build a complete output beside the destination, ready for atomic rename."""

    plan = store.load_plan()
    if final.exists() and _has_job_provenance(final, plan):
        _validate_final_output(final, plan)
        return
    if len(parts) == 1:
        if final.exists():
            shutil.rmtree(final)
        shutil.copytree(parts[0], final)
    else:
        merge_datasets(
            parts,
            final,
            auto=False,
            data_workers=plan.worker.workers,
            file_workers=plan.worker.video_workers,
            overwrite=True,
            validate=plan.validate,
        )
    _validate_final_output(final, plan)
    store._atomic_json(  # noqa: SLF001 - provenance is part of job state
        _provenance_path(final),
        {"protocol_version": PROTOCOL_VERSION, "job_id": plan.job_id},
    )


def try_finalize_distributed_job(job_dir: str | Path) -> DistributedStatus:
    """Publish once when every task result exists; otherwise return immediately."""

    store = JobStore(job_dir)
    with store.finalize_lock():
        status = store.status()
        if status.state == "published" or status.completed_tasks < status.total_tasks:
            return status
        plan = store.load_plan()
        destination = Path(plan.destination)
        # Publication and its shared-state marker cannot be one filesystem
        # transaction. Provenance in the published tree closes that crash gap.
        if destination.exists() and _has_job_provenance(destination, plan):
            _validate_final_output(destination, plan)
            store.mark_published()
            return store.status()
        results = store.results()
        for task, result in zip(plan.tasks, results, strict=True):
            if not _result_matches(plan, task, result, store.part_path(task.task_id)):
                raise ValueError(f"Task {task.task_id} has an invalid commit record")
        if sum(item.episodes for item in results) != plan.total_episodes:
            raise ValueError("Task results do not cover the planned episode count")
        if sum(item.frames for item in results) != plan.total_frames:
            raise ValueError("Task results do not cover the planned frame count")
        parts = [Path(item.part) for item in results]
        if not all(_part_is_valid(part, plan.validate) for part in parts):
            raise ValueError("At least one distributed part is missing or invalid")
        final = destination.with_name(
            f".{destination.name}.letools-dist-{plan.job_id}"
        )
        _build_final_output(store, parts, final)
        _restore_source_episode_stats(
            open_source_spec(plan.source), final, plan.target_version
        )
        if destination.exists() and not plan.overwrite:
            raise FileExistsError(f"Destination already exists: {destination}")
        if destination.exists():
            shutil.rmtree(destination)
        final.replace(destination)
        store.mark_published()
        return store.status()


def distributed_status(job_dir: str | Path) -> DistributedStatus:
    """Return scheduler-independent progress from durable shared state."""

    return JobStore(job_dir).status()


__all__ = [
    "distributed_status",
    "run_distributed_task",
    "try_finalize_distributed_job",
]
