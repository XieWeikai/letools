# letools documentation

This directory separates user-facing operation from implementation design and
performance evidence:

- [Usage guide](USAGE.md): installation, every CLI command, Python APIs,
  validation, HDF5 mappings, custom sources, Slurm operation, and troubleshooting.
- [Installation](INSTALLATION.md): direct command setup, uv tool internals,
  editable and locked development modes, updates, removal, and Slurm visibility.
- [Architecture](ARCHITECTURE.md): module ownership, SourceProvider construction,
  two-stage CLI parsing, data flow, native acceleration, and extension points.
- [Static planner](PLANNER.md): planner scope, resource and I/O inputs,
  heuristic selection, bounded calibration, fingerprints, and cache behavior.
- [Planner benchmark](PLANNER_BENCHMARK.md): the Slurm oracle matrix and
  acceptance measurements for the current planner.
- [HDF5 source MVP](HDF5_MVP.md): implemented scope, full XVLA throughput,
  real-data correctness, official-loader evidence, and regression results.
- [HDF5 mapping presets](HDF5_PRESETS.md): interactive authoring, user/project
  storage, CLI conversion, JSON schema, and supported source representations.
- [AgileX source acceptance](AGILEX.md): raw directory contract, synchronization,
  instruction semantics, real-data correctness, throughput, and five-iteration audit.
- [Current benchmark](../BENCHMARK.md): full-dataset conversion, round-trip,
  comparison throughput, resource use, and correctness results.
- [Self-improvement protocol](../self-improve/PROTOCOL.md): the required process
  for accepting future performance changes.

Start with the usage guide when operating the tool. Read the architecture and
planner documents before changing module contracts or performance policy.
