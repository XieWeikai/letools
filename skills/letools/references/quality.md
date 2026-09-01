# Quality, Doctor, And Visualization

## Dataset validation and comparison

Use `letools validate DATASET` for structural checks and `--deep` for a complete
read of data and media references. Deep validation is the final acceptance gate
for a newly converted or merged dataset when its cost is proportionate.

Use `letools compare EXPECTED ACTUAL` for semantic equivalence and include
`--videos` when encoded media must match. Distinguish decoded visual similarity
from packet-payload preservation in the report; they answer different
questions.

## LeRobot Doctor

Top-level `letools doctor` diagnoses the LeTools runtime. The `doctor` command
group exposes dataset checks, fixes, trims, scores, gates, and merge-readiness
through the pinned upstream integration. Inspect the exact subcommand help.
Use dry-run behavior before a mutating Doctor operation and require explicit
authorization for repair or trim. Sampled Doctor checks do not replace LeTools
deep validation.

## Visualizer

Run setup before the first visualizer use, then serve a selected dataset. Bind
to loopback by default. Public binding, browser opening, and uploading are
separate external effects and require an appropriate request and environment.
For a remote node, return the server address and an SSH forwarding command.
Keep the service process alive until the user is finished or explicitly asks
for a one-shot check.
