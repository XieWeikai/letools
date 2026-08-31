from __future__ import annotations

from pathlib import Path

from letools.cli import build_parser, main
from letools.source_providers import HDF5SourceProvider, SourceProviderContext
from letools.plugins import HDF5Source
from letools.tools.hdf5_preset import (
    HDF5Preset,
    inspect_hdf5,
    list_presets,
    load_preset,
    save_preset,
)
from letools.tools.hdf5_tui import run_hdf5_preset_wizard, select_hdf5_preset
from letools.validation import validate_dataset
from test_hdf5 import make_hdf5


def test_hdf5_inspection_and_preset_roundtrip(tmp_path: Path, monkeypatch) -> None:
    root, mapping = make_hdf5(tmp_path / "hdf5")
    sample, fields = inspect_hdf5(root)
    by_key = {field.key: field for field in fields}

    assert sample.name == "episode_0.hdf5"
    assert by_key["observations/qpos"].kind == "numeric"
    assert by_key["language_instruction"].kind == "text"
    image = by_key["observations/images/front"]
    assert (image.kind, image.width, image.height) == ("encoded_image", 32, 24)

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    preset = HDF5Preset("fixture", mapping, "test mapping")
    path = save_preset(preset)
    assert path.name == "fixture.json"
    assert load_preset("fixture") == preset
    assert list_presets() == ((path, preset),)


def test_hdf5_preset_wizard_and_cli_source_selection(tmp_path: Path) -> None:
    root, _ = make_hdf5(tmp_path / "hdf5")
    answers = iter(
        [
            "10",  # FPS
            "all",  # numeric fields
            "",  # action target
            "",  # action dtype
            "left,right",  # action names
            "",  # qpos target
            "",  # qpos dtype
            "",  # qpos names
            "all",  # video fields
            "",  # video target
            "",  # width
            "",  # height
            "",  # task dataset
            "test-arm",  # robot type
            "fixture preset",  # description
            "",  # save confirmation
        ]
    )
    output: list[str] = []
    preset_path = tmp_path / "presets" / "fixture.json"
    preset, saved = run_hdf5_preset_wizard(
        root,
        name="fixture",
        output=preset_path,
        input_fn=lambda _prompt: next(answers),
        output_fn=output.append,
    )

    assert saved == preset_path
    assert [field.target_key for field in preset.mapping.numeric_fields] == [
        "action",
        "observation.state",
    ]
    assert preset.mapping.video_fields[0].target_key == "observation.images.front"
    assert preset.mapping.task_key == "language_instruction"
    assert any("Preset preview" in line for line in output)

    provider = HDF5SourceProvider()
    source = provider.create(
        root,
        build_parser(provider).parse_args(
            [
                "convert",
                str(root),
                str(tmp_path / "unused"),
                "--source-format",
                "hdf5",
                "--preset",
                str(preset_path),
                "--to",
                "v3.0",
            ]
        ),
        SourceProviderContext(interactive=False),
    )
    assert isinstance(source, HDF5Source)
    assert source.metadata.total_frames == 7


def test_cli_exposes_hdf5_preset_commands_and_interactive_selection(
    tmp_path: Path, monkeypatch
) -> None:
    _, mapping = make_hdf5(tmp_path / "hdf5")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    preset = HDF5Preset("stored", mapping)
    save_preset(preset)

    selected = select_hdf5_preset(input_fn=lambda _prompt: "", output_fn=lambda _line: None)
    assert selected == preset
    args = build_parser().parse_args(["tools", "hdf5-preset", "show", "stored"])
    assert (args.command, args.tool, args.preset_command) == (
        "tools",
        "hdf5-preset",
        "show",
    )


def test_cli_converts_hdf5_with_explicit_preset(tmp_path: Path, capsys) -> None:
    root, mapping = make_hdf5(tmp_path / "hdf5")
    preset_path = save_preset(
        HDF5Preset("cli-fixture", mapping), tmp_path / "cli-fixture.json"
    )
    destination = tmp_path / "v30"

    exit_code = main(
        [
            "convert",
            str(root),
            str(destination),
            "--preset",
            str(preset_path),
            "--to",
            "v3.0",
            "--workers",
            "1",
            "--video-workers",
            "1",
        ]
    )

    assert exit_code == 0
    assert '"source_version": "hdf5-v1"' in capsys.readouterr().out
    assert validate_dataset(destination, deep=True).valid
