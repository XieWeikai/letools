"""Backend-owned execution policy for coarse media write jobs.

Sources describe whether a FrameSequence needs process isolation; they do not
create pools or decide worker counts. Backends package target-layout jobs here,
and this module selects the cheapest executor that satisfies the source's
concurrency contract. Worker entry points stay at module scope so process-safe
plugins can be serialized with Python's spawn start method.
"""

from __future__ import annotations

import multiprocessing
from collections.abc import Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TypeVar

from letools._video import write_episode_media, write_media_group
from letools.conversion_types import VideoEncodingConfig
from letools.model import FrameSequence, MediaInput


@dataclass(frozen=True)
class GroupMediaJob:
    """One target v3 shard assembled from one or more media inputs."""

    inputs: tuple[MediaInput, ...]
    output: Path
    fps: int
    encoding: VideoEncodingConfig
    local_staging: bool


@dataclass(frozen=True)
class EpisodeMediaJob:
    """One source-locality group fanned out into v2.1 episode files."""

    outputs: tuple[tuple[MediaInput, Path], ...]
    fps: int
    encoding: VideoEncodingConfig


_JobT = TypeVar("_JobT", GroupMediaJob, EpisodeMediaJob)


def _write_group_job(job: GroupMediaJob) -> None:
    write_media_group(
        job.inputs,
        job.output,
        job.fps,
        job.encoding,
        local_staging=job.local_staging,
    )


def _write_episode_job(job: EpisodeMediaJob) -> None:
    write_episode_media(job.outputs, job.fps, job.encoding)


def _media_inputs(jobs: Iterable[GroupMediaJob | EpisodeMediaJob]) -> Iterable[MediaInput]:
    for job in jobs:
        if isinstance(job, GroupMediaJob):
            yield from job.inputs
        else:
            yield from (media for media, _ in job.outputs)


def jobs_require_process_isolation(
    jobs: Sequence[GroupMediaJob | EpisodeMediaJob],
) -> bool:
    """Return true only for homogeneous process-isolated frame workloads.

    A mixed workload stays on threads: process isolation is an opt-in contract,
    and silently trying to pickle an arbitrary third-party MediaInput would make
    plugin compatibility depend on executor internals.
    """

    inputs = tuple(_media_inputs(jobs))
    return bool(inputs) and all(
        isinstance(media, FrameSequence) and media.worker_isolation == "process"
        for media in inputs
    )


def _run_jobs(
    jobs: Sequence[_JobT], worker: Callable[[_JobT], None], workers: int
) -> None:
    if not jobs:
        return
    count = min(max(1, workers), len(jobs))
    isolated = count > 1 and jobs_require_process_isolation(jobs)
    if isolated:
        # spawn never inherits an HDF5/Arrow handle or parent thread state.
        # Its small startup cost is paid once per backend media phase.
        context = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=count, mp_context=context) as pool:
            list(pool.map(worker, jobs))
        return
    with ThreadPoolExecutor(max_workers=count) as pool:
        list(pool.map(worker, jobs))


def run_group_media_jobs(jobs: Sequence[GroupMediaJob], workers: int) -> None:
    """Execute v3 shard jobs under their declared source isolation contract."""

    _run_jobs(jobs, _write_group_job, workers)


def run_episode_media_jobs(jobs: Sequence[EpisodeMediaJob], workers: int) -> None:
    """Execute v2.1 episode-media jobs under the source isolation contract."""

    _run_jobs(jobs, _write_episode_job, workers)
