from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from letools.conversion_types import ConversionConfig, ConversionResult


@dataclass(frozen=True)
class Distribution:
    count: int
    total: int
    minimum: int
    p50: int
    p95: int
    maximum: int


@dataclass(frozen=True)
class ResourceProfile:
    effective_cpus: int
    effective_memory_bytes: int
    affinity_cpus: int
    cgroup_cpus: int | None
    slurm_cpus: int | None
    cgroup_memory_bytes: int | None
    slurm_memory_bytes: int | None
    cpu_model: str
    hostname: str


@dataclass(frozen=True)
class StorageProfile:
    requested_path: Path
    existing_path: Path
    mount_point: Path
    filesystem: str
    storage_class: str
    device: str
    free_bytes: int


@dataclass(frozen=True)
class DatasetProfile:
    """Format-neutral workload shape consumed by planner policy."""

    source_kind: str
    source_configuration: str
    version: str
    episodes: int
    frames: int
    cameras: int
    data_files: int
    video_files: int
    encoding_media_inputs: int
    data_logical_bytes: Distribution
    data_physical_bytes: Distribution
    media_input_bytes: Distribution
    episodes_per_data_resource: Distribution

    # These aliases keep existing Python integrations source compatible while
    # plan JSON and new policy code use storage-neutral terminology.
    @property
    def parquet_uncompressed_bytes(self) -> Distribution:
        return self.data_logical_bytes

    @property
    def parquet_physical_bytes(self) -> Distribution:
        return self.data_physical_bytes

    @property
    def video_physical_bytes(self) -> Distribution:
        return self.media_input_bytes

    @property
    def episodes_per_data_file(self) -> Distribution:
        return self.episodes_per_data_resource


@dataclass(frozen=True)
class PerformanceOverrides:
    workers: int | None = None
    video_workers: int | None = None
    data_file_size_mb: int | None = None
    video_file_size_mb: int | None = None


@dataclass(frozen=True)
class CalibrationOptions:
    enabled: bool = False
    max_seconds: float = 10.0
    max_read_bytes: int = 1024**3
    max_write_bytes: int = 1024**3


@dataclass(frozen=True)
class CalibrationMeasurement:
    stage: str
    workers: int
    tasks: int
    input_bytes: int
    elapsed_seconds: float

    @property
    def throughput_bytes_per_second(self) -> float:
        return self.input_bytes / max(self.elapsed_seconds, 1e-9)


@dataclass(frozen=True)
class ConversionPlan:
    schema_version: int
    source: Path
    destination: Path
    source_version: str
    target_version: str
    workers: int
    video_workers: int
    data_file_size_mb: int | None
    video_file_size_mb: int | None
    resources: ResourceProfile
    dataset: DatasetProfile
    source_storage: StorageProfile
    destination_storage: StorageProfile
    fingerprint: str
    confidence: str
    planning_seconds: float
    estimated_peak_memory_bytes: int
    estimated_data_tasks: int
    estimated_video_tasks: int
    reasons: tuple[str, ...] = field(default_factory=tuple)
    measurements: tuple[CalibrationMeasurement, ...] = field(default_factory=tuple)
    cache_hit: bool = False

    def conversion_config(
        self,
        *,
        overwrite: bool = False,
        validate: bool = True,
    ) -> ConversionConfig:
        defaults = ConversionConfig()
        return ConversionConfig(
            workers=self.workers,
            video_workers=self.video_workers,
            data_file_size_mb=self.data_file_size_mb or defaults.data_file_size_mb,
            video_file_size_mb=self.video_file_size_mb or defaults.video_file_size_mb,
            overwrite=overwrite,
            validate=validate,
        )


@dataclass(frozen=True)
class PlannedConversionResult:
    plan: ConversionPlan
    conversion: ConversionResult
