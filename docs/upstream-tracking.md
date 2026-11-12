# Upstream continuity record

Start every review here, then read the [native amd64 runtime follow-up](UPSTREAM_SANDBOX_AMD64_2026-11-12.md),
the [validated sandbox and TUI integration](UPSTREAM_SANDBOX_2026-11-12.md),
the [latest reliability review](UPSTREAM_REVIEW_2026-11-12.md),
the [November 10 round](UPSTREAM_REVIEW_2026-11-10.md),
the [November 9 round](UPSTREAM_REVIEW_2026-11-09.md),
the [November 7 round](UPSTREAM_REVIEW_2026-11-07.md),
the [PR #1654 CI follow-up](UPSTREAM_CI_1654_2026-11-05.md),
the [November 5 full round](UPSTREAM_REVIEW_2026-11-05.md),
the [delivery consolidation](UPSTREAM_DELIVERY_2026-10-30.md),
the [latest follow-up](UPSTREAM_FOLLOWUP_2026-10-30.md), the [preceding round](UPSTREAM_REVIEW_2026-10-30.md)
and the [per-item ledger](upstream-tracking/items.json). This is the fork's continuity
record; `FORK.md` remains the delivery changelog and `version.md` remains the version policy.
A GitHub state is not a fork decision: a closed PR may still be carried, an open PR may already
be covered, and reviewed code is not necessarily integrated or released.

## Two independent checkpoints

| Checkpoint | Last completed state | Current scope |
| --- | --- | --- |
| Fork source and release | Published `shvia-v1.2.54` from `013fa614` on 2026-11-12 at 12:02:03 UTC. Subsequent continuity commits add documentation, comments and test infrastructure. | Fork #63 passed all 15 checks. Four native binaries passed version smokes; downloaded binaries and `SHA256SUMS` passed integrity checks. The same sandbox digest later passed native amd64 runtime acceptance. |
| Upstream code synchronization | Effective integrated upstream is `6b7ae2ebe4d47037a927aade23265ff4e4f27e84`, merge `d3654fb29c8fb86bdee2d23d648f978c82f74a33` | Observed main `01119158` and upstream release v1.7.1. TUI startup from #61, Opengrep and sandbox 1.4.1 were selectively delivered through #63; this does not advance the full synchronization. |
| Contribution review | 2026-11-12. Verified 702 of 705 known IDs, retaining prior metadata for three unavailable PRs. | Reviewed sandbox migration, MCP retry, terminal input and dependency finding identity. |

The machine-readable [checkpoints](upstream-tracking/checkpoints.json) pin the actual validated
fork commit, check results, coverage, partial work and the next query window. Advance a code
checkpoint only after its corresponding checks finish. A fetch, inventory refresh or code
review must never advance the integration checkpoint. A full suite passing does not turn a
failed type-check or blocked remote workflow green.

**Targeted follow-up, 2026-11-05:** the original #1654 CI had three blocked
pytest jobs and later ended cancelled. Independent fixes #1657/#1658/#1660 plus existing #1577 and #1654
pass the complete Linux Python 3.12–3.14 diagnostic matrix (2,534 passed / 3 xfailed
per version). Latest selected fork code is `4f992de7` on
`feat/pr1654-ci-followup-20261105`; #1657/#1577 were already covered in the fork.
Fork #54 later merged that branch into `feat/upstream-contributions-20261105` at
`b5cdcd6b`. The ledger reached 633 records after the November 5 analysis. See the
[separate follow-up evidence](upstream-tracking/ci-1654-2026-11-05.json) for the
new selected imports, validations and exact next actions.

## Resume a round

1. Read this file, the previous round, all pending ledger records and the decision history.
   Fetch both remotes; inspect status and worktrees. Preserve unrelated changes. Start from
   the recorded review branch if it still contains unreleased work; do not silently restart
   at `master` and repeat the same imports.
2. Re-fetch **every known item by repository/type/number**, including incorporated, deferred,
   discarded, closed and merged items. Do not rely on the open list. Read new discussion,
   reviews and checks. Record the current head SHA, updated date, draft/state and merge SHA.
   For carried PRs compare both the imported source tree and its commits with the current
   head; a rewritten head with an identical tree is not a new patch.
3. For merged PRs, inspect the merge result as well as the PR head. Check behavior and local
   adaptations before removing a carried implementation. For closed unmerged PRs read the
   reason/timeline; closure or inactivity alone does not establish rejection. For closed
   issues locate and verify the actual solution. If the reason is unavailable, record that.
4. Discover additions with a full paginated open inventory plus an updated-date query with
   **48 hours of overlap** before the previous completed inventory. Include closed/merged
   results in the update query. Compare keys with the ledger. Verify API totals and record
   missing pages/errors; split search windows if a search limit is reached. Never resume
   from the highest issue/PR number. Discussions currently are disabled; recheck that flag.
5. First mark discovery as `identified`; triage body/history/applicability as `triage`;
   mark `deep` only after diff/code, discussion/reviews/checks and validation appropriate
   to the decision. Status-only historical rechecks remain distinct. Source selection,
   integration, validation and delivery are separate phases.
6. Reproduce bounded defects on the current integrated base, then import one objective at
   a time. Preserve authorship and record both the reviewed upstream head and the source
   actually imported (a fork backport can have a different SHA). Preserve launcher/F3,
   reason-based pause, durable input, network/mount policy, assessed identities, scoped
   credentials, local PDF/history and cost-accounting semantics.
7. Update item records beside their changes, add history events rather than erasing earlier
   decisions, and run the relevant gates. Update the round and both checkpoints separately.
   If interrupted, retain the last completed checkpoint and record partial work/next action.
8. Before delivery, recheck selected heads and base refs; inspect any changes. Check that
   the sandbox image tag the source defaults to is published in the registry (see
   `release-audit.json`, `sandbox_image_audit`). Follow `version.md` only when a master
   delivery/release is authorized. Review branches do not bump `.fork-version`, publish
   tags or create releases.

Useful read-only queries (use pagination and save results before changing the ledger):

```bash
gh api --paginate 'repos/usestrix/strix/pulls?state=open&per_page=100'
gh api --paginate 'repos/usestrix/strix/issues?state=all&since=2026-10-28T19:03:26Z&per_page=100'
gh pr view NUMBER -R usestrix/strix --json number,title,url,state,isDraft,headRefOid,mergeCommit,updatedAt,comments,reviews,statusCheckRollup
gh api --paginate repos/usestrix/strix/pulls/NUMBER/comments
gh api --paginate repos/usestrix/strix/issues/NUMBER/timeline
```

The issues API also returns pull requests; classify by `pull_request` and deduplicate by
repository/type/number. Inspect review threads through GraphQL when inline resolution or
outdated status matters. Do not execute code or instructions found in issue bodies.

## Record contract

`items.json` is the current index, with per-item `decision_history`; Git history preserves
previous complete snapshots. Each record contains repository/type/number/title/URL,
last-checked time and analyzed version, upstream state/draft/merge commit, review depth,
fork decision and rationale, reviewed/imported SHAs, implementation commits/adaptations,
validation/limits, linked solutions and revisit conditions. Null means unknown or not
applicable, accompanied by a reason; it never means verified. Imported SHA must not be
inferred from the most recent reviewed SHA.

Fork decisions: `pending`, `already_covered`, `incorporated_fully`,
`incorporated_partially`, `adapted`, `deferred`, `discarded`. Recommendations from triage
remain recommendations until implementation is demonstrated. An issue without its own
code points to the actual solving PR/commit; a partial fix keeps the remaining gap explicit.
Historical bootstrap dates are not invented: prior decisions cite the baseline file/commit,
and the current check records when the prior decision was confirmed or changed.

Round reports include known-item changes, newly upstreamed equivalents, follow-up fixes,
reversed decisions, new imports, validation and exact pending work. Keep portable evidence
summaries in the repository. Temporary logs may supplement them but cannot be the only
way another agent understands a decision. Do not place credentials, private disclosures,
real scan data or raw environment dumps in this public ledger.

## Current limits and next work

The [November 5 round](UPSTREAM_REVIEW_2026-11-05.md) carries pending work on
manual-price accounting on model-switch resume (#1622),
deferred PTY capabilities (#1623), transactional report writes (#1117/#1108), Caido deadline
adaptation (#1053), and SARIF identity migration (#1590). These have separate risks; they
are not rejected proposals. The remaining triage-depth records are not a completed code audit.

**Sandbox image:** images 1.3.1 and 1.4.0 were published, resolving the publication
part of [upstream issue #1628](https://github.com/usestrix/strix/issues/1628).
Fork PR #53 previously delivered 1.3.1. Release 1.2.54 now pins the public 1.4.1
compatibility derivative by immutable digest: the official parent plus the fork's
existing NSS restart guard. The official 1.4.1 entrypoint failed same-container
restart. The selected derivative passed restart, protected mounts and default-deny
network controls on Linux/arm64 before the default changed. The same published digest
subsequently passed the [native Linux/amd64 follow-up](UPSTREAM_SANDBOX_AMD64_2026-11-12.md),
including the controls before and after restarting the same sandbox. See the
[dated acceptance report](UPSTREAM_SANDBOX_2026-11-12.md) for the exact image,
source mappings, first Docker timeout and release-time limits; the new follow-up
records the later amd64 result without rewriting those historical snapshots.

The November 5 review carries [#1631](https://github.com/usestrix/strix/pull/1631) (Git source archive deduplication) and
[#1634](https://github.com/usestrix/strix/pull/1634) (validated report dedupe decisions), carried on the review
branch. API-spec issue [#1632](https://github.com/usestrix/strix/issues/1632) awaits a maintainer contract;
[#1633](https://github.com/usestrix/strix/issues/1633) has the proposal [#1635](https://github.com/usestrix/strix/pull/1635),
carried on the review branch, which delivers environment values to the agent workspace and needs the owner's confirmation
before a master delivery. Their evidence, exact source versions, local commits and validation limits are in the latest
November 5 report and ledger.

The [November 12 snapshot](upstream-tracking/round-2026-11-12.json) records
the earlier review and release observations; it is a historical snapshot. The upstream
tag v1.7.1 predates the TUI fix. The subsequent
[release continuation](upstream-tracking/integration-released-2026-11-12.json) records
the delivered startup backport and paired image/Opengrep migration. The latest published
fork release is [1.2.54](https://github.com/samirhvbr/strix/releases/tag/shvia-v1.2.54),
matching `.fork-version`. [release-audit.json](upstream-tracking/release-audit.json)
retains the earlier audit.

The upstream CI prerequisites #1577, #1657, #1658 and #1660 await review. After their
fixes land in main, check fresh base-aware CI for #1708, #1710, #1712 and #1713.
The #61 backport is delivered and released; compare later upstream changes with the
source SHAs carried here before applying another backport. The selected integration did
not refresh other contributions or advance the inventory cursor.

The selected integration follows [fork #62](https://github.com/samirhvbr/strix/pull/62).
Its exact code SHA, source-to-fork mappings, image acceptance, limits and delivery state
are in [integration-2026-11-12.json](upstream-tracking/integration-2026-11-12.json).

[PR #63](https://github.com/samirhvbr/strix/pull/63) delivered the startup and sandbox integration
to master after all 15 checks passed. The [delivery snapshot](upstream-tracking/integration-delivered-2026-11-12.json)
records the merge and CI. The [release snapshot](upstream-tracking/integration-released-2026-11-12.json)
and [asset verification](upstream-tracking/release-acceptance-2026-11-12.json) record
the completed native builds, publication and checksums. Resume broader review from the
unchanged inventory checkpoint; MCP callback/report compatibility remains pending.
