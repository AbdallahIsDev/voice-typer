# CI workflow notes (relocated long-form rationale)

Evidence tags (NU-*, IPD-*, S1/S4/S10/CRIT-*, TX-*, WR-*, XS-*, MIG-*,
C-CI-*) remain inline in `.github/workflows/*.yml`. This file holds the
expanded narrative that previously bloated those comments. Comment-only
history; YAML structure/behavior unchanged.

## Pinned action majors (C-CI-1 / C-CI-5)

Node 24 majors only. Do not downgrade:
`checkout@v5`, `setup-python@v7`, `setup-node@v7`, `cache@v5`,
`upload-artifact@v6`, `download-artifact@v6`,
`attest-build-provenance@v4`, `setup-uv@v7`, `rust-toolchain@v1`.
v5 upload/download-artifact and setup-uv@v6 still run Node 20.

## Nuitka pin (NU-105 / C-CI-6)

`nuitka==2.8.10` in BOTH Windows install steps. Nuitka <2.8 crashes on
numpy>=2.5 PEP 695 type aliases (#3469) and generic classes
(#3392/#3561). Never fix a build by downgrading numpy; bump Nuitka
forward.

## Windows aarch64 matrix (TX-40 / C-CI-4)

Matrix template is commented only. No public `windows-11-arm` runner;
`matrix` is unavailable in `jobs.<id>.if` (0s validation failure).
Uncomment requires aarch64 PYBS asset, `tauri.windows-aarch64.conf.json`,
arch-suffixed artifact names, and lockstep `tauri-build.yml` updates.
push/PR triggers stay disabled until Phase 0-W host validation.

## Signing gates (TX-23 / S1-CR-99 / CRIT-7 / C-CI-11)

- `sign=true` + missing secrets must hard-fail; `sign=false` skips.
- Secrets must map to job-level `env` (unusable in step `if:`).
- Sign sidecar, prewarm, native listener, NSIS, MSI, standalone host exe,
  runtime-pack worker, full offline installer when present.

## Other Windows-build invariants

- `timeout-minutes: 240` (C-CI-3): real build 90–110 min.
- Fail-fast gate order before cargo (C-CI-7): versions --check → drift
  pytest → stub generation → `--check-icons`.
- Nuitka torch exclusions (NU-106 / C-CI-8): never exclude
  `torch.utils.data.distributed` / `torch.export` / `torch._functorch`
  / `torch.testing` / `torch.package`; keep
  `--module-parameter=torch-disable-jit=no`.
- Keep `--include-package-data=voice_typer.server`,
  `--windows-console-mode=disable`, `--onefile-tempdir-spec` (IPD-1 /
  C-CI-9).
- Per-arch resource narrowing + post-build no-non-Windows-binaries
  assertion (XS-28 / C-CI-10).
- `CLCACHE_DISABLE: "1"` job-level only (S10-CC-1 / C-CI-12).
- Artifact names fixed (C-CI-13); new pack names may be added only with
  lockstep aggregator download updates.
- Sidecar smoke test: .NET Process + WaitFor(180000), never `& $exe`
  (C-CI-14; GUI-subsystem PE).
- `tauri-binaries.json` record+check + SLSA gate on
  `workflow_dispatch && sign==true` (TX-24 / C-CI-15).
- Branding Authenticode description from branding constant (C-BRAND-1).

## build.yml quality gates

- C-CI-2: legacy packaging jobs retired; this file keeps test/lint/
  branding/drift/audit gates only. Do not edit tauri-*-build.yml as a
  first-line fix.
- XS-25: test job timeout 60m (30m cancelled slow ubuntu legs and broke
  coverage upload).
- Schedule-only jobs skip when `github.event_name != 'schedule'` is
  inverted via the existing `if:` expressions.

## host-validation / arm-validation / mutation

- host-validation consumes source-only; does not touch tauri-*-build.yml
  evidence (C-CI-2/4/5/13).
- arm-validation smoke uses C-CI-14 .NET Process pattern under emulation.
- mutation.yml (XS-87) is local-only by design; gate stays documented
  rather than `if: false` re-introduction without review.
