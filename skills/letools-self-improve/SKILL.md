---
name: letools-self-improve
description: Run LeTools performance self-improvement campaigns under the repository protocol. Use when the user explicitly asks to profile, optimize, self-improve, run one or more accepted optimization iterations, evaluate Python-to-Rust lowering, or continue an existing LeTools performance campaign. Do not use for ordinary conversion, feature implementation, or unmeasured code cleanup.
---

# LeTools Self-Improvement

Drive an evidence-based optimization loop in which correctness is a hard gate
and only measured, proportionate improvements reach the acceptance branch.

## Load the governing state

1. Resolve the repository root with Git and verify that it is LeTools.
2. Read the complete `self-improve/PROTOCOL.md` and
   `self-improve/SUMMARY.md` from that checkout before planning or editing.
3. Treat the checked-in protocol as authoritative. This skill explains how to
   operate it but does not override its workloads, resource ceiling, metrics,
   thresholds, correctness gates, archive layout, or commit-message standard.
4. Determine the named acceptance branch, current accepted tip, next unused
   iteration number, requested target, and relevant prior rejected candidates.
5. Verify that protocol artifacts are ignored except the tracked protocol and
   summary. Never commit raw datasets, profiles, scheduler logs, or iteration
   archives.

If the protocol is absent or conflicts with the requested campaign, pause for a
protocol decision rather than inventing replacement acceptance rules.

## Protect the accepted state

Require a clean acceptance branch before a campaign. Existing changes belong
to the user: do not stash, discard, reset, or overwrite them. Use a disposable
candidate branch and preferably a separate worktree for each iteration. Do not
modify the official LeRobot oracle or any frozen source dataset.

All full conversions, benchmarks, and correctness comparisons run as one-node
Slurm jobs under the protocol ceiling. Small source inspection, unit tests, and
report generation may run locally. Never overlap baseline and candidate runs
on shared storage.

## Run one candidate at a time

Read [campaign execution](references/campaign.md) and
[measurement discipline](references/measurement.md). Each candidate must:

1. begin from the current accepted tip;
2. target the largest actionable bottleneck supported by current profiles;
3. have an archived `draft.md` before implementation;
4. contain the smallest change that tests one main hypothesis;
5. pass unit and Micro checks before Medium profiling and Full acceptance;
6. compare alternating baseline/candidate runs under identical controlled
   conditions when fresh timing is required;
7. archive raw results, the candidate diff, correctness evidence, profiles, and
   a decision report before branch cleanup or acceptance.

Do not count rejected experiments toward a requested number of successful
iterations. After an accepted merge, its tip is the next baseline; never
multiply per-iteration speedups to claim an unmeasured cumulative result.

## Decide from evidence

Read [acceptance and reporting](references/acceptance.md). Apply the protocol's
correctness, measurable-improvement, resource-ratio, regression, and complexity
rules exactly. Requested Slurm resources are not measured resource use. A
profiled run is not an acceptance timing sample. A faster best sample is not a
median improvement.

Accept simplification with equal performance when the protocol permits it.
Reject marginal speedups that add disproportionate code or resource cost. Do
not attribute a bottleneck or speedup cause beyond what stage, cgroup, I/O, and
profile evidence supports.

For an accepted candidate, use the protocol's detailed performance commit body,
fast-forward it into the named acceptance branch, update tracked documentation
only where durable behavior or the campaign summary changed, and verify the
final branch. For a rejected candidate, archive its evidence before removing
the disposable branch/worktree and returning to the unchanged accepted tip.

## Finish the campaign

Continue through rejected ideas until the requested number of accepted
iterations is reached or a genuine external blocker prevents progress. For an
open-ended "optimize until exhausted" request, perform a final boundary audit
and stop only when remaining work is I/O/external-library bound, below the
measurement threshold, regressional, or unjustifiably complex. State that this
is an evidence-bounded stopping point, not proof of global optimality.

Report accepted and rejected iteration counts, commit IDs, raw baseline and
candidate medians, throughput changes, measured resource changes, correctness
results, Slurm allocations, archive paths, cumulative performance only when it
was remeasured end to end, and the next identified bottleneck.
