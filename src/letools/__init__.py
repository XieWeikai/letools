"""Stable public Python API for conversion, planning, and source plugins."""

from .conversion import ConversionConfig, ConversionResult, convert
from .conversion_types import VideoEncodingConfig
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
from .telemetry import StageMetrics
from .validation import ComparisonReport, ValidationReport, compare_datasets, validate_dataset

__all__ = [
    "AgileXSource",
    "ComparisonReport",
    "ConversionConfig",
    "ConversionPlan",
    "ConversionResult",
    "DatasetSource",
    "Episode",
    "HDF5Mapping",
    "HDF5NumericField",
    "HDF5Source",
    "HDF5VideoField",
    "LeRobotV21Source",
    "LeRobotV30Source",
    "PerformanceOverrides",
    "PlannedConversionResult",
    "StageMetrics",
    "ValidationReport",
    "VideoEncodingConfig",
    "compare_datasets",
    "convert",
    "open_dataset",
    "plan_and_convert",
    "plan_conversion",
    "validate_dataset",
]
