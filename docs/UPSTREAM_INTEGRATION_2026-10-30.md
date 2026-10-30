# Upstream integration — 2026-10-30

Third pass of the 2026-10-29 synchronization. It continues on the delivery branch
(`feat/upstream-sync-delivery-20261029`, fork 1.2.44), adds three upstream pull requests and
records the full inventory again. Read [the previous round](UPSTREAM_INTEGRATION_2026-10-29.md) first;
this file only describes what changed after it. Nothing here was pushed to `master`, tagged or released.

## Initial state

| Ref | Value |
| --- | --- |
| `upstream/main` | `6b7ae2eb`, unchanged since the synchronization (latest upstream release is still v1.7.0) |
| `origin/master`, tag `shvia-v1.2.43`, Release (Latest) | `8aa0791e`, all equal |
| `origin/main` (mirror) | `62b49643`, 50 commits behind `upstream/main`, 0 ahead |
| Delivery branch | `4d526eb5` pin commit and `300b8fd2` delivery commit on top of the synchronized review branch |
| Open upstream PRs by this account | 17 (#1174, #1279, #1469, #1470, #1471, #1472, #1562, #1563, #1612, #1613, #1614, #1615, #1621, #1624, #1631, #1634, #1635); none merged; #1173 stays closed |

Upstream merged nothing since the base. There is no new upstream code to synchronize; the only open
question about the base is the sandbox image tag (below).

## Changes to previously known items

The full open inventory and the 48-hour update query were fetched again (final refresh at
`2026-10-30T19:03Z`: **325 PRs + 208 issues = 533 open**, matching the API's `open_issues_count`;
Discussions are disabled). Every open item was compared with the stored ledger on `updated_at` and head SHA, the
48-hour update query (closed and merged included) returned 41 items, all of them in the ledger now, and all 44 fork
PRs were compared on state and head. The known items outside the open inventory were covered by that query, not
fetched one by one. Two upstream records changed, none of the fork PRs:

- **#1370** (OrcaRouter provider) was closed by its author: "Closing this in favor of a cleaner
  version rebased on current main." Recorded as closed and discarded; nothing had been carried.
  The successor is #1626, inferred from the identical title (the link is not stated upstream).
- **#1607** (case-sensitive LiteLLM catalog lookups) was deferred while no patch existed. #1627
  fixes it, so it is now `incorporated_fully` (below).

No carried PR's head moved: #1627, #1631 and #1634 still match the SHAs imported here.

## New items

| Item | Author | Decision |
| --- | --- | --- |
| PR #1627 fix(models): preserve case-sensitive catalog lookups (fixes #1607) | third party | **Incorporated** (cherry-pick, authorship preserved) |
| PR #1631 fix(cloud): deduplicate conflicted Git source paths (fixes #1629) | fork owner's account | **Incorporated** (cherry-pick) |
| PR #1634 fix(report): validate deduplication decisions (fixes #1630) | fork owner's account | **Incorporated** (cherry-pick) |
| Issue #1628 default sandbox tag 1.3.1 is not published | fork owner's account | **Adapted**: the fork's pin to 1.3.0 (`4d526eb5`) |
| Issues #1629, #1630 | fork owner's account | Already covered by the carried PRs above |
| Issues #1632 (local `$ref` files), #1633 (Postman environment values) | fork owner's account | Deferred: contract questions for maintainers |
| PR #1635 fix(api-spec): preserve Postman environment values (fixes #1633) | fork owner's account | Deferred pending an owner decision |
| PR #1625 Opengrep replaces Semgrep (image and prompts) | third party | Deferred |
| PR #1626 OrcaRouter provider | third party | Deferred |

**Parallel-session activity.** Issues #1628–#1630, #1632, #1633 and PRs #1631, #1634, #1635 were opened
on 2026-10-30 (15:17–16:19 UTC) from the fork owner's GitHub account in a parallel session (a different
agent on another machine). This session did not create them; they are recorded as that.

### #1627 — case-sensitive catalog lookups

LiteLLM's cost map is case-sensitive, but `_catalog_entry` lowercased the name first, so a mixed-case provider
id missed its own row. `model_supports_reasoning()` then returned False, `reasoning_effort` was never sent, and
the image-support and Responses-versus-chat decisions read the same lookup. Reproduced on the integrated base:
**125 of 130 mixed-case reasoning ids read False before, 0 after.** The patch tries the exact supplied id first
and keeps the old lowercase and bare-suffix fallbacks. Measured across 19,528 catalog name variants, lowercase
names resolve as before except one non-model row (`openai/container`); every other difference is a mixed-case
name, which is the intended fix. 100 targeted tests pass and the 9 new or changed tests fail on the unfixed
file (91 pass); Pyright on `strix/config/models.py` goes from 45 to 42 diagnostics.

### #1630 and #1629 — my own fixes, superseded

Both defects were reproduced on the integrated base with offline scripts before any patch existed. #1630: a
malformed dedupe verdict (`"false"` as a string, or a positive verdict with an empty or unknown id) suppressed a
real finding, and both reporting tools then told the agent not to re-file it. #1629: an unresolved merge lists a
path once per index stage, so a 66-byte file became 3 manifest entries, 198 bytes and 3 ZIP members with one name.

I wrote fixes for both (`ed3797f2`, `f1d0a5fa`). The fork owner's account then opened #1634 and #1631 for the same
defects. A differential showed the owner's versions are stricter: #1634 compares the verdict id verbatim (mine
truncated to 64 characters first, so an invented id sharing a prefix with a real one passed), and #1631 dedupes with a
set (mine only caught consecutive repeats). Of my 14 checks 13 pass against #1634; the one difference is a verdict that
omits `is_duplicate`, which #1634 sends to the existing logged error path (the finding is still stored). The owner's PR
heads were carried and my commits stay only on the local branch `wt-r3-superseded-own-fixes`.

### Deferred, with the reason

- **#1632 and #1633** were reproduced on the integrated base (output identical to the reports) and not fixed. Each
  asks upstream which contract is intended — copy contained local references, require a bundled spec, or deliver an
  effective variable map — and the second decides how environment secrets reach the model context.
- **#1635** implements one contract for #1633: enabled, referenced Postman environment values go into the staged
  collection's `variable` block, so credentials from the environment enter the agent's workspace (the host's Postman API
  key stays out), with a per-fetch snapshot directory and an exit-time cleanup. It is not carried: that is the secret
  delivery decision the issue leaves open, and it overlaps open #1158 and carried #1621. The diff was not reviewed line by
  line; the ledger records it at triage depth and keeps the PR body's figures as the author's, not re-measured.
- **#1625** swaps Semgrep for Opengrep in the image and in every white-box prompt (15 files). The pinned 1.3.0 image has
  Semgrep and no Opengrep, so the prompt change alone would send agents to a missing binary. Greptile reports two P2 gaps.
- **#1626** adds an OrcaRouter provider (+1,557 lines). The author discloses being on the vendor's team, the fork configures
  no provider of this vendor, and four P1 findings from the bot are unanswered.

## Validation

| Check | Result |
| --- | --- |
| Full suite on the round branch tip `111a34e9` (`LITELLM_MODE=PRODUCTION`, `LITELLM_LOCAL_MODEL_COST_MAP=True`) | **3604 passed, 12 skipped, 3 xfailed, 0 failed** in 7m42s |
| Tests for #1631 + #1634 and neighbors | 192 passed |
| Ruff, Mypy | clean on the touched files |
| Pyright, production files touched this round | 53 diagnostics before, 50 after, none new; the repository stays red at the recorded baseline |
| Remote CI | not available: the fork's GitHub Actions account is billing-locked and upstream CI waits for a maintainer |

**Live price map.** With LiteLLM's live cost map, `tests/test_pricing.py::test_resolves_common_bare_model_names` fails today:
the live map lists `kyma/grok-4.5` at a different price from the `xai/`, `perplexity/xai/` and `github_copilot/` rows, so bare
`grok-4.5` becomes ambiguous and the resolver (correctly) returns nothing. It is data drift, not a code change, and the test
passes with the bundled map. Run the full suite with `LITELLM_LOCAL_MODEL_COST_MAP=True` for a deterministic result (CLAUDE.md
now says so).

Not done: a full external scan, remote CI, new `pentest-<triple>` binaries, the line-by-line review of #1635 and #1626.

## Sandbox image

The registry was rechecked at 2026-10-29T23:27Z and 2026-10-30T15:22Z: `strix-sandbox:1.3.1` is still 404 (the positive control
`1.3.0` answers 200). The pin to 1.3.0 stands; libbrotli CVE-2026-111497 stays in the image used against hostile targets until
upstream publishes 1.3.1, and then both pins move (`RuntimeSettings.image`, `scripts/install.sh`).

## Version and release audit

Unchanged since 2026-10-29. `.fork-version` on `master`, tag `shvia-v1.2.43` and the Latest Release all say 1.2.43 and equal
`8aa0791e`; the delivery branch says 1.2.44 and is unmerged, untagged and unreleased. Upstream's latest release is v1.7.0 and the
package stays 1.7.0 by policy. Tags 1.2.41 and 1.2.42 were never created; 1.2.37 and 1.2.43 are lightweight. The published
engine binaries predate this synchronization and **no new binaries exist**: the `pentest-<triple>` workflow needs Actions.

## Coverage and exact next step

**533 current-open upstream items (325 PRs + 208 issues), all triaged; 33 upstream records are deep-reviewed** (the earlier 24 plus
#1627, #1631, #1634, #1607 and issues #1628–#1630, #1632, #1633). Another 8 known items outside the open inventory are in the
ledger. Ledger total: **585 records** (541 upstream, 44 fork PRs). The remaining 500 open upstream items have triage, not a code audit.
`metadata.counts` had still been the 570-record value of the first pass; it is recomputed here.

Next discovery window starts at `2026-10-28T19:03:26Z` (48 hours before this inventory).

Resume: priority queue unchanged (#1622 manual-price accounting on model-switch resume, #1623 deferred PTY, #1117/#1108 report writes,
#1590 SARIF identity, #1053, #1319, #1307, #1145, #911, #677). Re-read #1635 when the owner decides on the contract. Move the sandbox
pins when 1.3.1 exists. Delivery needs the owner's go: merge with `--no-ff`, annotated tag `shvia-v1.2.44`, Release
`SHVIA 1.2.44 (Strix 1.7.0)` and the `main` mirror push.
