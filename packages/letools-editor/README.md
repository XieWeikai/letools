# letools-editor

`letools-editor` is an optional package for editing local LeRobot v2.1 and
v3.0 datasets. It is deliberately separate from the base `letools` install:
conversion, merge, planner, Doctor, and Visualizer users do not pay for this
command or its editing implementation.

Install both commands from a recursive LeTools source checkout with Python
3.12+, uv, Rust 1.88+ and a platform linker. No FFmpeg development libraries,
libclang or shared-library configuration is required:

```bash
uv tool install --force --with ./packages/letools-editor --with-executables-from letools-editor .
letools editor inspect /data/demo --limit 10
letools editor plan /data/demo /data/edited --delete-episodes 1,5:8
letools editor apply /data/demo /data/edited \
  --delete-episodes 1,5:8 --set-task '3=Fold the cloth' \
  --resize 224x224 --video-codec libx264
```

`letools-editor` is an equivalent command. Episode IDs refer to the source;
ranges are half-open. Edits preserve the LeRobot version and write a separate
validated output. Combine task changes, episode deletion, feature removal and
camera transforms in one operation. Omit video-transform options to preserve
media where safe. H.264 cuts that fail the packet-remux proof require decoding.

FFmpeg is selected from `--ffmpeg`, `LETOOLS_EDITOR_FFMPEG`, `PATH`, then the
packaged executable. Python owns the physical manifest, scheduling and metadata;
FFmpeg owns encoding, and Rust streams exact decoded-output pixel statistics.
Omitted concurrency options enable a bounded automatic plan; there is no
editor `--auto`, plan cache, in-place mode or distributed executor.

For development from the repository root:

```bash
uv sync --locked --group test --group native-dev
uv pip install -e ./packages/letools-editor
uv run --no-sync letools editor --help
```

The root lockfile is base-only, so ordinary `uv sync` can remove this addon.
Use `--no-sync` or the `.venv` executables after installing it. Rust changes
need a rebuild. Source installation is supported; the base native-wheel
workflow does not publish editor wheels.

See the [editor guide](https://xieweikai.github.io/letools/EDITOR/) for every
CLI option, Python API, invariants and limits;
[measurements](https://xieweikai.github.io/letools/EDITOR_BENCHMARK/) for exact
resources and official comparisons; and
[development](https://xieweikai.github.io/letools/DEVELOPMENT/) for tests.
