# Installation and command discovery

## Recommended user installation

After cloning the repository, install letools as a user-level command:

```bash
git clone https://github.com/XieWeikai/letools.git
cd letools
uv tool install .
letools doctor
```

`uv tool install` creates an isolated runtime environment and publishes the
project's `letools` console script in the XDG user executable directory, normally
`$HOME/.local/bin`. It does not require root access or activation of a virtual
environment. The environment includes PyAV, PyArrow, HDF5 support, NumPy, and the
matching `letools-native` wheel declared by the project.

If the shell cannot find the new command, run this once and start a new shell:

```bash
uv tool update-shell
```

The executable directory can be inspected without guessing:

```bash
uv tool dir --bin
command -v letools
```

## What uv installs

The user-facing path is deliberately separate from a repository development
environment:

```text
shell PATH
    -> ~/.local/bin/letools
    -> uv-managed letools environment/bin/python
    -> installed letools package and runtime dependencies
    -> PyAV and optional letools-native capabilities
```

The console entry point comes from `[project.scripts]` in `pyproject.toml`:

```toml
[project.scripts]
letools = "letools.cli:main"
```

This is why `letools ...` works from any directory. `letools ...` is only
needed when deliberately running the project-local `.venv` without installing a
user command.

## Editable installation for repository work

Developers who want the direct command to import Python source from the current
checkout can install it in editable mode:

```bash
uv tool install --editable .
letools doctor
```

Python source changes are then visible without reinstalling the tool. Dependency,
entry-point, or native-wheel changes still require recreation:

```bash
uv tool install --force --editable .
```

The editable tool remains an isolated runtime environment. It resolves declared
runtime dependency ranges and is not the locked test environment.

## Locked development environment

For exact `uv.lock` reproduction, tests, and native development, use the project
environment:

```bash
uv sync --locked --group test --group native-dev
./scripts/link_letools.sh --no-sync
letools doctor
```

Running the script without `--no-sync` is the one-command setup for normal
locked runtime development:

```bash
./scripts/link_letools.sh
```

It runs `uv sync --locked`, then creates this user-owned link:

```text
$(uv tool dir --bin)/letools -> CHECKOUT/.venv/bin/letools
```

The script refuses to replace another `letools` command unless `--force` is
explicitly supplied. Remove only the link owned by the current checkout with:

```bash
./scripts/link_letools.sh --remove
```

Because this mode points into the checkout, moving or deleting the repository
breaks the link; rerun the script from the new location. Tests and developer-only
commands should still use `uv run pytest`, `uv run maturin`, and similar forms so
their environment is explicit.

## Updating and uninstalling

For a standalone user tool installed from a clone:

```bash
git pull
uv tool install --force .
```

For an editable tool, a normal source-only `git pull` is immediately visible.
Reinstall after dependency or packaging changes:

```bash
uv tool install --force --editable .
```

Remove a uv-managed standalone or editable tool with:

```bash
uv tool uninstall letools
```

This does not delete datasets, repositories, planner caches, or HDF5 presets.

## Slurm clusters

User-level installation is normally visible on every node when the home directory
and XDG data directories are shared. Slurm also needs the executable directory in
the job's inherited `PATH`. Verify both assumptions with a small allocation:

```bash
srun --partition=dev --cpus-per-task=1 --mem=1G letools doctor
```

The locked-link mode additionally requires the checkout and its `.venv` to be on
a path visible from compute nodes. A standalone `uv tool install .` does not need
the checkout at runtime. Neither mode installs system packages or modifies FFmpeg
environment variables; runtime provider selection remains the behavior described
in [Architecture](ARCHITECTURE.md).

The CI user-install job reproduces both command-publication paths: it runs
`uv tool install .` followed by direct `letools doctor`, then creates and removes
an isolated locked-environment link with `scripts/link_letools.sh`.

## Why not shell activation or aliases

Activating `.venv` changes one shell and is easy to forget in Slurm jobs. A shell
alias is often unavailable to non-interactive jobs. Publishing a real console
script in a user executable directory works for interactive shells, scripts, and
schedulers and preserves normal argument forwarding and exit codes.
