# Upstream continuity record

Start every review here, then read the [delivery consolidation](UPSTREAM_DELIVERY_2026-10-30.md),
the [latest follow-up](UPSTREAM_FOLLOWUP_2026-10-30.md), the [preceding round](UPSTREAM_REVIEW_2026-10-30.md)
and the [per-item ledger](upstream-tracking/items.json). This is the fork's continuity
record; `FORK.md` remains the delivery changelog and `version.md` remains the version policy.
A GitHub state is not a fork decision: a closed PR may still be carried, an open PR may already
be covered, and reviewed code is not necessarily integrated or released.

## Two independent checkpoints

| Checkpoint | Last completed state | Current scope |
| --- | --- | --- |
| Delivered fork | `master` / `shvia-v1.2.43` at `8aa0791ef2bd30b000f3e1d0f67cd388ccc8dfc5` | Existing release; no master, mirror, tag or release update in this round |
| Upstream code synchronization | `usestrix/strix:main` at `6b7ae2ebe4d47037a927aade23265ff4e4f27e84`, merge `d3654fb29c8fb86bdee2d23d648f978c82f74a33` | Fork code `41be416c96a1cc9dd9518907c7daaf0737a0420c` on `feat/upstream-sync-contributions-20261029`: 120 focused tests pass after #1635 and its cleanup follow-up; last full suite there remains at `3154805d` (3590 passed, 12 skipped, 3 xfailed). The delivery branch `feat/upstream-sync-delivery-20261029` (fork 1.2.44, unmerged) carries that code plus the sandbox pin and #1627; its own full-suite result is in the [delivery consolidation](UPSTREAM_DELIVERY_2026-10-30.md). Existing Pyright limits remain. |
| Contribution review | 2026-10-30 follow-up and delivery consolidation: all 597 records kept, 325 open PRs and 208 open issues triaged | 16 existing authored PRs reviewed, 81 targeted feedback tests pass; #1631, #1634 and #1635 are carried; #1627 was added by the delivery pass. |

The machine-readable [checkpoints](upstream-tracking/checkpoints.json) pin the actual validated
fork commit, check results, coverage, partial work and the next query window. Advance a code
checkpoint only after its corresponding checks finish. A fetch, inventory refresh or code
review must never advance the integration checkpoint. A full suite passing does not turn a
failed type-check or blocked remote workflow green.

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

See the latest round for priorities: manual-price accounting on model-switch resume (#1622),
deferred PTY capabilities (#1623), transactional report writes (#1117/#1108), Caido deadline
adaptation (#1053), and SARIF identity migration (#1590). These have separate risks; they
are not rejected proposals. The remaining triage-depth records are not a completed code audit.

**Sandbox image (reproduced 2026-10-30, resolved for delivery by a pin):** the synchronized source defaults to
`ghcr.io/usestrix/strix-sandbox:1.3.1`, which is not published (404; the published 1.3.0 is the access control).
[Upstream issue #1628](https://github.com/usestrix/strix/issues/1628) requests publication and distinguishes
the current installer from the released runtime. The delivery branch pins 1.3.0 in `RuntimeSettings.image` and
`scripts/install.sh` (a test keeps them equal), so the default runtime works; the libbrotli CVE-2026-111497 patch of
#1617 stays out of the image until upstream publishes 1.3.1. Then move both pins and drop this paragraph; on a sync
conflict in those lines keep the published tag.

The new fixes are [#1631](https://github.com/usestrix/strix/pull/1631) (Git source archive deduplication) and
[#1634](https://github.com/usestrix/strix/pull/1634) (validated report dedupe decisions), carried on the review
branch. API-spec issue [#1632](https://github.com/usestrix/strix/issues/1632) awaits a maintainer contract;
[#1633](https://github.com/usestrix/strix/issues/1633) has the proposal [#1635](https://github.com/usestrix/strix/pull/1635),
carried on the review branch, which delivers environment values to the agent workspace and needs the owner's confirmation
before a master delivery. Their evidence, exact source versions, local commits and validation limits are in the latest
round and ledger.

Version/release verification is recorded in [release-audit.json](upstream-tracking/release-audit.json).
The release is still 1.2.43, with upstream package version 1.7.0 by policy. Existing published
binaries predate this integration. A source commit, built wheel, remote CI, released binary
and installed/runtime acceptance are separate evidence.
