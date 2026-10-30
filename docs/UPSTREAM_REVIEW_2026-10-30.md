# Upstream operational reliability review — 2026-10-30

This round continues [2026-10-29](UPSTREAM_INTEGRATION_2026-10-29.md) from review-branch
commit `783d738a3f00d168e6bee968004f661e7e622254`. The new user request authorizes
focused issues and PRs in `usestrix/strix`. Fork delivery remains on the existing
review branch; `master`, its version, release and the `main` mirror are separate.

## Known items first

All **574 existing ledger records** were fetched individually, including closed,
merged, incorporated and deferred items: 530 upstream records and 44 fork PRs.
There were no missing responses. Only **#1370** changed state: the author closed
it in favor of a cleaner rebased provider implementation. This is not rejection.
New #1626 concerns OrcaRouter, but the closing comment does not identify its
number and the author accounts differ; the replacement relationship is not assumed.
The earlier fork applicability decision is preserved.

The remaining 573 known heads/states/updated dates/merge commits are unchanged.
Forty-four previously unknown fork draft fields were filled with `false`; this is
metadata enrichment, not 44 state transitions. Imported and reviewed source SHAs
were retained. There are no newly merged equivalents of the carried contributions
in this interval, and no reason to reapply or remove their implementations.

Conversations, reviews, inline threads and current-head checks were read for all
14 previously tracked contributions authored by this fork. There is no new request
after the prior inventory checkpoint. Their Actions remain `action_required`;
Mintlify preview notices are bot messages, not unanswered maintainer feedback.

## Discovery and selection

The initial all-state inventory contains **1,154 PRs and 414 issues**, matching the
API totals; 322 PRs and 203 issues were open before this round's publications.
The final catalog contains 1,156 PRs and 419 issues, with 324 and 208 open respectively.
The only additions during work were our two PRs and five issues; no other item's
updated date changed and no catalog item disappeared. The ledger now has **596 records**,
including 12 additional historical items inspected for overlap and the three new triages.
The previous checkpoint was 2026-10-29 20:58:03 UTC; discovery includes the 48-hour
overlap starting 2026-10-27 20:58:03 UTC. The complete catalog also covers older
updates and closures. Discussions remain disabled. Catalog coverage is not a claim
of complete code review of every historical proposal.

Three newly identified proposals were triaged: #1625 (Opengrep), #1626 (OrcaRouter)
and #1627 (case-sensitive model catalog lookup). They remain unimported. The first
two require toolchain/provider acceptance and validation; #1627 is existing work
for #1607 and must not be duplicated. Exact heads and review depth are in the ledger.

Source review covered cloud HTTP/CLI/upload handling, report persistence/deduplication,
runtime lifecycle, target staging and API specifications. Existing proposals for
report transactions (#1117/#1108), report revisions (#1307), session rewriting
(#1311), sandbox cleanup (#831), Caido deadlines (#1053), clone deadlines (#1145)
and deferred PTY capabilities (#1623) were excluded from new implementation.
The ledger records targeted overlap checks without claiming full acceptance reviews.

## Delivered contributions

| Contribution | Evidence and benefit | Fork status |
| --- | --- | --- |
| [#1631: deduplicate conflicted Git source paths](https://github.com/usestrix/strix/pull/1631), fixes [#1629](https://github.com/usestrix/strix/issues/1629) | A real unmerged Git index produced three ZIP members and 198 counted bytes for one 66-byte file. Streaming deduplication preserves selection and raw enumeration bounds. | Source `5c57940ac11f2d2bf26d5a80ac90f882c48154e4`, cherry-picked with provenance as `8890f1b5`; no adaptations. |
| [#1634: validate dedupe decisions](https://github.com/usestrix/strix/pull/1634), fixes [#1630](https://github.com/usestrix/strix/issues/1630) | String `"false"`, an empty duplicate ID or an unknown ID suppressed a finding. Require a boolean decision and an exact existing ID; malformed results use the existing fallback that preserves the finding. | Source `beb55e88d5b36cfdf27e7490b434b09e3f376638`, cherry-picked with provenance as `3154805d`; no adaptations. |
| [#1628: missing sandbox image](https://github.com/usestrix/strix/issues/1628) | Registry manifest `1.3.1` returns 404 while `1.3.0` returns 200. The public installer redirects to main and requests the missing tag. | Maintainer publication needed. Source from #1617 is already integrated; the runtime image is not delivered by a source merge. Existing workflow proposal #960 is linked rather than duplicated. |
| [#1632: external API-spec references](https://github.com/usestrix/strix/issues/1632) | A local schema exists before staging but is absent from the sandbox directory, leaving the accepted contract incomplete. | Await bounded-reference versus bundled-spec policy; no arbitrary directory copy implemented. |
| [#1633: Postman environment handoff](https://github.com/usestrix/strix/issues/1633) | Real environment parsing honors enabled flags and URL precedence, but tenant/token values disappear before staging and root-task construction. | Await effective collection variables, separate environment file or substitution contract. No credentials or real environment data used. |

The missing-image report distinguishes source `main` from release v1.7.0: the
released runtime still defaults to published `1.3.0`, so it does not claim all
released scans are blocked. No rollback of #1617's patch is proposed.

## Validation boundaries

The two fixes were reproduced on unchanged upstream
`6b7ae2ebe4d47037a927aade23265ff4e4f27e84` with Python 3.12.14 and frozen dependencies,
then validated in isolated HOME environments with dotenv loading disabled.
The cloud fix has four failing-before regressions, five passing-after cases and
353 passing cloud/local-source tests. The dedupe fix has 16 failing-before cases,
two valid controls and 210 passing focused tests. An independent review exercised
four additional dependency-report filing cases, including actual JSON/Markdown persistence.

The unmodified upstream full suite reports 2,480 passed, 19 failed and 3 xfailed.
The failures are existing runner fixture drift around `skills` and telemetry settings.
Each contribution passes repository Ruff, Mypy and Bandit. Upstream Pyright remains
941 errors with zero added/removed normalized diagnostics for either patch.
These are explicit quality-gate limits, not green `make check-all` results.

The API-spec issues have setup/staging reproductions and 39 passing existing tests.
No live LLM, managed cloud upload, Postman request, Docker scan or real target was used.
Container builds and Windows runtime acceptance were not performed. Registry probes
read manifest metadata only. The per-round evidence record stores final integrated
test results separately from these source-branch checks.

On the fork at **`3154805db216d40f35852b8fb608126a9dabcfcd`**, the full suite completed:
**3,590 passed, 12 skipped, 3 xfailed** (519.13 seconds). Ruff format/lint, Mypy on 176
source files and Bandit pass. Pyright remains **1,259 errors**, exactly the same normalized
diagnostics as the previous validated code `b4fb64c0`, with zero additions/removals.
Existing Docker-dependent skips were not separately rerun this round. The code checkpoint
advanced only after these results; upstream base, delivered master and release did not advance.
The tests-only #597 truncation assertion is not present in this fork; neither cherry-pick
required adaptation. Both patch IDs match their source commits.

The dedupe PR's full upstream suite also completed: **2,498 passed, 19 failed, 3 xfailed**,
with exactly the same 19 failed test IDs as the unchanged baseline. The new PRs both have
`action_required` CI. Their Greptile reviews report no actionable requests at the published
heads; automated review is separate from maintainer approval and CI.

Portable result summaries, source checks and provenance are stored in
[round-2026-10-30.json](upstream-tracking/round-2026-10-30.json).

## Continuity and next action

See [checkpoints.json](upstream-tracking/checkpoints.json) for the last completed
code checkpoint and the separate review checkpoint, and
[items.json](upstream-tracking/items.json) for exact source heads, imported versions,
local commits, decisions and histories. Do not equate a reviewed head with an import.

Next round: re-fetch every ledger item, especially the new issues/PRs and #1370/#1626,
before discovery. Recheck publication of sandbox `1.3.1`, maintainer decisions on
#1632/#1633, review feedback/CI on the two fixes, and the previous priority queue
(#1622, #1623, #1117/#1108, #1053, #1590, #1319, #1307, #1145, #911, #677).
Keep the deeper backlog marked pending; no broad audit completion is claimed.

Version/release metadata was rechecked: fork `1.2.43` / `shvia-v1.2.43` still matches
`master` at `8aa0791ef2bd30b000f3e1d0f67cd388ccc8dfc5`; upstream release/package remains
`1.7.0`. The fork mirror stays `62b496430da5df5e9e79a190e7af6f92529883a3`.
These two contributions and the previous integration are unreleased. The sandbox
artifact blocker and existing Pyright/remote-CI limits remain before master delivery.
