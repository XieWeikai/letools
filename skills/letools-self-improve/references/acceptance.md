# Acceptance And Reporting

## Apply gates in order

1. Correctness: every required direction, deep validator, official loader,
   Arrow semantic/statistics comparison, packet-payload comparison, round trip,
   and transactional-failure check passes.
2. Measurement: the median improvement clears the protocol threshold relative
   to observed noise.
3. Resources: measured RSS, CPU, and process/thread costs satisfy the protocol
   ratios and half-node ceiling.
4. Regressions: non-target directions and the required low-resource lane remain
   within measured noise.
5. Complexity: the gain justifies the code, ownership remains local, and the
   architecture is at least as clear as before.

Do not weaken validation to accept a candidate. If one direction wins and the
other materially regresses, reject or narrow the optimization so selection is
based on an explicit source capability rather than scattered type checks.

## Accepted iteration

Write `report.md` with an explicit accepted decision and archive all inputs.
Create a commit using the exact sectioned format in `PROTOCOL.md`: Change,
Profile, Result, and Validation. Include raw medians and percentages, baseline
and candidate commits, workload counts, Slurm allocation, observed workers and
threads, sample method, peak RSS and CPU, regression lane, and correctness.

Fast-forward the accepted commit into the named acceptance branch. Run a final
status/log check and update `self-improve/SUMMARY.md` when the campaign's durable
record changed. The summary must distinguish per-iteration comparisons from a
freshly measured cumulative benchmark.

## Rejected iteration

Write `report.md` with the rejection reason and useful profile conclusion.
Archive `candidate.diff` and raw evidence before removing the candidate
worktree and branch. Verify the acceptance branch still points to its original
tip and is clean. Do not make an empty performance commit or retain a dormant
feature flag for a rejected path.

## Campaign result

Report each accepted commit and its own baseline/candidate median, each rejected
hypothesis and reason, correctness status, observed resource effects, archive
location, and remaining bottleneck. If pushing or merging was requested, verify
the remote branch and CI rather than treating local commit creation as finish.
