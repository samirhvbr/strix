# Upstream operational reliability review, November 19, 2026

Reviewed upstream main at `8db295a5b3564be2b430bb3a9afdb4db7850fa24`.
The affected build and viewer files also match upstream tag v1.7.1. The
[November 18 CI follow-up](UPSTREAM_PR_FOLLOWUP_2026-11-18.md) supplies the prior
owned-PR checks. The fork started this round at `0f69075f`, with source version
1.2.56 and published release `shvia-v1.2.55` at `02d6a1a7`.

## Published changes

| Contribution | Defect and benefit | Validation |
| --- | --- | --- |
| [PR #1784](https://github.com/usestrix/strix/pull/1784) | The local build produced an archive and exited zero after its executable failed the startup smoke test. The wrapper now validates the binary before packaging and returns the original error. It references [issue #1533](https://github.com/usestrix/strix/issues/1533). The wider [build proposal #1532](https://github.com/usestrix/strix/pull/1532) retains the faulty branch at its reviewed head. | Two regression cases failed on base. Six build and installer tests passed after the change. The Windows ZIP path used stubs. Ruff, Bash syntax, Mypy, Bandit and lockfile checks passed. |
| [PR #1786](https://github.com/usestrix/strix/pull/1786), [issue #1785](https://github.com/usestrix/strix/issues/1785) | A local run removed between two filesystem lookups could interrupt the viewer history or default-run selection. Both paths now reuse one metadata read per record. | Both deterministic removal cases failed on base. The viewer and PDF suites passed with 68 tests after adding the before-stat removal cases requested in review. Ruff, focused Mypy, Bandit and diff checks passed. Tests used temporary local records. |

The Pyright comparison retained the 953 source diagnostics present on current
main for both proposals. The full Python suite on upstream main reached its
15-minute limit on both versions in [run 44436137677](https://github.com/usestrix/strix/actions/runs/44436137677).
Both published proposals have separate upstream Actions runs. The
[round record](upstream-tracking/round-2026-11-19.json) records their observed
status and source SHAs. Fork adoption is pending.

## Inventory and prior items

The metadata refresh retrieved 720 of 723 known IDs. GitHub still omitted
#1067, #1294 and #1338. Their last known observations are retained. The
paginated open inventory initially contained 610 distinct entries. The
all-state update query covered 110 entries since November 10 at 00:00 UTC,
overlapping the previous cursor. Discovery added 43 items, plus the two PRs
and one issue published this round. These catalog observations are separate
from the selected code and discussion reviews. Preserve the previous broad
review checkpoint for the remaining deeper review.

- [#1621](https://github.com/usestrix/strix/pull/1621) advanced to `9462580b`
  after the diagnostic. The test-only change removes stored workspace paths to
  verify recomputation. Its newer upstream Python runs timed out. The combined
  November 18 result predates that commit.
- [#1577](https://github.com/usestrix/strix/pull/1577) merged at `fe3b30ea`.
  The fork already had equivalent fixtures. #1657, #1658, #1660 and #1708
  remain open. [#1771](https://github.com/usestrix/strix/pull/1771) overlaps
  one of the three fixture calls in #1657.
- [#1742](https://github.com/usestrix/strix/pull/1742) proposes fixes for
  several base failures. The coverage-only selection in the November 18 test
  was diagnostic. Current main still reproduces the [#1767](https://github.com/usestrix/strix/issues/1767)
  coverage failure with 20 other coverage tests passing.
- [#1117](https://github.com/usestrix/strix/pull/1117) still awaits qualified
  review. [#1506](https://github.com/usestrix/strix/pull/1506), #1044 and #946
  already cover atomic executive report writes or cleanup.
- The author closed #1680, #1678, #1676, #1674, #1671, #1670, #1691 and #1689
  for inactivity. This records their closure without changing their previous
  fork decisions. The observed discussion supplied no reason for the closure
  of #1358. Its fork provenance remains in the ledger.
- #1668 and #1664 were previously closed as duplicates of #1657 and #1654
  respectively. No additional proposal was opened for their existing scope.

The GitHub Discussions feature is disabled. The upstream security policy
page reports no SECURITY.md. This review and the publications cover local
operational reliability.

For the next round, recheck Actions and feedback for #1784 and #1786, then
resume the known-item recheck and inventory overlap from
[checkpoints.json](upstream-tracking/checkpoints.json). The retained
synchronization SHA refers to integrated fork code. The upstream SHA above
identifies the code examined in this round.
