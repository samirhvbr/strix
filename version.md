# Version — SHVIA fork of Strix

**Current version:** `1.2.44`
**Released upstream base:** Strix `1.7.0` + 64 commits (`6b7ae2eb`, verified ancestry).
The previous release, 1.2.43, sat on `1.7.0` + 15 commits (`f7a32ae6`); its published notes
name `62b4964` (+14), one README-only commit earlier, and are preserved.

**Sandbox image:** the fork pins `ghcr.io/usestrix/strix-sandbox:1.3.0` in `RuntimeSettings.image` and
`scripts/install.sh` (a test keeps them equal). Upstream's own default, `1.3.1`, is not in the registry yet.
Move both pins when `docker manifest inspect` finds it; see [the continuity record](docs/upstream-tracking.md).

> **Source of truth:** this file. [`.fork-version`](.fork-version) mirrors the version on one
> line for tools, and [`FORK.md`](FORK.md) is the changelog, one row per delivery. When you
> bump, change all three together.

---

## Our versioning

The fork has its own `X.Y.Z`, the house standard, **independent of upstream's number**.
Upstream's version is never part of ours; it is recorded apart as the *base* the fork sits on.

| Part | Meaning | When it changes |
|---|---|---|
| `Z` | One delivery | Every validated delivery on `master` that changes behavior, tooling, rules, security or tests. **One delivery = one row in `FORK.md`.** |
| `Y` | A new phase | The fork moves onto a new upstream release (1.5 → 1.6 → 1.7), or we make a structural change of our own. `Z` goes back to 0. |
| `X` | Stable release | Declared by Samir. It is `1` from the first delivery: the fork has been in daily use since then. |

**Does not bump:** wording fixes, formatting or lint, anything that changes neither behavior
nor contract.

We never edit `version` in `pyproject.toml`: it would conflict with every upstream merge. That
field keeps carrying upstream's version, which is also what silences Strix's self-update prompt.

**Upstream base** is recorded in the field above, in the "Upstream base" column of
`FORK.md`, and in the title of each Release. Update it at every sync.

## Names before 2026-10-06

The first 23 deliveries (up to `1.2.3`) were named `<upstream version>+shvia.<n>`. They became:

| Now | Was | Upstream base |
|---|---|---|
| `1.0.0` to `1.0.13` | `1.5.3+shvia.1` to `1.5.3+shvia.14` | Strix 1.5.3 |
| `1.1.0` to `1.1.4` | `1.6.2+shvia.15` to `1.6.2+shvia.19` | Strix 1.6.2 (`1.1.0` is the sync onto it) |
| `1.2.0` to `1.2.3` | `1.7.0+shvia.20` to `1.7.0+shvia.23` | Strix 1.7.0 (`1.2.0` is the sync onto it) |

The rule is `shvia.n` → `1.0.(n-1)` up to `n = 14`, `1.1.(n-15)` for `n = 15..19`, and
`1.2.(n-20)` from `n = 20`. The `FORK.md` table shows both names on every row. Text inside a row
keeps the wording of its time, so a `shvia.N` there is the old name.

## Tags, Releases and commits

- **Tag:** annotated `shvia-vX.Y.Z`. The prefix is there because upstream's own tags
  (`v1.0.1` and so on) live in this same repository, so a bare `vX.Y.Z` would collide with them.
- **Release:** titled `SHVIA X.Y.Z (Strix <upstream release>)`, notes in English taken from the
  `FORK.md` row. Every version gets one, otherwise the Releases box shows only upstream tags.
- **Commits:** the delivery commit, the one that bumps, is `X.Y.Z - description` in English, as in
  the other house repositories. Commits that go upstream as a PR (`fix/*`, `feat/*` branches)
  stay conventional commits without a version, so they read cleanly there. Merge commits keep
  their own message.

## How to bump

1. The delivery is validated on `master` (it ran, it was tested).
2. Update `.fork-version` and "Current version" here; update "Upstream base" too when it is a sync.
3. Add a new row on top of the `FORK.md` table: version, upstream base, date, what changed.
4. Commit as `X.Y.Z - description` and `git push origin master`. A sync also pushes the `main`
   mirror.
5. Tag and Release:

   ```bash
   V="$(cat .fork-version)"; BASE="1.7.0"        # BASE = the upstream release we sit on
   git tag -a "shvia-v$V" -m "SHVIA $V (Strix $BASE)" && git push origin "shvia-v$V"
   gh release create "shvia-v$V" --verify-tag --latest \
     --title "SHVIA $V (Strix $BASE)" --notes-file <notes.md>
   ```

   Never create a Release for an older version without `--latest=false`, or it steals the
   "Latest" badge.
6. After a sync, and whenever the "Contribuições enviadas ao upstream" table in `FORK.md`
   changes, review our open upstream pull requests with `bin/pr-watch` (read-only) and deal
   with every `!!` line (below).

## Reviewing our pull requests upstream

A pull request can go stale without anybody noticing: upstream fixes the same thing another
way (PR #1173 stayed open for 40 days after that), the branch starts to conflict, a reviewer
asks a question nobody answers, or CI never runs. `bin/pr-watch` lists every open PR of ours
and flags what to do:

| Flag | What to do |
|---|---|
| `CLOSED` / `MERGED` | Update the "Contribuições enviadas ao upstream" table in `FORK.md`; if it was merged, drop our carried copy at the next sync and keep upstream's. |
| `CONFLICT` | Merge `upstream/main` into the PR branch, resolve, test on the merged tree and push. Do not force-push: the auto-mode classifier blocks it, and a merge keeps the review history anyway. |
| `UPSTREAM TOUCHED` | Read the listed commits. If they fix the same thing, close our PR with a comment that names them and update the table and `CLAUDE.md`. If not, and it is still mergeable, nothing to do. |
| `HUMAN FEEDBACK ... not answered` | Read it, check the claim by measuring, answer. Measure before agreeing or disagreeing; a reviewer's number is a claim until reproduced (for #1279 it was right). |
| `bot reviews` | Read the inline findings (`gh api repos/usestrix/strix/pulls/<n>/comments`); each P1/P2 is either fixed or answered. |
| `CI waiting for a maintainer` | We cannot approve our own first-time runs. Run `ruff`, `mypy` and the touched tests on the PR merged into `upstream/main`, in a venv from the upstream `uv.lock`, and say so in the PR. |

The fork carries our own PRs by merge. When a PR is changed after review, bring the same
change into `master` as a commit of its own (the updated PR branch contains a merge of
`upstream/main`, which `master` does not want before the next sync).

> **`Build & Release` (`build-release.yml`) must stay disabled on the fork.** It is inherited
> from upstream and fires on every `v*` tag (5-OS PyInstaller build plus an auto-created
> Release). Our `shvia-v*` tags do not match that trigger, but the upstream tags we mirror
> (`v1.7.0`) do: pushing one started two builds once, cancelled by hand. It is switched off with
> `gh workflow disable build-release.yml -R samirhvbr/strix` (state `disabled_manually`). A fork
> registers upstream workflows when new workflow files are pushed, so check the state again after
> each sync with `gh workflow list -R samirhvbr/strix --all`. The upstream `CI` workflow stays on:
> it runs on pull requests and on pushes to `main` (the mirror), never on `master`.

> The house COMMITTER does not operate on this repository (no `.committer.yml`): versioning is
> manual, done by the agent that delivers.

## Release audit — 2026-10-29

The latest [SHVIA 1.2.43 release](https://github.com/samirhvbr/strix/releases/tag/shvia-v1.2.43)
and its tag resolve to delivered `master` at `8aa0791e`. The audit below was taken before the
1.2.44 delivery branch existed; `pyproject.toml` intentionally stays upstream 1.7.0.
See [the full audit](docs/upstream-tracking/release-audit.json).

- Deliveries 1.2.41 and 1.2.42 have no corresponding tags/releases. All 61 existing fork
  version tags do have matching releases. No historical release was created by this audit.
- Tags 1.2.37 and 1.2.43 are lightweight, contrary to the annotated-tag rule above. Preserve
  published refs; create future tags according to the rule instead of rewriting them.
- Current release assets are documented as coming from build 047cc8cb, before the tagged
  source 8aa0791e. API digests match the notes and SHA256SUMS manifest; this audit did not
  download or execute the four binaries or establish fresh-build equivalence.
- Build & Release remains disabled. The observed Actions billing lock still prevents
  treating remote jobs as successful. Exact-tag binary builds and native acceptance are
  required for a future delivery; this review branch does not update installed binaries.

### Recheck — 2026-10-30

Read-only, nothing changed: `.fork-version` on `master`, tag `shvia-v1.2.43` and the Latest Release still resolve to `8aa0791e`,
equal to remote `master`; `upstream/main` is still `6b7ae2eb` and its latest release v1.7.0. The 1.2.44 delivery is on its branch only
(unmerged, untagged, unreleased) and no new engine binaries exist, because the `pentest-<triple>` workflow needs Actions. The sandbox
tag `1.3.1` is still 404 in the registry, so the 1.3.0 pin stands. See [the round record](docs/UPSTREAM_INTEGRATION_2026-10-30.md).
