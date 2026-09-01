from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from letools.cli import parse_cli_args
from letools.source_providers import (
    AgileXSourceConfig,
    AgileXSourceProvider,
    HDF5SourceProvider,
    LeRobotSourceConfig,
    LeRobotSourceProvider,
    SourceProviderContext,
    SourceProviderRegistry,
    source_providers,
)
from letools.plugins import HDF5Mapping, HDF5NumericField
from letools.source_providers import HDF5SourceConfig


def test_registry_resolves_canonical_names_and_aliases() -> None:
    assert source_providers.choices() == ("lerobot", "auto", "hdf5", "agilex")
    assert source_providers.get("auto") is source_providers.get("lerobot")

    registry = SourceProviderRegistry()
    registry.register(LeRobotSourceProvider())
    with pytest.raises(ValueError, match="already registered"):
        registry.register(LeRobotSourceProvider())


def test_two_phase_parser_exposes_only_selected_provider_options(capsys) -> None:
    args = parse_cli_args(
        [
            "convert",
            "/data/raw",
            "/data/output",
            "--source-format",
            "agilex",
            "--instruction",
            "  fold the towel  ",
            "--fps",
            "20",
            "--robot-type",
            "  test-arm  ",
            "--to",
            "v3.0",
        ]
    )
    assert isinstance(args._source_provider, AgileXSourceProvider)
    config = args._source_provider.config_from_args(
        args, SourceProviderContext(interactive=False)
    )
    assert config == AgileXSourceConfig("fold the towel", 20, "test-arm")

    with pytest.raises(SystemExit):
        parse_cli_args(
            [
                "convert",
                "/data/raw",
                "/data/output",
                "--source-format",
                "hdf5",
                "--preset",
                "fixture",
                "--instruction",
                "not an HDF5 option",
                "--to",
                "v3.0",
            ]
        )
    assert "unrecognized arguments: --instruction" in capsys.readouterr().err


def test_provider_specific_help_and_legacy_preset_selection(capsys) -> None:
    with pytest.raises(SystemExit) as help_exit:
        parse_cli_args(
            ["convert", "in", "out", "--source-format", "agilex", "--help"]
        )
    assert help_exit.value.code == 0
    help_text = capsys.readouterr().out
    assert "--instruction" in help_text
    assert "--preset" not in help_text

    args = parse_cli_args(
        ["convert", "in", "out", "--preset", "fixture", "--to", "v3.0"]
    )
    assert isinstance(args._source_provider, HDF5SourceProvider)
    assert args.source_format == "auto"
    assert args.preset == "fixture"

    distributed = parse_cli_args(
        [
            "dist",
            "plan",
            "in",
            "out",
            "--source-format",
            "agilex",
            "--instruction",
            "fold",
            "--to",
            "v3.0",
            "--job-dir",
            "job",
        ]
    )
    assert isinstance(distributed._source_provider, AgileXSourceProvider)
    assert distributed.instruction == "fold"


def test_source_configs_are_immutable_and_validate_early() -> None:
    config = AgileXSourceConfig(" task ", fps=30, robot_type=" arm ")
    assert (config.instruction, config.robot_type) == ("task", "arm")
    with pytest.raises(FrozenInstanceError):
        config.fps = 60  # type: ignore[misc]
    with pytest.raises(ValueError, match="instruction"):
        AgileXSourceConfig("  ")
    with pytest.raises(ValueError, match="FPS"):
        AgileXSourceConfig("task", fps=0)


def test_providers_own_distributed_source_serialization(tmp_path) -> None:
    lerobot = LeRobotSourceProvider().distributed_spec(
        tmp_path / "dataset", LeRobotSourceConfig()
    )
    assert lerobot.kind == "lerobot"
    assert lerobot.options == {}

    agilex = AgileXSourceProvider().distributed_spec(
        tmp_path / "raw", AgileXSourceConfig("fold", 20, "arm")
    )
    assert agilex.kind == "agilex"
    assert agilex.options == {
        "instruction": "fold",
        "fps": 20,
        "robot_type": "arm",
    }

    hdf5 = HDF5SourceProvider().distributed_spec(
        tmp_path / "hdf5",
        HDF5SourceConfig(
            HDF5Mapping(
                fps=30,
                numeric_fields=(HDF5NumericField("action", "action"),),
                default_task="fold",
            )
        ),
    )
    assert hdf5.kind == "hdf5"
    assert hdf5.options["preset"]["mapping"]["default_task"] == "fold"
