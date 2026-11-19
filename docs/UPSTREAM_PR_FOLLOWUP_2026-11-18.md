# Open upstream PR follow-up, November 18, 2026

This review covers all 31 open upstream PRs by `samirhvbr` observed at the start
of the round. The reviewed base was upstream `1152d535`. See the
[per-item results](upstream-tracking/pr-followup-2026-11-18.json).

| Initial CI status | PRs |
| --- | ---: |
| Awaiting maintainer approval | 12 |
| Approval expired before jobs ran | 3 |
| Pytest timed out | 13 |
| Inherited test failures (#1658) | 1 |
| Actionlint download failed and pytest timed out (#1660) | 1 |
| No Actions run on the reviewed head (#1708) | 1 |

[PR #1469](https://github.com/usestrix/strix/pull/1469) was updated to `c2a48b70`
with a fixture adjustment for the upstream dependency report schema. Two tests
failed before the adjustment. All five race tests passed afterward. In the
expanded suite, 212 passed and the one failure matched the existing coverage
issue [#1767](https://github.com/usestrix/strix/issues/1767). Pyright reported
the same 945 diagnostics as the base. The [individual upstream run](https://github.com/usestrix/strix/actions/runs/44288778569)
passed lint, TUI, viewer and wheel. Both Python jobs reached the 15-minute limit.

[PR #1577](https://github.com/usestrix/strix/pull/1577) merged at `fe3b30ea`.
The fork already has equivalent runner fixtures. PRs #1657, #1658 and #1660
remain open. [PR #1742](https://github.com/usestrix/strix/pull/1742) also covers
several base failures, including the missing coverage mapping in #1767.

The [combined fork diagnostic](https://github.com/samirhvbr/strix/actions/runs/44289647242)
passed all six jobs at `f5658da4`. Python 3.12 and 3.14 each reported 2,845 passed
and three expected failures. Its tree combines the 31 reviewed heads, including
the updated #1469, and the ten-line coverage mapping from #1742. The record
lists both composition conflict resolutions. The suite ran both Python jobs
without provider test exclusions. These results apply to the diagnostic tree.

The initial CI statuses, reviewed heads, newer #1469 status, source provenance,
and completed checks are recorded separately from fork adoption. The November 19
follow-up records later GitHub observations.
