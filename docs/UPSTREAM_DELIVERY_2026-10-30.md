# Delivery consolidation — 2026-10-30

Two sessions worked on the same upstream round on 2026-10-30 without seeing each other's branch: one built the
**delivery branch** (`feat/upstream-sync-delivery-20261029`, fork 1.2.44), the other pushed to the **review branch**
(`feat/upstream-sync-contributions-20261029`). This record explains how they were joined and what is new. The
per-item record is [items.json](upstream-tracking/items.json); the review branch's own reports stay as they are:
[the operational reliability review](UPSTREAM_REVIEW_2026-10-30.md) and
[the issue implementation follow-up](UPSTREAM_FOLLOWUP_2026-10-30.md). Read the
[2026-10-29 round](UPSTREAM_INTEGRATION_2026-10-29.md) first. Nothing here was merged to `master`, tagged or released.

## Starting point

| Ref | Value |
| --- | --- |
| `upstream/main` | `6b7ae2eb`, unchanged since the synchronization; latest upstream release still v1.7.0. Nothing new to synchronize. |
| `origin/master`, tag `shvia-v1.2.43`, Release (Latest) | `8aa0791e`, all equal |
| `origin/main` (mirror) | `62b49643`, 50 commits behind `upstream/main`, 0 ahead; **fast-forwarded to `6b7ae2eb` at the end of this pass** (no content of its own) |
| Delivery branch before the merge | `68564bbd`: the 2026-10-29 synchronization, the sandbox pin (`4d526eb5`) and #1627, #1634, #1631 |
| Review branch on `origin` | `4936c89b`: six commits pushed on 2026-10-30 (12:24–13:32 BRT) by the other session |

The review branch's six commits are #1631 and #1634 (cherry-picks of the same upstream heads this branch had already
carried), the Postman fix #1635 with its cleanup follow-up, and the re-fetch of every ledger record with two reports.
Both sessions cherry-picked identical patches, so the merge applied each change once and produced no code conflict; the
duplicate commits remain in history (`8890f1b5`/`111a34e9` for #1631, `3154805d`/`f8cf41f3` for #1634).

## What this pass added

- **#1627** (case-sensitive LiteLLM catalog lookups; fixes #1607), cherry-picked as `d2adc9c9` with the author's identity.
  LiteLLM's cost map is case-sensitive but `_catalog_entry` lowercased the name first, so a mixed-case provider id missed its
  own row: `model_supports_reasoning()` returned False (so `reasoning_effort` was never sent), and image support and the
  Responses-versus-chat choice read the same lookup. Reproduced on the integrated base: **125 of 130 mixed-case reasoning
  ids read False before, 0 after.** Measured over 19,528 catalog name variants, lowercase names resolve as before except
  one non-model row (`openai/container`). 100 targeted tests pass and the 9 new or changed tests fail on the unfixed file.
  The review branch had triaged it only ("keep as a future candidate"); the ledger now says `incorporated_fully` with the
  measurements, and #1607 follows.
- **Sandbox image (#1628).** The registry was rechecked at 2026-10-30T15:22Z: `strix-sandbox:1.3.1` is 404, `1.3.0` is 200. The
  fork answers with a pin to 1.3.0 in `RuntimeSettings.image` and `scripts/install.sh` (`4d526eb5`, a test keeps them equal);
  the review branch had left #1628 as a maintainer matter. The cost is unchanged: libbrotli CVE-2026-111497 stays in the image
  used against hostile targets until upstream publishes 1.3.1.
- **My own fixes for #1629/#1630 were dropped.** Both defects were reproduced here first; the owner's PRs #1631/#1634 are
  stricter (verbatim ID match instead of a 64-character prefix; a set instead of consecutive-only dedupe), so their heads were
  carried. Mine live only on the local branch `wt-r3-superseded-own-fixes`.
- **Live price map.** With LiteLLM's live cost map, `tests/test_pricing.py::test_resolves_common_bare_model_names` fails today:
  `kyma/grok-4.5` is priced differently from the `xai/`, `perplexity/xai/` and `github_copilot/` rows, so bare `grok-4.5`
  is ambiguous and the resolver correctly returns nothing. Data drift, not code; it passes with the bundled map, and CLAUDE.md now
  says to run the full suite with `LITELLM_LOCAL_MODEL_COST_MAP=True`.
- **FORK.md tables.** Eight third-party and five own pull requests that the delivery carries were missing from its tables; they are
  listed now (third-party table 43 rows, CLAUDE.md count updated).

## Ledger reconciliation

The review branch's ledger (597 records, every record re-fetched) is the base. This pass overlaid only #1627, #1607 and #1628,
added one `incremental_updates` entry and recomputed `metadata.counts` and the open-inventory list from a full inventory taken at
`2026-10-30T19:03:26Z`: **325 open PRs + 208 open issues = 533**, equal to the API's `open_issues_count`; the 48-hour update
query returned 41 items, all in the ledger. Everything else keeps the review branch's decisions: #1625 and #1626 deferred, #1370
closed by its author with the fork decision unchanged (the successor link to #1626 is not stated upstream), #1632 awaiting a
maintainer contract. The 44 fork PRs match the ledger on state and head.

**#1635 is the one decision to confirm.** The review branch carries it (initial import `e219e11a`, cleanup `41be416c`, fork
adaptation: the existing `resume=True` missing-file validation is kept). It writes enabled, referenced Postman environment values,
credentials included, into the staged collection's `variable` block, so they reach the agent's workspace; the host's Postman
API key stays out. The owner asked that session for fixes of the open issues, so it is part of this delivery; it is also a
secret-delivery contract upstream has not agreed to, and it overlaps open #1158. Remove it from the delivery if that is not wanted.

## Validation of the merged tree

Full suite on the merged tree `d3d221b9` (`LITELLM_MODE=PRODUCTION`, `LITELLM_LOCAL_MODEL_COST_MAP=True`): **3612 passed, 12 skipped, 3 xfailed, 0 failed** in 7m38s (3604 on this branch's own tip plus the 8 tests of #1635). Ruff format and lint, Mypy (176 source files) and Bandit pass. Pyright was not used as a gate: in this environment it reports 5941 diagnostics for `strix/`, which is not comparable with the 1,259 recorded on macOS with `uv`; the production files changed by this pass went from 53 to 50 diagnostics (`strix/config/models.py` from 45 to 42), none new. `make check-all` therefore still stops at Pyright, as before. The 12 skipped tests are the existing Docker-dependent ones and were not rerun separately. With LiteLLM's live price map one pricing test fails today (see above).

Not done: remote CI (the fork's GitHub Actions account is billing-locked and upstream CI waits for a maintainer), a full external
scan, new `pentest-<triple>` binaries (the published ones predate this synchronization), a line-by-line review of #1626.

## Our upstream PRs: is any ready to merge?

None. All 17 open PRs from this account (#1174, #1279, #1469, #1470, #1471, #1472, #1562, #1563, #1612, #1613, #1614, #1615, #1621,
#1624, #1631, #1634, #1635) read `MERGEABLE` (no conflicts) and `BLOCKED` by `REVIEW_REQUIRED`; every one needs a maintainer to
approve the CI run first, and the contributor cannot merge. `bin/pr-watch` flags #1469 and #1470 (upstream touched two and three
of their files since they branched; still conflict-free), #1612 (upstream touched `docs/advanced/configuration.mdx`; the Mintlify
bot's preview request is unanswered) and #1635 (same bot). No human maintainer has commented on any of them. Nothing was pushed
to those PRs or commented on by this pass.

## Our fork PRs: what can be merged

#38 to #44 (`master` ← `feat/backport-*`, `feat/fix-dependency-dedupe-race`, `feat/cloud-download-error-recovery`) are `MERGEABLE`
and their checks fail only because Actions cannot start (billing lock). Their heads are all ancestors of the delivery branch, which is
itself a fast-forward of `origin/master` (0 commits behind). So they are not merged one by one: they carry no version bump, and
`master` takes a version, tag and Release together. Merging the delivery branch with `--no-ff` closes all seven at once.

## Exact next step

1. Done in this pass: `feat/upstream-sync-delivery-20261029` is on `origin` and the delivery PR is [#45](https://github.com/samirhvbr/strix/pull/45) to `master` on the fork (not the upstream). Its checks cannot run (Actions billing lock).
2. The owner confirms #1635 and the pinned sandbox image, then merges that PR with `--no-ff` and creates the annotated tag
   `shvia-v1.2.44` and the Release `SHVIA 1.2.44 (Strix 1.7.0)`. The `main` mirror is already at `upstream/main`.
3. When upstream publishes `strix-sandbox:1.3.1`, move both pins. Next discovery window: `2026-10-28T19:03:26Z`.

## After the merge

PR #45 was merged into `master` as `e2ec6f9d` after a review that covered the #1635 diff line by line (only the values a request
template references enter the staged collection; an unrelated environment value and the host's Postman key stay out; files are
mode 0600; the downloaded snapshot is removed after staging; resume reuses the staged copy; 8 tests pass) and a check that nothing
outside documentation changed since the validated tree `d3d221b9`. The owner then published the Release `SHVIA 1.2.44 (Strix 1.7.0)` (lightweight tag `shvia-v1.2.44` at `8dab158f`, no assets); see `version.md`.
One residual: the staged copy under the system temp directory keeps the credentials, mode 0600, until the temp directory is cleaned,
because resume needs it.
