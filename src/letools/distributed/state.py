"""Atomic shared-filesystem state used independently of scheduler metadata."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterator, TextIO

from .types import DistributedPlan, DistributedStatus, TaskResult

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised on Windows
    _fcntl = None

try:
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover - exercised on POSIX
    _msvcrt = None


def _lock_stream(stream: TextIO) -> None:
    if _fcntl is not None:
        _fcntl.flock(stream.fileno(), _fcntl.LOCK_EX)
        return
    if _msvcrt is None:  # pragma: no cover - supported Python platforms provide one
        raise RuntimeError("No process file-lock implementation is available")
    if stream.seek(0, os.SEEK_END) == 0:
        stream.write("\0")
        stream.flush()
    stream.seek(0)
    _msvcrt.locking(stream.fileno(), _msvcrt.LK_LOCK, 1)


def _unlock_stream(stream: TextIO) -> None:
    if _fcntl is not None:
        _fcntl.flock(stream.fileno(), _fcntl.LOCK_UN)
        return
    assert _msvcrt is not None
    stream.seek(0)
    _msvcrt.locking(stream.fileno(), _msvcrt.LK_UNLCK, 1)


class JobStore:
    """Durable job state rooted on storage visible to every worker node."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    @property
    def plan_path(self) -> Path:
        return self.root / "plan.json"

    @property
    def parts_dir(self) -> Path:
        return self.root / "staging" / "parts"

    @property
    def results_dir(self) -> Path:
        return self.root / "results"

    def part_path(self, task_id: int) -> Path:
        return self.parts_dir / f"task-{task_id:06d}"

    def result_path(self, task_id: int) -> Path:
        return self.results_dir / f"task-{task_id:06d}.json"

    @staticmethod
    def _atomic_json(path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(value, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except Exception:
            Path(temporary).unlink(missing_ok=True)
            raise

    def create(self, plan: DistributedPlan) -> None:
        if self.plan_path.exists():
            raise FileExistsError(f"Distributed job already exists: {self.root}")
        self.parts_dir.mkdir(parents=True, exist_ok=False)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self._atomic_json(self.plan_path, plan.to_dict())

    def load_plan(self) -> DistributedPlan:
        return DistributedPlan.from_dict(json.loads(self.plan_path.read_text()))

    def load_result(self, task_id: int) -> TaskResult | None:
        path = self.result_path(task_id)
        if not path.exists():
            return None
        return TaskResult(**json.loads(path.read_text()))

    def write_result(self, result: TaskResult) -> None:
        self._atomic_json(self.result_path(result.task_id), asdict(result))

    def results(self) -> tuple[TaskResult, ...]:
        plan = self.load_plan()
        return tuple(
            result
            for task in plan.tasks
            if (result := self.load_result(task.task_id)) is not None
        )

    @contextmanager
    def finalize_lock(self) -> Iterator[None]:
        """Serialize final publication across workers finishing concurrently."""

        path = self.root / ".finalize.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+") as stream:
            _lock_stream(stream)
            try:
                yield
            finally:
                _unlock_stream(stream)

    def mark_published(self) -> None:
        self._atomic_json(self.root / "published.json", {"published": True})

    def status(self) -> DistributedStatus:
        plan = self.load_plan()
        results = self.results()
        published = (self.root / "published.json").exists()
        state = "published" if published else (
            "ready_to_finalize" if len(results) == len(plan.tasks) else "running"
        )
        return DistributedStatus(
            job_id=plan.job_id,
            state=state,
            completed_tasks=len(results),
            total_tasks=len(plan.tasks),
            completed_episodes=sum(result.episodes for result in results),
            total_episodes=plan.total_episodes,
            destination=Path(plan.destination),
        )


__all__ = ["JobStore"]
