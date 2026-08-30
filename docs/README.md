# letools documentation

This directory separates user-facing operation from implementation design and
performance evidence:

- [Usage guide](USAGE.md): installation, every CLI command, Python APIs,
  validation, custom sources, Slurm operation, and troubleshooting.
- [Architecture](ARCHITECTURE.md): module ownership, boundaries, data model,
  conversion pipelines, native acceleration, and extension points.
- [Static planner](PLANNER.md): planner scope, resource and I/O inputs,
  heuristic selection, bounded calibration, fingerprints, and cache behavior.
- [Planner benchmark](PLANNER_BENCHMARK.md): the Slurm oracle matrix and
  acceptance measurements for the current planner.
- [Current benchmark](../BENCHMARK.md): full-dataset conversion, round-trip,
  comparison throughput, resource use, and correctness results.
- [Self-improvement protocol](../self-improve/PROTOCOL.md): the required process
  for accepting future performance changes.

Start with the usage guide when operating the tool. Read the architecture and
planner documents before changing module contracts or performance policy.
