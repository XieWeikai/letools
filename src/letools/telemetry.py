"""Low-overhead in-process phase timing shared by planner and backends."""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class StageMetrics:
    """Accumulated wall time and optional work counters for one named phase."""

    elapsed_seconds: float
    tasks: int = 0
    input_bytes: int = 0
    output_bytes: int = 0


class StageRecorder:
    """Low-overhead, thread-safe conversion stage aggregation."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[str, list[float | int]] = {}

    def add(
        self,
        name: str,
        elapsed_seconds: float,
        *,
        tasks: int = 0,
        input_bytes: int = 0,
        output_bytes: int = 0,
    ) -> None:
        """Atomically accumulate one observation into a named stage."""

        with self._lock:
            value = self._values.setdefault(name, [0.0, 0, 0, 0])
            value[0] += elapsed_seconds
            value[1] += tasks
            value[2] += input_bytes
            value[3] += output_bytes

    @contextmanager
    def measure(
        self,
        name: str,
        *,
        tasks: int = 0,
        input_bytes: int = 0,
        output_bytes: int = 0,
    ) -> Iterator[None]:
        """Measure a context and accumulate elapsed time even on failure."""

        started = time.perf_counter()
        try:
            yield
        finally:
            self.add(
                name,
                time.perf_counter() - started,
                tasks=tasks,
                input_bytes=input_bytes,
                output_bytes=output_bytes,
            )

    def snapshot(self) -> dict[str, StageMetrics]:
        """Return an immutable point-in-time copy of all recorded stages."""

        with self._lock:
            return {
                name: StageMetrics(float(value[0]), int(value[1]), int(value[2]), int(value[3]))
                for name, value in self._values.items()
            }
