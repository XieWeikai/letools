"""Built-in source factories and the registry used by the command frontend."""

from .agilex import AgileXSourceConfig, AgileXSourceProvider
from .base import SourceProvider, SourceProviderContext
from .hdf5 import HDF5SourceConfig, HDF5SourceProvider
from .lerobot import LeRobotSourceConfig, LeRobotSourceProvider
from .registry import SourceProviderRegistry


source_providers = SourceProviderRegistry(
    (
        LeRobotSourceProvider(),
        HDF5SourceProvider(),
        AgileXSourceProvider(),
    )
)


__all__ = [
    "AgileXSourceConfig",
    "AgileXSourceProvider",
    "HDF5SourceConfig",
    "HDF5SourceProvider",
    "LeRobotSourceConfig",
    "LeRobotSourceProvider",
    "SourceProvider",
    "SourceProviderContext",
    "SourceProviderRegistry",
    "source_providers",
]
