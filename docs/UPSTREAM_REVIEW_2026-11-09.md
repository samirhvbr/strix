# Upstream review, 2026-11-09

Reviewed current `usestrix/strix:main` at `215482eb2204cd30ac6b87520e3f5c6511e2e2e8`. Its single change since the November 7 review removes references to absent agent-browser skill files. The [previous report](UPSTREAM_REVIEW_2026-11-07.md), [item ledger](upstream-tracking/items.json) and [current machine-readable round](upstream-tracking/round-2026-11-09.json) keep observations, fork imports and validation separate.

Read the upstream contributor rules and bug template. GitHub Discussions remain disabled. The repository provides a private vulnerability-reporting route without a `SECURITY.md`. [Contribution rules](https://github.com/usestrix/strix/blob/215482eb2204cd30ac6b87520e3f5c6511e2e2e8/CONTRIBUTING.md) · [Security page](https://github.com/usestrix/strix/security/policy)

## Changes since November 7

- Rechecked all 663 known records individually, including closed and merged items. Twelve changed in state, head or update time. Before publication the paginated inventory found 352 open PRs and 225 open issues. The all-state update query returned 61 items with the previous 48-hour overlap. Identification and triage coverage are recorded separately from reproduction and code review.
- The authors closed #1664, #1665, #1668, #1669 and #1672 as duplicates of #1654, #1658, #1657, #1660 and #1635, respectively. #1692 moved a viewer regression to #1664 before it too closed. #1693 and #1694 closed as duplicates of #1613 and #1615. The source decisions remain with the original open proposals. #1666 closed at the author's request. [#1664](https://github.com/usestrix/strix/pull/1664) · [#1692](https://github.com/usestrix/strix/pull/1692) · [#1666](https://github.com/usestrix/strix/pull/1666)
- The prior #1684/#1685/#1686/#1688 patches are open with unchanged heads. All eight of their Python 3.12/3.14 pytest jobs finished cancelled. On #1688, the 3.12 log ends after a 74 percent progress line and contains no active-test traceback. Current main shows the same timeout symptom. This evidence leaves the remote cause unresolved. [#1688 CI](https://github.com/usestrix/strix/actions/runs/42373722946) · [Main CI](https://github.com/usestrix/strix/actions/runs/42431780800)
- #1579 updated its Claude Code schema description in `ba18d48c` to avoid requesting protected reasoning for the operator narration field. This is an author-reported live provider result, and the PR still has merge conflicts. The fork did not import it. [#1579 author follow-up](https://github.com/usestrix/strix/pull/1579#issuecomment-6728231368)

## Published contributions

| Issue and PR | Operational benefit | Reviewed source and validation |
| --- | --- | --- |
| [#1705](https://github.com/usestrix/strix/issues/1705), [#1708](https://github.com/usestrix/strix/pull/1708) | Print Python thread stacks before a hung CI pytest job is cancelled. | `040614cf7abca58ddaa493430d97f39806900642`. Exact command dumped a stack at 120 seconds and the synthetic 121-second test then passed. actionlint 1.7.12 and zizmor 1.22.0 passed. |
| [#1707](https://github.com/usestrix/strix/issues/1707), [#1710](https://github.com/usestrix/strix/pull/1710) | Reject NaN and Infinity before source preparation, uploads or scan requests. | `151eacbb3569aa908ce51dc2f63c6b9af9808d0b`. Fifteen baseline failures and six finite controls. All 21 regressions and 369 Cloud/local-source tests pass with real Requests preparation and synthetic send. |
| [#1706](https://github.com/usestrix/strix/issues/1706), [#1712](https://github.com/usestrix/strix/pull/1712) | Restore transcripts in local directories whose names contain URI-significant characters. | `0964106b66d43225d754ab439f72f5c010f9c4b3`. Ten failing baseline path/history cases and four passing controls. All 78 focused tests passed, covering viewer, TUI, committed WAL data, relative paths and Windows drive/UNC spelling on the SQLite parser. |
| [#1711](https://github.com/usestrix/strix/issues/1711), [#1713](https://github.com/usestrix/strix/pull/1713) | Keep the TUI responsive when a command write and backend collection replay are backpressured. | `5fb5ca8bfe2ae1ba48265ccb0f53bd16312a6a73`. Two focused regressions fail and one passes on main; all three pass after. The real macOS Unix socket and Python backend reproduce one failure and one passing control on main, then both pass. Go race suite passes with 1.27.3. Go 1.24.0 tests, vet and build pass. |

The interactive start screen can send `setup.start` with only the first of two entered URLs. The Go regression drove the real model and IPC client with the first acknowledgement withheld. The second distinct addition returned `ErrCommandPending`, and the start command was still transmitted. The proposal in #1101 replaces this sequence with one start payload. Maintainers can select that broader contract or an interim fix that waits for and preserves the full target set. The repeated CLI `-t` route bypasses the interactive sequence by code inspection. [Issue #1709](https://github.com/usestrix/strix/issues/1709) · [Related PR #1101](https://github.com/usestrix/strix/pull/1101)

## Reproduced or reviewed without a competing patch

- A controlled cancellation between deletion and replacement left a real temporary SQLite session empty in both image-budget and full-history rewrites on upstream main. An independent reader saw the empty state. The transaction from draft #1311 preserved both original rows throughout and after a simulated insertion failure, with exactly the same synthetic inputs. The fork already carries source `dbd078b3` as `013e9712`, and the same probe passed on the exact 1.2.49 release source. The draft's full diff has drift in `execution.py`; only its session module was applied on main for the focused probe. [#1311](https://github.com/usestrix/strix/pull/1311)
- A corrupt UTF-8 cloud authentication record prevents local logout and other commands from reaching their recovery path. #1680 is already extending tolerant reads across state/auth surfaces and its current diff omits this reader. Reassess after that PR's scope is settled. [#1680](https://github.com/usestrix/strix/pull/1680)
- The readiness report #1704 describes a possible gap after guest login. #1053 and the fork #47 fix cover the earlier guest-login retry deadline. A current container reproduction establishing the post-login cause is still pending. [#1704](https://github.com/usestrix/strix/issues/1704) · [Fork #47](https://github.com/samirhvbr/strix/pull/47)

## Validation

The combined Python diagnostic tree at `4c6e8ba9f328d067f72764b056fa9cf089b3488b` includes the November 7 diagnostic prerequisites, the current main skill update and #1708/#1710/#1712. With frozen dependencies and Python 3.12.14 on macOS, `pytest -q -o faulthandler_timeout=75` finished with **2,592 passed and 3 xfailed**. Ruff, Mypy, Bandit and the lock check passed. `make check-all` stops at the 941 pre-existing Pyright diagnostics. The normalized comparison shows no additions or removals.

The Go TUI fix was verified separately at its published head with the real local Unix socket and unchanged Python backend, the full Go race suite, and tests, vet and build with Go 1.24.0. The SQLite reader's Windows path spellings were exercised through the SQLite parser on macOS. Live scans, managed Cloud and native Windows transport were outside validation.

At the recorded remote check, pytest was running on #1710, #1712 and #1713. #1708 had no reported Actions run or checks for its head. The current main CI and the previous four PRs timed out before a useful traceback. The cause of the remote stall remains pending.

## CI follow-up

Rechecked the four published heads on November 9. Greptile gave each 5/5 without actionable findings. GitHub reported no formal reviews or inline threads, and all four linked issues remained open without comments.

| PR and head | Remote result |
| --- | --- |
| [#1708](https://github.com/usestrix/strix/pull/1708) at `040614cf` | Greptile and supply-chain passed. The Actions API returned zero runs for the head. |
| [#1710](https://github.com/usestrix/strix/pull/1710) at `151eacbb` | Six checks passed. Both pytest jobs reached the 15-minute limit. After accounting for 21 additional passing cases, the printed results match main's prefix. [Run](https://github.com/usestrix/strix/actions/runs/42599090477) |
| [#1712](https://github.com/usestrix/strix/pull/1712) at `0964106b` | Seven checks passed. Both pytest jobs reached the 15-minute limit. The 1,872 published result markers match main across both versions. [Run](https://github.com/usestrix/strix/actions/runs/42599523253) |
| [#1713](https://github.com/usestrix/strix/pull/1713) at `5fb5ca8b` | Seven checks passed, including the Go race suite. Both pytest jobs reached the 15-minute limit. Their 20 failure markers and final 74 percent line match main. [Run](https://github.com/usestrix/strix/actions/runs/42600153315) |

The isolated Ubuntu run at `92abd3b3a905d0e64d6f6c9fdaac919d234c436d` changed only the workflow on current main `215482eb`. The 120-second dump shows the cost callback entering LiteLLM's GitHub Copilot device authentication during provider inference. The five-minute bound interrupted the diagnostic with exit 124 after 1,908 passes, 20 failures and 3 expected failures. The 20 named failures are covered by the existing budget fixtures #1657, runner fixtures #1577 and price catalog test #1660. The upstream logs lack their own stack traces. [Linux stack](https://github.com/samirhvbr/strix/actions/runs/42636022292/job/129224277424) · [Main run](https://github.com/usestrix/strix/actions/runs/42431780800)

The existing #1658 patch supplies the provider during cost estimation. The exact patch reapplied cleanly to current main, and 386 focused cost and provider tests passed with 3 expected failures on macOS. The full upstream matrix remains pending. The four contribution heads are unchanged. The #1708 description now links the Linux evidence. The [follow-up record](upstream-tracking/ci-1708-2026-11-09.json) pins the check URLs, review evidence and next resume conditions. [Existing fix #1658](https://github.com/usestrix/strix/pull/1658)

## Base fixes for CI

Current upstream main is `215482eb2204cd30ac6b87520e3f5c6511e2e2e8`. The four PRs are open and their heads are outside its ancestry. All apply cleanly in a three-way merge and together in the diagnostic branch. Each has a Greptile 5/5 result and no formal human review or inline thread. GitHub reports `REVIEW_REQUIRED` and `BLOCKED`.

| PR and head | Remaining gap in main | Recorded upstream check |
| --- | --- | --- |
| [#1658](https://github.com/usestrix/strix/pull/1658) at `d48174eb` | Two cost estimators still infer a provider and can wait for Copilot device authentication. | Pytest 3.12 completes with 20 failures from the other three gaps below. [CI](https://github.com/usestrix/strix/actions/runs/42055312290) |
| [#1657](https://github.com/usestrix/strix/pull/1657) at `072a5178` | Three child-runner calls still omit `skills`, causing seven budget-test failures. | Historical pytest jobs cancelled. [CI](https://github.com/usestrix/strix/actions/runs/42054380267) |
| [#1577](https://github.com/usestrix/strix/pull/1577) at `d21e58bc` | One of five telemetry fixtures is already covered by #1592. Four remain, causing twelve failures. | Historical pytest jobs cancelled. [CI](https://github.com/usestrix/strix/actions/runs/39988365999) |
| [#1660](https://github.com/usestrix/strix/pull/1660) at `23271d8a` | One resolver test still depends on the live Grok catalog. | Historical pytest jobs cancelled. The actionlint installer failed to extract its download before linting started. Current actionlint passes locally. [CI](https://github.com/usestrix/strix/actions/runs/42057090513/job/127348326523) |

The fork diagnostic at `29a6a8cb4ea4949d14ce7eac1cc7f5a1ebf49d55` tested current main plus the four original patches. Both Ubuntu jobs completed with **2,524 passed and 3 xfailed**. Python 3.12 took 286.25 seconds and 3.14 took 273.39 seconds. The 71 focused tests and local actionlint also passed. This verifies the combined fixes against the current base. The individual upstream checks retain their earlier results. [Linux matrix](https://github.com/samirhvbr/strix/actions/runs/42641120618)

The current account has read access to upstream and write access to the fork. The upstream maintainers own integration. After main carries the fixes, validate #1708, #1710, #1712 and #1713 with new executions against the updated base. GitHub's rerun operation retains the old SHA and ref. The previous results remain in the [CI follow-up](#ci-follow-up), and the [base-fix record](upstream-tracking/ci-base-fixes-2026-11-09.json) pins this review and its exact resume condition. [GitHub rerun behavior](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/re-run-workflows-and-jobs)

Maintainer review was requested in the four PR conversations with the combined Linux result. At verification, all four PRs were open and main was still `215482eb`. The post-integration CI recheck remains pending. [#1658](https://github.com/usestrix/strix/pull/1658#issuecomment-6743639428) · [#1657](https://github.com/usestrix/strix/pull/1657#issuecomment-6743639971) · [#1577](https://github.com/usestrix/strix/pull/1577#issuecomment-6743640575) · [#1660](https://github.com/usestrix/strix/pull/1660#issuecomment-6743641174)

## Fork release and continuation

Fork `master` and the peeled annotated tag `shvia-v1.2.49` resolve to `a9fd40e5383b2c255d6372233eda5652fbea8d6e`. Its `.fork-version` is 1.2.49 and the published release has two binaries and `SHA256SUMS`. The latest upstream release is 1.7.0. The fork delivers sandbox image 1.3.1 through #53; current upstream source defaults to 1.4.0. The release check covered metadata and ancestry. Installed binaries and asset digests were outside this round. [Fork release](https://github.com/samirhvbr/strix/releases/tag/shvia-v1.2.49) · [Upstream release](https://github.com/usestrix/strix/releases/tag/v1.7.0)

Fork #55 delivered 1.2.49 to `master`. Fork #54 merged at `b5cdcd6b` into `feat/upstream-contributions-20261105`, and its review code and tracking remain on that branch. The Python 3.12–3.14 pytest checks passed on their recorded heads. Fork #56 is open for 1.2.50. The effective integrated upstream SHA stays `6b7ae2ebe4d47037a927aade23265ff4e4f27e84`. The present branch updates tracking; the new source patches are on independent upstream PRs. [Fork #54](https://github.com/samirhvbr/strix/pull/54) · [Fork #55](https://github.com/samirhvbr/strix/pull/55) · [Fork #56](https://github.com/samirhvbr/strix/pull/56)

Resume by rechecking all IDs and querying updated items since `2026-11-07T09:55:23.656519+00:00`. Inspect #1708's first runner stack when available, compare the final #1101 behavior with the two-target reproducer, and use the exact new PR heads to assess fork imports.
