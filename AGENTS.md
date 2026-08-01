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
  `voice_typer/server/config.py`), redaction (SEC-003), the
  per-renderer IPC rate limiter (SEC-019), and the TCP auth token
  boundary. Do NOT add fields to `set_config` outside the SEC-002
  allowlist; do NOT bypass the renderer→main→backend allowlist chain.
- **§6.4 IPC command parity** (`CONTRIBUTING.md` §6.4) — the THREE
  allowlists must stay in lockstep:
  1. Server: `_COMMAND_REGISTRY` in `voice_typer/server/ipc_server.py`.
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
uv pip install -e . -r requirements-lock.txt

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
  `tests/conftest.py:232`) stubs `pystray`, `pynput`, `sounddevice`,
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
  (heartbeat vs. shutdown ordering).
- `PERF-*` — performance-sensitive path where a "trivial" change
  could regress hot-loop or memory. Examples: `PERF-005`
  (relaunch_ack fast-path), `PERF-006` (audio ring buffer zero-copy).

When you add a new tag, update this list AND link the tag to the
relevant `CONTRIBUTING.md` section (or, for tags not covered by
CONTRIBUTING.md, to a brief rationale comment at the tag's first
occurrence).
