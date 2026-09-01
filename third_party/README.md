# Third-party source policy

`external/` contains immutable snapshots of upstream projects that letools
integrates at runtime. The snapshots make builds reproducible and keep the web
application available without downloading source code during normal use.

Do not edit files below `external/` directly. LeTools-owned adapters live under
`src/letools/`, and source transformations needed by an upstream application
live as reviewable patches under `patches/`.

`UPSTREAM.toml` records the exact source URL, commit, retrieval date, and
license for every snapshot. To update a project:

1. review the new upstream commit and license;
2. replace its complete directory while excluding `.git` and build outputs;
3. update `UPSTREAM.toml`;
4. rebase the corresponding files under `patches/`;
5. run Python, upstream, packaging, and integration tests;
6. describe upstream behavior changes in the commit message.

The Apache-2.0 license text used by both current snapshots is archived at
`licenses/Apache-2.0.txt`. Each upstream project's own README and license files
are retained when supplied by upstream.

