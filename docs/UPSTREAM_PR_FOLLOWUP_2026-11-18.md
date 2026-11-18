# Open upstream PR follow-up, November 18, 2026

Status: in progress. This targeted follow-up covers all 31 open upstream PRs by
`samirhvbr`. The broad inventory and full synchronization are unchanged.
Pinned upstream main: `1152d53579d5b3af37ada8c798f197ab093b99c6`.

The initial CI audit separates pending/expired approval, pytest timeouts also
observed on main, inherited test failures and an actionlint download failure.
PR #1577 merged at `fe3b30ea`; #1657, #1658 and #1660 remain pending upstream.

PR #1469 was updated without rewriting history to `c2a48b70765b8ae4558217a2c2605251440db797`.
Its dependency race fixture now supplies the reachability verdict and explicit
synthetic call path required by current upstream. Two failures pass after adaptation.
The new upstream CI is being observed separately.

Local combined verification of 13 runtime PRs passed 425 tests and the Go race suite.
The #1469 reporting suite passed 212 tests and exposed one independent base failure
already reported in #1767 and covered by the open proposal #1742.

A [separate combined diagnostic](https://github.com/samirhvbr/strix/actions/runs/44289647242)
at `f5658da4` now tests the 31 heads on current main, with only the missing coverage
mapping from #1742. It uses no provider-test exclusion or process-isolation workaround.
Two integration conflicts were explicitly reconciled and are recorded in provenance.
Neither local result nor this combined workflow is individual upstream PR CI.

Next: observe both running workflows and complete the per-item ledger update.
Preserve imported SHAs, previous histories, code-sync and inventory cursors.
See the [progress record](upstream-tracking/pr-followup-2026-11-18.json).
