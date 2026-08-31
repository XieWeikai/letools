"""Stable public Python API for conversion, planning, and source plugins."""

from .conversion import ConversionConfig, ConversionResult, convert
from .conversion_types import VideoEncodingConfig
from .merge import merge_datasets, plan_merge
from .merge_types import MergeContribution, MergePlan, MergeResult
from .planner import (
    ConversionPlan,
    PerformanceOverrides,
    PlannedConversionResult,
    plan_and_convert,
    plan_conversion,
)
from .plugins import (
    AgileXSource,
    DatasetSource,
    Episode,
    HDF5Mapping,
    HDF5NumericField,
    HDF5Source,
    HDF5VideoField,
    LeRobotV21Source,
    LeRobotV30Source,
    open_dataset,
)
from .source_providers import (
    AgileXSourceConfig,
    AgileXSourceProvider,
    HDF5SourceConfig,
    HDF5SourceProvider,
    LeRobotSourceConfig,
    LeRobotSourceProvider,
    SourceProvider,
    SourceProviderContext,
    SourceProviderRegistry,
    source_providers,
)
from .telemetry import StageMetrics
from .validation import ComparisonReport, ValidationReport, compare_datasets, validate_dataset

__all__ = [
    "AgileXSource",
    "AgileXSourceConfig",
    "AgileXSourceProvider",
    "ComparisonReport",
    "ConversionConfig",
    "ConversionPlan",
    "ConversionResult",
    "DatasetSource",
    "Episode",
    "HDF5Mapping",
    "HDF5NumericField",
    "HDF5Source",
    "HDF5SourceConfig",
    "HDF5SourceProvider",
    "HDF5VideoField",
    "LeRobotV21Source",
    "LeRobotV30Source",
    "LeRobotSourceConfig",
    "LeRobotSourceProvider",
    "MergeContribution",
    "MergePlan",
    "MergeResult",
    "PerformanceOverrides",
    "PlannedConversionResult",
    "StageMetrics",
    "SourceProvider",
    "SourceProviderContext",
    "SourceProviderRegistry",
    "ValidationReport",
    "VideoEncodingConfig",
    "compare_datasets",
    "convert",
    "merge_datasets",
    "open_dataset",
    "plan_and_convert",
    "plan_conversion",
    "plan_merge",
    "source_providers",
    "validate_dataset",
]
