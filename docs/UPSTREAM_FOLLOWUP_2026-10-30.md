# Issue implementation and PR feedback follow-up — 2026-10-30

Continues the [previous round](UPSTREAM_REVIEW_2026-10-30.md) from fork review-branch
commit `e6752b5c7f5a9fd903960d0aa7b87d6dd4270b73`. The user requested PRs for our
open issues, then asked to check responses on all our open upstream PRs.

## Known items and feedback

All **596 known ledger items** were re-fetched by repository/type/number, including
closed, merged, deferred and incorporated records. No head, state, merge commit,
draft flag or updated date changed; there were no missing items. Full all-state
catalogs and a 48-hour overlapping update comparison found no untracked updates
before our new PR. Upstream main remains `6b7ae2ebe4d47037a927aade23265ff4e4f27e84`.
No previously incorporated contribution newly merged, and no import was repeated.

All **16 existing open PRs authored by samirhvbr** had their conversations,
reviews, inline thread status and current-head Actions inspected. All are mergeable
without conflicts, but merge remains blocked by upstream requirements and every
current-head CI run reports `action_required`. Bot recommendations are not CI or
maintainer approval. No new unanswered human request was found.

The comments with technical requests were checked against the actual source heads:

| PR | Request and current evidence | Fresh focused validation |
| --- | --- | --- |
| [#1614](https://github.com/usestrix/strix/pull/1614) | Recovery command now shows effective scopes, fixed in `f13c192ff724932d54eb9a11d28fb999557f1ee1`. | 10 cloud session tests passed. |
| [#1470](https://github.com/usestrix/strix/pull/1470) | `test_telemetry_labels_the_scan_from_the_same_check` covers both telemetry sinks, added in `b9c1c0d0b331b8f3826e93bd4749f40c6e52262f`. | 12 scan-target tests passed. |
| [#1469](https://github.com/usestrix/strix/pull/1469) | Tests seed unrelated findings and exercise the real dedupe path with a waiting model, including dependency fallback; head `b6cdb58ae7134321d304e93f6e5b28ba6f82f297`. | 5 race tests passed. |
| [#1174](https://github.com/usestrix/strix/pull/1174) | Non-OpenAI usage-limit exceptions receive the resumable-stop path; head `2872fc3234157c23dfad3ebf1eba2c4d954df4fb`. | 33 runner/retry tests passed. |
| [#1279](https://github.com/usestrix/strix/pull/1279) | Native-provider preference stays inside the accepted same-price branch; guard fixtures include provider metadata. Head `ad33c2c2200d620096092540574f814180740b8e`; the human review already has an author reply. | 21 pricing tests passed. |

These **81 tests** confirm existing fixes; no further source changes were needed
for those comments. Four inline conversations remain unresolved in GitHub, even
though their requested code/test changes are present. Three are marked outdated;
the #1470 thread is not. They were not silently marked resolved on behalf of the
reviewers. #1631/#1634 have favorable Greptile feedback at their current heads with
no actionable request. The remaining existing PRs have no outstanding code request
in the inspected discussions. Exact heads, comment URLs and CI URLs are in the
[portable follow-up evidence](upstream-tracking/followup-2026-10-30.json).

## New contribution

[PR #1635](https://github.com/usestrix/strix/pull/1635) implements the template-variable
case reported in [#1633](https://github.com/usestrix/strix/issues/1633). Enabled
environment values used by `{{name}}` templates are included in the collection's
standard variable block, with environment precedence and transitive dependencies.
Templates and existing variable metadata remain intact. Unused environment values
and the host API key are excluded. Each fetch gets a distinct private snapshot so
two environments of the same collection cannot overwrite each other before staging.

Greptile then identified accumulation of download snapshots in its review of the
initial head. This was reproduced and fixed in the same PR: downloads are removed
after every staging copy succeeds; failed staging can be retried, and abandoned
downloads are cleaned at normal process exit. The run copy is retained for resume.
The [review comment](https://github.com/usestrix/strix/pull/1635#discussion_r4425901702)
is addressed by source commit `5f65feb9c501daf8eb7cead1f68372e8fe392b06`.

The earlier issue-only decision changed after the user's request to prepare fixes:
this is a bounded, reviewable effective-variable proposal, not an agreed upstream
contract. Variables accessed only through script APIs are explicitly outside its
scope. It does not execute Postman scripts, flatten requests or copy the complete
environment. The small writer overlap with active #1158 is disclosed in the PR;
that PR's ID-validation work remains separate and was not imported here.

- Reviewed/published source: `5f65feb9c501daf8eb7cead1f68372e8fe392b06`, following
  initial `34e67e55786a55a412cbc6579f3baec2fecd9196`, both based on upstream
  `6b7ae2ebe4d47037a927aade23265ff4e4f27e84`.
- Fork imports: `e219e11aeec4d68ec48614ad693afce75f923b5f` (initial, identical patch)
  and `41be416c96a1cc9dd9518907c7daaf0737a0420c` (cleanup follow-up) on
  `feat/upstream-sync-contributions-20261029`. The second import preserves the fork's
  existing `resume=True` missing-file validation and adds a resume assertion after
  download cleanup. Its source/fork patch IDs intentionally differ; both are recorded.
- Unchanged-base reproduction: 5 failures and 1 passing control. Two further
  cleanup regressions fail on the initial PR head and pass on the final head.
  Final source: **114 focused tests passed**; integrated fork: **120 passed**,
  including API-spec staging/resume, inputs, target lists and private-file handling.
  A real subprocess also confirms cleanup when exiting normally before staging.
- Ruff format/lint, Mypy and Bandit passed for both source and fork. Pyright remains
  941 on source and 1259 on the fork, with zero added/removed normalized diagnostics.
  `make check-all` therefore still fails at the existing Pyright gate.
- No live Postman account, LLM or Docker scan was used. The full suite was not
  rerun for this focused patch. The last full fork result remains **3590 passed,
  12 skipped, 3 xfailed at `3154805db216d40f35852b8fb608126a9dabcfcd`**; it must
  not be presented as a full-suite result for the new import.

The code checkpoint advances to the new import only for the recorded focused
validation. The upstream synchronization SHA remains unchanged. Master, mirror,
version, tags and releases remain at their previous checkpoints.

## Remaining issues and next action

- [#1628](https://github.com/usestrix/strix/issues/1628): manifest `1.3.1` still
  returns 404; `1.3.0` returns 200 (fresh read-only registry check). Maintainers
  must publish the artifact. Existing workflow PR #960 must not be duplicated.
- [#1632](https://github.com/usestrix/strix/issues/1632): no maintainer answer yet.
  Preserve the decision boundary between bounded local-reference support and a
  bundled-spec requirement. Do not expand host-directory access implicitly.
- #1622 (historical per-model pricing) and #1623 (deferred PTY capability contract)
  remain pending their recorded design decisions; no patch is claimed here.
- Recheck #1635's current head, feedback and CI alongside all prior ledger items;
  coordinate its writer hunk with #1158 before either is incorporated from upstream.
- Resume from [the ledger](upstream-tracking/items.json) and
  [separate checkpoints](upstream-tracking/checkpoints.json). The deeper backlog
  remains pending. Approval to execute fork workflows belongs to upstream maintainers.
