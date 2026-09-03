# Acceptance And Documentation

## Correctness gate

Tests must cover:

- deterministic episode discovery, contiguous indices, lengths, totals, tasks,
  features, dtypes, shapes, timestamps, generated indices, and exact statistics;
- missing paths, malformed shapes, inconsistent stream lengths, invalid
  mappings, and other source-specific failures with actionable errors;
- media frame count, order, dimensions, and representative decoded values;
- conversion of representative fixtures to v2.1 and v3.0;
- deep validation of both outputs and semantic comparison across them and the
  source, including video when present;
- provider-specific help, option isolation, immutable config, and explicit
  rejection of missing semantic inputs;
- external entry-point and local-module discovery, duplicate-name rejection,
  provenance shown by `letools providers list`, and CLI selection after a clean
  package install;
- planner identity, physical/logical profiles, locality grouping, and encoding
  classification;
- distributed specification JSON round-trip and worker reopening when that
  surface is supported.
- external distributed-provider API-version mismatch must fail before any
  source data is read.

Use official LeRobot metadata and dataset loaders in compatibility tests where
the repository already provides those fixtures or environments. Do not replace
full validation with metadata-only checks.

## Performance gate

Measure a representative source conversion in the scheduler allocation used
for performance work. Report dataset counts and bytes, CPU and memory limits,
source/destination filesystems, worker choices, median wall time, episode/frame
throughput, peak RSS, process/thread count, and validation outside any timed
region.

Run the established LeRobot-to-LeRobot benchmark before and after the source
integration with identical inputs, resource limits, cache classification, and
validation. Treat a change outside the benchmark's measured noise or repository
acceptance threshold as a regression until explained and fixed.

## Documentation gate

Before each milestone commit, review at least:

- `README.md` for the supported-source summary and one runnable example;
- `docs/USAGE.md` for all provider options, semantic requirements, and errors;
- `docs/ARCHITECTURE.md` for new modules and boundary changes;
- distributed documentation when `SourceSpec` support was added;
- installation documentation when a new dependency or native library is needed.

Document how users obtain or create mappings, instructions, calibrations, and
other semantic inputs. Include limitations and unsupported variants explicitly.
Use detailed commit bodies that state the boundary added, correctness results,
performance measurements, and documentation reviewed.
