"""Built-in source factories and the registry used by the command frontend."""

from .agilex import AgileXSourceConfig, AgileXSourceProvider
from .base import SourceProvider, SourceProviderContext
from .hdf5 import HDF5SourceConfig, HDF5SourceProvider
from .lerobot import LeRobotSourceConfig, LeRobotSourceProvider
from .registry import ENTRY_POINT_GROUP, ProviderInfo, SourceProviderRegistry


source_providers = SourceProviderRegistry(
    (
        LeRobotSourceProvider(),
        HDF5SourceProvider(),
        AgileXSourceProvider(),
    )
)
source_providers.discover()


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
    "ProviderInfo",
    "ENTRY_POINT_GROUP",
    "source_providers",
]
