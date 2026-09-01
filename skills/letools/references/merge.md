# Same-Version Merge

The merge engine is a specialized physical-layout path. It supports multiple
LeRobot v2.1 inputs to one v2.1 output, or multiple v3.0 inputs to one v3.0
output. It is not a generic conversion, feature-union, rename, resample,
deduplication, or missing-value-fill operation.

Preserve the user-declared source order because it defines output episode and
task remapping. Require at least two unique sources and a destination distinct
from every source.

Use plan-only mode when the user wants to inspect tuning without writing. For a
substantial accepted merge, prefer `letools merge ... --auto` and current CLI
help. Do not add overwrite or validation bypass flags without explicit scope.

Merge phases are sequential, so data and file workers are not necessarily
simultaneously active and must not be added together as a peak concurrency
claim. Report each selected worker class, result counts, wall time, throughput,
and the normal deep-validation result.
