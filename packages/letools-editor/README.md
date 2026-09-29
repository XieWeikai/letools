# letools-editor

`letools-editor` is an optional package for editing local LeRobot v2.1 and
v3.0 datasets. It is deliberately separate from the base `letools` install:
conversion, merge, planner, Doctor, and Visualizer users do not pay for this
command or its editing implementation.

Install it from a source checkout with Rust installed (no FFmpeg development
libraries or shared-library configuration is required):

```bash
uv sync --locked
uv pip install -e packages/letools-editor
uv run --no-sync letools editor --help
```

The command is then available as either `letools-editor` or
`letools editor`. See `docs/EDITOR.md` in the repository for the complete
operation contract and performance model.
