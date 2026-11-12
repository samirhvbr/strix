# Native amd64 IPv6 and full Opengrep acceptance, 2026-11-12

This follow-up closes the two explicitly untested scopes in the
[preceding native amd64 acceptance](UPSTREAM_SANDBOX_AMD64_2026-11-12.md).
The [native job](https://github.com/samirhvbr/strix/actions/runs/43230646471/job/131224745650)
passed on attempt 1 using source `9d9bfb6b94e244bab9389ef63482248c11d6034d`
on a GitHub-hosted Linux x86_64 runner. Host, Docker daemon, containers and the
Opengrep ELF64 binary were checked as native amd64.
The [complete workflow](https://github.com/samirhvbr/strix/actions/runs/43230646471)
also passed all 16 jobs, including Python 3.12, 3.13 and 3.14 suites.

The image is the existing published 1.4.1 compatibility derivative, index
`sha256:c8d87cdefe834c9e7829fc571ceba84b57c0a583f4f0e61c3ef749f01c894f30`,
config `sha256:e72efc9fe87c6fa1d1dc89786c418d8c7703a120f5b6e3ec42e8e7c6a34d51e1`.
Its default reference, application version 1.2.54 and release artifacts were not changed.

## Real IPv6 traffic

The real fork backend used a disposable internal dual-stack Docker bridge and unique
ULA subnet. Its policy authorized only the synthetic target's IPv6 address on TCP
18080; TCP 18081 remained denied. No extra firewall rule was installed by the harness.

Before and after restarting the same sandbox:

- An independent peer received the expected payload from both target ports.
- The sandbox received that payload through the authorized IPv6 endpoint, using the
  guard namespace's IPv6 address and a recorded route.
- The denied port timed out; it did not fail with a missing route or refused service.
  The IPv6 DROP counter increased by one packet and 80 bytes in each phase.

The existing IPv4, protected-mount, capability, Caido, PTY, NSS certificate and
same-container restart gates also passed. Every owned container, network and fixture
was removed, with no cleanup error.

## Complete offline rule bundle

The SAST check used the same image with `--network none`, no mounts or published ports,
and a `sleep` entrypoint. The separate runtime check above exercised the real entrypoint.
It scanned `/opt/opengrep-rules` in full with strict JSON output and `--taint-intrafile`.

| Measurement | Observed result |
| --- | --- |
| Opengrep | 1.31.0; ELF64 little-endian x86-64 (`e_machine=62`). |
| Bundle | 1,934 files, including 1,933 YAML files; catalog unchanged after scanning. |
| Rules | 2,006 loaded; 706 applicable Python/YAML rules applied. |
| Inputs | Seven synthetic files, all present in the scanned-path list. |
| Findings | Four exact rule/path/line matches: Python `eval(input())`, two mutable GitHub-owned action references and one mutable third-party action reference. |
| Negative controls | Clean Python, SHA-pinned action and local action produced no findings. |
| Completion | Exit 0, JSON `errors=[]`, elapsed 63.885 seconds within a 600-second scan limit. |

The catalog fingerprint is
`93221b8a0069ed0997f4af83f5c6e0a05a7b53d7dcbdeb8721afcc0f3539a821`.
The binary SHA-256 is
`dbbe3c2058dbe600c449a8ba22ea79a1643a6660658400090d0323a8d0d31147`.
All rules were loaded; seven files do not exercise every language, rule or taint flow.
Elapsed time is an observation, without a performance comparison to the earlier arm64 run.

## Evidence and continuity

Artifact `13950761882` (`sandbox-runtime-linux-amd64`, 115,856 bytes) matched GitHub's
SHA-256 `0dbf736da83a8b3e4c143b0326f9b71d88f0105db9ef9139b7004550f8f82ee3`.
The [portable record](upstream-tracking/sandbox-amd64-ipv6-opengrep-2026-11-12.json)
retains both result reports, exact findings, source/run/attempt, job steps, catalog
fingerprint, artifact/file hashes and cleanup evidence. Complete scanner JSON, stderr,
fixture hashes and the file catalog are also in the workflow artifact.

Repeat the optional native check with:

```sh
gh workflow run ci.yml -R samirhvbr/strix --ref master -f sandbox_runtime_acceptance=true
```

This tests the selected ref; the historical source is pinned above. The versioned
[runtime harness](../scripts/sandbox_runtime_acceptance.py),
[SAST harness](../scripts/sandbox_opengrep_acceptance.py) and
[workflow](../.github/workflows/ci-sandbox-runtime.yml) define the gates.

Earlier arm64 and initial amd64 reports remain historical evidence. Five current ledger
validations receive this extension; their previous validations and all implementation,
review, release and source mappings are preserved. Full upstream synchronization and
the broad-review inventory cursor remain unchanged. No live model, external target,
full interactive UI session or production deployment was tested.
