# Conversion And Source Providers

Run `letools convert --help` and the provider-specific help for the installed
version before constructing a command.

## LeRobot sources

LeTools auto-detects LeRobot v2.1 and v3.0 sources. Select the opposite target
version explicitly. Unless the user's consumer requires v2.1, prefer v3.0 for a
new output and state that assumption.

For substantial conversion, prefer:

```bash
letools convert SOURCE DESTINATION --to VERSION --auto
```

Use `letools plan` when the user requests a read-only plan or wants to inspect
chosen resources before execution. Data and video target-size settings affect
v3.0 layout only; do not present them as v2.1 tuning controls.

## External source providers

List discovered providers before constructing a command:

```bash
letools providers list
```

User-owned providers are installed as packages advertising the
`letools.source_providers` entry-point group. Private development modules can
be loaded through `~/.config/letools/providers.toml` or
`LETOOLS_PROVIDER_MODULES`. Select the provider explicitly with
`--source-format NAME`; then read its provider-specific help because only that
provider's options are registered. For package layout, configuration
serialization, and distributed-worker requirements, read
`skills/letools-add-source/references/external-provider.md`.

## Mapped HDF5

HDF5 containers have no universal robot-data meaning. Require a reviewed preset
that maps episode discovery, timestamps, actions, observations, cameras, tasks,
and metadata. List and inspect existing presets before selecting one. Use the
interactive preset creator only in a TTY; in non-interactive work, require an
explicit preset path or name.

Never infer a field mapping from similar names alone. Treat creation of a new
preset as a semantic decision that needs user input or a documented source
schema. After accepting a new preset, convert a representative sample to both
supported target versions and deep-validate it before a full run.

## AgileX recordings

Select the AgileX source format explicitly and require a non-empty instruction
or task description. Do not derive that instruction from a directory name. The
provider applies the declared instruction to each episode; confirm that this is
the intended dataset semantics.

## Completion

Keep built-in validation enabled. Parse the final JSON result for counts,
planner choices, timings, and validation status. For high-assurance conversion,
run deep validation and semantic comparison against an available oracle or
round trip, including video packet payloads when media preservation is part of
the requirement.
