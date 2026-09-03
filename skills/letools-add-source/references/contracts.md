# Architecture Contracts

Read the current implementations before editing because these contracts may
evolve:

- `src/letools/plugins/base.py` defines the runtime `DatasetSource` protocol;
- `src/letools/model.py` defines `DatasetMetadata`, `Episode`, media inputs, and
  profile types;
- `src/letools/source_providers/base.py` defines the CLI factory protocol;
- `src/letools/source_providers/registry.py` owns deterministic provider lookup;
- `src/letools/distributed/types.py` defines portable source manifests;
- `src/letools/distributed/source.py` reconstructs sources on workers;
- `docs/ARCHITECTURE.md` is the user-facing boundary specification.

## DatasetSource

A source owns format parsing and exposes stable, zero-based episodes to every
consumer. It provides `root`, `metadata`, `episodes`, and `read_episode()`. The
Arrow table returned for an episode must contain exactly that episode's rows in
canonical schema and frame order. Metadata totals, features, tasks, episode
lengths, statistics, and generated index columns must agree.

Override `read_episodes()` only for useful batched access. Override
`data_profile()`, `media_input()`, and `media_profile()` when the source is not
ordinary path-based Parquet/MP4. Profiles must distinguish one episode's
logical contribution from a shared container's physical bytes and expose a
stable locality key, otherwise planning double-counts storage and groups work
poorly. Include semantic configuration in `planner_identity()` so cached plans
cannot cross incompatible mappings.

Sources read only. They never choose worker counts, target shards, destination
paths, or backend versions.

## SourceProvider

A provider is a frontend factory, not a reader. `add_arguments()` owns only its
source-specific flags. `config_from_args()` validates and normalizes them into
an immutable typed configuration. `open()` constructs the source. It must not
traverse episodes, profile the dataset, invoke conversion, or write output.

The CLI parses source selection first and then exposes only the chosen
provider's flags. Preserve that isolation rather than adding global optional
arguments and central source-format conditionals.

## Distributed source specification

A distributed source specification is JSON, scheduler-neutral, and sufficient
to reopen the same semantics without local process state. Embed resolved
mappings and normalized options when a preset store or user configuration is
not shared. Built-in formats retain their named kinds; external providers use
`kind="provider"` with a canonical `provider` name and `provider_api_version`.
Never pickle live source objects or depend on login-node-only paths.

Set `config_type` to a frozen dataclass to obtain the default JSON-safe
`config_to_dict()` and `config_from_dict()` behavior. Override both methods for
nested mappings or other values that need normalization. The default
`distributed_spec()` stores the absolute root, provider name, API version, and
options. `open_source_spec()` validates that API version and delegates source
construction to the registered provider. Workers must install the same package
or load the same local module.

The registry discovers installed packages through the
`letools.source_providers` entry-point group. It can additionally load explicit
`module:object` references from `~/.config/letools/providers.toml` or
`LETOOLS_PROVIDER_MODULES`. Duplicate names and aliases are errors, and
`letools providers list` exposes provenance for auditing.
