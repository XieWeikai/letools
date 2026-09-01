# Runtime Setup

Read this reference only when LeTools is missing, environment diagnosis fails,
or the user explicitly asks for installation help.

## Use a checkout

Initialize the pinned integrations before installing an existing clone:

```bash
git submodule update --init --recursive
uv tool install .
letools doctor
```

`uv tool install .` creates the user-level command. For repository development,
follow `docs/INSTALLATION.md` and use the checkout's `.venv/bin/letools` when the
global command does not point at the checkout being changed.

Release wheels provide the normal PyAV and native-video arrangement. Do not
modify system FFmpeg, loader paths, or the user's shell environment merely to
run ordinary LeTools commands. Use `letools doctor` to identify the selected
video path before proposing native build work.

## Preserve the execution environment

The planner observes only resources visible to its current process, including
CPU affinity, cgroup memory, and storage endpoints. Run it inside the Slurm or
container allocation that will execute conversion. A plan made on a login node
does not describe a later compute allocation.

The presence of `sbatch` or `kubectl` is not authorization to submit work.
Before batch execution, inspect the operator's repository instructions and
quote paths in generated scripts. Keep the submitted script, manifest, and job
identifier available for diagnosis.
