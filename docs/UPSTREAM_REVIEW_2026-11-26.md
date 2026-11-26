# Upstream operational review, November 26, 2026

Reviewed upstream main at `9ed84f99c8f13fc921baebc59e0c696108cfab3e`.
The source package declares Strix 1.7.1. The review began on fork master
`982caf14f85c2910d4084a55c4e7fa9e8b378269`, the commit tagged
[SHVIA 1.2.58](https://github.com/samirhvbr/strix/releases/tag/shvia-v1.2.58).

## Published contributions

| Contribution | Benefit and evidence | Follow-up |
| --- | --- | --- |
| [PR #1840](https://github.com/usestrix/strix/pull/1840), [issue #1837](https://github.com/usestrix/strix/issues/1837) | Replace the viewer's absolute local-only promise with the data sent for email verification, feedback, report delivery, and usage telemetry. Synthetic request checks and 11 viewer auth tests passed. Documentation navigation and diff checks passed. | Greptile noted that telemetry occurs at viewer startup, including with `--no-open`. Commit `f37744a6` clarifies the timing. Follow [CI on that head](https://github.com/usestrix/strix/actions/runs/45815799641). The hosted docs render was outside local validation. |
| [Issue #1838](https://github.com/usestrix/strix/issues/1838) | A macOS ARM64 wheel built with `GOOS=linux` contains a Linux ELF sidecar while its filename and metadata identify macOS ARM64. The native control contains a macOS Mach-O sidecar under the same wheel tag. Both real builds succeeded. | Confirm whether to fail early on non-native Go settings or specify cross-build support. The wheel contents were inspected without installing or launching either executable. |
| [Issue #1839](https://github.com/usestrix/strix/issues/1839) | The documented `make check-all` stops on 968 Pyright errors on unchanged main using the lockfile and Python 3.12.14. Ruff and Mypy passed, as did the separately invoked Bandit and lockfile checks. The corresponding upstream lint job passed. | Confirm which commands contributors should run and how to handle the existing Pyright baseline. [CI lint job](https://github.com/usestrix/strix/actions/runs/45671540630/job/139407824208) |

## Prior contributions and CI

[#1784](https://github.com/usestrix/strix/pull/1784) and
[#1786](https://github.com/usestrix/strix/pull/1786) remain open at `7c2a6374`
and `156e2574`. No new maintainer comments or exact-head CI runs were found.
The [November 19 follow-up](UPSTREAM_CI_1784_1786_2026-11-19.md) has the
individual run results. The latest main's Python 3.12 and 3.14 jobs hit the
15-minute limit. Lint, TUI, viewer and wheel passed. [Main CI](https://github.com/usestrix/strix/actions/runs/45671540630)

#1577 is already merged. The pending fixes #1657, #1658, #1660 and #1742
remain open. [#1802](https://github.com/usestrix/strix/pull/1802) overlaps a
skills fixture covered in #1657, and
[#1816](https://github.com/usestrix/strix/pull/1816) overlaps #1658's provider
authentication correction. [#1767](https://github.com/usestrix/strix/issues/1767)
remains open. #1708 supplies timeout diagnostics.

## Inventory and routing

Of 769 existing ledger IDs, 765 were verified by ID. GitHub did not return
#1067, #1294, #1338, or #1474. Their last known observations remain available
in the ledger. The seven open-inventory pages contained 645 unique items
before publication. Three all-state pages retrieved 202 items updated since
November 7 at 09:55 UTC, using 48 hours of overlap with the last completed
inventory. This found 49 additional IDs; the four publications bring the
ledger to 822. Cataloged items await deeper review unless marked otherwise.

- #1765 closed as superseded by merged #1787. #1721 and #1772 closed after
  another dependency update path became available. The inspected public
  discussions did not give a closure reason for #840 or #1763.
- The prior build smoke and disappearing-record failures have open fixes
  in #1784 and #1786. Installer cleanup is covered by #1726, and #1271 and
  #1296 cover unmanaged executables and shell setup. The local viewer's
  non-finite output handling is already proposed in #1728.
- GitHub Discussions is disabled. The [upstream security policy page](https://github.com/usestrix/strix/security/policy)
  reports no policy file. The contributions concern documentation, build
  artifact compatibility, and contributor checks.

## Release observation and resumption

The latest upstream release remains
[v1.7.1](https://github.com/usestrix/strix/releases/tag/v1.7.1). The annotated
fork tag `shvia-v1.2.58` points to the master source observed at the start of
this review. It publishes an Apple Silicon binary and SHA256SUMS. These were
verified as release metadata. The fork's package version is 1.7.0, and
`.fork-version` declares the separately tracked 1.2.58.

Resume from the [machine-readable round record](upstream-tracking/round-2026-11-26.json)
and the retained [inventory and synchronization checkpoints](upstream-tracking/checkpoints.json).
First check the four publications, the pending pytest fixes and any moves in
known items. The code synchronization milestone remains pinned to the last
validated integration.

## Follow-up on PR #1713

Arcanist reviewed the current head `5fb5ca8bfe2ae1ba48265ccb0f53bd16312a6a73`
and recorded a clear verdict without inline findings. Its impact notes
describe possible regressions and the checks used to rule them out.
GitHub still requires human review. The linked #1711 remains open.
[Review](https://github.com/usestrix/strix/pull/1713#pullrequestreview-6080012235) ·
[Review summary](https://github.com/usestrix/strix/pull/1713#issuecomment-7202427661) ·
[Issue #1711](https://github.com/usestrix/strix/issues/1711)

The same-head CI still shows successful lint, TUI, viewer, and wheel jobs
and 15-minute timeouts for both pytest versions. Binary passed in its
separate workflow. Follow a new head, a new CI run, or human review.
[CI](https://github.com/usestrix/strix/actions/runs/42600153315) ·
[Binary](https://github.com/usestrix/strix/actions/runs/42600153217)
