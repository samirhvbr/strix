# Upstream integration — 2026-10-29

Review branch: `feat/upstream-sync-contributions-20261029`, fork `samirhvbr/strix`.
This is **unreleased**. Master, the main mirror, tags, releases and deployments were not
changed. No upstream PR/issue/comment was created in this round. Start the next round at
[the permanent continuity guide](upstream-tracking.md), then the [ledger](upstream-tracking/items.json)
and [checkpoints](upstream-tracking/checkpoints.json); do not reconstruct this work from chat.

## Initial state and synchronized base

- Delivered fork: `8aa0791ef2bd30b000f3e1d0f67cd388ccc8dfc5`, version 1.2.43.
- Upstream: `usestrix/strix:main`, `6b7ae2ebe4d47037a927aade23265ff4e4f27e84`.
- Actual common ancestor: `f7a32ae62d94872631bc8415073868d02a6ffdaf`.
  The previous release text names 62b4964, one earlier README-only commit.
- Separate synchronization merge: `d3654fb29c8fb86bdee2d23d648f978c82f74a33`;
  **49 upstream commits** were absent from the fork. Original checkout was preserved.
- Latest upstream release remains v1.7.0. This main snapshot is 64 commits after that tag.
- Latest fully validated code commit and pending work are pinned in checkpoints.json.

The sync adds soft skill turn limits, opt-in parallel calls, request-body customization,
malformed tool-argument repair, configurable coverage/report tools, request timings,
proxy evidence fallback, sandbox device forwarding and optional deferred startup.
Sandbox source moves to 1.3.1. Runtime lock changes: aiohttp 3.14.3→3.14.5,
graphql-core 3.2.11→3.2.13, yarl 1.24.2→1.25.1. The fork's cryptography >=50 floor
(locked 50.0.2) and the upstream five-day dependency cooldown remain.

## Changes to previously known contributions

All 43 upstream PRs referenced in the baseline FORK.md and its 8 issue references were
re-fetched, including closed items. Of 35 third-party carried PRs, 31 current heads match
those originally imported. #1462, #1497 and #1509 have rewritten SHAs but **identical
file trees**; importing them again would add no behavior. Their old/current source SHAs
and original fork merge commits are in the ledger.

- #1113 and #1157 are now closed without merge, by their authors. Full timelines contain
  no stated reason. That is not evidence of technical rejection. Keep the applicable
  carried fixes; #1157's older review finding was already fixed in the imported head.
- #1173 remains closed and superseded by upstream's warm-up synchronization. Its replacement
  commits 1f3f9b31/7f46dd17/e60fd839 were adopted in the 1.1.0 sync. Do not restore our old shim.
- No currently carried PR was newly merged upstream in this historical recheck.
- #1446 received real follow-up corrections; see the follow-up section below. No new source
  delta was identified for the other carried PRs; their existing decisions were retained, subject to integration compatibility checks.
- #1469's newer dependency-deduplication fix is now carried too. During adaptation, the
  newly upstreamed code_locations/fix_verification fields remain inside the per-run lock.
- #1470 still classifies cloned repository targets as white-box after the runner/setup
  changes; #1471 home isolation, #1472 local PDF fields, #1174 terminal quota handling and
  #1279 native-provider price tie handling remain covered by the integrated tests.
- The no-session input contract carried with #1161 required a merge follow-up: queued input
  was consumed but discarded when resume had no SDK session. Commit `e552b459` appends it
  once to text/list input; SQLite sessions replay from persisted history. Two new cases
  fail before the fix; two SQLite controls and the after-tests protect both paths.

The `bin/pr-watch` review found upstream file changes in #1469/#1470/#1612; these were
checked in the integration. Mintlify preview messages were bot output, not unanswered
human review. This round did not change upstream PR branches or post replies.

## Contributions incorporated on the review branch

The table names the **actual imported source**, which differs from the current upstream
head for fork companion PRs. Full reviewed SHA, source repo, state, adaptations, validations
and decision history are in the ledger. All 16 source upstream PRs remained open at the
final selected-head check; these are not upstream merges or released fork features.

| Source PR | Benefit | Actual imported source SHA | Fork integration / adaptation commits |
| --- | --- | --- | --- |
| [#1613](https://github.com/usestrix/strix/pull/1613) | Rejected note/todo edits no longer mutate stored records before validation. | `8a6aa7aeff84311a474a2d3f535160e70730a1c2` (fork #38) | `b20077d7` |
| [#1614](https://github.com/usestrix/strix/pull/1614) | A successful remote scope update followed by a failed local write reports actionable partial success, including JSON output. | `66f460efd7b0fc6bb4a70b123becfb1d36c49da4` (fork #39) | `db1adf16` |
| [#1612](https://github.com/usestrix/strix/pull/1612) | Docker clients honor shell-selected DOCKER_CONTEXT while preserving DOCKER_HOST precedence. | `ebe3ef0095418e7224568e0a34cf0ec62e88b469` (fork #40) | `23d8719f` |
| [#1615](https://github.com/usestrix/strix/pull/1615) | Distinct tool calls sharing one provider ID no longer overwrite each other. | `160663fcaf420650a7d5f54170c3646bf83c983f` (fork #41) | `f5581cc3` |
| [#1469](https://github.com/usestrix/strix/pull/1469) | Concurrent dependency filings serialize duplicate detection and storage, including model-based legacy matching. | `1f5bc2c1b619e367d2592f086560ae14c3cf95e9` (fork #42) | `56b42357` |
| [#1624](https://github.com/usestrix/strix/pull/1624) | Interrupted HTTP error bodies become controlled cloud transport errors; output files and JSON/stdout contracts are retained. | `bfadbf54f168b7c6037a9237713ee585bfd3e9af` (fork #43) | `fc3142ed` |
| [#1621](https://github.com/usestrix/strix/pull/1621) | API-spec scans restore their staged snapshot and mounts when resuming. | `50c365329ddb8a45fba41ebee37a6143631b2883` (fork #44) | `9c365eac` |
| [#1562](https://github.com/usestrix/strix/pull/1562) | Diff scope preserves complete default branch names such as release/stable. | `1cca4b6b1c5aa5e39f69670910bf8d0ca9880cf3` | `ce4698cb` |
| [#1563](https://github.com/usestrix/strix/pull/1563) | Cloud wait polls the explicitly created scan ID instead of a related resource ID. | `8c9ae6abbf33ec6975ebb622021cf69a8e00e5d2` | `8992e646` |
| [#1531](https://github.com/usestrix/strix/pull/1531) | Content-filtered completions are logged as provider errors while retaining HTTP status and cost. | `e3e21bfb6903162a0a3f73885d6a0f4e0cd4449b` | `19591c90` |
| [#1420](https://github.com/usestrix/strix/pull/1420) | Parallel Ollama calls retain separate names and arguments through the SDK stream accumulator. | `dca391898b984f9e3a6d4591cbcb1028ea41e61f` | `c6ed5035` |
| [#1515](https://github.com/usestrix/strix/pull/1515) | Invalid image and non-image URL responses use bounded image stripping/retry instead of replaying the failing history. | `21fac52785ab9fd157ab9f27aca6564dc81c0169` | `bae51b0b` |
| [#1580](https://github.com/usestrix/strix/pull/1580) | Cloud --wait stops polling and rejects completion after its deadline. | `01a4b24dc111bd442c14a2a2436d6bc8f6a3c8fa` | `c8932433` |
| [#1502](https://github.com/usestrix/strix/pull/1502) | Large-output previews avoid whole-output allocation while preserving existing bytes/line results. | `4c5b30e55fea9c62204e3652ddb2649ebf70ee42` | `86a84777`, `e25b1e43`, `5d32a147` |
| [#1499](https://github.com/usestrix/strix/pull/1499) | Sandbox restart skips NSS database initialization when cert9.db already exists; CA import still runs. | `546a38c4b991e9e7f0bc0c175f26b000ba66c233` | `8c5f4358` |
| [#1571](https://github.com/usestrix/strix/pull/1571) | HTTP 202 source uploads are polled until processing completes instead of deleted as malformed responses. | `81882da444093d532797e37ed22a2be413cd0341` | `25d94a99`, `b3e8b311` |

Fork companions 38–44 remain open against master; their code is included by merges in this
review branch. Do not merge/reapply them independently without checking ancestry and
behavior. Issues 1608/1609/1610/1611/1619/1620/1560/1561 point to their actual solving PRs.
Issue 1528 is only partially addressed: filtered completions are logged correctly, but raw
Bedrock refusal details are still unverified. Closing the issue would overstate the fix.

## Compatibility adaptations and follow-up fixes

- Pause is reason-based: operator pause maps to its own reason; messages cannot release a
  test pause. Snapshot compatibility and budget recomputation are retained.
- Deferred startup is disabled for guarded sessions. Network policy requires the real
  sandbox and guard to be ready before admission; a regression covers this ordering.
- Assessment tool authorization, scoped credentials, idempotent history, F3 terminal quota
  stops, mount restrictions and proxy query/header/structured-error behavior survive the sync.
- Output #1502 needed a correction to preserve the legacy tail when only the byte limit
  is exceeded (7 lines/max_lines 16 was a counterexample). 32 boundary cases and 10,000
  differential cases pass. Local helper peak allocation fell from 544,434 to below 250,000
  bytes in the regression; this is not an end-to-end scanner speed claim.
- Upload #1571 uses #1580's remaining deadline and retains uncertain uploads after timeout.
  No new poll/scan starts after expiry. Requests still has connect/read timeout semantics,
  not absolute wall-clock cancellation of DNS or continuously trickling response bodies.
- NSS #1499 skips only database creation; CA import remains unconditional. Real temporary
  NSS initialization hangs on restart before the patch and returns in 8 ms with the guard.
  Full main sandbox image rebuild is still required for installed containers to benefit.
- #1420 runs before SDK aggregation; unique-ID repair and malformed-argument repair run
  later. SDK replays check they do not interfere with one another.
- Test harness commit 188e9550 resolves the modern Bash executable before isolating PATH and
  persists the intended ambient telemetry setting before checking config immutability.
  These 16 fixture failures also occur on the unchanged fork; production launcher behavior
  and credentials were not changed by that harness correction.

### CSPT follow-up from #1446

The original imported source was `2a76196f78040436037ef746ebceba4c2493275c` in
fork merge `75ea64ca`. Current reviewed source is `ac70e0ed1034ca5bdf38f6d6f5a4b7b06e28026f`.
Six bounded follow-ups are integrated through `b4fb64c0`, preserving their author's commits:
`8490f81e`, `899ada6c`, `d651acef`, `8adba04e`, `4d7c2010`, `85f78bd3`.
The ledger contains each original source SHA; the redundant formatting commit and broad
upstream merge were not replayed.

CSPT findings now accept PoC-backed revisions instead of being treated as dependency
records. Dependency markers no longer collapse unrelated CVEs into one SARIF fingerprint.
The dynamic tool reserves dependency_cve for the dependency tool, nullish classes normalize
correctly, and dedicated CSPT work satisfies the corresponding browser coverage surface.
Source-page guidance replaces unstable payload-destination identity. Existing stored reports
are not migrated. Coverage remains a wording heuristic, with tested short-token boundaries.

Before: **13 failed, 43 passed**. After: **671 passed**, plus 20 coverage tests after the final
assertion. Ruff/Mypy/Bandit pass. Dedupe locks, assessment evidence and incomplete-agent rules
are preserved. No browser exploit execution or real target scan was performed.

## Validation and boundaries

Commands and portable result summaries are in [validation.json](upstream-tracking/validation.json).
Python 3.12.14, frozen dependencies, isolated HOME/environment, production LiteLLM mode,
local model-cost map, disabled telemetry and update checks. Modern Bash 5.3.15 and Go 1.27.3
were used. Go was downloaded from the official distribution with its SHA256 verified.
No live provider account, paid call, managed-cloud service or external scan target was used.

| Check | Result |
| --- | --- |
| Unchanged fork Python baseline |16 failed, 3219 passed, 12 skipped, 1 deselected, 3 xfailed. The excluded PTY test hangs on the baseline and passes after upstream's fix. |
| Base-sync focused tests |414 passed; runtime 142 passed; pause/PTY regressions pass after integration fixture updates. |
| Own carried-fix batch |1111 passed, 3 xfailed. |
| Integrated suite at 834c8d77 |3548 passed, 12 skipped, 3 xfailed; no deselection. |
| Final suite after follow-up fixes | **3567 passed, 12 skipped, 3 xfailed** at `b4fb64c0`; no deselection. |
| Docker-controlled checks |12 passed: network/mount guard and durable pause/process restart, using owned local fixtures. Main sandbox image not rebuilt. |
| Ruff / Mypy / Bandit |Pass; Bandit run separately because check-all stops at Pyright. |
| Pyright / make check-all |Not green. Baseline 1259 diagnostics; sync 1263; final integrated 1259 (zero signature changes after mailbox/CSPT follow-ups). Same count does not mean same diagnostics: 16 added/16 removed versus baseline, of which 11 additions are already on upstream and 5 originate in the Ollama shim's unknown SDK types. |
| Go TUI |Race-enabled tests, vet/gofmt and binary build pass. |
| Viewer |npm ci/build pass; regenerated assets byte-identical. |
| Wheel |Platform wheel builds, with Go sidecar, fork controls and static assets; no .env/node_modules. Package version 1.7.0 is upstream policy, not fork release identity. |
| Remote CI |Not a pass: fork jobs blocked by account billing; most candidate upstream runs action_required. #1515 remote pytest cost fixture failed, though the same test passes locally. |

A focused mailbox follow-up batch had 248 passes and one timeout in the existing test-pause
stress test; the same test was already intermittently failing on the unchanged fork.
Keep that failed attempt visible alongside the isolated/full reruns. A failed invocation
using a nonexistent test path collected nothing and provides no validation evidence.
No Python 3.13/3.14, Windows/Linux application binary acceptance, signed Desktop installation,
real provider scan or new published sandbox image was validated by this local macOS round.

## Deferred and decision-dependent work

| Item | Evidence / reason | Exact resumption condition |
| --- | --- | --- |
| Issue #1622 manual prices |Historical usage can be repriced with the new model on resume; unsafe to claim this opt-in combination validated for failover. |Design durable per-call/model pricing and reconcile budget accounting before enabling it with model switches. |
| Issue #1623 deferred PTY |Pending session advertises no PTY; first long command can take a one-shot timeout path and omit write_stdin. |Define backend capability contract and test first-call behavior before adopting deferred startup broadly. Default/guarded startup remains blocking. |
| #1117 / issue #1108 |Write-failure reproduction confirmed; proposed rollback can leave disk report/telemetry ahead of memory. |Bounded transaction design across mutations and report outputs; test failures after each write before import. |
| #1590 |Synthetic SARIF collisions remain relevant; patch would change historical alert identity and must retain CSPT/ReDoS classification. |Decide identity migration/dismissal compatibility, then adapt and reproduce. |
| #1053 |Caido readiness deadline is useful but patch predates deferred lifecycle and fork project-setup retry. |Preserve #1205 retry/authorization, adapt timeout propagation and add readiness regressions. |
| #1176 |Already covered by upstream #1192 tokenized MarkdownIt/fallback. |Only revisit on a new PDF rendering regression; do not reintroduce obsolete regex code. |
| #1546 |Author explicitly marks experimental work not ready to merge. |Author evaluation/readiness changes; no implementation assumed available. |
| Sandbox image 1.3.1 (#1617) |Tag defaulted by the synchronized source is not in the registry (404); master pins the published 1.3.0. |Decide: wait for upstream's image, pin 1.3.0 meanwhile, or publish an image under the fork's control. Required before any master delivery. |
| Routing/auth, Strix2, MCP server changes |Material architecture/product expansion, not bounded reliability patches. |Explicit scope decision and compatibility design. |

Remaining useful triage candidates include #1319 SDK refusal, #1307 report revision,
#1145 clone timeout, #911 atomic child limits and #677 synchronous telemetry. They still
need current-code reproduction, full review/discussion and tests; no patch was imported
based only on their descriptions. The individual ledger states the rationale for every item.

## Version and release audit

[Release 1.2.43](https://github.com/samirhvbr/strix/releases/tag/shvia-v1.2.43), tag and
remote master all resolve to `8aa0791e`. `version.md` now distinguishes the effective
released ancestry (+15, f7a32ae6), the older published wording (+14, 62b4964), and this
unreleased sync. The product stays 1.2.43; the upstream package stays 1.7.0 by policy.

All 61 existing fork version tags have matching releases. Deliveries **1.2.41 and 1.2.42**
have no tag/release; tags **1.2.37 and 1.2.43** are lightweight despite the annotated-tag rule.
These historical refs/releases were not rewritten or filled in. Build & Release remains
`disabled_manually`.

The four published binary digests match API metadata, release notes and SHA256SUMS. The
release attributes them to build `047cc8cb`, before tagged source `8aa0791e`. Binaries were
not downloaded/executed and fresh-build equivalence was not established. A future authorized
release needs exact-tag builds, native smoke tests and asset provenance; Desktop signing,
sidecar pinning and installed acceptance remain separate. Full evidence is in
[release-audit.json](upstream-tracking/release-audit.json).

## Second pass (same day, 20:58 UTC inventory)

Run from the review branch with both remotes fetched. The upstream code did not move: `main` is still
`6b7ae2eb`, the synchronized base, so no code checkpoint advanced.

- **Known items rechecked.** All 530 upstream ledger records and all 44 fork PRs were compared with the live API on
  state, `updated_at` and head SHA. One changed: **#1393** (opt-in Parallel web-search provider) was rebased onto
  `6b7ae2eb` and carries four commits of its own. It is now deep-reviewed and stays **deferred**: the earlier
  condition (truncation and config persistence) is met, but the fork configures no web-search provider and the
  provider adds about 200 lines to a shared module for a service this fork does not use. Its egress profile is the
  same as the existing Perplexity and Exa providers, so that is not the reason.
- **Discovery.** Open inventory unchanged: 320 PRs + 203 issues, equal to the API's `open_issues_count`. The
  update query (48 h overlap, closed and merged included) returned 29 items; every one was already in the ledger
  except four closed items, now recorded: **#1616** (price LLM usage from model config; merge commit
  `6b7ae2eb`, the synchronized head itself), **#1617** (brotli CVE patch in the sandbox image, tag 1.3.1,
  merge `dfdbe4b2`), **#1603** (Vercel AI Gateway attribution headers, merge `98e3df14`) and issue **#1529**
  (a usage question about report language; no code). The three PRs are ancestors of the review branch, so they
  arrived through the base synchronization and need no import. The next discovery window starts at
  `2026-10-27T20:58:03Z`.
- **`bin/pr-watch`.** The upstream-touched flags on #1469, #1470 and #1612 are the ones already checked during
  the integration; the only "human feedback" flag is Mintlify's preview-approval bot.
- **Version and releases rechecked.** Unchanged and consistent: `.fork-version`, `version.md`, tag
  `shvia-v1.2.43` and the Latest Release all say 1.2.43 and resolve to `8aa0791e`, equal to remote `master`; the
  upstream latest release is still v1.7.0 and the package version 1.7.0 on both sides. `origin/main` is 50 commits
  behind `upstream/main`. Tags 1.2.41 and 1.2.42 are still absent and 1.2.37/1.2.43 are lightweight.

### New delivery blocker: the sandbox image tag

#1617 moved the default sandbox image to `ghcr.io/usestrix/strix-sandbox:1.3.1`, and the synchronized source
(`strix/config/settings.py`, `scripts/install.sh`) points at it. **That tag is not published**: an anonymous
manifest request returns 404 for 1.3.1 and 200 for 1.3.0 (the positive control for the token). The published
semver tags end at 1.3.0; the delivered `master` pins 1.3.0 and works. Upstream's `main` is ahead of its own
registry until it cuts a release, which is normal for them, but it matters here: a scan or an engine binary
built from this branch cannot pull its default sandbox image (`STRIX_IMAGE` overrides it). Evidence is in
[release-audit.json](upstream-tracking/release-audit.json) (`sandbox_image_audit`). Options, none taken:
wait for upstream's image; pin the fork to 1.3.0 meanwhile (the runtime works, but libbrotli 1.2.0-4,
CVE-2026-111497, stays in the image used against hostile targets); or build and publish an image under the
fork's control from `containers/Dockerfile`. This must be decided before any master delivery.

## Coverage and exact next step

**523 current-open upstream items: 320 PRs + 203 issues**, all triaged; **24 deeply reviewed
(22 PRs and 2 issues)** after the second pass (#1393 was re-reviewed in depth when its head moved). Another **7
known items outside the open inventory** are in the ledger: 3 closed PRs rechecked, 3 PRs merged upstream during the
window and 1 closed question, bringing the upstream ledger to 530. The **44 fork PRs** are recorded separately: 7
deep, 1 release-provenance triage and 36 metadata-only. Total: **574 unique records**. Historical recheck covers all
51 previously known Strix items; it overlaps the inventory and must not be added to it again.

The full inventory was fetched again at **2026-10-29 20:18:34 UTC** and was identical,
including bodies, heads and updated dates, and once more in the second pass at **20:58:03 UTC** (identical totals;
only #1393's head and update date differ). No pages were truncated. Discussions are disabled.
The whole closed/merged backlog was not deeply reviewed; related catalog/timeline searches
were targeted. The remaining 499 open items have triage, not complete code review.

No partial code integration is left in progress. The latest validated code is
`b4fb64c04d3bf3a6c8193651049941d84b5c8424`; the documentation commit does not change that code.
Resume from the review branch with the priority queue in checkpoints.json and recheck the
current upstream head before taking new candidates. Source SHA/commit mappings, earlier
fork decisions and re-evaluation reasons survive in the ledger and Git history.

Recheck every ledger item, including the three closed upstream PRs, then query all updates
since 2026-10-27T20:58:03Z (48-hour overlap with the second-pass inventory) and compare against
the saved keys. The final base/head check can be newer than the inventory watermark; it must
not move that watermark forward and skip unrelated updates. Resume implementation from
this branch; compare future #1446 changes against ac70e0ed and read checkpoints before taking new candidates.
If the user authorizes delivery, resolve the stated quality/release acceptance limits first,
then apply the version policy. This round itself grants no master/release authorization.
