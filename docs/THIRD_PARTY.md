# External Git submodules

LeTools tracks external source as Git submodules pinned by gitlink commit. This
keeps upstream history and ownership explicit without committing a second copy
of every upstream file into the letools repository. Current pins and licenses
are also machine-readable in `third_party/UPSTREAM.toml`.

## Layout

```text
third_party/
  UPSTREAM.toml
  external/
    lerobot-doctor/                 pinned upstream Git submodule
    lerobot-dataset-visualizer/     pinned upstream Git submodule
  patches/
    lerobot-dataset-visualizer/     letools-owned cache transformations
  licenses/
```

Files under `external/` are checked out directly from upstream and must not
receive letools changes. Python adapters live in `src/letools`; reviewable
upstream transformations live under `patches` and apply only to runtime cache
copies.

Doctor is packaged as an importable Python package and delegated to directly.
Visualizer source, patch, provenance, and license resources are packaged in the
letools wheel. At runtime it is copied to an XDG cache before patching or Bun
installation, so an installed wheel remains immutable.

## Clone and build

Initialize both submodules while cloning:

```bash
git clone --recurse-submodules https://github.com/XieWeikai/letools.git
```

Repair an existing Git checkout before building:

```bash
git submodule sync --recursive
git submodule update --init --recursive
```

The submodules are build inputs. Hatch packages the Doctor modules and the
Visualizer application into built wheels, so users installing a wheel do not
need Git or submodules at runtime. Building from a checkout requires initialized
submodules; builds intentionally do not fetch network content implicitly.
GitHub-generated source archives do not embed submodule contents; use a Git
clone or a published letools wheel instead of those archives.

## Updating a pin

1. Review the new upstream source, behavior, dependency lock, and license.
2. Fetch and checkout the reviewed commit inside the relevant submodule.
3. Stage the changed gitlink and update commit, retrieval date, and license in
   `UPSTREAM.toml`.
4. Rebase integration patches without editing the external tree.
5. Run upstream Doctor tests and upstream Visualizer tests, type-check, lint,
   format check, and production build.
6. Run letools tests, wheel-content checks, and a real local-dataset acceptance.
7. Describe the old/new pins and behavior changes in the commit message.

Every CI job that builds or tests the Python package initializes recursive
submodules. Native-only and package-index jobs skip the unrelated download. The
external-integration job enforces these boundaries on every pull request.
See [Dataset Doctor](DOCTOR.md) and [Dataset Visualizer](VISUALIZER.md) for the
user-facing and runtime boundaries.
