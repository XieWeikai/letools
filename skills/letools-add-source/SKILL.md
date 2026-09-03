---
name: letools-add-source
description: Add or revise a LeTools DatasetSource and SourceProvider. Use when implementing support for a new robot dataset format, exposing source-specific CLI inputs, adding portable distributed reconstruction, or reviewing a source plugin against LeTools correctness and performance contracts. Do not use for adding an output backend or a one-off dataset conversion.
---

# Add A LeTools Data Source

Implement source support at the narrowest LeTools boundary that fits the
request. Keep source-specific parsing out of the CLI, planner, conversion
coordinator, and output backends.

## Establish the source semantics first

Inspect representative files read-only and obtain an explicit answer for:

- how episodes are discovered and ordered;
- which samples form one frame and how streams align;
- FPS or timestamps and the policy for missing or duplicate samples;
- action, state, sensor, and image field meanings, shapes, and dtypes;
- task text, task changes, robot type, and feature names;
- whether media is encoded video, encoded images, or decoded arrays;
- required metadata and whether statistics exist or must be calculated.

Do not infer robot semantics from field names alone. When one container format
can carry different schemas, design an explicit versioned mapping or preset
instead of hard-coding one observed dataset. Record alignment, truncation, and
missing-data policies in tests and documentation.

## Select the integration scope

Read [architecture contracts](references/contracts.md), then choose one scope:

1. A Python API-only source implements `DatasetSource`; callers construct it
   directly. It needs no CLI provider.
2. An external CLI source packages a typed `SourceProvider` and advertises it
   through the `letools.source_providers` Python entry-point group. This is the
   preferred path for user-specific formats because no LeTools checkout changes
   are needed.
3. A built-in CLI source registers in the repository's built-in registry only
   when the format is maintained as part of LeTools itself.
4. A distributed source additionally serializes every construction input into
   `SourceSpec` and supports reconstruction on a clean worker.

Do not promise distributed support merely because local conversion works.
External providers are discovered at import time. For an un-packaged checkout,
use `~/.config/letools/providers.toml` or `LETOOLS_PROVIDER_MODULES` to load an
explicit `module:object` reference. Read [external-provider.md](references/external-provider.md)
for the package and local-module workflows.

## Implement in dependency order

Follow [implementation workflow](references/implementation.md). Build the
version-neutral source model first, then the provider, then optional distributed
reconstruction. Backend code must continue to see only `DatasetSource`.

Keep episode reads columnar and bounded. Use Arrow tables, `read_episodes()` for
real shared-resource batching, `FrameSequence.iter_batches()` for encoded image
streams, and stable locality keys for shared physical resources. Keep expensive
handles open for the coarsest safe operation rather than reopening per frame.
Declare thread/process isolation from the actual dependency constraints; do not
choose concurrency in the source.

Add concise English docstrings and comments for semantic choices, invariants,
resource lifetime, synchronization, and compatibility behavior. Avoid comments
that merely restate code.

## Prove the integration

Use [acceptance and documentation](references/acceptance.md). A source
is not complete until representative data converts to both LeRobot v2.1 and
v3.0, both outputs deep-validate, and their decoded semantics agree with the
source. Include provider isolation, invalid configuration, planner profiling,
and distributed round-trip tests for every supported surface.

Run large conversion and performance work through the repository's required
scheduler. Compare an unchanged LeRobot-to-LeRobot benchmark before and after;
source extensibility must not add format branches or a measurable regression to
the existing hot path.

Before every commit, review architecture, usage, source-format, and distributed
documentation affected by the change. Commit only one coherent milestone at a
time and include correctness checks and measured performance evidence in the
commit message.
