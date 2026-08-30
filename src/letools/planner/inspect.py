from __future__ import annotations

import os
import platform
import re
import socket
from collections import Counter
from pathlib import Path

from letools.planner.types import DatasetProfile, Distribution, ResourceProfile, StorageProfile
from letools.plugins import DatasetSource


_MIB = 1024**2
_UNLIMITED = {"", "max", "-1"}
_NETWORK_FILESYSTEMS = {
    "fuse.juicefs",
    "fuse.s3fs",
    "nfs",
    "nfs4",
    "ceph",
    "cifs",
    "lustre",
    "gpfs",
}


def _positive_int(value: str | None) -> int | None:
    if value is None or value.strip() in _UNLIMITED:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _parse_cpu_set(value: str) -> int | None:
    cpus: set[int] = set()
    try:
        for part in value.strip().split(","):
            if not part:
                continue
            if "-" in part:
                start, end = (int(item) for item in part.split("-", 1))
                cpus.update(range(start, end + 1))
            else:
                cpus.add(int(part))
    except ValueError:
        return None
    return len(cpus) or None


def _read_first(paths: list[Path]) -> str | None:
    for path in paths:
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
    return None


def _cgroup_paths(filename: str) -> list[Path]:
    paths = [Path("/sys/fs/cgroup") / filename]
    try:
        groups = Path("/proc/self/cgroup").read_text(encoding="utf-8").splitlines()
    except OSError:
        return paths
    for row in groups:
        fields = row.split(":", 2)
        if len(fields) == 3:
            paths.append(Path("/sys/fs/cgroup") / fields[2].lstrip("/") / filename)
    return paths


def _slurm_cpu_limit(environment: dict[str, str]) -> int | None:
    direct = _positive_int(environment.get("SLURM_CPUS_PER_TASK"))
    if direct:
        return direct
    direct = _positive_int(environment.get("SLURM_CPUS_ON_NODE"))
    if direct:
        return direct
    value = environment.get("SLURM_JOB_CPUS_PER_NODE", "")
    match = re.match(r"(\d+)", value)
    return int(match.group(1)) if match else None


def _slurm_memory_limit(environment: dict[str, str], cpus: int | None) -> int | None:
    per_node = _positive_int(environment.get("SLURM_MEM_PER_NODE"))
    if per_node:
        return per_node * _MIB
    per_cpu = _positive_int(environment.get("SLURM_MEM_PER_CPU"))
    if per_cpu and cpus:
        return per_cpu * cpus * _MIB
    return None


def _host_memory_bytes() -> int:
    try:
        rows = Path("/proc/meminfo").read_text(encoding="utf-8").splitlines()
        values = {
            key.rstrip(":"): int(value) * 1024
            for key, value, *_ in (row.split() for row in rows)
        }
        return values.get("MemTotal", values.get("MemAvailable", 1 << 30))
    except (OSError, ValueError):
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))


def _cpu_model() -> str:
    try:
        for row in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if row.startswith("model name"):
                return row.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()


def inspect_resources(environment: dict[str, str] | None = None) -> ResourceProfile:
    environment = dict(os.environ if environment is None else environment)
    affinity = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count() or 1
    cgroup_cpus = _parse_cpu_set(
        _read_first(_cgroup_paths("cpuset.cpus.effective") + _cgroup_paths("cpuset.cpus")) or ""
    )
    slurm_cpus = _slurm_cpu_limit(environment)
    cpu_limits = [affinity, *(value for value in (cgroup_cpus, slurm_cpus) if value)]

    cgroup_memory = _positive_int(_read_first(_cgroup_paths("memory.max")))
    slurm_memory = _slurm_memory_limit(environment, slurm_cpus or affinity)
    memory_limits = [
        _host_memory_bytes(),
        *(value for value in (cgroup_memory, slurm_memory) if value),
    ]
    return ResourceProfile(
        effective_cpus=max(1, min(cpu_limits)),
        effective_memory_bytes=max(_MIB, min(memory_limits)),
        affinity_cpus=affinity,
        cgroup_cpus=cgroup_cpus,
        slurm_cpus=slurm_cpus,
        cgroup_memory_bytes=cgroup_memory,
        slurm_memory_bytes=slurm_memory,
        cpu_model=_cpu_model(),
        hostname=socket.gethostname(),
    )


def _existing_ancestor(path: Path) -> Path:
    current = path.resolve(strict=False)
    while not current.exists() and current != current.parent:
        current = current.parent
    return current


def _unescape_mount(value: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), value)


def inspect_storage(path: str | Path) -> StorageProfile:
    requested = Path(path).resolve(strict=False)
    existing = _existing_ancestor(requested)
    best: tuple[Path, str, str] | None = None
    try:
        rows = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError:
        rows = []
    for row in rows:
        before, separator, after = row.partition(" - ")
        if not separator:
            continue
        fields = before.split()
        filesystem = after.split()
        if len(fields) < 5 or len(filesystem) < 2:
            continue
        mount = Path(_unescape_mount(fields[4]))
        try:
            existing.relative_to(mount)
        except ValueError:
            continue
        if best is None or len(mount.parts) > len(best[0].parts):
            best = (mount, filesystem[0], filesystem[1])
    mount, filesystem, source = best or (Path("/"), "unknown", "unknown")
    if filesystem == "tmpfs":
        storage_class = "memory"
    elif filesystem in _NETWORK_FILESYSTEMS or filesystem.startswith("fuse."):
        storage_class = "network"
    elif filesystem in {"ext2", "ext3", "ext4", "xfs", "btrfs", "zfs"}:
        storage_class = "local"
    else:
        storage_class = "unknown"
    stat = os.statvfs(existing)
    return StorageProfile(
        requested_path=requested,
        existing_path=existing,
        mount_point=mount,
        filesystem=filesystem,
        storage_class=storage_class,
        device=source,
        free_bytes=stat.f_bavail * stat.f_frsize,
    )


def _distribution(values: list[int]) -> Distribution:
    if not values:
        return Distribution(0, 0, 0, 0, 0, 0)
    ordered = sorted(values)

    def percentile(value: float) -> int:
        return ordered[min(len(ordered) - 1, round((len(ordered) - 1) * value))]

    return Distribution(
        count=len(ordered),
        total=sum(ordered),
        minimum=ordered[0],
        p50=percentile(0.50),
        p95=percentile(0.95),
        maximum=ordered[-1],
    )


def inspect_dataset(source: DatasetSource) -> DatasetProfile:
    source_kind, source_configuration = source.planner_identity()
    data_resources = {}
    media_resources = {}
    episodes_per_resource: Counter[str] = Counter()
    for episode in source.episodes:
        data = source.data_profile(episode)
        data_resources.setdefault(data.locality_key, data)
        episodes_per_resource[data.locality_key] += 1
        for key in source.metadata.video_keys:
            media = source.media_profile(episode, key)
            media_resources.setdefault(media.locality_key, media)
    data_profiles = tuple(data_resources.values())
    media_profiles = tuple(media_resources.values())
    return DatasetProfile(
        source_kind=source_kind,
        source_configuration=source_configuration,
        version=source.metadata.version,
        episodes=source.metadata.total_episodes,
        frames=source.metadata.total_frames,
        cameras=len(source.metadata.video_keys),
        data_files=len(data_profiles),
        video_files=len(media_profiles),
        encoding_media_inputs=sum(
            profile.requires_encoding for profile in media_profiles
        ),
        data_logical_bytes=_distribution(
            [profile.resource_logical_bytes for profile in data_profiles]
        ),
        data_physical_bytes=_distribution(
            [profile.resource_physical_bytes for profile in data_profiles]
        ),
        media_input_bytes=_distribution(
            [profile.input_bytes for profile in media_profiles]
        ),
        episodes_per_data_resource=_distribution(list(episodes_per_resource.values())),
    )
