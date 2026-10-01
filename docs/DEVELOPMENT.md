# Development, skills and documentation

The base package and optional editor have separate build boundaries. Work from
a recursive checkout so the pinned Doctor/Visualizer sources are present:

```bash
git clone --recurse-submodules https://github.com/XieWeikai/letools.git
cd letools
uv sync --locked --group test --group native-dev
```

An existing non-recursive clone needs `git submodule update --init --recursive`.
See [Installation](INSTALLATION.md) for direct commands, user tools and node
visibility. Use the checkout environment for development rather than an
unrelated globally installed command.

## Tests and optional editor build

Base-only tests:

```bash
uv run --no-sync pytest -q tests
```

For the editor, install Rust 1.88+ and a platform linker, then build its
extension into the same environment. No FFmpeg SDK is required:

```bash
uv pip install -e ./packages/letools-editor
uv run --no-sync letools editor --help
PYTHONPATH=tests uv run --no-sync pytest -q tests packages/letools-editor/tests
PYO3_PYTHON="$PWD/.venv/bin/python" \
  cargo test --locked --manifest-path packages/letools-editor/rust/Cargo.toml
cargo fmt --check --manifest-path packages/letools-editor/rust/Cargo.toml
PYO3_PYTHON="$PWD/.venv/bin/python" \
  cargo clippy --locked --manifest-path packages/letools-editor/rust/Cargo.toml -- -D warnings
```

These are workstation commands. On a Slurm cluster, run dataset-generating
tests and benchmarks inside an allocation. The checked-in test job accepts
normal `sbatch` overrides:

```bash
sbatch --partition=dev --output=/scratch/USER/editor-test-%j.log \
  scripts/test_editor.sbatch
```

Use paths visible on the selected node. If `/scratch` is node-local, pin the
node holding the fixtures and environment. The script requests 16 CPUs/64 GiB;
adapt that request to local limits. It runs from the submitting checkout and
uses that checkout's `.venv`.

The editor is deliberately outside the base lockfile. A plain `uv sync` or
automatic `uv run` synchronization can remove it; reinstall after such a sync
and use `--no-sync` for editor commands. Rebuild after editor Rust changes:

```bash
uv pip install --reinstall -e ./packages/letools-editor
```

The editor crate and `native/` are different extensions. The latter supplies
core copy/remux/packet primitives and has portable and FFmpeg-enabled builds.
Its SDK requirements and commands are in [Usage](USAGE.md#15-development-setup).
Replacing its released wheel with a developer build changes the runtime being
benchmarked; record the build and preserve separate baseline/candidate binaries.

## Correctness and performance changes

The editor suite covers both layouts, source immutability, task/index/split
updates, feature removal, H.264 remux proof and fallback, output statistics,
roundtrips, and rollback. The optional-import tests verify that base commands
work without the addon. Acceptance also uses the unmodified official metadata
and dataset loaders; historic v2.1 requires a compatible historic LeRobot
environment. See [Editor acceptance](EDITOR_BENCHMARK.md).

Use the checked-in
[self-improvement protocol](https://github.com/XieWeikai/letools/blob/main/self-improve/PROTOCOL.md)
for optimization: archive a hypothesis, compare repeated alternating runs,
validate semantics, report measured resources and noise, then accept or reject.
The tracked
[summary](https://github.com/XieWeikai/letools/blob/main/self-improve/SUMMARY.md)
records decisions. Raw datasets, scheduler logs and iteration archives remain
outside Git. A faster sample, reduced thread count or SIMD instruction alone
is not an accepted end-to-end speedup.

## Agent skills

Canonical sources are under
[`skills/`](https://github.com/XieWeikai/letools/tree/main/skills), with repository
discovery links in `.agents/skills` and `.claude/skills`:

| Skill | Purpose |
| --- | --- |
| `letools` | Execute conversion, merge, optional editing, validation and inspection workflows |
| `letools-add-source` | Implement user-owned providers without modifying core, including worker reconstruction |
| `letools-self-improve` | Run measured optimization campaigns under the repository protocol |

Update a skill's relevant reference when an operation gains new requirements.
Keep source episode IDs, same-environment installation and editor versus
converter flag names explicit. Do not duplicate dataset operations in agent
scripts or silently invent HDF5 mappings/task labels.

## Documentation preview and review

`README.md` is the repository entry point. `docs/index.md` is the website
landing page; `docs/README.md` is the repository documentation index and is
excluded from the site. MkDocs renders the same guides using `mkdocs.yml`.
Add new guides to navigation and update cross-links before committing.

Build exactly the versions used by Pages:

```bash
uvx --from mkdocs==1.6.1 --with mkdocs-material==9.7.7 \
  mkdocs build --strict --site-dir /tmp/letools-docs
uvx --from mkdocs==1.6.1 --with mkdocs-material==9.7.7 \
  mkdocs serve --dev-addr 127.0.0.1:8000
```

Check CLI examples against current `--help`, installation environment ownership,
internal links, and source/target boundaries. Mark benchmarks with exact
revisions, sample counts, allocation, cache and filesystem; historical results
must not be labeled as measurements of an untested new tip. Separate conversion,
remux, resize and reencoding claims. Use site-relative links between guides and
GitHub URLs for files outside `docs/` so published links remain valid.

## GitHub Pages and CI

Pushing `main` triggers `ci.yml` and `pages.yml`. CI covers base installation,
Python fallback, core native builds, the optional Python/Rust editor, and pinned
external integrations. Editor builds do not add its dependencies to base installs.
Pages builds strictly, downloads the existing native release wheels, adds the
`/simple/` Python package index, and deploys the combined artifact. Preserve
that index: normal Linux installation resolves `letools-native` through it.

```bash
gh run list --branch main --limit 10
gh run view RUN_ID
gh api repos/XieWeikai/letools/pages
```

Verify both workflow conclusions and the published page after a push. A local
successful build is not a deployment. The repository About link points to
<https://xieweikai.github.io/letools/>. `pages.yml` can also be dispatched
manually; native releases trigger an index refresh. The native release workflow
publishes `letools-native`, not `letools-editor`; a main push does not publish
an editor wheel or a new package release.

Do not edit Doctor or Visualizer submodule contents for local behavior changes.
The [external integration guide](THIRD_PARTY.md) explains pinned commits,
cache-applied patches and their additional validation.
