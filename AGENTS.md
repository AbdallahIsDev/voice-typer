# Project-level agent instructions for voice-typer

## Branding — DO NOT HARDCODE APP NAME

**CRITICAL RULE:** Never replace `APP_NAME` usages with the hardcoded string
"Voice Typer" (or "VoiceTyper") anywhere in the codebase. Even though
`APP_NAME` currently resolves to "Voice Typer", the VARIABLE exists so the
app name can be changed in ONE place and propagate everywhere automatically.

If you are an AI agent and feel tempted to inline the value — **DON'T**.

- **Python:** `from voice_typer.server.branding import APP_NAME`
- **TypeScript (main):** `import { APP_NAME } from './branding'`
- **TypeScript (renderer):** `import { APP_NAME } from '../branding'`

This is enforced by `scripts/check_branding.py` in CI. Violations will fail
the build. The check is NOT optional and must NOT be disabled or bypassed.

Source of truth files (only these may contain the literal string):
1. `voice_typer/server/branding.py`
2. `voice_typer/client/src/main/branding.ts`
3. `voice_typer/client/src/renderer/src/branding.ts`

## Pinned Action Versions — DO NOT DOWNGRADE

All GitHub Actions are pinned to Node 24-compatible versions (see the comment
block at the top of `.github/workflows/build.yml`). Node 20 was deprecated
2025-09-19. Do not downgrade action versions (e.g. `actions/checkout@v5` back
to `@v4`) — it reintroduces Node 20 runtime deprecation warnings and the
`[DEP0040] punycode` DeprecationWarning.

## Tauri release workflows — DO NOT BREAK (50+ failed runs)

`.github/workflows/tauri-windows-build.yml` (plus `tauri-macos-build.yml`,
`tauri-linux-build.yml`, and the `tauri-build.yml` orchestrator) is the most
fragile CI in the repo: it failed **50+ times** before the current
configuration passed end-to-end (first full success 2026-08-11). Every
non-obvious decision inline in the file is the product of a failed run and
carries an evidence tag (NU-105, NU-106, IPD-1, S1-CR-99, S4-CR-25,
S10-CC-1, CRIT-7, TX-23, TX-24, TX-40, WR-17, XS-28, MIG-1.5).

**Do NOT edit these workflows as a first-line fix for a failing build.**
Diagnose the root cause; any genuinely required workflow change must be
validated by a full re-run and confirmed with the user. The rules below are
binding — full rationale for each is in `CONSTRAINTS.md` `C-CI-2`–`C-CI-15`:

- **Never cut `timeout-minutes: 240`** (C-CI-3) — real build takes 90-110
  min; it was raised twice after a 120-min ceiling canceled a build mid-way.
- **Never re-enable the aarch64 matrix leg, never use `matrix.*` in
  `jobs.<id>.if`, never uncomment push/PR triggers** (C-CI-4) — `matrix` is
  unavailable in job-level `if:` (0s validation failure on every push), no
  public `windows-11-arm` runner exists, and ADR-0020 §15 keeps releases
  manual-only until Phase 0-W host validation passes.
- **Keep every action on its Node-24 major** (C-CI-5):
  `checkout@v5`, `setup-python@v7`, `setup-node@v7`, `cache@v5`,
  `upload-artifact@v6`, `download-artifact@v6`, `attest-build-provenance@v4`,
  `setup-uv@v7`, `rust-toolchain@v1`. `upload-artifact@v5` /
  `download-artifact@v5` / `setup-uv@v6` still run Node 20 → deprecation
  warnings now, hard failure when Node 20 is removed (fall 2026).
- **Keep `nuitka==2.8.10` in BOTH install steps** (C-CI-6, NU-105) — Nuitka
  <2.8 crashes compiling numpy 2.5's PEP 695 type aliases; never fix a build
  by downgrading numpy below 2.5 — bump Nuitka forward.
- **Never remove/reorder the pre-build fail-fast gates** (C-CI-7):
  `sync_versions.py --check`, config-drift pytest, stub generation, and
  `--check-icons`. Stub generation MUST stay AFTER the drift pytest (its
  autouse fixture `--clean` deletes freshly generated stubs).
- **Never add `--nofollow-import-to` for `torch.utils.data.distributed` /
  `torch.export` / `torch._functorch` / `torch.testing` / `torch.package`;
  never remove `--module-parameter=torch-disable-jit=no`** (C-CI-8, NU-106) —
  unconditional imports break `import torch`, silently disabling Silero VAD
  in the shipped exe; disable-jit=no is required for `torch.jit.load`.
- **Never remove `--include-package-data=voice_typer.server`,
  `--windows-console-mode=disable`, or `--onefile-tempdir-spec`** (C-CI-9,
  IPD-1) — missing package data = FileNotFoundError at launch that builds
  fine in CI.
- **Never widen the Windows `bundle.resources` narrowing or drop
  `--target`/`--config tauri.windows-x86_64.conf.json`** (C-CI-10, XS-28) —
  loses the no-non-Windows-binaries assertion; base config hard-fails the
  resource copy and the installer would bloat 50-100 MB.
- **Never change the signing gates or drop any of the 4 signing steps**
  (C-CI-11, TX-23/S1-CR-99/CRIT-7) — `sign=true` + missing secrets must
  hard-fail; secrets must stay mapped to job-level env (unusable in step
  `if:`); MSI/native/standalone exe must be signed too (SmartScreen).
- **Keep `CLCACHE_DISABLE: "1"` at job level** (C-CI-12, S10-CC-1) —
  step-level does not propagate to the SCons subprocess; torch C compilation
  hangs indefinitely.
- **Never rename the artifact/binary names** (C-CI-13) — `tauri-build.yml`
  downloads `tauri-windows-installer` by literal; `mig18` tests grep the
  default binary names.
- **Never revert the sidecar smoke test to `& $exe --version`** (C-CI-14) —
  GUI-subsystem PEs must be launched via .NET Process + WaitForExit.
- **Never remove the tauri-binaries.json record/check gates or change the
  SLSA attestation gate** (C-CI-15, TX-24) — the manifest is the
  fail-closed integrity source at login; attestation fires on
  workflow_dispatch + sign=true.

Greppable anchors in the workflow file: `NU-105`, `NU-106`, `IPD-1`,
`S1-CR-99`, `S4-CR-25`, `S10-CC-1`, `CRIT-7`, `TX-23`, `TX-24`, `TX-40`,
`WR-17`, `XS-28`, `MIG-1.5` — each names the session/finding that produced
the surrounding code.

## npm Overrides — DO NOT REMOVE

`voice_typer/client/package.json` contains `overrides` that force-upgrade
deprecated transitive deps (`@electron/asar`, `@electron/get`,
`@hono/node-server`). These eliminate deprecation warnings and security
vulnerabilities. Do not remove or downgrade them. See the `//overrides_note`
comment in package.json for full rationale.

## Critical contracts (read before editing IPC / security surfaces)

Three cross-file contracts are NON-NEGOTIABLE. Violating any of them
silently breaks parity tests, opens security holes, or both. The full
rationale lives in `CONTRIBUTING.md`:

- **§6.3 Security** (`CONTRIBUTING.md` §6.3) — non-negotiable rules:
  the `set_config` SEC-002 allowlist (`IPC_CONFIG_ALLOWLIST` in
  `voice_typer/server/config_validators/__init__.py`), redaction (SEC-003), the
  per-renderer IPC rate limiter (SEC-019), and the TCP auth token
  boundary. Do NOT add fields to `set_config` outside the SEC-002
  allowlist; do NOT bypass the renderer→main→backend allowlist chain.
- **§6.4 IPC command parity** (`CONTRIBUTING.md` §6.4) — the THREE
  allowlists must stay in lockstep:
  1. Server: `_COMMAND_REGISTRY` in `voice_typer/server/ipc/registry.py`.
  2. Electron main: `ALLOWED_COMMANDS` in
     `voice_typer/client/src/main/allowed-commands.ts`.
  3. Renderer types: `PythonRequest` / `PythonPushEvent` unions in
     `voice_typer/client/src/renderer/src/types/ipc.ts`.
  The parity test
  `tests/test_electron_ipc_and_build.py::test_allowlist_matches_server_commands`
  slices the literal `ALLOWED_COMMANDS = new Set([` substring out of
  `allowed-commands.ts` and diffs it against the server registry — do
  NOT reformat that Set's declaration (the test relies on the exact
  `new Set([` opening and `]);` closing).
- **Branding** (see section above) — `APP_NAME` must never be inlined.

When in doubt, link to the relevant `CONTRIBUTING.md` section in your
PR description rather than paraphrasing it.

## Dev loop

From the repo root (the directory containing this `AGENTS.md` file):

```bash
# Python backend — one-time env setup
uv venv
source .venv/bin/activate
# Full dev+test extras (pytest, ruff, mypy, pre-commit) + the hash-pinned
# lock. NOTE: requirements-lock.txt is BASE-deps only (it does NOT contain
# pytest/test extras), so install both — the pre-push hook's pytest needs
# the [test] extra present in the venv.
uv pip install -e ".[test,dev]" -r requirements-lock.txt

# Renderer + main process — one-time install
cd voice_typer/client && npm install && cd -

# Run both dev servers (Electron + Python backend hot-reload)
npm run dev
# (equivalently: cd voice_typer/client && npm run dev)

# Python tests
pytest

# Renderer + main TS tests
cd voice_typer/client && npx vitest run
```

`npm run dev` boots the Electron main process, the React renderer
(Vite HMR), and the Python backend (with `--reload`). The first
`get_config` round-trip from the renderer establishes the IPC
bridge — if you see a "Lost connection" screen for >5s on cold
start, the Python backend is still booting (model warmup).

## Test patterns

Reusable test fixtures and helpers live in two places — prefer them
over reinventing mocks:

- **Python IPC fixtures:** `tests/fixtures/ipc_test_helpers.py` —
  exports `make_ipc_server_with_fakes()` (returns
  `(ipc_server, fake_app, fake_service)` wired together with all
  heavy imports mocked), plus assertion helpers for response
  envelopes. The `mock_heavy_imports` autouse fixture (see
  `tests/conftest.py:434`) stubs `pystray`, `pynput`, `sounddevice`,
  `whisper`, and other platform/audio deps so tests run headless on
  Linux CI without those packages installed.
- **Renderer test helpers:** `voice_typer/client/src/renderer/src/__tests__/helpers/renderApp.tsx`
  — exports `renderApp()` which mounts `<App />` with the
  Zustand store, i18n provider, and `window.python` mock all wired
  up (so component tests don't have to re-create the React tree
  boilerplate). Sibling files: `fixtures.ts` (config snapshots),
  `mocks.tsx` (mock components / window.python bridge).

When adding a new test that needs the IPC server, prefer
`make_ipc_server_with_fakes()` over constructing an `IpcServer`
directly — the helper sets up the auth token, fake app, and fake
service in one call.

## Tag convention (inline code comments)

Long-lived cross-cutting concerns are tagged inline with a prefix
so they're greppable across the codebase. Use these prefixes when
adding new tags (do NOT invent new prefixes — add to this list
instead):

- `SEC-*` — security boundary / hardening. Examples: `SEC-002`
  (set_config allowlist), `SEC-018` (TCP auth token), `SEC-019`
  (renderer IPC allowlist), `SEC-026` (sandboxed bubble preload).
- `RACE-*` — concurrency / ordering invariant. Examples:
  `RACE-001` (download-progress event ordering), `RACE-002`
  (heartbeat vs. shutdown ordering), `RACE-011` (launcher
  bundle-completeness probe — a missing renderer/preload bundle lets
  `electron .` linger as a blank hidden zombie that holds the
  single-instance lock and kills every later launch).
- `PERF-*` — performance-sensitive path where a "trivial" change
  could regress hot-loop or memory. Examples: `PERF-005`
  (relaunch_ack fast-path), `PERF-006` (audio ring buffer zero-copy),
  `PERF-007` (test suite speed: vitest pool/isolate, pytest
  import-mode/xdist/cov).

When you add a new tag, update this list AND link the tag to the
relevant `CONTRIBUTING.md` section (or, for tags not covered by
CONTRIBUTING.md, to a brief rationale comment at the tag's first
occurrence).
