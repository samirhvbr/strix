# Upstream operational reliability review — 2026-11-05

This round starts at fork master `895ab37d8178155db8b373a7e25a84a3c500d3c2`
(released as **1.2.47**), reading the prior
[delivery](UPSTREAM_DELIVERY_2026-10-30.md),
[follow-up](UPSTREAM_FOLLOWUP_2026-10-30.md) and per-item ledger first. The previous
continuity page still named 1.2.44; current release/tag/master metadata was checked
before preparing work. No master, tag, release or mirror update is part of this round.

## Known items first

All **597 previous records** were queried individually by repository/type/number,
including closed, merged, carried, deferred and discarded items. None was missing.
**36 records changed metadata; 24 changed a state or source head.** Every changed
state/head is recorded separately from the fork decision and imported source SHA.
No carried PR changed its source head. Changed unimported heads retain their old
reviewed version and are explicitly marked as needing further review.

Important changes:

- Our [PR #1634](https://github.com/usestrix/strix/pull/1634) was merged as
  `09536cf1481731c790995bd8060db19a3b75ff48`, closing #1630. Both production and
  regression-test files match the carried `beb55e88d5b36cfdf27e7490b434b09e3f376638`
  version exactly; **18 dedupe tests pass** on current upstream. The fork already
  carries that behavior, so it was not applied again.
- [#1625](https://github.com/usestrix/strix/pull/1625) was merged as `cb581bf9`.
  The final head `4d793375` and merge have the same tree, while the head differs
  from our old reviewed `352221cf` (Dockerfile and image defaults changed). Upstream
  now defaults to sandbox **1.4.0** with Opengrep. The fork's tool/image transition
  remains deferred; this is not an imported implementation or a completed image test.
- [#1628](https://github.com/usestrix/strix/issues/1628) is closed and its reported
  artifact problem is independently resolved: anonymous GHCR manifest reads return
  **200 for 1.3.0, 1.3.1 and 1.4.0**. Existing fork
  [PR #53](https://github.com/samirhvbr/strix/pull/53) already proposes the 1.3.1 pin
  and version 1.2.48. Its head is `2a6bb91e`; it remains open and unreleased. No
  competing pin patch or release was created. Its Docker/full-suite evidence is
  author-reported; this round checked registry metadata, not a running container.
- #1621 and #1631 received independent reproductions from `phant0um`, with no
  blocker. Suggestions about immutable spec mounts, TMPDIR, and enumeration memory
  are recorded as follow-ups, not additional proven defects or permission to weaken
  selection limits. All 16 existing open authored PRs had conversations, inline
  thread status and current-head Actions checked. No new code change was required.
- The authors closed #907 and #910 as obsolete. The pinned LiteLLM stream iterator
  already classifies failed events on sync and async paths; current Claude caching
  already uses its four-point budget. These exact old proposals change from deferred
  to discarded, without treating their broader issues as solved. No shim or fifth
  cache breakpoint was introduced. #1328 closed without an explanatory comment;
  the mount-free requirement remains in #1080 and is deferred, not rejected.
- Closed empty issue #1604 stays discarded. #1252 stays already covered by current
  import warmup. Closed dependency batch #1433 is kept separate from new #1643;
  neither closure nor a bot recommendation establishes compatibility.

## Discovery and selection

Full all-state catalogs were compared with the inventory and a **48-hour overlap**
beginning **2026-10-28T19:03:26Z**. Initial catalogs had 1169 PRs and 424 issues;
the final catalog has **1170 PRs and 425 issues**, including our new issue and PR.
There are **330 open PRs and 210 open issues**. Discussions remain disabled.
The inventory includes nine newly recorded fork PRs (#45–#53). The ledger now has
**625 records**. Metadata coverage is not a deep review of all historical code.

New proposals already address our prior issues: #1638 for local spec dependencies
(#1632), #1639 for cross-model resume pricing (#1622), and #1648 for pending PTY
capabilities (#1623). #1647 addresses cold Go startup (#1191); #1642 concerns pairing
observed costs with response estimates. All remain proposals pending their recorded
acceptance/validation conditions; none was duplicated or silently imported.

The deeper source pass covered run summaries, resume selection and record readers,
cloud response/download handling, report writers, usage accounting, deferred
runtime capabilities, and overlap with recent fork fixes. Several real defects
already have work in progress: executive-report replacement (#828/#1506), artifact
failure reporting (#1108/#1117), explicit resume scan mode (#1507/#1510), and child
context limits (#1645/#1646). Existing atomic cloud download handling also ruled
out an unsubstantiated overwrite contribution. Fork-only native-control and test-ledger
fixes have no corresponding upstream subsystem to patch. No additional PR was forced.

## Delivered contribution

[Issue #1653](https://github.com/usestrix/strix/issues/1653) and
[PR #1654](https://github.com/usestrix/strix/pull/1654) address damaged run metadata
blocking the resume list for healthy sibling runs. Invalid UTF-8 in `run.json` or
`vulnerabilities.json` escaped the JSON fallback; non-array `targets_info` could
raise `TypeError`. The patch preserves the existing summary defaults and workspace/
instruction fallback. Selecting an undecodable record by name now produces the
existing usage error. Artifacts are not rewritten and damaged runs are not declared
successfully resumable.

- Upstream base reviewed: `09536cf1481731c790995bd8060db19a3b75ff48`.
- Published source: `b6ac4037b8c6e18507d6cb476ebe775a594795b4`.
- Fork import: `91938128177fd90c7845dfd04b324f68bf501e7c` on
  `feat/upstream-contributions-20261105`, based on master `895ab37d`.
- Import conflict was limited to the append point for CLI tests: the fork's existing
  scan-mode tests and all new tests were retained. Production functions and the
  appended test functions have identical ASTs. Whole-commit patch IDs differ due
  to the test context; both IDs and function hashes are recorded.
- Six new cases failed on the unchanged upstream base, with three new shape controls
  passing. The additional named-resume case failed before its reader fix.
- Final focused suites: **178 passed upstream, 189 passed on the fork**. Ruff
  formatting/lint, Mypy and Bandit passed. Pyright improved from **941 to 940**
  upstream and **1257 to 1256** on the current fork, with one diagnostic removed
  and none added in each comparison. `make check-all` still fails at Pyright.
- No live LLM, scan target or Docker session was used. The full suite was not rerun
  locally for this focused patch. CI and its matrix results are recorded separately
  in [round-2026-11-05.json](upstream-tracking/round-2026-11-05.json).

The [Greptile review](https://github.com/usestrix/strix/pull/1654#issuecomment-6659988768)
gives **5/5** at the published source SHA and requests no adjustment. Automated
feedback is separate from maintainer approval, remote checks and merge.

Remote CI was allowed to run: the workflow, Ruff, Mypy, Bandit, lock, Go, viewer,
wheel and binary jobs passed (10 jobs). The Python 3.12/3.13/3.14 pytest matrix
was still in progress at the recorded checkpoint. It is not a completed green
workflow; follow its run URL and exact head in the machine-readable record.

## Continuity

The effective upstream synchronization remains **6b7ae2eb**. Observing upstream
**09536cf1**, reviewing its merges and carrying one selected patch do not integrate
the intervening Opengrep/image transition. Code synchronization, selected patch
validation, upstream review and released fork state stay separate in
[checkpoints.json](upstream-tracking/checkpoints.json).

The released fork remains **1.2.47**, tag/master `895ab37d`, with two binary assets
and `SHA256SUMS` in the release. Asset names/sizes were inspected; binaries and their
digests were not revalidated. Upstream release/package remains **1.7.0**; source
main includes two newer commits. No version bump is appropriate for this unreleased
review branch.

Next round: re-fetch every known ID first, then overlap updated-date discovery.
Priorities are #1654's feedback/CI, fork #53's image delivery, #1638/#1639/#1648 and
their issues, and the prior persistence/runtime/SARIF backlog. Do not reapply #1634,
duplicate #53, or equate a new observed SHA with an integrated/validated upstream base.
