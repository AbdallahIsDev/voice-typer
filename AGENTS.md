Default response style: caveman full.
Use terse, token-efficient replies by default: no filler, no pleasantries, fragments OK, technical terms exact.
Keep code blocks, commands, commit messages, PR text, and exact error quotes normal and precise.
Use fuller clarity for security warnings, irreversible actions, or multi-step instructions where compression could confuse.
Exit this style only when the user says `normal mode` or `stop caveman`.

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

# CONSTRAINTS.md — Hard "Don'ts" (HIGHEST PRIORITY)

> This file is the **single source of truth for things the agents must NOT do**, even when those things would "improve" the project. Every rule here is a HARD CONSTRAINT that overrides:
> - `PROMPT.md` (cloud agent) — including `## Current Tasks`, `## Execution TODOs`, `review.md` entries, and any "would-improve" idea.
> - `MERGE-SESSIONS.md` (cloud merge agent) — including "the better-implemented version wins".
> - `VERIFY.md` (local verifier) — the verifier flags any change that violates a rule here.
> - `TRIVIAL-FIXES.md`, `SERIOUS-FIXES.md`, `PUSH.md` (local fixer / documenter / committer) — all respect these rules.
> - Every sub-agent launched by the orchestrator — the orchestrator MUST embed the relevant rules into each sub-agent's prompt.
>
> If a `review.md` task, a sub-agent finding, or an "improvement" idea conflicts with a rule here, the agent MUST SKIP the work and record the skip in `worklog.md` with the conflicting rule cited. CONSTRAINTS.md is the ONLY file that can forbid work that would otherwise look like an improvement.
>
> **The user is the only one who can edit this file.** Agents must NOT add, modify, or delete rules here. If an agent believes a rule should be added or removed, it should RECOMMEND the change in `worklog.md` (or in the chat report) and let the user decide.

---

## Constraint categories

Constraints below are organized by category. Each constraint has:
- **ID** (e.g. `C-TRAY-1`) — for citing in `worklog.md` skip-reasons.
- **Rule** — the prohibition, stated clearly.
- **Rationale** — why this rule exists (so the agent understands it's not arbitrary).
- **Applies to** — which agents / modes the constraint affects.

---

## Category: Tray Menu & Application Close

```
C-TRAY-1
Rule: Do NOT add a "Repaste Last transcription" button to the tray menu.
Rationale: The tray menu is intentionally minimal;
Applies to: All agents, all modes.
```

---

## Category: UI & UX


---

## Category: Localization & i18n

```
C-I18N-1
Rule: Do NOT add any user-facing text (UI labels, buttons, tooltips, dialogs, notifications, tray menu, errors) without adding it to ALL locale files. User-visible strings MUST go through the i18n layer (`useT()` / `t()` in the renderer, `mainT()` in the main process) and the new key MUST be added to every locale under `voice_typer/client/src/renderer/src/i18n/translations/`: `en.json`, `ar.json`, `de.json`, `es.json`, `fr.json`, `hi.json`, `ru.json`, `zh.json`. Adding the key to `en.json` only, or to a subset of locales, is a violation — every locale must contain the key (see `SUPPORTED_LOCALES` in `i18n/locale.ts`).
Rationale: The app is multilingual (8 locales). A key missing from a locale file means users of that language fall back to English (or see raw keys) — a silent downgrade that is invisible when only English is tested.
Applies to: All agents, all modes.
```

```
C-I18N-2
Rule: Do NOT copy the English source text verbatim into a non-English locale file. Every non-English locale value MUST be genuinely translated into that language (e.g. `ar.json` values must be Arabic, `fr.json` values must be French — not English text pasted under the key). If the agent cannot translate reliably, it MUST translate via a translation tool/LLM or record the entry as pending in `worklog.md` (with the key name) so a later session completes it — never ship untranslated English inside a non-English locale.
Rationale: Hardcoded English inside e.g. `ar.json` defeats the multilingual promise: Arabic users see English strings. This is the #1 observed i18n downgrade — the key exists in every locale file, so missing-key tooling won't catch it, only the translation itself is wrong.
Applies to: All agents, all modes.
```

---

## Category: Branding

```
C-BRAND-1
Rule: Do NOT hardcode the app-name display string anywhere — always use the dynamic branding constant: Python `APP_NAME` (`voice_typer/server/branding.py`), TS main `APP_NAME` (`src/main/branding.ts`), TS renderer `APP_NAME` (`src/renderer/src/branding.ts`), Rust `crate::branding::APP_NAME`. Locale files (BOTH `renderer/src/i18n/translations/*.json` AND `main/i18n/locales/*.json`) MUST use the `{appName}` placeholder token (runtime-substituted, as `_withAppName` in `main/i18n.ts` already does) — never a literal brand string, not even in `en.json`. Prose comments describing the app must also avoid the literal brand. This does NOT apply to internal identifiers (types like `VoiceTyperConfig`, mutex/binary names like `VoiceTyperSingleInstance` / `VoiceTyper.exe`) — those are OS/API identifiers, not the user-facing brand, and must not be renamed.
Rationale: An agent hardcoded the brand inside locale files (dozens of literal strings across all 8 `i18n/translations/*.json`) plus crash-dialog titles, HTML `<title>` tags, and backend error messages. `scripts/check_branding.py` (BRAND-001) deliberately EXEMPTS renderer translations and comment lines, so those literals bypass CI enforcement — a future product rename becomes a hundreds-of-strings edit instead of a one-constant change. The `{appName}` placeholder pattern already exists in main-process locales; renderer locales must adopt the same pattern.
Applies to: All agents, all modes. Enforced in CI by `scripts/check_branding.py` for non-locale, non-comment code.
```

---

## Category: IPC & Command Surface


---

## Category: Architecture & Module Boundaries

```
C-ARCH-1
Rule: `src-tauri/src/main.rs` MUST stay wiring-only (≤ ~300 lines). Do NOT add implementation logic to `main.rs` — even if a task asks for it. Logic goes in focused modules under `src-tauri/src/`.
Rationale: Rule 19 in PROMPT.md; prevents the 2277-line spaghetti regression.
Applies to: All agents, all modes.
```

---

## Category: Cross-Platform Behavior


---

## Category: Dependencies & Supply Chain


---

## Category: CI/CD & Build Pipeline

```
C-CI-1
Rule: Do NOT unpin GitHub Actions versions. All actions must be pinned to specific Node-24-runtime versions (see the header of `build.yml`). Unpinning introduces supply-chain risk via tag re-pointing.
Rationale: Security — pinned versions prevent a compromised action update from silently breaking CI or exfiltrating secrets.
Applies to: All agents, all modes. Especially relevant to IMPROVE mode targeting Group 6 (Testing & CI).
```

```
C-CI-2
Rule: Do NOT edit `.github/workflows/tauri-windows-build.yml`, `.github/workflows/tauri-macos-build.yml`, `.github/workflows/tauri-linux-build.yml`, or the `.github/workflows/tauri-build.yml` orchestrator as a first-line fix for a failing build. Diagnose the root cause first. Any workflow change that is genuinely required must be validated by a full re-run of the affected job and confirmed with the user before it is kept.
Rationale: This workflow is the most fragile piece of CI in the repo — it failed 50+ times before the current configuration passed end-to-end (first success 2026-08-11). Nearly every past failure was caused by an agent "fixing" the workflow (bumping a pin, adding/removing a Nuitka flag, reordering gates, changing the timeout) without understanding the accumulated, evidence-documented constraints inline in the file. Every non-obvious decision in the file carries an inline tag (NU-105, NU-106, IPD-1, S1-CR-99, S4-CR-25, S10-CC-1, CRIT-7, TX-23, TX-24, TX-40, WR-17, XS-28, MIG-1.5) documenting the failed attempt that produced it.
Applies to: All agents, all modes, all sub-agents.
```

```
C-CI-3
Rule: Do NOT reduce `timeout-minutes: 240` on the `tauri-windows-build` job, and do NOT "fix" a job timeout by editing the workflow file.
Rationale: The real build takes 90-110 min (Nuitka C compilation + prewarm + cargo tauri build + signing). The timeout was raised 90→120 (2026-07-27) → 240 (2026-07-28) after a real run hit the 120-min ceiling and was canceled mid-build: the sidecar was built but the job died before prewarm + tauri + signing + upload. 240 min provides ~2h of buffer for timestamp-server retries during signing, flaky upload retries, and dependency bumps that invalidate the Nuitka ccache. If a build hits 240 min, the bottleneck is C compilation — reduce it with `--nofollow-import-to` exclusions or a larger runner, never by cutting the timeout.
Applies to: All agents, all modes, all sub-agents.
```

```
C-CI-4
Rule: Do NOT re-enable the aarch64 matrix leg in `tauri-windows-build.yml` (it exists only as a commented template in the `strategy.matrix` block), do NOT add any `matrix.*` reference to a `jobs.<id>.if` condition anywhere in the workflow, and do NOT uncomment the `push:` / `pull_request:` triggers.
Rationale: (1) The `matrix` context is NOT available in `jobs.<id>.if` — GitHub rejects the workflow file at validation time with "Unrecognized named-value: 'matrix'", failing every push in 0s. (2) GitHub does not ship a public `windows-11-arm` runner (as of 2026-08), so an active aarch64 leg could never be scheduled and has no valid gate to skip it. (3) push/PR triggers must stay disabled until Phase 0-W passes on a real Windows host (ADR-0020 §15: manual dispatch only, no auto-update). Uncommenting requires: a `tauri.windows-aarch64.conf.json`, a PYBS release with an aarch64-pc-windows-msvc asset, arch-suffixed artifact names, AND tauri-build.yml's download steps updated in lockstep — all documented in the TX-40 GATE STATUS block at the top of the file.
Applies to: All agents, all modes, all sub-agents.
```

```
C-CI-5
Rule: Do NOT downgrade — and do NOT "upgrade" to a wrong major of — any pinned GitHub Action in the Tauri workflows below its Node-24-runtime version: `actions/checkout@v5`, `actions/setup-python@v7`, `actions/setup-node@v7`, `actions/cache@v5`, `actions/upload-artifact@v6`, `actions/download-artifact@v6`, `actions/attest-build-provenance@v4`, `astral-sh/setup-uv@v7`, `dtolnay/rust-toolchain@v1`. In particular `actions/upload-artifact@v5` / `actions/download-artifact@v5` / `astral-sh/setup-uv@v6` still run Node.js 20 — they produce deprecation warnings now and will HARD-FAIL when GitHub removes Node 20 from hosted runners (fall 2026). When bumping an action, verify the release notes declare `runs.using: node24` before pinning the new major.
Rationale: Verified by an actual run (2026-08-11): with upload-artifact@v5 + setup-uv@v6 pinned, GitHub emitted "Node.js 20 is deprecated… being forced to run on Node.js 24". The repo's old header comments incorrectly claimed those majors were Node 24. The Node 24 majors are upload-artifact@v6+ / download-artifact@v6+ (released 2025-12-12; requires runner ≥2.327.1 — fine on hosted runners) and setup-uv@v7+ (released 2025-10-07; removed the `server-url` input, which no workflow here uses). This supersedes the earlier (incorrect) comments in the workflow headers.
Applies to: All agents, all modes, all sub-agents.
```

```
C-CI-6
Rule: Do NOT change the `nuitka==2.8.10` pin — it must stay in BOTH install steps of `tauri-windows-build.yml` (the uv dev-env step AND the PYBS frozen-env step). Do NOT "fix" a build failure by downgrading `numpy` below 2.5 in `requirements-lock.txt` instead.
Rationale: Nuitka <2.8.0 CRASHES compiling numpy>=2.5: numpy 2.5 ships PEP 695 type-generic aliases (`type NDArray[ScalarT: np.generic] = ...` in numpy/_typing/_array_like.py) which trip Nuitka 2.5.x's `buildTypeAliasNode assert not node.type_params` (Nuitka issue #3469; generic PEP 695 classes also segfaulted — #3392/#3561). The 2.8 line is the first release supporting both. If a future build fails here, bump the Nuitka pin FORWARD — never downgrade numpy. (Tag: NU-105, documented inline at both install steps.)
Applies to: All agents, all modes, all sub-agents.
```

```
C-CI-7
Rule: Do NOT remove, reorder, or bypass the fail-fast gates in `tauri-windows-build.yml` that run BEFORE `cargo tauri build`: (1) `scripts/build/sync_versions.py --check` (version lockstep across pyproject.toml / package.json / tauri.conf.json / Cargo.toml); (2) the config-drift pytest set (`tests/tauri/test_bundle_identifier_parity.py`, `test_gen_tauri_icons_stub.py::test_tauri_conf_icon_list_matches_tracked_icons`, `::test_per_arch_configs_do_not_override_bundle_icon`, `test_config_script_drift.py`); (3) stub generation (`python scripts/gen_tauri_icons_stub.py --check || python scripts/gen_tauri_icons_stub.py`); (4) `python scripts/gen_tauri_icons_stub.py --check-icons`. The stub-generation step MUST stay AFTER the drift pytest — the icons module's autouse fixture runs `--clean`, which deletes freshly-generated STUB files (real binaries are preserved by the `_is_stub_file` heuristic); reversing the order silently deletes the stubs `cargo tauri build` needs.
Rationale: These gates convert 30-60 min build failures into <1 min failures (identifier↔appId parity, bundle.icon↔git lockstep, config↔script registry pairs, version lockstep, icon validity). Removing or reordering them re-exposes the drift regressions that caused long, expensive failed runs.
Applies to: All agents, all modes, all sub-agents.
```

```
C-CI-8
Rule: Do NOT add `--nofollow-import-to` for `torch.utils.data.distributed`, `torch.export`, `torch._functorch`, `torch.testing`, or `torch.package` to the sidecar Nuitka invocation, and do NOT remove `--module-parameter=torch-disable-jit=no`.
Rationale: torch 2.13 imports those modules UNCONDITIONALLY at `import torch` time (torch/utils/data/__init__.py:32, torch/__init__.py:2869/:2324, torch/_jit_internal.py:47); excluding any of them makes `import torch` raise ModuleNotFoundError inside the frozen exe. `vad.py` catches that as ImportError and SILENTLY DISABLES Silero VAD in the shipped binary (verified on-host with a minimal frozen probe reproducing the exact traceback). `torch-disable-jit=no` is REQUIRED because Nuitka's torch plugin disables torch.jit by default in standalone mode, and VAD loads the bundled model via `torch.jit.load(silero_vad.jit)` — without the flag VAD fails with "module 'torch' has no attribute 'jit'" and silently degrades to the RMS fallback. The ONLY safe exclusions are the lazily-imported modules already listed in the file (`torch._dynamo`, `torch._inductor`, `torch.onnx`, `torch.utils.benchmark`, `transformers`, `scipy.*`, `psutil._ps*`, `sympy`, `mpmath`, `pytest`, `PIL.*` non-UI modules). (Tag: NU-106.)
Applies to: All agents, all modes, all sub-agents.
```

```
C-CI-9
Rule: Do NOT remove `--include-package-data=voice_typer.server` from EITHER Nuitka invocation (sidecar AND prewarm), and do NOT remove `--windows-console-mode=disable` or `--onefile-tempdir-spec="..."` from either invocation.
Rationale: The frozen sidecar reads package data at import time (`voice_typer/server/hotkey_reserved.json` via a __file__-relative path in config_validators/hotkey.py, plus corrections.json, model_hashes.json, native/binaries.json, silero_vad.jit). Without `--include-package-data` the onefile payload is missing them and the exe crashes on launch with FileNotFoundError — even though it BUILDS fine, so no CI existence check catches it (IPD-1). The console-mode flag is what makes the sidecar a GUI-subsystem PE (the smoke-test step depends on that behavior); the tempdir spec keeps onefile extraction in a predictable per-user cache dir instead of %TEMP%.
Applies to: All agents, all modes, all sub-agents.
```

```
C-CI-10
Rule: Do NOT remove the "Post-build assertion — bundle has no non-Windows binaries" step, do NOT widen `src-tauri/tauri.windows-x86_64.conf.json` `bundle.resources` beyond Windows-only files, and do NOT remove `--target <matrix.target>` / `--config <matrix.tauri_config>` from the `cargo tauri build` step.
Rationale: The per-arch config narrows the base `tauri.conf.json`'s all-platform `bundle.resources` superset. If the narrowing is lost (or `--config` silently stops being applied), the NSIS installer bundles ~5 unnecessary prewarm binaries + 2 native key-listeners from macOS/Linux, bloating it by ~50-100 MB. The assertion fails the build if any forbidden non-Windows binary appears in the bundle directory. Building against the base config hard-fails at the tauri-build resource-copy step on a Windows host because the macOS/Linux-only files don't exist here. (Tag: XS-28.)
Applies to: All agents, all modes, all sub-agents.
```

```
C-CI-11
Rule: Do NOT change the code-signing gates in `tauri-windows-build.yml`: `sign=true` + missing secrets MUST hard-fail the build; `sign=false` MUST skip signing even when secrets exist. Do NOT drop or merge any of the four signing steps (sidecar + prewarm + native listener; NSIS; MSI; standalone `voice-typer-tauri.exe`). Do NOT remove the job-level `env:` mapping of `WIN_CSC_LINK` / `WIN_CSC_KEY_PASSWORD`, and do NOT replace it with a `secrets.*` reference inside a step `if:` condition.
Rationale: TX-23 — a misconfigured release must not silently ship unsigned (that's why sign=true + missing secrets hard-fails). S1-CR-99 — before the fix, the native listener, the MSI, and the standalone exe shipped UNSIGNED inside a signed installer; SmartScreen on Windows 11 flags unsigned binaries inside a signed installer ("Windows protected your PC" on first launch) and MSI/standalone users hit it on every install/launch. CRIT-7 — the `secrets` context is NOT populated in step `if:` conditions, so a gate on `secrets.X != ''` NEVER matches and signing is silently skipped; secrets must be mapped to job-level env first (empty on PR/fork builds → steps skip).
Applies to: All agents, all modes, all sub-agents.
```

```
C-CI-12
Rule: Do NOT remove `CLCACHE_DISABLE: "1"` from the job-level `env:` block of the `tauri-windows-build` job, and do NOT move it into a step-level `$env:CLCACHE_DISABLE = "1"` assignment only.
Rationale: S10-CC-1 — a step-level env var does NOT propagate to the Nuitka/SCons C compiler subprocess; with clcache enabled, torch module C compilation hangs indefinitely and the job times out (~90 min wasted). Job-level env is inherited by every subprocess and fixes the hang.
Applies to: All agents, all modes, all sub-agents.
```

```
C-CI-13
Rule: Do NOT rename the artifact names produced by `tauri-windows-build.yml` (`tauri-windows-installer`, `VoiceTyper-Tauri-MSI`, `VoiceTyper-Tauri-Sidecar-Binaries`, `VoiceTyper-Tauri-SHA256SUMS`, `tauri-binaries-manifest-windows`), and do NOT change the default binary filenames (`python-sidecar-<triple>.exe`, `prewarm-<triple>.exe`, `windows-key-listener.exe`).
Rationale: `tauri-build.yml`'s aggregate job downloads `name: tauri-windows-installer` by exact literal, and `tests/tauri/mig18/test_windows_signing.py` greps the default binary names — renaming breaks aggregation and/or the signing tests. If the aarch64 leg is ever enabled, arch-suffix the artifact names AND update tauri-build.yml's download steps in the same commit.
Applies to: All agents, all modes, all sub-agents.
```

```
C-CI-14
Rule: Do NOT revert the sidecar smoke-test step to a naive `$output = & $sidecar --version 2>&1` invocation. The launch MUST use .NET `ProcessStartInfo` + `Process.WaitForExit(180000)` with redirected stdout/stderr and a hard 180 s kill.
Rationale: The sidecar is frozen with `--windows-console-mode=disable`, making it a GUI-subsystem PE. PowerShell 7 does NOT wait for GUI-subsystem processes launched with `&` and never sets `$LASTEXITCODE` for them — the naive pattern yields empty output + a null exit code and ALWAYS fails the gate (reproduced with a minimal Nuitka onefile exe on pwsh 7.x). The .NET Process pattern is the only reliable way to capture exit code + stdout for GUI-subsystem binaries; the 180 s wait covers onefile bootloader extraction of the ~100 MB payload before argparse exits.
Applies to: All agents, all modes, all sub-agents.
```

```
C-CI-15
Rule: Do NOT remove the tauri-binaries.json recording + integrity gates in `tauri-windows-build.yml` (`update_tauri_manifests.py --triple <triple>` then `--check --triple <triple>`, plus the manifest artifact upload), and do NOT change the SLSA attestation gate (`github.event_name == 'workflow_dispatch' && inputs.sign == 'true'`).
Rationale: `tauri-binaries.json` is the integrity manifest the autostart launcher verifies fail-closed at login; the `--check` gate aborts the run on an empty/malformed hash so a bad manifest can never ship. The attestation gate must stay on workflow_dispatch + sign=true — the old `startsWith(github.ref, 'refs/tags/v')` gate never fired because the tag-push trigger is commented out (TX-24).
Applies to: All agents, all modes, all sub-agents.
```

---

## Category: Data & Privacy

```
C-DATA-1
Rule: Do NOT add network calls to the production code path UNLESS they fall into an explicitly allowed category: (1) cloud transcription / LLM providers the USER has configured and consented to (openai / groq / deepgram / custom `cloud_api_url` — see `cloud_engines.py` / `llm_polish.py`); (2) auto-update — "Check for Updates" / silent update check against the GitHub API (see `docs/auto-update-feature.md`); (3) model downloads (see ADR-0005, `docs/adr/0009-audio-filter-chain-architecture.md`). Anything NOT user-configured and user-initiated — telemetry, analytics, tracking, phone-home, or any other unsolicited egress — remains forbidden.
Rationale: The original wording ("no network call ever") predates cloud ASR/LLM engines and the auto-update feature, so agents applied it as a blanket offline ban and downgraded legitimate user-initiated features (e.g. "Check for Updates" CSP, cloud provider calls). The product promise is "no unsolicited phone-home", NOT "no network access ever". Agents that previously skipped, removed, or reworked network functionality citing the old wording MUST re-audit that work (search `worklog.md` / `review.md` for C-DATA-1 skips) and restore or improve anything that was downgraded.
Applies to: All agents, all modes.
```

---

## Category: Testing & Baselines

```
C-TEST-1
Rule: Do NOT revert the Vitest `pool: "threads"` setting in `voice_typer/client/vitest.config.ts`. The fork pool (default) creates a new child process per test file (~200ms overhead × 237 files = ~47s wasted). Threads share memory and eliminate this overhead, cutting Vitest suite time by ~2.5x (measured: 66s → 25s).
Rationale: On 2026-08-02, `isolate: false` was also tested alongside `pool: "threads"` but was reverted — it caused 22 additional test failures from Zustand store / localStorage mock state leaking between test files. The `pool: "threads"` change alone is safe and provides the bulk of the speedup. Do NOT remove or change `pool` back to the default ("forks").
Applies to: All agents, all modes. Especially relevant to IMPROVE mode targeting Group 6 (Testing & CI).
```

```
C-TEST-2
Rule: Do NOT remove `--import-mode=importlib` from `[tool.pytest.ini_options].addopts` in `pyproject.toml`. The default `prepend` import mode copies every test file to a temp directory and adjusts `sys.path` per file, adding ~1-2s of I/O overhead at collection time on a 544-file suite. `importlib` mode uses `importlib.import_module()` directly — faster and avoids subtle `__file__` / `__name__` mismatches.
Rationale: Added as part of PERF-007 test suite optimization on 2026-08-02. Verified safe — all existing tests pass with `importlib` mode. The `norecursedirs` entry in the same section prevents pytest from crawling `.venv/`, `node_modules/`, `.git/`, etc. during collection.
Applies to: All agents, all modes. Especially relevant to IMPROVE mode targeting Group 6 (Testing & CI).
```

```
C-TEST-3
Rule: Do NOT remove `pytest-xdist` (`-n auto --dist=loadgroup`) from local `make test` and CI pytest invocations. `pytest-xdist` parallelizes test execution across all available CPU cores, providing ~2-3x speedup on multi-core machines. The package is already declared in `[project.optional-dependencies].test` and CI's `build.yml` installs it explicitly.
Rationale: Added as part of PERF-007 on 2026-08-02. The Makefile's `test` target now uses `-n auto --dist=loadgroup` (previously single-threaded). CI already used `-n auto` but the local `make test` did not — contributors running `make test` were not getting the parallelism benefit.
Applies to: All agents, all modes. Especially relevant to IMPROVE mode targeting Group 6 (Testing & CI).
```

```
C-TEST-4
Rule: Do NOT add `--cov` or `--coverage` flags to local test runs (Makefile `test-client`, individual `pytest` invocations). Coverage instrumentation adds 15-25% overhead. Use `--no-coverage` for local dev; CI (`build.yml`) and pre-push hooks are the correct places for coverage enforcement.
Rationale: Added as part of PERF-007 on 2026-08-02. The Makefile `test-client` target now passes `--no-coverage` to Vitest. The Makefile `test` target does not pass `--cov` (it relies on `addopts` which includes `--cov`, but local devs can override with `--no-cov`). CI explicitly passes `--cov` and `--cov-fail-under=65` in its own step.
Applies to: All agents, all modes. Especially relevant to IMPROVE mode targeting Group 6 (Testing & CI).
```

```
C-TEST-5
Rule: Do NOT put test code inside production source files. Tests MUST live in separate test files/folders, for every language: Python → `tests/`; Rust → a sibling `tests.rs` module wired via `#[cfg(test)] mod tests;` (or `src-tauri/tests/` integration tests); frontend → `*.test.ts` / `*.spec.ts` files (or the renderer `__tests__/` convention). No inline `#[cfg(test)] mod tests` blocks in `.rs` source files, no test assertions inside Python modules, no test cases inside production TS/TSX. When splitting a module, new tests for it go in the module's separate test file — never appended inline to the production file.
Rationale: Inline tests bloat production files and mix concerns — `src-tauri/src/platform/logging.rs` carried 89 `#[test]` fns inside a 3183-line production file, and split sessions silently lost or mis-wired inline test blocks. The repo's own conventions already separate tests everywhere else (Python `tests/`, bubble `tests.rs`, renderer `__tests__/`); inline Rust tests were the remaining inconsistency.
Applies to: All agents, all modes, all sub-agents.
```
---

## Category: Code Style & Naming

```
C-STYLE-1
Rule: Do NOT add task IDs, session prefixes, or ticket numbers to source code (file names, function names, class names, variable names, comments). The session prefix (e.g. `CR`, `X7`) belongs ONLY in metadata files (`review.md`, `SUMMARY.md`, `worklog.md`). This is also enforced as Rule 21h in PROMPT.md and pattern M12 in VERIFY.md — but it is a CONSTRAINT here because agents repeatedly violate it.
Rationale: Task IDs are transient; a future session has a different prefix. Code named after a task ID becomes meaningless noise once the entry is removed from `review.md`.
Applies to: All agents, all modes, all sub-agents. THE ORCHESTRATOR MUST EMBED THIS RULE IN EVERY SUB-AGENT'S PROMPT.
```

---

## Category: Tauri Config

```
C-TAURI-1
Rule: Do NOT use Tauri v1 config keys in `tauri.conf.json`. The project uses Tauri v2 (schema URL: `https://schema.tauri.app/config/2`). V1 keys (`postInstall`, `preRemove`) must be renamed to their v2 equivalents (`postInstallScript`, `preRemoveScript`). Reverting to v1 keys will break the Tauri build.
Rationale: The Tauri build process reads tauri.conf.json and fails on unrecognized v1 key names. The project already migrated to v2 keys; reverting introduces a build blocker.
Applies to: All agents, all modes. Especially relevant to IMPROVE mode targeting Group 1 (Architecture) or Group 6 (Testing & CI).
```

---

## Category: Logging & Observability

```
C-LOG-1
Rule: Do NOT change the canonical log line template. FILE lines MUST be exactly `YYYY-MM-DD  HH:MM:SS  LEVEL  msg` — TWO spaces between the date and the time, seconds-only precision (NO millisecond fraction), no `T` separator, no timezone offset, short level labels (`DEBUG` / `INFO` / `WARN` / `ERROR` / `CRITICAL` — `WARN`, never `WARNING`), and NO per-line session id, thread name, function name, or module/component path. The ONLY sanctioned occurrence of the session id is the trailing `session=xxxxxxxx` field on the FIRST line of the session — the `[STARTUP] logging initialized:` banner emitted by `voice_typer/server/logging_setup.py` (the banner is logged once per process with the 8-char hex id returned by `log.setup_logging`); it must NEVER appear on any other line. This keeps the session mechanism alive and greppable (`session=`) while keeping every other line clean. TERMINAL (stderr) lines MUST be `HH:MM:SS  LEVEL  msg` — TIME ONLY, no date (the date lives only in the log file; console output shows just the clock). This applies to BOTH the Python formatters (`_iso_timestamp`, `_FileFormatter`, `_ColorFormatter` in `voice_typer/server/log/formatters.py`) AND the Rust sidecar (`now_timestamp` / `now_time_only` / `now_timestamps` in `src-tauri/src/util.rs`, `CombinedLogger` / `EarlyLogger` in `src-tauri/src/platform/logging.rs`). The ONLY sanctioned exception is the opt-in JSON formatter (`VOICE_TYPER_LOG_JSON=1`), which keeps the UTC `Z` + millisecond timestamp for log aggregators.
Rationale: The legacy format rendered `2026-08-06T23:14:33.352+0300 [2ae8edcc] [MainThread] INFO [voice_typer.server.logging_setup] ...` — ISO-8601 with tz offset + millis, a per-process session id, thread name, and module path on EVERY line, plus the long-form `WARNING` label, all of which made the log unreadable. The clean template above replaced it on 2026-08-08 and is pinned by `tests/test_logging.py` (`_EXACT_FILE_LINE_RE`), `tests/test_log_formatting.py` (`_TS_RE_TEXT` / `_TS_RE_TERM`), and `src-tauri/src/util_tests.rs` (`test_now_timestamp_format` / `test_now_time_only_format` / `test_now_timestamps_pair_consistent`). Reverting ANY part of it — re-adding millis, `T`/tz, session id, thread, module path, the `WARNING` label, or moving the date to the terminal / removing it from the file — regresses the readability fix. When touching logging, keep the format unchanged and update these tests if a (user-approved) format change is ever made.
Applies to: All agents, all modes, all sub-agents.
```

```
C-LOG-2
Rule: Do NOT remove the `_<duration>` suffix from lifecycle-completion log lines. Every log line that reports the END of a timed operation (startup complete, model/VAD/CUDA-DLL load, warm-up inference, transcription, recording stop, and any future timed load/transaction) MUST carry a duration suffix produced by `format_duration()` in `voice_typer/server/duration.py` (dependency-free; import it rather than inlining ad-hoc `f"..._{x:.1f}s"` strings that drift from the minutes case): `_2.3s` for sub-minute durations, `_1m 2.3s` for anything longer. Never bare `2.3s`, never `took=2.3s`, never `-- 2.3s` — the underscore form is the canonical, greppable performance marker. The suffix is attached to the timed event, normally at line END; the recording line is the one intentional mid-line placement (`Recording stopped _30.0s of audio, ...` reads naturally — the duration IS the subject). Timed lines today: `[STARTUP] Startup complete (model still loading in background)_3.7s`, `[MODEL] Model loaded via ... _1.4s`, `[PERF] Warm-up inference completed — CUDA kernels primed_2.4s`, `[CUDA-DLL] Prepended to PATH: [...]_0.8s`, `[TRANSCRIBE] Transcription complete (len=..., cycle=...)_0.8s`, `[DICTATION] Recording stopped _30.0s of audio, ...`, `[VAD] Silero VAD model preloaded + warmed_1.2s`. The measurement source is always `time.perf_counter()` (monotonic) captured at the start of the operation and diffed at the completion log. Grep anchor: `_\d+(m \d+)?\.\ds`.
Rationale: Added 2026-08-08 so performance is measurable at a glance in the log file — how long startup took, how long the model/packages took to load, how long transcription took, and how long the user recorded. Prior to this, several completion lines had no duration at all (startup) or used inconsistent ad-hoc formats (`took=%.1fs`, `— %.1fs`, `-- %.1fs of audio`) that could not be grep-summed. Reverting to a duration-less or non-underscore format regresses the performance observability the user explicitly requested.
Applies to: All agents, all modes, all sub-agents.
```

---

## How the adds / edits constraints

1. Add a new constraint block under the appropriate category (or create a new category with a `## Category: <name>` header).
2. Fill in the `Rule`, `Rationale`, and `Applies to` fields.
3. Save. The next cloud session / local agent run will read the updated file at start.

**Template for a new constraint:**
```
C-<CATEGORY>-<N>
Rule: <one-sentence prohibition, starting with "Do NOT...">
Rationale: <why this rule exists — 1-2 sentences>
Applies to: <all agents / specific agents / specific modes>
```

---

## Audit trail (when constraints are cited)

When an agent skips work due to a constraint, the skip is recorded in `worklog.md` (cloud agent) or in the chat report (local agent) with the format:

```
SKIPPED: <task ID or finding ID> — conflicts with CONSTRAINTS.md: <C-ID> (<one-line rule summary>)
```

The user can `grep` `worklog.md` for `SKIPPED:` to see every constraint-driven skip across sessions. This audit trail is essential for understanding why work was deferred — and for deciding whether a constraint should be relaxed in the future.

---

## Final note

This file is intentionally spare — the user fills it in over time as they discover areas where the cloud agent's "improvements" would damage the project's intent. Every rule here was added because a cloud agent (or a session in a merge) previously did the prohibited thing and the user had to revert it. Adding a rule here prevents the next agent from repeating the mistake.
