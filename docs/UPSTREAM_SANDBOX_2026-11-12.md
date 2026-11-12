# Selective sandbox and TUI integration, 2026-11-12

This delivery continues the tracking merged in [fork #62](https://github.com/samirhvbr/strix/pull/62) and carries the startup backport from [fork #61](https://github.com/samirhvbr/strix/pull/61) onto master baseline 5483b42b6c1dbdec5854f45616255895d3a1fd13.

## Sources and adaptations

- TUI startup uses upstream 29fe9aaaa86a73168e2fd1d7ef24ea70bb01c1a7 and 01119158f8f50027591cbae73f37cf0a2fea644d as fork e9b6ef728bda0c5d7ea4b2072b5ab24176ccef5e and ac3c42b544a94574477cf7715b50a960250a0027. Source launches get time to compile, and Windows TCP startup detects an early child exit. The fork shutdown handling is preserved. [Upstream #1647](https://github.com/usestrix/strix/pull/1647)
- Opengrep source comes from cb581bf93dd3ed49a59782cebb6ad829857adfe4. Runtime tool builds come from 48d93cd3d9bd6fd320f44dc4534fbcdb62005027 and 9798ecc404bff228629dba7b60879bc9a498d8fe. Fork 160181c80128f9ccd12d4909a9acada25a0d5f0a adapts these sources, keeps CSPT guidance and the fork installer, and prepares the compatibility image. [Upstream #1625](https://github.com/usestrix/strix/pull/1625) · [Upstream #1729](https://github.com/usestrix/strix/pull/1729)
- The selected sandbox is ghcr.io/samirhvbr/strix-sandbox:1.4.1-shvia-ac827485f0ca, pinned to index sha256:c8d87cdefe834c9e7829fc571ceba84b57c0a583f4f0e61c3ef749f01c894f30. Both the installer and runtime select that exact digest.
- containers/Dockerfile.sandbox-compat starts from official index sha256:c8e70302ba4a69b678b4001c909b134d8d0b84625d7b23feed59ece02672c1d2 and copies the existing fork entrypoint. The resulting rootfs preserves all parent layers and adds one. Entrypoint SHA-256 is cbdb5ca54544c2f7989a4981133e5faa5bfe4f65ee15d67e106082e95029b818. Publication adds OCI source labels pointing to the fork. [Image build](https://github.com/samirhvbr/strix/actions/runs/43207006843)

## Acceptance before the default changed

| Check | Observed result |
| --- | --- |
| Official 1.4.1 first start | Passed on arm64. |
| Official same-container restart | Failed at certutil -N, repeating password/input errors before a second ready marker. Reproduced with and without an added test certificate. |
| Published compatibility image restart | Passed with the same container ID. Both the default CA and a synthetic sentinel certificate survived. |
| Registry | Anonymous HTTP 200 for the index, Linux amd64/arm64 manifests and configs. SHA-256 checked against response bytes. |
| Real fork backend | Default-deny blocked an otherwise reachable owned TCP target. IPv4/IPv6 firewall defaults were DROP. NET_ADMIN, NET_RAW and SYS_ADMIN were dropped. |
| Mounts | Root could not write to readonly source, metadata .git or protected run paths. A writable sibling control succeeded. |
| Caido and PTY | Guest bootstrap worked through loopback, a peer could not reach the control port, and SDK PTY start, stdin, poll and exit passed. |
| Opengrep 1.31.0 | Python positive/clean controls passed. GitHub Actions tag and branch controls matched the fork rule. SHA40, local and other-owner controls were excluded by that rule. |
| Full offline rule bundle | Exit 0, 2,006 rules loaded, 706 applied to seven synthetic files, errors=[], expected JSON paths and three expected findings. Completed in 169.247 seconds after the first 60-second bound expired. |
| Source checks | 150 configuration/runtime tests and 115 CSPT, credential and inference tests passed. Ruff, Mypy and Bandit passed. Pyright had 1,287 diagnostics on both master baseline and candidate, with zero added or removed. |

One published-image guarded start timed out after 60 seconds in Docker before the entrypoint ran. Cleanup also exposed a temporary-directory removal error in the test harness. The next execution passed with the same limits and all gates. The cause of the Docker timeout remains unestablished. Both attempts are retained in the structured integration evidence.

All temporary containers were removed. Image execution covered Linux/arm64 on Docker Desktop. amd64 manifests and configs were verified through the registry. Native amd64 runtime, live models and external targets are outside this acceptance.

## Reproduce the restart

The dated [restart script](upstream-tracking/sandbox-restart-2026-11-12.sh) uses the immutable image, no host mounts or published ports, a network-isolated disposable container and bounded readiness/log reads. It imports a synthetic certificate, stops and starts the same container, and asserts both CA nicknames survive.

~~~sh
docker pull ghcr.io/samirhvbr/strix-sandbox:1.4.1-shvia-ac827485f0ca@sha256:c8d87cdefe834c9e7829fc571ceba84b57c0a583f4f0e61c3ef749f01c894f30
sh docs/upstream-tracking/sandbox-restart-2026-11-12.sh
~~~

The full upstream integration checkpoint is 6b7ae2ebe4d47037a927aade23265ff4e4f27e84. The selected sources and adaptations above are recorded independently in the per-item ledger.

## Master delivery

[PR #63](https://github.com/samirhvbr/strix/pull/63) merged at 1a55f43ec5b3199d2ebf7c2a71d8bfa6f196af3d. Its tree equals the validated head 026f3f54ad6976105a55608bff0451e89917203d. All 15 checks passed, including the complete Python 3.12, 3.13 and 3.14 suites. [CI run](https://github.com/samirhvbr/strix/actions/runs/43209192193)
