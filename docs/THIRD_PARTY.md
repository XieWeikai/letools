# External source snapshots

LeTools vendors source when an integration must remain reproducible and usable
after cloning without fetching an application's moving default branch. Current
pins and licenses are machine-readable in `third_party/UPSTREAM.toml`.

## Layout

```text
third_party/
  UPSTREAM.toml
  external/
    lerobot-doctor/                 immutable complete Python snapshot
    lerobot-dataset-visualizer/     immutable complete web/backend snapshot
  patches/
    lerobot-dataset-visualizer/     letools-owned cache transformations
  licenses/
```

Files under `external/` are upstream source, tests, documentation, and license
material with only nested Git metadata and generated build output omitted. They
must not receive direct letools changes. Python adapters live in `src/letools`;
reviewable upstream transformations live under `patches`.

Doctor is packaged as an importable Python package and delegated to directly.
Visualizer source, patch, provenance, and license resources are packaged in the
letools wheel. At runtime it is copied to an XDG cache before patching or Bun
installation, so an installed wheel remains immutable.

## Updating a pin

1. Review the new upstream source, behavior, dependency lock, and license.
2. Replace the complete external snapshot without `.git` or generated output.
3. Update repository, commit, retrieval date, and license in `UPSTREAM.toml`.
4. Rebase integration patches without editing the external tree.
5. Run upstream Doctor tests and upstream Visualizer tests, type-check, lint,
   format check, and production build.
6. Run letools tests, wheel-content checks, and a real local-dataset acceptance.
7. Describe the old/new pins and behavior changes in the commit message.

The CI external-integration job enforces these boundaries on every pull request.
See [Dataset Doctor](DOCTOR.md) and [Dataset Visualizer](VISUALIZER.md) for the
user-facing and runtime boundaries.

