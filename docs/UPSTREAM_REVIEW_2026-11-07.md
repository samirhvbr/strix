# Upstream review, 2026-11-07

Reviewed `usestrix/strix` at `06015506625a78b981ae0b34be40239fc1b389be` with frozen dependencies. The [per-item ledger](upstream-tracking/items.json) retains the earlier decisions and source versions. The [round record](upstream-tracking/round-2026-11-07.json) contains the inventory and test provenance.

## Changes since November 5

- Rechecked all 633 known items individually, including closed and merged items. Seven changed in state, head or update time. The paginated open inventory contained 345 PRs and 218 issues before publication. The all-state query started at `2026-11-03T14:10:41Z` and returned 51 updated items. GitHub Discussions are disabled. The ledger adds 30 records, including the new issue/PR pairs, for 663 total.
- The original #1654 workflow ended cancelled. Upstream #1662 merged as `06015506` and sets a 15-minute limit on pytest. Both pytest jobs in the first run on that commit were cancelled. The Python 3.12 log stopped at 74 percent and ran to the limit. Lint, wheel, viewer and Go passed in that run. [Original #1654 CI](https://github.com/usestrix/strix/actions/runs/42041347484) · [Current main CI](https://github.com/usestrix/strix/actions/runs/42109098607)
- #1654/#1664, #1658/#1665, #1657/#1668 and #1660/#1669 are open pairs. The two budget-fixture patches have identical stable patch IDs. The alternatives differ in scope on transcript readers, provider/model splitting and synthetic catalog coverage. Compare the selected merge against the exact fork imports before reconciling them. #1672 also overlaps the staged Postman environment work in #1635.
- #1621 moved from `c652b20a` to `badf61f8`. The delta names the temporary directory in the recovery error and types saved targets. Twenty-four API-spec and target-list tests passed at the new head. Its earlier fork import retains its own source provenance. The independent reproduction on #1631 received an owner response confirming the four pre-fix failures and the candidate-path bound. [#1621](https://github.com/usestrix/strix/pull/1621) · [#1631](https://github.com/usestrix/strix/pull/1631)
- The author closed #835 without a new explanation. The proposal targets the old Textual renderer and the current Go renderer has a different implementation. Keep the issue deferred until the current behavior and desired display are verified. The Ollama report in #580 concerns an earlier release and needs current provider validation. [#835](https://github.com/usestrix/strix/pull/835) · [#580](https://github.com/usestrix/strix/issues/580)
- The new email-identity skill merged as `851743ad`. The CI rewrite and the skill update are observations of upstream, and the integrated source checkpoint is `6b7ae2ebe4d47037a927aade23265ff4e4f27e84`.

## Published fixes

| Issue and PR | Operational benefit | Reviewed source and regression |
| --- | --- | --- |
| [#1681](https://github.com/usestrix/strix/issues/1681), [#1684](https://github.com/usestrix/strix/pull/1684) | Header-only and unchanged HTTP replays preserve significant whitespace in the captured text body. | `ddf609fb1441a421291adb1c86dfe8ff1d2e1b14`. Ten failures and eight controls on base. Forty-two tests pass after the fix across host and sandbox replay with Caido transport stubbed. |
| [#1682](https://github.com/usestrix/strix/issues/1682), [#1685](https://github.com/usestrix/strix/pull/1685) | A failed or interrupted final ZIP stat/hash removes the source archive and returns the existing structured CLI error. | `f4d4e69a42335fe738a093ae1335a6519198cb16`. Four failing cases on base pass after the fix. The cloud and local source suites pass 352 tests with mock loopback listeners. |
| [#1683](https://github.com/usestrix/strix/issues/1683), [#1686](https://github.com/usestrix/strix/pull/1686) | PDF code and evidence retain indents and repeated spaces. An indented Python PoC remains usable when copied from the tested PDF. | `298aedc71cb7e0a3b74ff828521d1f822cbc5a1e`. Seven failures and one passing control on base. Eighty-four tests pass after the fix, including long lines, pagination, escaping and local viewer behavior. The rendered PDF was also inspected. |
| [#1687](https://github.com/usestrix/strix/issues/1687), [#1688](https://github.com/usestrix/strix/pull/1688) | The stream timeout test fixture can tear down an accepted idle HTTP connection, so the suite can continue. | `4ad4161b35bda226b5c4ac2a7dfeb5722e384677`. The new regression fails on the original behavior and cleans up. All seven stream idle tests pass with the fix. The 14 existing focused Pyright diagnostics are unchanged. |

The fixes use local synthetic inputs. Live Caido/Docker, managed Cloud, external report relay and other PDF viewers remain outside this validation. The contribution branches target upstream main. The fork ledger marks the imports pending.

## Validation and CI

Ruff lint/format, Mypy, Bandit and the lock check passed for the three production patches combined at `370d0fc16edbd290021522545f2b914c3412b509`. Pyright reported the same 941 diagnostics on base and the combined tree.

A diagnostic full suite with those patches and the prior #1657/#1658/#1660/#1577 candidates reached 1,832 passes and three failures before the fixture hung. The two MCP discovery failures passed in isolation on base, together with the other two cases in their parameterized test. The packaging failure also reproduced on base with an empty offline build cache and passed after resolving its build dependencies. The fixture hang reproduced on base with a loopback socket that sent no request bytes. The fixture shutdown was waiting for the HTTP handler to finish reading the request line.

On #1684, #1685 and #1686, lint, Go, viewer, wheel, supply-chain and Greptile checks passed. The 3.12 and 3.14 pytest jobs were cancelled after 15 minutes. The final 3.12 progress lines were 76, 74 and 74 percent, respectively, without failure tracebacks or a teardown stack in the logs. The remote cause remains unconfirmed.

The retest at `12829a1fb85930a97c73824e4fd0cebb59eda444` adds #1688 to the diagnostic tree. `pytest -q -o faulthandler_timeout=75` finished with **2,555 passed, 3 xfailed** on macOS with Python 3.12.14 and the warmed offline build cache. This is combined diagnostic evidence with the earlier budget, pricing, catalog and runner fixes. The upstream #1688 pytest jobs were still running at the recorded check. [#1688 CI](https://github.com/usestrix/strix/actions/runs/42373722946)

## Release and next review

Fork `master` resolves to `895ab37d8178155db8b373a7e25a84a3c500d3c2`, with `.fork-version` 1.2.47. The latest published release is `shvia-v1.2.47`. Upstream's latest release is `v1.7.0`. The open fork PRs #53 and #55 propose the 1.2.48 and 1.2.49 deliveries. Fork #54 passed all 15 checks reported at `e6d2a32b` before this documentation update. [Fork release](https://github.com/samirhvbr/strix/releases/tag/shvia-v1.2.47) · [Upstream release](https://github.com/usestrix/strix/releases/tag/v1.7.0) · [Fork #54](https://github.com/samirhvbr/strix/pull/54)

Resume with `items.json` and `checkpoints.json`. Refresh every known ID and start the next update query at `2026-11-05T12:12:03.418863+00:00`. Compare the overlapping PRs at their actual merge SHAs and recheck the published heads of #1684, #1685, #1686 and #1688. If #1688 completes pytest, inspect the per-test results and compare them with the known budget, pricing and runner fixes. Evaluate source imports on the fork separately from release decisions.
