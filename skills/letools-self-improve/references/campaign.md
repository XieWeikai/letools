# Campaign Execution

## Establish scope

Extract the requested target precisely: component, direction, source format,
resource lane, acceptance branch, and number of accepted iterations. A request
for five effective optimizations means five accepted commits; rejected trials
remain useful evidence but do not reduce the target.

Inspect recent commits, `self-improve/SUMMARY.md`, existing ignored iterations,
stage telemetry, and profiles before proposing another direction. Continue the
next unused numeric iteration rather than reusing an archived directory.

## Baseline

Record the exact accepted commit, lockfile/native versions, dataset manifest,
source and destination filesystems, node class, CPU affinity, memory limit,
worker settings, and cache classification. Refresh the baseline when any of
these changed or when the protocol requires a fresh paired comparison.

Use unique destinations. Keep generation, dependency setup, validation, and
profiling outside the timed conversion. External CLI wall time is the end-to-end
authority when internal result timing excludes stages.

## Draft

Before code changes, archive a draft containing:

- profile evidence and the dominant stage or resource bottleneck;
- one falsifiable hypothesis;
- the smallest proposed implementation and affected ownership boundary;
- expected effect on throughput, CPU, memory, threads, and I/O;
- correctness risks, affected directions/workloads, and rollback plan;
- commands and acceptance samples to run.

Avoid bundles of unrelated tuning knobs. A failed hypothesis should produce a
clear conclusion that informs the next draft.

## Candidate isolation

Create `opt/NNNN-short-name` at the accepted tip in a disposable worktree when
practical. Never build a new candidate on a rejected diff. Keep user work out
of candidate cleanup. Record the candidate commit or diff hash in all jobs.

Run the cheapest falsification first: unit/Micro correctness, then a Medium
profile. Advance to Full bidirectional and paired timing only when evidence
still supports acceptance. One high-resource improvement also needs the
protocol's established low-resource regression lane.

## Archive before decision

Write raw machine-readable samples without rounding, correctness output,
profiles, candidate diff, and `report.md` into the iteration directory. The
report names the hypothesis, result, noise, decision, resource ratios,
complexity tradeoff, and next evidence. Confirm the archive exists before
deleting a rejected branch or worktree.
