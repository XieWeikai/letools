"""Dependency-free terminal wizard for authoring HDF5 mapping presets.

The UI intentionally uses ordinary terminal input instead of a full-screen
curses application. It remains usable over SSH and inside an interactive Slurm
allocation, while injected input/output functions keep the workflow testable.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from letools.plugins.hdf5 import HDF5Mapping, HDF5NumericField, HDF5VideoField
from letools.tools.hdf5_preset import (
    HDF5FieldDescription,
    HDF5Preset,
    inspect_hdf5,
    list_presets,
    save_preset,
)


Input = Callable[[str], str]
Output = Callable[[str], None]


def _suggest_target(key: str, *, video: bool = False) -> str:
    """Offer an editable convention-based target without claiming semantics."""

    leaf = key.rsplit("/", 1)[-1]
    lowered = leaf.lower()
    if video:
        camera = re.sub(r"^(camera_|cam_)", "", lowered)
        camera = camera.replace("_camera", "").replace("-", "_")
        return f"observation.images.{camera}"
    if lowered in {"action", "actions"}:
        return "action"
    if lowered in {"qpos", "joint_position", "joint_positions"}:
        return "observation.state"
    if lowered in {"qvel", "joint_velocity", "joint_velocities"}:
        return "observation.velocity"
    if lowered in {"timestamp", "time_stamp", "time"}:
        return "source.timestamp"
    return key.strip("/").replace("/", ".")


def _ask(
    prompt: str,
    *,
    default: str | None,
    input_fn: Input,
    validate: Callable[[str], bool] | None = None,
    output_fn: Output,
) -> str:
    while True:
        suffix = f" [{default}]" if default is not None else ""
        value = input_fn(f"{prompt}{suffix}: ").strip()
        if not value and default is not None:
            value = default
        if value and (validate is None or validate(value)):
            return value
        output_fn("Invalid value; please try again.")


def _ask_indices(
    prompt: str,
    fields: Sequence[HDF5FieldDescription],
    *,
    required: bool,
    input_fn: Input,
    output_fn: Output,
) -> tuple[HDF5FieldDescription, ...]:
    if not fields:
        if required:
            raise ValueError(f"No compatible fields are available for {prompt.lower()}")
        return ()
    for index, field in enumerate(fields, 1):
        details = f"shape={list(field.shape)} dtype={field.dtype}"
        if field.kind == "encoded_image":
            details += f" decoded={field.width}x{field.height}"
        output_fn(f"  {index:>2}. {field.key}  ({details})")
    while True:
        value = input_fn(f"{prompt} (comma-separated indexes or 'all'): ").strip().lower()
        if not value and not required:
            return ()
        try:
            indexes = (
                set(range(1, len(fields) + 1))
                if value == "all"
                else {int(item.strip()) for item in value.split(",") if item.strip()}
            )
            selected = tuple(fields[index - 1] for index in sorted(indexes))
            if selected and all(1 <= index <= len(fields) for index in indexes):
                return selected
        except (ValueError, IndexError):
            pass
        message = (
            "Select one or more listed indexes."
            if required
            else "Select listed indexes or leave blank."
        )
        output_fn(message)


def _print_inventory(
    sample: Path,
    fields: Sequence[HDF5FieldDescription],
    output_fn: Output,
) -> None:
    output_fn("\nHDF5 mapping preset wizard")
    output_fn(f"Representative episode: {sample}")
    output_fn("Discovered datasets:")
    for field in fields:
        shape = "x".join(map(str, field.shape)) or "scalar"
        output_fn(f"  [{field.kind:<13}] {field.key:<45} {shape:<18} {field.dtype}")


def _task_policy(
    text_fields: Sequence[HDF5FieldDescription],
    *,
    input_fn: Input,
    output_fn: Output,
) -> tuple[str | None, str | None]:
    if text_fields:
        output_fn("\nTask source:")
        for index, field in enumerate(text_fields, 1):
            output_fn(f"  {index:>2}. HDF5 dataset: {field.key}")
        output_fn(f"  {len(text_fields) + 1:>2}. Fixed task text")
        choice = int(
            _ask(
                "Choose task source",
                default="1",
                input_fn=input_fn,
                output_fn=output_fn,
                validate=lambda value: value.isdigit()
                and 1 <= int(value) <= len(text_fields) + 1,
            )
        )
        if choice <= len(text_fields):
            return text_fields[choice - 1].key, None
    task = _ask(
        "Fixed task text",
        default=None,
        input_fn=input_fn,
        output_fn=output_fn,
    )
    return None, task


def run_hdf5_preset_wizard(
    root: str | Path,
    *,
    name: str | None = None,
    output: str | Path | None = None,
    episode_glob: str = "*.hdf5",
    overwrite: bool = False,
    input_fn: Input = input,
    output_fn: Output = print,
) -> tuple[HDF5Preset, Path]:
    """Interactively inspect an HDF5 source and save a reusable preset."""

    sample, fields = inspect_hdf5(root, episode_glob=episode_glob)
    _print_inventory(sample, fields, output_fn)
    numeric_candidates = tuple(field for field in fields if field.kind == "numeric")
    video_candidates = tuple(field for field in fields if field.kind == "encoded_image")
    text_candidates = tuple(field for field in fields if field.kind == "text")

    fps = int(
        _ask(
            "Dataset FPS",
            default="30",
            input_fn=input_fn,
            output_fn=output_fn,
            validate=lambda value: value.isdigit() and int(value) > 0,
        )
    )
    output_fn("\nNumeric features:")
    selected_numeric = _ask_indices(
        "Select numeric features",
        numeric_candidates,
        required=True,
        input_fn=input_fn,
        output_fn=output_fn,
    )
    numeric_fields = []
    for field in selected_numeric:
        output_fn(f"\nMap numeric field {field.key}")
        target = _ask(
            "LeRobot target key",
            default=_suggest_target(field.key),
            input_fn=input_fn,
            output_fn=output_fn,
        )
        dtype = input_fn(f"Output dtype [{field.dtype}; blank keeps source]: ").strip() or None
        names_value = input_fn("Component names [comma-separated; blank omits]: ").strip()
        names = tuple(item.strip() for item in names_value.split(",")) if names_value else None
        numeric_fields.append(HDF5NumericField(field.key, target, dtype=dtype, names=names))

    output_fn("\nEncoded image features (leave blank to omit all):")
    selected_videos = _ask_indices(
        "Select video features",
        video_candidates,
        required=False,
        input_fn=input_fn,
        output_fn=output_fn,
    )
    video_fields = []
    for field in selected_videos:
        assert field.width is not None and field.height is not None
        output_fn(f"\nMap image field {field.key}")
        target = _ask(
            "LeRobot target key",
            default=_suggest_target(field.key, video=True),
            input_fn=input_fn,
            output_fn=output_fn,
        )
        width = int(
            _ask(
                "Frame width",
                default=str(field.width),
                input_fn=input_fn,
                output_fn=output_fn,
                validate=lambda value: value.isdigit() and int(value) > 0,
            )
        )
        height = int(
            _ask(
                "Frame height",
                default=str(field.height),
                input_fn=input_fn,
                output_fn=output_fn,
                validate=lambda value: value.isdigit() and int(value) > 0,
            )
        )
        video_fields.append(
            HDF5VideoField(
                field.key,
                target,
                width=width,
                height=height,
                encoded_format=field.encoded_format or "image",
            )
        )

    task_key, default_task = _task_policy(
        text_candidates, input_fn=input_fn, output_fn=output_fn
    )
    robot_type = input_fn("Robot type [blank for unknown]: ").strip() or None
    preset_name = name or _ask(
        "Preset name",
        default=Path(output).stem if output is not None else None,
        input_fn=input_fn,
        output_fn=output_fn,
    )
    description = input_fn("Description [optional]: ").strip()
    preset = HDF5Preset(
        name=preset_name,
        description=description,
        mapping=HDF5Mapping(
            fps=fps,
            numeric_fields=tuple(numeric_fields),
            video_fields=tuple(video_fields),
            task_key=task_key,
            default_task=default_task,
            robot_type=robot_type,
            episode_glob=episode_glob,
        ),
    )
    output_fn("\nPreset preview:")
    output_fn(json.dumps(preset.to_dict(), indent=2))
    confirm = _ask(
        "Save preset? (yes/no)",
        default="yes",
        input_fn=input_fn,
        output_fn=output_fn,
        validate=lambda value: value.lower() in {"yes", "y", "no", "n"},
    )
    if confirm.lower() in {"no", "n"}:
        raise KeyboardInterrupt("Preset creation cancelled")
    destination = save_preset(preset, output, overwrite=overwrite)
    output_fn(f"Saved preset: {destination}")
    return preset, destination


def select_hdf5_preset(
    *,
    input_fn: Input = input,
    output_fn: Output = print,
) -> HDF5Preset:
    """Let an interactive convert/plan command select a stored preset."""

    presets = list_presets()
    if not presets:
        raise FileNotFoundError(
            "No user HDF5 presets exist; run 'letools tools hdf5-preset create SOURCE'"
        )
    output_fn("Available HDF5 presets:")
    for index, (path, preset) in enumerate(presets, 1):
        mapping = preset.mapping
        output_fn(
            f"  {index:>2}. {preset.name}  fps={mapping.fps} "
            f"numeric={len(mapping.numeric_fields)} video={len(mapping.video_fields)} "
            f"({path})"
        )
    choice = int(
        _ask(
            "Choose preset",
            default="1",
            input_fn=input_fn,
            output_fn=output_fn,
            validate=lambda value: value.isdigit() and 1 <= int(value) <= len(presets),
        )
    )
    return presets[choice - 1][1]


def require_interactive_terminal() -> None:
    """Fail clearly instead of hanging a non-interactive batch job for input."""

    if not sys.stdin.isatty():
        raise ValueError("Interactive preset selection requires a TTY; pass --preset NAME_OR_PATH")


__all__ = [
    "require_interactive_terminal",
    "run_hdf5_preset_wizard",
    "select_hdf5_preset",
]
