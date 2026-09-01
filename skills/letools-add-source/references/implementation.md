# Implementation Workflow

## 1. Inventory and fixture

Inspect several representative episodes, including short, long, missing-field,
and multi-task cases. Create the smallest deterministic fixture that preserves
the format's real hierarchy, dtypes, timestamps, and media encoding. Keep
copyrighted or private production data out of Git.

Write down unresolved semantic choices before coding. If the source schema is
configurable, define and version a serializable mapping model with strict
validation.

## 2. Source plugin

Add `src/letools/plugins/<format>.py` and export the public types from
`plugins/__init__.py` and the package API when appropriate.

During initialization, discover episodes in deterministic order, validate
schema consistency, calculate metadata, and build immutable episode descriptors.
Do not retain full payloads in memory. In `read_episode()`, read arrays in bulk,
normalize them into Arrow columns, and verify row counts before returning.

Represent existing encoded clips as `VideoSlice`. Represent encoded per-frame
images with a `FrameSequence` whose batch iterator keeps container handles open
and returns buffer-protocol values without redundant whole-payload copies.
Specify accurate physical/logical media profiles and worker isolation.

## 3. Frontend provider

For built-in CLI support, add a frozen config dataclass and provider under
`src/letools/source_providers/`. Register and export it in
`source_providers/__init__.py`. Names and aliases must not conflict. Validate
user-facing semantics before constructing the source; provider-only flags must
remain absent from unrelated source help.

Prefer explicit inputs over content guessing. A required task instruction,
calibration, or mapping belongs to this typed provider configuration.

## 4. Distributed reconstruction

Only when requested, extend `SourceKind`, `SourceSpec.from_dict()`, and
`open_source_spec()` together. Implement the provider's `distributed_spec()`
with absolute shared paths and complete JSON-safe configuration. Test
serialize -> deserialize -> reopen in a context without the original preset
store or Python object.

## 5. Performance

First remove per-frame Python work, redundant copies, repeated opens, and
duplicate source scans. Then use existing native or Arrow primitives where they
fit. Add a new primitive only when profiling shows a durable bottleneck and its
contract can remain source-neutral. Do not put worker policy into a plugin or
add source type checks to backend hot loops.
