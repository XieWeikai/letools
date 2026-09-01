"""Thin scheduler adapters; conversion semantics never enter this module."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .executor import run_distributed_task, try_finalize_distributed_job
from .state import JobStore
from .types import SubmissionResult


class SchedulerAdapter(ABC):
    """Submit an existing immutable plan without interpreting its source."""

    name: str

    @abstractmethod
    def submit(self, job_dir: str | Path) -> SubmissionResult:
        raise NotImplementedError


class LocalScheduler(SchedulerAdapter):
    """Synchronous adapter used for development and scheduler-neutral tests."""

    name = "local"

    def __init__(self, max_parallel: int = 1):
        if max_parallel <= 0:
            raise ValueError("max_parallel must be positive")
        self.max_parallel = max_parallel

    def submit(self, job_dir: str | Path) -> SubmissionResult:
        store = JobStore(job_dir)
        plan = store.load_plan()
        with ThreadPoolExecutor(max_workers=min(self.max_parallel, len(plan.tasks))) as pool:
            list(
                pool.map(
                    lambda task: run_distributed_task(store.root, task.task_id),
                    plan.tasks,
                )
            )
        try_finalize_distributed_job(store.root)
        return SubmissionResult(self.name, None, store.root)


class SlurmScheduler(SchedulerAdapter):
    """Render and submit a Slurm array; each task self-finalizes when last."""

    name = "slurm"

    def __init__(
        self,
        *,
        max_parallel: int | None = None,
        cpus_per_task: int | None = None,
        memory: str | None = None,
        partition: str | None = None,
        account: str | None = None,
        time_limit: str | None = None,
        submit: bool = True,
    ):
        if max_parallel is not None and max_parallel <= 0:
            raise ValueError("max_parallel must be positive")
        if cpus_per_task is not None and cpus_per_task <= 0:
            raise ValueError("cpus_per_task must be positive")
        self.max_parallel = max_parallel
        self.cpus_per_task = cpus_per_task
        self.memory = memory
        self.partition = partition
        self.account = account
        self.time_limit = time_limit
        self.should_submit = submit

    def submit(self, job_dir: str | Path) -> SubmissionResult:
        store = JobStore(job_dir)
        plan = store.load_plan()
        required_cpus = max(plan.worker.workers, plan.worker.video_workers)
        if self.cpus_per_task is not None and self.cpus_per_task < required_cpus:
            raise ValueError(
                f"cpus_per_task={self.cpus_per_task} is below the plan's "
                f"node-local concurrency {required_cpus}"
            )
        artifact_dir = store.root / "scheduler"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        script = artifact_dir / "slurm-worker.sh"
        script.write_text(
            "#!/bin/bash\nset -euo pipefail\n"
            f"{shlex.quote(sys.executable)} -m letools.cli dist worker "
            f"{shlex.quote(str(store.root))} "
            '--task-id "${SLURM_ARRAY_TASK_ID}"\n',
            encoding="utf-8",
        )
        script.chmod(0o755)
        parallel = self.max_parallel or len(plan.tasks)
        command = [
            "sbatch",
            "--parsable",
            f"--array=0-{len(plan.tasks) - 1}%{parallel}",
            f"--job-name=letools-{plan.job_id[:8]}",
            f"--output={artifact_dir}/slurm-%A_%a.out",
        ]
        for option, value in (
            ("--cpus-per-task", self.cpus_per_task or required_cpus),
            ("--mem", self.memory),
            ("--partition", self.partition),
            ("--account", self.account),
            ("--time", self.time_limit),
        ):
            if value is not None:
                command.append(f"{option}={value}")
        command.append(str(script))
        command_artifact = artifact_dir / "slurm-command.json"
        command_artifact.write_text(
            json.dumps(command, indent=2) + "\n", encoding="utf-8"
        )
        scheduler_id = None
        if self.should_submit:
            completed = subprocess.run(command, check=True, text=True, capture_output=True)
            scheduler_id = completed.stdout.strip().split(";", 1)[0]
        return SubmissionResult(
            self.name,
            scheduler_id,
            store.root,
            (script, command_artifact),
        )


class KubernetesScheduler(SchedulerAdapter):
    """Render and optionally apply a Kubernetes Indexed Job."""

    name = "kubernetes"

    def __init__(
        self,
        image: str,
        *,
        namespace: str = "default",
        max_parallel: int | None = None,
        cpu: str | None = None,
        memory: str | None = None,
        pvc_claim: str | None = None,
        mount_path: str = "/shared",
        submit: bool = True,
    ):
        if not image:
            raise ValueError("A Kubernetes worker image is required")
        if max_parallel is not None and max_parallel <= 0:
            raise ValueError("max_parallel must be positive")
        self.image = image
        self.namespace = namespace
        self.max_parallel = max_parallel
        self.cpu = cpu
        self.memory = memory
        self.pvc_claim = pvc_claim
        self.mount_path = mount_path
        self.should_submit = submit

    def submit(self, job_dir: str | Path) -> SubmissionResult:
        store = JobStore(job_dir)
        plan = store.load_plan()
        if self.cpu is not None and self.cpu.isdigit():
            required_cpus = max(plan.worker.workers, plan.worker.video_workers)
            if int(self.cpu) < required_cpus:
                raise ValueError(
                    f"cpu={self.cpu} is below the plan's node-local concurrency "
                    f"{required_cpus}"
                )
        else:
            required_cpus = max(plan.worker.workers, plan.worker.video_workers)
        if self.pvc_claim:
            mount = Path(self.mount_path)
            required_paths = (
                store.root,
                Path(plan.source.root),
                Path(plan.destination),
            )
            outside = [str(path) for path in required_paths if not path.is_relative_to(mount)]
            if outside:
                raise ValueError(
                    "Kubernetes PVC paths must retain their absolute names below "
                    f"{mount}: {', '.join(outside)}"
                )
        name = f"letools-{plan.job_id[:12]}"
        container: dict = {
            "name": "worker",
            "image": self.image,
            "command": [
                "letools",
                "dist",
                "worker",
                str(store.root),
                "--task-id-env",
                "JOB_COMPLETION_INDEX",
            ],
        }
        requests = {
            key: value
            for key, value in (
                ("cpu", self.cpu or str(required_cpus)),
                ("memory", self.memory),
            )
            if value
        }
        if requests:
            container["resources"] = {"requests": requests, "limits": requests}
        pod_spec: dict = {"restartPolicy": "Never", "containers": [container]}
        if self.pvc_claim:
            container["volumeMounts"] = [{"name": "shared", "mountPath": self.mount_path}]
            pod_spec["volumes"] = [
                {
                    "name": "shared",
                    "persistentVolumeClaim": {"claimName": self.pvc_claim},
                }
            ]
        manifest = {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {"name": name, "namespace": self.namespace},
            "spec": {
                "completionMode": "Indexed",
                "completions": len(plan.tasks),
                "parallelism": min(self.max_parallel or len(plan.tasks), len(plan.tasks)),
                "backoffLimitPerIndex": 3,
                "template": {"spec": pod_spec},
            },
        }
        artifact_dir = store.root / "scheduler"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact = artifact_dir / "kubernetes-job.json"
        artifact.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        scheduler_id = None
        if self.should_submit:
            subprocess.run(["kubectl", "apply", "-f", str(artifact)], check=True)
            scheduler_id = name
        return SubmissionResult(self.name, scheduler_id, store.root, (artifact,))


__all__ = [
    "KubernetesScheduler",
    "LocalScheduler",
    "SchedulerAdapter",
    "SlurmScheduler",
]
