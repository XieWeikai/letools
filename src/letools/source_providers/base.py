"""CLI-facing factories that turn typed source options into DatasetSource objects."""

from __future__ import annotations

import argparse
import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Generic, TypeVar

from letools.plugins import DatasetSource

if TYPE_CHECKING:
    from letools.distributed.types import SourceSpec


@dataclass(frozen=True)
class SourceProviderContext:
    """Frontend capabilities available while resolving source configuration."""

    interactive: bool


_ConfigT = TypeVar("_ConfigT")


class SourceProvider(ABC, Generic[_ConfigT]):
    """Construct one source family without exposing its options to the core CLI."""

    name: str
    aliases: tuple[str, ...] = ()
    # Increment when the serialized provider/configuration contract changes.
    api_version: int = 1
    # Optional dataclass type used by the default wire-format decoder.
    config_type: type[_ConfigT] | None = None

    @abstractmethod
    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Register only the CLI options owned by this source family."""

        raise NotImplementedError

    @abstractmethod
    def config_from_args(
        self, args: argparse.Namespace, context: SourceProviderContext
    ) -> _ConfigT:
        """Validate parsed options and return an immutable, typed configuration."""

        raise NotImplementedError

    @abstractmethod
    def open(self, source: Path, config: _ConfigT) -> DatasetSource:
        """Construct the DatasetSource represented by a resolved configuration."""

        raise NotImplementedError

    def create(
        self,
        source: Path,
        args: argparse.Namespace,
        context: SourceProviderContext,
    ) -> DatasetSource:
        """Resolve frontend options and construct one source object."""

        return self.open(source, self.config_from_args(args, context))

    def distributed_spec(self, source: Path, config: _ConfigT) -> SourceSpec:
        """Serialize worker construction inputs for distributed conversion.

        Dataclass configurations get a portable default representation. A
        provider with legacy or non-JSON values can override this method and
        retain full control over its wire format.
        """

        from letools.distributed.types import SourceSpec

        return SourceSpec(
            kind="provider",
            root=str(source.resolve()),
            options=self.config_to_dict(config),
            provider=self.name,
            provider_api_version=self.api_version,
        )

    def config_to_dict(self, config: _ConfigT) -> dict[str, object]:
        """Return JSON-compatible construction options for a distributed plan.

        Dataclass configurations are supported automatically. Providers using a
        richer configuration (for example, a path object or enum) should
        override this method and normalize those values explicitly.
        """

        if not is_dataclass(config):
            raise TypeError(
                f"Provider {self.name!r} must implement config_to_dict for "
                f"{type(config).__name__}"
            )
        value = asdict(config)
        try:
            json.dumps(value)
        except TypeError as error:
            raise TypeError(
                f"Provider {self.name!r} config is not JSON serializable; "
                "override config_to_dict"
            ) from error
        return value

    def config_from_dict(self, value: dict[str, object]) -> _ConfigT:
        """Rebuild a configuration embedded in a distributed source spec."""

        if self.config_type is not None:
            return self.config_type(**value)

        raise NotImplementedError(
            f"Provider {self.name!r} must implement config_from_dict for "
            "distributed conversion"
        )


__all__ = ["SourceProvider", "SourceProviderContext"]
