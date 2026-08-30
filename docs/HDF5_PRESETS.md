# HDF5 mapping presets

## Purpose

HDF5 stores arrays and groups but does not define robotics semantics. A preset
records the decisions needed to construct an `HDF5Source`: which HDF5 datasets
become LeRobot features, their target names, FPS, task policy, camera dimensions,
and optional component names. It is JSON data, not executable Python code.

The preset tool scans one representative episode and presents compatible fields.
It suggests conventional target names, but every suggestion is editable. The
scanner never decides that a field is semantically correct merely because its
name resembles `qpos`, `action`, or a camera name.

## Create a preset

Run the wizard in an interactive terminal:

```bash
uv run letools tools hdf5-preset create /data/hdf5 --name soft-fold
```

The wizard:

1. Selects the first naturally sorted file matching `*.hdf5`.
2. Lists every HDF5 dataset with its shape, dtype, and supported kind.
3. Asks for FPS and the numeric fields to export.
4. Asks for each target feature key, optional dtype cast, and component names.
5. Detects dimensions of supported encoded images and asks which cameras to export.
6. Selects a scalar string dataset as the task source, or asks for fixed task text.
7. Asks for `robot_type`, displays the complete JSON, and confirms before saving.

Use a different episode pattern when necessary:

```bash
uv run letools tools hdf5-preset create /data/hdf5 \
  --name soft-fold \
  --episode-glob 'episode_*.hdf5'
```

Without `--output`, the preset is written to:

```text
${XDG_CONFIG_HOME:-$HOME/.config}/letools/hdf5-presets/NAME.json
```

That user store is convenient for local repeated use. To review and version a
preset with a project, give an explicit path:

```bash
uv run letools tools hdf5-preset create /data/hdf5 \
  --name soft-fold \
  --output ./presets/soft-fold.json
```

An existing file is protected unless `--overwrite` is supplied.

## Inspect stored presets

```bash
uv run letools tools hdf5-preset list
uv run letools tools hdf5-preset show soft-fold
uv run letools tools hdf5-preset show ./presets/soft-fold.json
```

`list` includes only the standard user store. `show`, `convert`, and `plan`
accept either a stored name or an explicit JSON path.

## Convert with a preset

The reproducible, batch-safe form names the preset explicitly:

```bash
uv run letools convert \
  /data/hdf5 \
  /data/lerobot-v30 \
  --source-format hdf5 \
  --preset soft-fold \
  --to v3.0 \
  --auto
```

An explicit path works identically:

```bash
uv run letools convert /data/hdf5 /data/lerobot-v21 \
  --source-format hdf5 \
  --preset ./presets/soft-fold.json \
  --to v2.1
```

In an interactive terminal, omit `--preset` to choose from the user store:

```bash
uv run letools convert /data/hdf5 /data/lerobot-v30 \
  --source-format hdf5 \
  --to v3.0
```

Non-interactive shells and Slurm batch jobs must pass `--preset`; letools fails
instead of waiting indefinitely for terminal input. The planner uses the same
source options:

```bash
uv run letools plan /data/hdf5 /data/lerobot-v30 \
  --source-format hdf5 \
  --preset soft-fold \
  --to v3.0 \
  --calibrate
```

## Preset schema

The current schema version is 1:

```json
{
  "schema_version": 1,
  "name": "soft-fold",
  "description": "Three-camera cloth-folding episodes",
  "mapping": {
    "fps": 30,
    "episode_glob": "episode_*.hdf5",
    "robot_type": "example-arm",
    "task_key": "language_instruction",
    "default_task": null,
    "numeric_fields": [
      {
        "source_key": "observations/qpos",
        "target_key": "observation.state",
        "dtype": null,
        "names": null
      },
      {
        "source_key": "action",
        "target_key": "action",
        "dtype": null,
        "names": null
      }
    ],
    "video_fields": [
      {
        "source_key": "observations/images/cam_high",
        "target_key": "observation.images.front",
        "width": 640,
        "height": 480,
        "encoded_format": "jpeg"
      }
    ]
  }
}
```

Set exactly one of `task_key` and `default_task`. Presets require at least one
numeric field, unique target keys, positive FPS, and positive video dimensions.
`names`, when present, must match the flattened feature shape; that check occurs
when the preset is opened against the actual dataset.

## Inspection boundary and limitations

The first version recognizes:

- frame-aligned numeric arrays with integer, unsigned, float, or boolean dtype;
- scalar HDF5 string datasets as task candidates;
- one-dimensional variable-length `uint8` datasets containing individually
  encoded images, with dimensions decoded from the first frame.

Other datasets remain visible as `unsupported` but cannot be selected. In
particular, raw image tensors such as `(frames, height, width, channels)`, HDF5
compound records, external image paths, ragged numeric arrays, and dataset-level
attribute conventions need future source capabilities before a preset can map
them.

Inspection reads only one representative file. Opening `HDF5Source` subsequently
validates every matched episode, including key existence, equal frame counts,
consistent numeric schema, task availability, and component-name lengths. The
preset tool therefore improves authoring ergonomics without weakening runtime
validation or moving robot-specific semantics into the source plugin.
