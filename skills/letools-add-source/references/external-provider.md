# External Provider Workflow

Use this reference when the new source should live outside the LeTools
repository. The package entry point is the stable installation path; local
module loading is a development escape hatch.

## Package layout

```text
my-robot-letools/
|-- pyproject.toml
`-- src/my_robot_letools/provider.py
```

The package metadata advertises one object:

```toml
[project]
name = "my-robot-letools"
dependencies = ["letools>=0.1"]

[project.entry-points."letools.source_providers"]
my_robot = "my_robot_letools.provider:provider"
```

The object may be a `SourceProvider` instance, a subclass, or a zero-argument
factory. Keep imports cheap: entry-point loading happens when LeTools starts,
not only when the selected source is used.

## Provider implementation

Implement `add_arguments`, `config_from_args`, and `open`. The latter must
return a normal `DatasetSource`; it must not write output or select workers.
Prefer an immutable dataclass configuration:

```python
from dataclasses import dataclass
from pathlib import Path
from letools import DatasetSource, SourceProvider, SourceProviderContext

@dataclass(frozen=True)
class RobotConfig:
    prompt: str

class RobotProvider(SourceProvider[RobotConfig]):
    name = "my_robot"
    config_type = RobotConfig

    def add_arguments(self, parser):
        parser.add_argument("--prompt", required=True)

    def config_from_args(self, args, context):
        prompt = args.prompt.strip()
        if not prompt:
            raise ValueError("--prompt cannot be empty")
        return RobotConfig(prompt)

    def open(self, source: Path, config: RobotConfig) -> DatasetSource:
        return RobotDatasetSource(source, config.prompt)

provider = RobotProvider()
```

`config_type` enables the default JSON serialization used by distributed
manifests. For nested mapping objects, paths, enums, or binary values, override
both `config_to_dict` and `config_from_dict` and document the wire format.
Increment `api_version` when that wire format changes incompatibly.

## Install and use

```bash
uv pip install -e /work/my-robot-letools
letools providers list
letools convert /data/raw /data/lerobot-v30 \
  --source-format my_robot --prompt "fold the towel" --to v3.0 --auto
```

The selected provider's options are isolated by the two-phase CLI parser. A
provider flag is not accepted for another source format.

## Local development without packaging

Create `~/.config/letools/providers.toml`:

```toml
[providers.my_robot]
module = "my_robot_letools.provider:provider"
pythonpath = ["/work/my-robot-letools/src"]
enabled = true
```

Or load one process explicitly:

```bash
LETOOLS_PROVIDER_MODULES=my_robot_letools.provider:provider \
PYTHONPATH=/work/my-robot-letools/src letools providers list
```

The registry rejects duplicate canonical names and aliases. Disable a local
entry with `enabled = false`; remove the file or unset the environment variable
to stop loading it.

## Distributed workers

`letools dist plan` embeds the provider name, API version, absolute source root,
and normalized configuration in `SourceSpec`. Every worker environment must
install the external package (or load the same local module) before executing
`letools dist worker`. A missing provider or API mismatch fails before source
data is opened. The source path and any referenced files must be visible on all
workers through the scheduler's shared mount.

Test this surface with a manifest round trip and a clean worker environment;
do not rely on a live provider object or a coordinator-only preset store.
