# Upstream CI and feedback follow-up, November 26, 2026

[PR #1840](https://github.com/usestrix/strix/pull/1840) remains open and requires
human review. Its current head `f37744a662f14f8d2610a4f459575c4fc8deffe9`
contains the wording requested by Greptile about viewer startup and `--no-open`.
The earlier thread is open and marked outdated. Arcanist reviewed the current
head without actionable findings. Mintlify preview needs someone with write
access to approve the fork preview. [Greptile thread](https://github.com/usestrix/strix/pull/1840#discussion_r4708058059) ·
[Arcanist](https://github.com/usestrix/strix/pull/1840#pullrequestreview-6073811976) ·
[Mintlify](https://github.com/usestrix/strix/pull/1840#issuecomment-7191410049)

Lint, TUI, viewer, and wheel passed on that head. GitHub cancelled both
pytest jobs after 15 minutes, the same observed result on the exact
`9ed84f99` base. The full suite outcome and timeout cause remain unresolved.
The available checks and reviews did not identify another documentation
correction. [Current PR CI](https://github.com/usestrix/strix/actions/runs/45815799641) ·
[Exact-base CI](https://github.com/usestrix/strix/actions/runs/45671540630)

## Issues and related work

- **#1838** remains open without direct comments. A community contributor
  opened #1841, proposing native builds with a Go target and wheel tag
  consistency check. It references the issue and awaits review. Its author
  reports no automated testing, and Actions reports `action_required` with
  no jobs for both CI and Binary. The diff was inspected without running
  the third-party patch. Recheck the proposed build contract and test
  results before considering adoption. [Issue #1838](https://github.com/usestrix/strix/issues/1838) ·
  [PR #1841](https://github.com/usestrix/strix/pull/1841) ·
  [CI](https://github.com/usestrix/strix/actions/runs/45823765172)
- **#1839** remains open. A contributor reproduced the 968 Pyright errors
  on the reported base and offered a documentation patch once maintainers
  choose between the CI lint checks with advisory Pyright and requiring no
  new Pyright diagnostics. No maintainer decision or linked patch was
  found. [Issue #1839](https://github.com/usestrix/strix/issues/1839) ·
  [Independent reproduction](https://github.com/usestrix/strix/issues/1839#issuecomment-7195053186)
- **#1837** remains open with #1840 as its proposed solution. No direct
  comments were recorded. [Issue #1837](https://github.com/usestrix/strix/issues/1837)

Resume with the [targeted evidence](upstream-tracking/ci-1840-2026-11-26.json).
Check for a newer #1840 head or base and human review, a CI run or review
on #1841, and a contributor-check decision on #1839. The
[operational review](UPSTREAM_REVIEW_2026-11-26.md) has broader inventory
coverage and the release observation.
