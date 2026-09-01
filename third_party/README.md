# Third-party source policy

`external/` contains Git submodules pinned to exact upstream commits. The
gitlinks keep the letools repository small while preserving reproducible source
identity. Clone with `--recurse-submodules`, or run
`git submodule update --init --recursive` before building letools.

Do not edit files below `external/` directly. LeTools-owned adapters live under
`src/letools/`, and source transformations needed by an upstream application
live as reviewable patches under `patches/`.

`UPSTREAM.toml` records the exact source URL, commit, retrieval date, and
license for every submodule. To update a project:

1. review the new upstream commit and license;
2. fetch and checkout the reviewed commit inside its submodule;
3. stage the updated gitlink and update `UPSTREAM.toml`;
4. rebase the corresponding files under `patches/`;
5. run Python, upstream, packaging, and integration tests;
6. describe upstream behavior changes in the commit message.

The Apache-2.0 license text used by both current submodules is archived at
`licenses/Apache-2.0.txt`. Each upstream project's own README and license files
are retained when supplied by upstream.

Repository-wide integration boundaries, wheel packaging, and the acceptance
matrix are documented in `docs/THIRD_PARTY.md`. User commands and runtime
behavior are documented in `docs/DOCTOR.md` and `docs/VISUALIZER.md`.
