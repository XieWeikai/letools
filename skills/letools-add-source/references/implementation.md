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

For an external CLI source, put the frozen config and provider in the user's
package and add a `[project.entry-points."letools.source_providers"]` entry.
Install it into LeTools' environment and verify it with `letools providers list`.
For a format maintained in this repository, add the provider under
`src/letools/source_providers/`, register it in `source_providers/__init__.py`,
and export its public types. Names and aliases must not conflict. Validate
user-facing semantics before constructing the source; provider-only flags must
remain absent from unrelated source help.

Prefer explicit inputs over content guessing. A required task instruction,
calibration, or mapping belongs to this typed provider configuration.

## 4. Distributed reconstruction

Only when requested, use the generic `kind="provider"` source specification.
Set `config_type` for a dataclass configuration or implement
`config_to_dict()`/`config_from_dict()` explicitly. Override
`distributed_spec()` only for a deliberate wire-format exception. Test
serialize -> deserialize -> reopen in a context without the original preset
store or Python object, and test API-version mismatch failure.

## 5. Performance

First remove per-frame Python work, redundant copies, repeated opens, and
duplicate source scans. Then use existing native or Arrow primitives where they
fit. Add a new primitive only when profiling shows a durable bottleneck and its
contract can remain source-neutral. Do not put worker policy into a plugin or
add source type checks to backend hot loops.
