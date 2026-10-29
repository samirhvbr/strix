# Upstream integration — 2026-10-29

This unreleased review branch is `feat/upstream-sync-contributions-20261029`.
Destination: `samirhvbr/strix`, eventual target `master`; upstream: `usestrix/strix`, `main`.
No master/mirror update, tag, release, deployment or upstream message is part of this work.

## Reference points

- Fork base: `8aa0791ef2bd30b000f3e1d0f67cd388ccc8dfc5` (1.2.43).
- Upstream snapshot: `6b7ae2ebe4d47037a927aade23265ff4e4f27e84` (latest release v1.7.0).
- Actual merge base: `f7a32ae62d94872631bc8415073868d02a6ffdaf`.
- 49 upstream commits are absent from the fork ancestry. Earlier release documentation
  records `62b4964`; this is not the actual common ancestor of today's tips.
- Open inventory: 320 PRs and 203 issues; queried with pagination, counts match API totals.
  Upstream discussions are disabled. Item records and final totals follow in a later
  documentation commit after the integration batches.

## Base synchronization

Merge upstream with a separate two-parent merge commit, keeping authors and ancestry.
Preserve the fork's reason-based pause state and acknowledgment persistence, F3 usage-limit
stops, idempotent sessions, assessed-identity authorization, network/mount protections,
proxy query/header handling, chat content compatibility, local PDF/download behavior,
launcher and cryptography >=50 floor (locked50.0.2).

New upstream behavior includes soft per-skill turn limits, opt-in parallel calls, request
body customization, repaired malformed tool arguments, configurable coverage/report tools,
request timing, proxy evidence fallback, sandbox device forwarding and deferred startup,
updated sandbox1.3.1 and dependency fixes. Runtime lock deltas are aiohttp3.14.3→3.14.5,
graphql-core3.2.11→3.2.13 and yarl1.24.2→1.25.1; uv/npm retain the upstream5-day cooldown.

Integration adaptations:

- Operator pause maps to the existing independent `operator` reason, with compatibility
  for upstream snapshots; user messages cannot release a parked test pause.
- Deferred startup is used only without a network policy: guarded scans require a real
  client/guard before admission. A regression proves that simply enabling defer bypasses
  this readiness requirement.
- Child factory uses upstream configurable base tools while retaining controlled-assessment
  whitelisting; proxy logging preserves the fork's structured error details.
- Runner test doubles include new telemetry/skills fields. Redundant root-ID guards after
  the ID has been validated are removed so Pyright can still analyze the combined runner.
- Viewer rebuilt from fork sources; generated assets are byte-identical, preserving its
  local PDF and session-gated local history behavior.

## Validation in progress

Python3.12.14, frozen dependencies, isolated HOME/environment, production LiteLLM mode,
local model-cost map, disabled telemetry and update checks. No provider keys or real scans.
Initial focused run:414passed; runtime network/session/proxy subset142passed. Ruff/Mypy
pass. Bandit is run separately because the documented check-all stops at Pyright.
Baseline Pyright1259; synchronized tree1263 (11added,7removed signatures); every added
signature is present on unchanged upstream6b7 (941errors), not a merge-only diagnostic.
Go tests with race detector, vet/format and sidecar build pass using temporary Go1.27.3
from go.dev with the published SHA256 verified. Viewer npm ci/build passes.
Full Python baseline/final counts and controlled Docker checks are recorded after completion.

## Known upstream limitations

- [#1622](https://github.com/usestrix/strix/issues/1622): manual pricing can reprice historical
  agent usage after a model switch on resume. This opt-in configuration must not be treated
  as validated for launcher failover; a durable per-call/model cost policy needs its own work.
- [#1623](https://github.com/usestrix/strix/issues/1623): deferred startup reports no PTY before
  backend readiness, affecting the first long-running command. Default startup remains
  blocking; guarded scans also retain blocking startup in this fork.

## Continuation

Complete per-item triage/deep reviews, then carry selected unmerged PRs in distinct commits
only after base-sync validation. Record actual SHAs/commits and keep unreviewed patches deferred.
