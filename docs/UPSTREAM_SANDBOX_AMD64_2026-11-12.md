# Native amd64 sandbox acceptance, 2026-11-12

This targeted follow-up validates the existing 1.2.54 default on a native Linux amd64
GitHub-hosted `ubuntu-22.04` runner. It continues the
[original migration](UPSTREAM_SANDBOX_2026-11-12.md); its arm64-only release evidence
and earlier Docker timeout remain historical observations.

The [runtime job](https://github.com/samirhvbr/strix/actions/runs/43224996480/job/131205797615)
passed on attempt 1 using source `99d49d5843e9718372cfe59b3b9bf01d32c8bde8`.
The [complete CI workflow](https://github.com/samirhvbr/strix/actions/runs/43224996480)
also succeeded: all 16 jobs passed, including Python 3.12, 3.13 and 3.14 suites.
The image remains
`ghcr.io/samirhvbr/strix-sandbox:1.4.1-shvia-ac827485f0ca@sha256:c8d87cdefe834c9e7829fc571ceba84b57c0a583f4f0e61c3ef749f01c894f30`.
This is the published compatibility derivative with the NSS restart guard, not a
new image build. Its amd64 config is
`sha256:e72efc9fe87c6fa1d1dc89786c418d8c7703a120f5b6e3ec42e8e7c6a34d51e1`.

| Gate | Observed result |
| --- | --- |
| Native architecture | Linux host, Docker daemon and container reported x86_64; image and fixtures were amd64. No cross-architecture emulation was used. |
| Image identity | Exact published index/config, official parent layers plus one, and reviewed entrypoint SHA-256 matched. |
| User mapping | The real fork backend ran `pentester` as host UID 1001 with `/home/pentester`. |
| Restart and NSS | The same container reached readiness twice. Default CA and synthetic sentinel certificate hashes were identical before and after restart. |
| Network before/after restart | Owned TCP control worked; sandbox traffic was denied and IPv4 DROP counters rose from 1 to 2. IPv4/IPv6 DROP defaults, dropped capabilities and peer isolation of the control port were checked. |
| Mounts before/after restart | Root could not overwrite readonly source, `.git` metadata or protected run files. A writable sibling control succeeded and host fixture contents were checked. |
| Services before/after restart | Real Caido guest bootstrap over loopback and SDK PTY start/stdin/poll/exit succeeded. |
| Opengrep | Native version smoke returned 1.31.0. |
| Cleanup | No owned containers or fixture files remained; cleanup reported no errors. |

The downloaded artifact `sandbox-runtime-linux-amd64` (ID `13947941160`) was 4,154
bytes. Its ZIP SHA-256
`890b548ce8e4d0d58554f1ae07c14bce561f7e32355da796db0e04147a3304b2`
matched the GitHub artifact digest. The
[portable acceptance record](upstream-tracking/sandbox-amd64-acceptance-2026-11-12.json)
retains the runtime JSON, source/run/attempt, job steps and integrity witnesses so
continuity does not depend on temporary files or artifact retention.

## Repeat the acceptance

The versioned [harness](../scripts/sandbox_runtime_acceptance.py) and
[workflow](../.github/workflows/ci-sandbox-runtime.yml) use owned synthetic fixtures
through the real fork backend. The regular CI workflow has an optional manual input:

```sh
gh workflow run ci.yml -R samirhvbr/strix --ref master -f sandbox_runtime_acceptance=true
```

This checks the selected ref; the exact historical source is pinned above. The image,
config and entrypoint witnesses must be reviewed when testing a replacement. Check
the separate `sandbox-runtime / amd64` job and the overall workflow: the existing
`ci-passed` aggregate does not depend on this optional job.

## Limits and continuity

IPv4 denial was exercised; IPv6 firewall defaults were inspected without an IPv6
traffic probe. Opengrep was smoke-tested on amd64; the full offline rule-bundle scan
remains the separate arm64 evidence. No live model, external target, full interactive
UI session or production deployment was tested.

No application behavior, default image, version, tag or release artifact changed.
Version 1.2.54 remains released from `013fa614`; this follow-up adds test infrastructure
and documentation. The full upstream synchronization and broad-review inventory cursor
remain unchanged. Current validations supersede the amd64-pending limitation while
preserving prior per-item validation and release snapshots.

A later [native IPv6 and full-bundle follow-up](UPSTREAM_SANDBOX_AMD64_IPV6_OPENGREP_2026-11-12.md)
closed the two coverage gaps stated above. The statements in this report remain the
scope observed in run 43224996480; the new record preserves the later evidence separately.
