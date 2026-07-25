# Contributing to Voice Typer

Thank you for your interest in improving Voice Typer — a premium offline
background voice-to-text utility that lives in your system tray. This
document is the canonical reference for getting a development
environment running, understanding the project layout, and shipping
changes that pass CI and respect the security model.

> **TL;DR** — install [`uv`](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`),
> then `uv venv && uv pip install -e ".[test,dev]"`, `cd voice_typer/client && npm install`,
> `pre-commit install`, then `pytest tests/ -v` and `npm run test`.
>
> (`uv` is 10-100x faster than pip for cold installs and is the preferred
> dev-environment setup. Plain `pip install -e ".[test,dev]"` still works —
> see §2.)

---

## 1. Prerequisites

Voice Typer is a cross-platform desktop app with a Python backend and an
Electron + React frontend. Both halves must be present to develop
locally.

### Common

- **Python 3.10 or newer** (3.12 recommended; 3.13/3.14 are also
  supported per `[tool.uv].environments`). Install from
  [python.org](https://python.org) or your OS package manager.
- **Node.js 20 or newer** (matches the `"engines": {"node": ">=20"}`
  constraint in `voice_typer/client/package.json`). The recommended way
  is via [nvm](https://github.com/nvm-sh/nvm) or
  [fnm](https://github.com/Schniz/fnm) so you can match CI exactly.
- **Git** with LFS not required (we keep binary fixtures under 500 KB —
  see the `check-added-large-files` pre-commit hook).
- **A working microphone** for end-to-end dictation tests. Headless
  unit tests mock audio capture via the `mock_heavy_imports` autouse
  fixture (see `tests/conftest.py`).

### OS-specific notes

| OS | Required system packages / toolchain |
|----|---------------------------------------|
| **Windows 10/11** | "Desktop development with C++" workload from Visual Studio Build Tools (for compiling `pynput` keyboard hooks and the optional `windows-key-listener.c` native helper). Run `pip install -e ".[windows]"` to pull `pycaw`, `comtypes`, and `pywin32` for volume ducking and shortcut creation. |
| **macOS 13+** (Ventura) | Xcode Command Line Tools (`xcode-select --install`). The `pyobjc-core`, `pyobjc-framework-CoreAudio`, and `pyobjc-framework-Cocoa` deps (declared with `sys_platform == 'darwin'` markers) require a working Clang. Grant **Accessibility** permission to the terminal (or the built app) the first time you press the hotkey — the native key listener needs it. CI runners pin to `macos-13` (Intel/x64) and `macos-14` (Apple Silicon/arm64); macOS 12 may work but is not tested. |
| **Linux (X11 or Wayland)** | `libxdo-dev` and `libxtst-dev` (Debian/Ubuntu: `sudo apt install libxdo-dev libxtst-dev`; Fedora: `sudo dnf install xdo-devel libXtst-devel`). Add your user to the `input` group so the native key listener can read `/dev/input/event*`: `sudo usermod -aG input $USER` then log out/in. See `scripts/linux/99-voice-typer.rules` and `scripts/linux/install_permissions.py` for the packaged udev/polkit story. |

> **GPU users (optional):** if you want CUDA-accelerated transcription,
> install the matching `torch` wheel *before* `pip install -e .` using
> the `--index-url https://download.pytorch.org/whl/cu118` (or `cu121`)
> flag. CPU-only installs work fine — `faster-whisper` and the optional
> `qwen-asr` extra both fall back to CPU automatically.

---

## 2. Development Setup

There are two supported paths: **`uv`** (preferred — 10-100x faster than pip
for cold installs, parallel downloads, global cache) and **plain `pip`**
(documented below). Both produce equivalent environments; pick whichever
you already have installed.

### 2.0 Preferred: `uv`-based setup

```bash
# 1. Clone
git clone https://github.com/AbdallahIsDev/voice-typer.git
cd voice-typer

# 2. Install uv (one-time, any of):
curl -LsSf https://astral.sh/uv/install.sh | sh   # macOS / Linux
pip install uv                                       # anywhere with pip
winget install astral-sh.uv                          # Windows

# 3. Create the venv (uv picks a Python 3.12.x by default,
#    honoring [tool.uv] in pyproject.toml). Use --python 3.11
#    etc. to override.
uv venv
source .venv/bin/activate          # macOS / Linux
# .venv\Scripts\activate           # Windows

# 4. Install Python deps (editable + test + dev extras).
#    `uv pip install` is uv's pip-compatible mode. The canonical
#    `uv sync --extra test --extra dev` would also work, but is
#    currently blocked by an upstream `qwen-asr` release gap
#    (only 0.0.6 exists on PyPI; the [qwen] extra requires >=0.1
#    and uv's lockfile resolves all extras). See README §"Using uv"
#    for the full backstory.
uv pip install -e ".[test,dev]"
#    Option B — pin to the hash-pinned locked set used by CI:
#    (XZ-CC-9: requirements.txt was removed; pip-installable deps now
#    live ONLY in pyproject.toml. For reproducible builds with
#    --require-hashes, use requirements-lock.txt.)
uv pip install -r requirements-lock.txt   # reproducible exact versions + sha256 hashes

# 5. Install the Electron + React frontend
cd voice_typer/client
npm install

# 6. Run the app in dev mode (two terminals, or use `npm run dev`
#    which spawns the Python subprocess automatically)
#    Terminal 1 — Python backend (optional; `npm run dev` starts it
#    automatically, but running it standalone is useful for debugging):
python -m voice_typer.server.ipc_server
#    Terminal 2 — Electron + Vite HMR:
npm run dev

# 7. Run tests (uv runs them in .venv automatically; --no-sync
#    skips the uv lockfile step that would otherwise trigger the
#    qwen-asr resolution failure described above):
uv run --no-sync pytest tests/ -v
```

### 2.1 Alternative: `pip`-based setup

```bash
# 1. Clone
git clone https://github.com/AbdallahIsDev/voice-typer.git
cd voice-typer

# 2. Create a dedicated venv (matches the path the launcher expects
#    in production — see ~/.voice-typer/venv in docs/home-directory.md)
python -m venv ~/.voice-typer/venv

# 3. Activate it
#    Windows (PowerShell):
~/.voice-typer/venv/Scripts/Activate.ps1
#    Windows (cmd):
~/.voice-typer/venv/Scripts/activate.bat
#    macOS / Linux:
source ~/.voice-typer/venv/bin/activate

# 4. Install Python deps (editable + test + dev extras).
#    Option A — extras syntax (preferred):
pip install -e ".[test,dev]"
#    Option B — pin to the hash-pinned locked set used by CI:
#    (XZ-CC-9: requirements.txt was removed; pip-installable deps now
#    live ONLY in pyproject.toml. For reproducible builds with
#    --require-hashes, use requirements-lock.txt.)
pip install -r requirements-lock.txt   # reproducible exact versions + sha256 hashes

# 5. Install the Electron + React frontend
cd voice_typer/client
npm install

# 6. Run the app in dev mode (two terminals, or use `npm run dev`
#    which spawns the Python subprocess automatically)
#    Terminal 1 — Python backend (optional; `npm run dev` starts it
#    automatically, but running it standalone is useful for debugging):
python -m voice_typer.server.ipc_server
#    Terminal 2 — Electron + Vite HMR:
npm run dev
```

### Optional extras

```bash
pip install -e ".[qwen]"       # experimental Qwen3-ASR-0.6B backend
pip install -e ".[deepfilternet]"  # premium DeepFilterNet noise filter
pip install -e ".[build]"      # PyInstaller for producing the .exe/.app
```

### Dependency Management

This project has a **single source of truth** for Python dependencies:
`pyproject.toml`'s `[project.dependencies]` (runtime) and
`[project.optional-dependencies]` (extras: `test`, `dev`, `build`, `windows`,
`macos`, `linux`, `qwen`, `deepfilternet`). The legacy `requirements.txt`
mirror file was **removed** (XZ-CC-9) because it drifted out of sync with
`pyproject.toml` — most notably it omitted two macOS pyobjc frameworks
(`pyobjc-framework-CoreFoundation`, `pyobjc-framework-ApplicationServices`)
that `pyproject.toml` correctly declares, causing `pip install
-r requirements.txt` on macOS to silently break the mic watcher and the
accessibility probe (XZ-CC-8).

For reproducible builds, use **`requirements-lock.txt`** — it is generated
via `uv pip compile --generate-hashes` and is safe to install with
`pip install --require-hashes -r requirements-lock.txt`. The completeness
of the lockfile against `pyproject.toml` is enforced by
`tests/test_requirements_lock_completeness.py` (regression guard for H-20).

To regenerate the lockfile after adding/removing a dependency:

```bash
uv pip compile --generate-hashes --python-version 3.12 pyproject.toml -o requirements-lock.txt
```

#### Frontend: TypeScript pin policy (XZ-CC-14)

> **DO NOT DOWNGRADE `typescript` below 7.x.** `typescript@7.0.2` is the
> LATEST STABLE RELEASE (verify with `npm view typescript version`). A
> prior agent wrongly assumed 7.x was unstable and pinned the lockfile to
> `5.6.3`, which broke `npm ci`. The `typescript` entry in
> `voice_typer/client/package.json` is pinned to the **exact** version
> `"7.0.2"` (no `^` or `~` caret) so a fresh `npm install` cannot
> accidentally float to a different release. If you upgrade TypeScript,
> keep `package-lock.json` in sync (run `npm install --package-lock-only`
> after any change) and verify `npm ci && npm run typecheck` succeeds
> before committing.

This warning was previously inlined as a `"//devDependencies_note"` key
in `package.json`. It has been moved here because (a) JSON doesn't
officially support comments, (b) `package.json` is not the right place
for prose rationale, and (c) a contributor is more likely to read this
section before touching deps than to read a JSON string field.

### Pre-commit hooks

```bash
pre-commit install        # runs ruff, mypy, biome, and basic checks
pre-commit install --hook-type commit-msg   # if you wire commit-msg checks
pre-commit run --all-files   # run the whole suite manually
```

### Tauri Development (migration in progress)

> **Note:** The Tauri stack is **NOT the default shipping app yet** — Electron is still the default. Cutover is per-platform per [`docs/migration/cutover-playbook.md`](docs/migration/cutover-playbook.md). The Tauri stack is additive: the Electron code is untouched and remains a reversible fallback. See [README § Runtime Architecture](README.md#runtime-architecture) and [ADR-0020](docs/adr/0020-desktop-runtime-migration-analysis.md) for the migration contract.

The Tauri v2 + Python sidecar host lives in `src-tauri/`. The React renderer (`voice_typer/client/src/renderer/`) is **shared between both stacks** — the same bundle runs under Electron (via `voice_typer/client/src/preload/index.ts`'s `contextBridge`) and under Tauri (via `voice_typer/client/src/renderer/src/lib/tauri-bridge.ts`, which auto-detects the host and installs the `window.python` / `window.bubble` / `window.window_` namespaces using Tauri's global `__TAURI__` API).

#### Prerequisites (in addition to the common prereqs in §1)

- **Rust toolchain** — install via [rustup](https://rustup.rs/). The `src-tauri/rust-toolchain.toml` file pins the channel; `rustup` reads it automatically when you `cd src-tauri`.
- **Tauri v2 system deps** (per-OS):
  - **Linux (X11 or Wayland)**: `webkit2gtk-4.1`, `gtk-3`, `librsvg`, `libssl-dev`, `libayatana-appindicator3-dev` (Debian/Ubuntu: `sudo apt install libwebkit2gtk-4.1-dev build-essential curl wget file libxdo-dev libssl-dev libgtk-3-dev libayatana-appindicator3-dev librsvg2-dev`). Set `PKG_CONFIG_PATH` if `cargo check` can't find `webkit2gtk-4.1`.
  - **macOS 13+** (Ventura): Xcode Command Line Tools (already required above). CI pins to `macos-13` (Intel) and `macos-14` (Apple Silicon).
  - **Windows 10/11**: WebView2 runtime (preinstalled on Windows 11; Windows 10 may need the Evergreen Bootstrapper from <https://developer.microsoft.com/microsoft-edge/webview2/>) + MSVC build tools (already required above).
- **Nuitka** (only needed for `cargo tauri build`, not for `cargo tauri dev`): see [`docs/migration/tauri-build-runbook.md`](docs/migration/tauri-build-runbook.md) for the freeze workflow.

#### Environment variables

| Variable | Purpose | When to set it |
|---|---|---|
| `TAURI_SIDECAR=1` | Tells the Python backend it is running under the Tauri host. Disables the Python-side heartbeat watchdog (ADR-0018) and the Win32 single-instance mutex — the Tauri host provides both via `tauri-plugin-single-instance` and the supervisor. | Set automatically when the sidecar is launched with `--ws` (i.e. `python -m voice_typer.server.ipc_server --ws`). Set manually only when debugging the WS server in isolation. |
| `VOICE_TYPER_SIDECAR_DEV=1` | Tells the Tauri Rust host to spawn `python -m voice_typer.server.ipc_server --ws` as a subprocess instead of the Nuitka-frozen `externalBin` binary. Lets you iterate on UI/transport changes in seconds — no ~10-minute Nuitka rebuild required. | Set when running `cargo tauri dev` (see below). Do NOT set for `cargo tauri build` — production builds must use the frozen sidecar. |

#### Common commands

```bash
cd src-tauri

# Type-check + lint the Rust host (no display server required — runs in CI)
cargo check
cargo clippy --all-targets -- -D warnings

# Dev mode — runs the Rust host against a live Python subprocess.
# Requires a display server for the WebView (WebView2 on Windows,
# WKWebView on macOS, webkit2gtk on Linux). On a headless box,
# `cargo check` is the most you can do.
VOICE_TYPER_SIDECAR_DEV=1 cargo tauri dev

# Production build — bundles the Nuitka-frozen sidecar + native hotkey
# binaries + prewarm binaries per target triple. Requires the sidecar
# binary to exist in src-tauri/bin/ — see the build runbook.
cargo tauri build
```

> **Headless dev containers:** `cargo tauri dev` and `cargo tauri build` both require a display server for the WebView. `cargo check` and `cargo clippy` do not — they are the recommended validation commands in CI and on headless dev machines. See [`docs/migration/tauri-sidecar-bridge.md`](docs/migration/tauri-sidecar-bridge.md) § "What's NOT implemented this round" for the current host-validation status.

#### What's where

| Path | Purpose |
|---|---|
| `src-tauri/src/main.rs` | The Rust host (~250 lines): spawns sidecar via `externalBin`, opens WS client, performs bearer-token auth, exposes a generic `dispatch` Tauri command, bridges server-initiated events to Tauri events, coalesces `bubble_level` 60Hz→30Hz, runs supervisor with 500ms→1s→2s→4s→8s backoff (cap 5 → full-app relaunch via `AppHandle::restart()`). |
| `src-tauri/Cargo.toml` | Tauri v2 + plugins (`shell`, `notification`, `clipboard-manager`, `single-instance`, `dialog`) + `enigo` (keystroke injection) + `tokio-tungstenite` (WS client). |
| `src-tauri/tauri.conf.json` | Per-arch `externalBin` (6 target triples) + `resources` (3 native hotkey binaries + 6 prewarm binaries) + Tauri v2 capabilities. `withGlobalTauri: true` exposes `window.__TAURI__`. |
| `src-tauri/capabilities/main-runtime.json` + `bubble-runtime.json` | Least-privilege capability split (CR-5 / SEC-026): `main-runtime` grants the privileged main window scoped `shell:allow-spawn` per sidecar binary, `notification`, `clipboard-manager`, `single-instance`, `dialog`, and `core:tray:*`; `bubble-runtime` is minimal (`core:event:default` + `core:window:allow-start-dragging`) so a compromised bubble renderer cannot spawn, write clipboard, or touch the tray. (The legacy `migrate-runtime.json` file was split into these two scopes.) |
| `voice_typer/client/src/renderer/src/lib/tauri-bridge.ts` | React ↔ Tauri bridge. Auto-installs `window.python` / `window.bubble` / `window.window_` using Tauri's global API when Tauri is detected; no-op under Electron (the preload already installed the namespaces). |
| `voice_typer/server/sidecar_ws.py` | WebSocket server side of the bridge. Binds `127.0.0.1:0`, emits `{"event":"server_started","port":N}` to stdout, performs HMAC/bearer-token auth handshake, dispatches WS frames via `IPCServer._dispatch` (reuses the 73-command registry unchanged — CR-18 reconciliation 2026-07-19), handles `{"type":"shutdown"}` cooperative shutdown. |
| `voice_typer/server/ipc_server.py` | `--ws` CLI flag + `TAURI_SIDECAR=1` env gate. Under `TAURI_SIDECAR=1`: heartbeat thread is NOT started; Win32 single-instance mutex is NOT acquired. Electron path unchanged. |

#### Cutover status

The Tauri stack is gated on a per-platform Phase 0 validation spike before it can become the default. See:

- [`docs/migration/windows-validation-runbook.md`](docs/migration/windows-validation-runbook.md) — Phase 0-W (Windows, in progress).
- [`docs/migration/macos-validation-runbook.md`](docs/migration/macos-validation-runbook.md) — Phase 0-M (macOS, not started).
- [`docs/migration/linux-validation-runbook.md`](docs/migration/linux-validation-runbook.md) — Phase 0-L (Linux X11 + Wayland, not started).
- [`docs/migration/cutover-playbook.md`](docs/migration/cutover-playbook.md) — per-platform cutover gates.
- [`docs/migration/tauri-build-runbook.md`](docs/migration/tauri-build-runbook.md) — full Nuitka + Tauri build instructions.
- [`docs/migration/tauri-sidecar-bridge.md`](docs/migration/tauri-sidecar-bridge.md) — bridge architecture + current implementation status.

---

## 3. Project Structure

```
voice-typer/
├── voice_typer/
│   ├── server/                       # Python backend (the "real" app)
│   │   ├── ipc_server.py             # TCP JSON-lines server, SEC-018 token auth
│   │   ├── app.py                    # VoiceTyperApp — orchestrator
│   │   ├── config.py                 # SEC-002 allowlist, SEC-003 redaction
│   │   ├── security.py               # token / URL / file-perm helpers
│   │   ├── tray.py / tray_menu.py    # pystray tray icon + menu
│   │   ├── recording/                # PortAudio capture → deque (package; see docs/rw04-recording-decomposition.md)
│   │   ├── transcription.py          # ASR dispatch (whisper/qwen/parakeet)
│   │   ├── text_cleanup.py           # dedup, misspellings, capitalization
│   │   ├── vocabulary.py             # user corrections (single source)
│   │   ├── templates.py              # text templates / snippets
│   │   ├── history_db.py             # SQLite WAL, SEC-007 0o600 perms
│   │   ├── crash_recovery.py         # RELIABILITY-005 async flush
│   │   ├── cloud_engines.py          # RELIABILITY-004 URL allowlist
│   │   ├── llm_polish.py             # PRIVACY-001 consent gate
│   │   ├── audio_filters/            # ADR 0009 — RNNoise, gate, EQ, …
│   │   ├── native/                   # C/Swift key listeners per OS
│   │   └── ...
│   │
│   └── client/                       # Electron + React frontend
│       ├── src/
│       │   ├── main/index.ts         # Electron main process — spawns Python
│       │   ├── preload/index.ts      # SEC-014 contextIsolation bridge
│       │   ├── preload/bubble.ts     # SEC-016 bubble-scoped bridge
│       │   └── renderer/src/
│       │       ├── App.tsx           # React root, routing
│       │       ├── pages/            # Home, Settings, History, Models, …
│       │       ├── components/       # Sidebar, StatCards, ThemeSwitch, …
│       │       ├── hooks/            # usePython, useSnackbar, useStatsShare
│       │       └── types/            # ipc.ts, config.ts, stats.ts
│       ├── package.json              # scripts: dev, build, test, typecheck
│       ├── biome.json                # formatter: tabs + double quotes
│       └── electron-builder.yml
│
├── tests/                            # pytest suite (1300+ tests)
│   ├── conftest.py                   # mock_heavy_imports autouse fixture
│   ├── fixtures/                     # WAV files for audio tests
│   ├── manual/                       # scripts you run by hand (cublas, etc.)
│   ├── mutmut_config.py              # mutation testing config
│   └── test_*.py                     # one test module per feature/round
│
├── docs/
│   ├── ARCHITECTURE.md               # the big picture (READ THIS)
│   ├── API.md                        # IPC message reference
│   ├── PLATFORM_STATUS.md            # per-OS support matrix
│   ├── home-directory.md             # ~/.voice-typer/ layout
│   └── adr/                          # Architecture Decision Records
│       ├── README.md                 # ADR index — read this first
│       ├── template.md               # boilerplate scaffold for new ADRs
│       └── 0000-0019                 # one file per decision (see index)
│
├── scripts/
│   ├── build/                        # PyInstaller spec, icon generators
│   └── linux/                        # udev rules, polkit, postinst
│
├── bench/                            # startup + transcription benchmarks
├── pyproject.toml                    # project metadata, ruff, mypy, pytest, ALL deps
├── requirements-lock.txt             # hash-pinned exact versions (--require-hashes safe)
├── .pre-commit-config.yaml           # ruff + mypy + biome + sanity hooks
├── README.md
├── CONTRIBUTING.md                   # ← you are here
└── SECURITY.md
```

---

## 4. Development Workflow

### 4.1 Python tests

```bash
# Full suite — verbose, with coverage gate at 65% (see pyproject.toml)
pytest tests/ -v

# Single test file / single test
pytest tests/test_ipc_server.py -v
pytest tests/test_config.py::test_set_config_allowlist -v

# Markers (see tests/conftest.py for the full list)
pytest -m real_pynput      # tests that need the real pynput.keyboard listener
pytest -m real_pil         # tests that need the real PIL.ImageDraw

# Coverage report (HTML)
pytest --cov=voice_typer --cov-report=html
open htmlcov/index.html
```

The `addopts` in `pyproject.toml` already include `-v --tb=short --cov=voice_typer --cov-fail-under=65`, so a bare `pytest` is enough for CI-equivalent output.

### 4.2 Frontend tests

```bash
cd voice_typer/client

npm run test           # vitest run (one-shot, CI-friendly)
npm run test:watch     # vitest in watch mode during development

# Lint + format + typecheck (run all three before pushing)
npx biome check        # formatter (tabs + double quotes) + linter
npm run lint           # biome check (formatter + linter)
npm run typecheck      # tsc --noEmit × 3 configs (root, web, node)
npm run build          # electron-vite build (full production bundle)
```

### 4.3 Pre-commit

```bash
pre-commit install                 # wire the hooks into .git/hooks/pre-commit
pre-commit run --all-files         # run every hook against the whole tree
pre-commit run ruff --all-files    # run a single hook
```

Hooks (see `.pre-commit-config.yaml`): `ruff` (lint + format), `mypy`
(server-only, with `--ignore-missing-imports` and `--no-strict-optional`
to keep the dev loop fast), `pre-commit-hooks` (trailing whitespace,
end-of-file fixer, YAML/JSON validation, merge-conflict markers,
large-file cap at 500 KB, LF line endings), plus two local hooks that
shell out to `npx biome check` and `npm run typecheck` for the client.

### 4.4 Benchmarks

```bash
python bench/bench_startup.py        # cold-start time of the tray icon
python bench/bench_transcription.py  # transcribe a fixed WAV and report WPS
```

### 4.5 Mutation testing (expensive — do not run in CI)

```bash
mutmut run --paths-to-mutate=voice_typer/server/text_cleanup.py,voice_typer/server/config.py
mutmut results
mutmut show <mutant-id>
```

See `tests/mutmut_config.py` and `[tool.mutmut]` in `pyproject.toml`.

---

## 5. Architecture Overview

Voice Typer is a **two-process desktop app**. The Electron **main
process** (`voice_typer/client/src/main/index.ts`) is the entry point:
it generates a 32-byte `IPC_TOKEN` via `crypto.randomBytes`, spawns the
Python backend as a child process with that token injected through the
`VOICE_TYPER_IPC_TOKEN` environment variable, then opens the main React
window and a small always-on-top "bubble" window for live waveform
feedback. The Python process (`voice_typer/server/ipc_server.py`) binds
to **`127.0.0.1:9876`** and speaks JSON-lines over TCP. The very first
frame Electron sends is `{"type":"auth","token":...}` — the connection
is dropped unless the token matches (SEC-018). All subsequent IPC is
untrusted-by-default: each inbound message is size-capped at 1 MB
(SEC-009), rate-limited at 200 burst / 60 sustained messages per second
(RELIABILITY-006), and dispatched through a per-method allowlist
(`set_config` enforces the SEC-002 allowlist (`IPC_CONFIG_ALLOWLIST` in
``voice_typer/server/config_validators.py``) with type/range/
enum/URL validation; `get_config` redacts API keys via SEC-003).

The Python **backend** is a long-running tray app. `VoiceTyperApp`
(in `app.py`) wires together: a **pystray** tray icon (with a minimal
menu — most configuration lives in the Electron UI), three hotkey
backends (Win32, macOS CGEvent, Linux `/dev/input` — see ADR 0007), a
PortAudio recorder that captures 16 kHz mono into a bounded `deque`,
and a transcription pipeline. The pipeline dispatches to one of three
**ASR engines**: `faster-whisper` (default, CUDA-first with CPU
fallback), `qwen_engine.QwenEngine` (experimental Qwen3-ASR-0.6B), or
`parakeet_engine.ParakeetEngine` (NVIDIA Parakeet). ARCH-013 unified
the latter two through a generic `_init_asr_engine` dispatcher. After
ASR, text flows through `text_cleanup` (dedup, misspellings,
self-corrections, capitalization — skipped per ARCH-009 if the
VocabularyManager is enabled to avoid double-application), the
VocabularyManager, TemplateManager, optional LLM polish (gated by
PRIVACY-001 consent), auto-punctuation, then is copied to the clipboard
and pasted into the focused field. History entries land in a SQLite
WAL database (SEC-007: `0o600` perms on POSIX) and crash-recovery
state is flushed through a daemon thread (RELIABILITY-005).

The **React renderer** never talks to Python directly — it goes through
the **preload bridge**. Electron's `webPreferences` are locked down
per SEC-014 (`contextIsolation: true`, `sandbox: true`,
`webSecurity: true`, `nodeIntegration: false`), so the renderer sees
only the small typed surface that `src/preload/index.ts` exposes via
`contextBridge.exposeInMainWorld`. The bubble window has its own
preload (`src/preload/bubble.ts`) with an even smaller surface; every
IPC handler that the bubble can invoke is guarded by
`assertFromBubble(event)` (SEC-016) so a compromised renderer cannot
replay bubble-scoped messages back through the main window. Broadcasts
from Python to Electron are filtered to `mainWindow` only (SEC-017).
The renderer itself is a standard Vite + React 19 + Tailwind 4 app
with shadcn/ui components; all backend interaction goes through the
shared `usePython()` hook (`src/renderer/src/hooks/usePython.ts`),
which handles reconnects, request/response correlation, and event
subscription. See `docs/ARCHITECTURE.md` for the full diagram and
`docs/adr/` for the rationale behind each major decision.

---

## 6. Coding Standards

### 6.1 Python

- **Type hints are mandatory** on all new public functions. The
  codebase has many untyped legacy functions (`disallow_untyped_defs`
  is `false` in `pyproject.toml`), but new code must be typed. Run
  `mypy voice_typer/server/` locally; the pre-commit hook already
  scopes mypy to `^voice_typer/server/` with
  `--ignore-missing-imports --no-strict-optional` for speed.
- **Use `log.exception(...)`** for error paths, not bare `print()` or
  `logging.error(...)` without a traceback. The exception is
  automatically attached. See `voice_typer/server/log.py` for the
  shared logger setup.
- **Never use `# type: ignore`** to silence a real type error — fix
  the type or add a per-module override in `pyproject.toml` (the list
  under `[[tool.mypy.overrides]]` enumerates every library that
  genuinely lacks stubs).
- **Never use `except: pass`** to swallow exceptions. At minimum log
  them; usually re-raise.
- **Inline-tag comments** document non-obvious decisions and link back
  to the issue/ADR that introduced them. The convention is
  `# TAG-NNN: <one-line rationale>`. Examples:
  ```python
  # SEC-018: token must be 32 bytes of crypto.random — do NOT shorten
  token = secrets.token_bytes(32)
  # RACE-016: acquire the lock BEFORE checking the flag, otherwise
  # we race with the stop-dictation path and can record a half-frame.
  with self._rec_lock:
      if self._recording:
          return
  ```
  Common prefixes: `SEC-*` (security), `RACE-*` (concurrency),
  `PERF-*` (performance), `RELIABILITY-*` (resilience),
  `ARCH-*` (architecture), `UX-*` (UX), `TEST-*` (test infra),
  `BUILD-*` (build/packaging), `ADR NNNN` (references a decision
  record under `docs/adr/`).
- **Formatter:** ruff (line-length 120, target `py310`). Run
  `ruff check --fix` and `ruff format` before committing; the
  pre-commit hook does this automatically.
- **Mocking convention (TEST-033):** import mock objects directly —
  `from unittest.mock import MagicMock, patch` — never
  `from unittest import mock` followed by `mock.MagicMock(...)`.
  Prefer `pytest`'s `monkeypatch` fixture for attribute/item
  replacement (auto-cleaned); use `unittest.mock.patch` only when you
  need to assert call counts.

### 6.2 TypeScript / React

- **Formatter:** Biome (`voice_typer/client/biome.json`) —
  `indentStyle: "tab"`, `quoteStyle: "double"`. Run
  `npx biome check --write` to auto-fix. The pre-commit hook runs
  `npx biome check` (no `--write`) and fails if files are dirty.
- **Linter:** Biome (`biome check`). The config lives at
  `voice_typer/client/biome.json`.
- **Type checker:** `tsc --noEmit` across three configs (`tsconfig.json`,
  `tsconfig.web.json`, `tsconfig.node.json`). `npm run typecheck` runs
  all three; use `npm run typecheck:web` to scope to the renderer only.
- **Path aliases:** `#ui/*` → `./src/renderer/src/components/ui/*`,
  `#utils` → `./src/renderer/src/lib/utils.ts` (declared in
  `package.json#imports` and mirrored in the tsconfigs). Prefer these
  over relative paths that climb above two levels.
- **React 19 + shadcn/ui** — components live under
  `src/renderer/src/components/` (with `ui/` for shadcn primitives).
  Pages live under `src/renderer/src/pages/`. Hooks live under
  `src/renderer/src/hooks/`.
- **IPC discipline:** all backend calls go through `usePython()`.
  Validate every IPC response at runtime before casting — see
  `asRecordingState` in `types/ipc.ts`. Never `JSON.parse` an
  untrusted string from the backend without a schema check.
- **Inline-tag comments** apply the same way as Python — e.g.
  `// SEC-016: this handler is bubble-scoped, do not expose to main`.

### 6.3 Security — non-negotiable

The `SEC-*` tags in the codebase are load-bearing controls documented
in `docs/ARCHITECTURE.md` § "Security boundaries". **Never bypass a
SEC-* control** without an ADR and an explicit code review. In
particular:

- Do not weaken the IPC token check (SEC-018) or remove the
  loopback-only bind.
- Do not add fields to `set_config` outside the SEC-002 allowlist
  without type/range/enum/URL validation.
- Do not log API keys, tokens, or transcription text — `SEC-003` and
  `RELIABILITY-004` redact them; new logging must follow the same
  pattern.
- Do not disable `contextIsolation`, `sandbox`, or `webSecurity` in
  Electron `webPreferences` (SEC-014), even for debugging. Use
  `!app.isPackaged` guards (SEC-013) to scope DevTools instead.
- Do not remove `assertFromBubble(event)` from bubble IPC handlers
  (SEC-016), even if it looks redundant.

If you believe a control is wrong, open an issue tagged `security` and
write a draft ADR (`docs/adr/template.md`) before changing code.

### 6.4 IPC command parity (keep the three allowlists in lockstep)

Voice Typer's IPC surface is a **two-sided allowlist**: the Python
backend only dispatches commands it knows about, and the Electron main
process only *forwards* commands the renderer is allowed to send. A new
command is useless — or, worse, silently blocked — unless **all three**
of the following are updated together:

1. **Server command registry** — add the command + its handler to
   `_COMMAND_REGISTRY` in `voice_typer/server/ipc_server.py`
   (≈ lines 1911–2030). This is what actually routes the inbound
   `{"type": "…"}` message to a handler.
2. **Electron main-process allowlist** — add the same command string to
   `ALLOWED_COMMANDS` in `voice_typer/client/src/main/allowed-commands.ts`
   (≈ lines 40–159). The main process refuses to forward any command
   not in this list to the Python backend (SEC-002 lateral boundary).
3. **Renderer type-safe wrapper** — add the command to the
   `type`/response discriminated union in
   `voice_typer/client/src/renderer/src/types/ipc.ts` so the renderer's
   `call<T>()` helper can type-check requests and responses.

> **Common mistake (Finding 2):** adding a command to the server
> registry *without* updating `ALLOWED_COMMANDS` means the renderer's
> `call()` is rejected by the main process before it ever reaches
> Python. This has happened 10 times in the past. When you add or
> rename a command, grep for the command string across all three
> locations and update each one.

> **Regression guard:** `tests/test_electron_ipc_and_build.py` (and the
> bidirectional parity test recommended in Finding 2) assert that
> `_COMMAND_REGISTRY` and `ALLOWED_COMMANDS` stay in sync. If you add a
> command to one side only, that test fails in CI — but adding this
> section means you won't need the test to catch it first.

---

## 7. Testing Guidelines

### 7.1 Python — pytest

- **Framework:** pytest with `pytest-asyncio`, `pytest-mock`,
  `pytest-timeout`, `pytest-cov`, `hypothesis`, and `pytest-benchmark`
  (declared in `[project.optional-dependencies].test`).
- **Headless by default:** the autouse `mock_heavy_imports` fixture in
  `tests/conftest.py` mocks `sounddevice`, `faster_whisper`, `pynput`,
  `pystray`, `PIL`, and `pyperclip` so the suite runs on any CI
  runner without a display or microphone.
- **Opt-out markers** (registered in `pytest_configure`):
  - `@pytest.mark.real_pynput` — use the real `pynput.keyboard`
    listener (for tests that exercise the actual key dispatch path).
  - `@pytest.mark.real_pil` — use the real `PIL.ImageDraw` (for tests
    that render the tray icon bitmap).
- **Coverage threshold:** 65 %, enforced by `--cov-fail-under=65` in
  `pyproject.toml`. If your change drops coverage below 65 %, add
  tests or mark unreachable branches with `# pragma: no cover`.
- **Property-based testing:** use `hypothesis` for parsers and pure
  functions — see `tests/test_text_cleanup_hypothesis.py` and
  `tests/test_property_based.py` for patterns.
- **Benchmarks:** `pytest-benchmark` is available; put slow benchmarks
  in `tests/test_benchmarks.py` (CI runs them but does not fail on
  regression — that's a manual decision).
- **Mutation testing:** `mutmut` is configured (see `[tool.mutmut]`
  in `pyproject.toml`) for `text_cleanup.py`, `config.py`, `tray.py`,
  and `tray_menu.py`. Run it locally before merging changes to those
  modules — `mutmut run` then `mutmut results`.
- **WAV fixtures:** `tests/fixtures/` ships `silence.wav`,
  `tone.wav`, `noise.wav`, and `test_440hz_1s_16k.wav` with a
  `metadata.json`. Regenerate via `tests/fixtures/generate_fixture.py`
  if you change the format expectations.
- **Every fix ships with a regression test.** The test file naming
  convention is `test_<feature>.py` or `test_round<N>_<theme>.py` for
  batch review rounds.

### 7.2 Frontend — vitest + Testing Library

- **Framework:** vitest 2.x with jsdom, `@testing-library/react` 16,
  and `@testing-library/jest-dom` (set up in
  `voice_typer/client/src/renderer/src/test-setup.ts`).
- **Run:** `npm run test` (one-shot) or `npm run test:watch`.
- **Co-located tests:** test files sit next to the code under
  `__tests__/` directories — see
  `src/renderer/src/components/__tests__/ThemeSwitch.test.tsx`,
  `Sidebar.test.tsx`, `ErrorBoundary.test.tsx`, and
  `src/renderer/src/pages/__tests__/Home.test.tsx`.
- **Mocking `@hugeicons/react`:** the project uses `@hugeicons/react`
  for icons, which has no good test stub — mock it as a `<span
  data-testid="hugeicon" data-name={icon?.name}>` and stub
  `@hugeicons/core-free-icons` exports as `{ name }` tagged objects.
  See the existing `__tests__` files for the exact pattern.
- **Mocking `usePython`:** use `vi.hoisted` + `vi.mock("@/hooks/usePython",
  ...)` to control the IPC layer. Use `vi.resetModules()` in
  `beforeEach` and dynamic `import("@/pages/Home")` if the module
  under test has module-level caches (the Home page's `_cachedStats` /
  `_cachedRecent` are examples).
- **Accessibility contracts are tests, not afterthoughts:** assert
  `aria-label`, `aria-current`, `role="alert"`, etc. See
  `src/renderer/src/a11y/accessibility.test.tsx` for the global a11y
  suite.
- **Coverage:** vitest is configured in `vitest.config.ts`; aim for
  ≥ 65 % to match the Python gate.

### 7.3 Manual tests

`tests/manual/` contains scripts that need real hardware or a real
model — `cublas_fallback.py`, `runtime_proof.py`, `diagnose_f2.py`.
Run them by hand when investigating GPU or hotkey issues; they are
not part of the CI suite.

### 7.4 Testing patterns

A handful of patterns recur across the suite.  Reach for these before
inventing a new arrangement — they keep tests fast, isolated, and
consistent with what reviewers expect.

#### 7.4.1 Headless hardware mocks (autouse)

Every test inherits the `mock_heavy_imports` autouse fixture in
`tests/conftest.py`, which installs `MagicMock`s for `sounddevice`,
`faster_whisper`, `pynput`, `pystray`, `PIL`, and `pyperclip`.  This
lets the suite run on any CI runner without a display, microphone,
or audio stack.  Two markers opt out:

```python
@pytest.mark.real_pynput
def test_uses_real_pynput(): ...

@pytest.mark.real_pil
def test_renders_tray_bitmap(): ...
```

When adding a new hardware-touching module, prefer extending this
fixture over `monkeypatch`-ing the same import in 20 individual
tests.  When you need to *un-mock* one of these for a single test
(e.g. a real-PIL test that runs after a `sys.modules.setdefault("PIL",
MagicMock())` from another module), the fixture already evicts mock
entries before importing the real package — see the long comment in
`conftest.py` for the rationale.

#### 7.4.2 Dependency-injection service seam

`IPCServer(app, service=fake)` accepts an injected `service` argument
that lets you exercise the IPC dispatch layer in isolation from
`VoiceTyperService`.  The fixture `make_fake_service()` in
`tests/fixtures/ipc_test_helpers.py` returns a `MagicMock`-based fake
that satisfies the `AppProtocol` structural type.  Use it for tests
that assert on dispatch behaviour, error codes, or push events
without coupling to the service implementation:

```python
from tests.fixtures.ipc_test_helpers import make_fake_service
from voice_typer.server.ipc_server import IPCServer

def test_get_history_bounds_limit(monkeypatch):
    app = MagicMock()
    app._config_mutation_lock = threading.RLock()
    fake = make_fake_service()
    server = IPCServer(app, service=fake)
    resp = server._dispatch({"type": "get_history",
                             "data": {"limit": 10**9}, "id": "x"})
    assert resp["type"] == "history"
    # SEC-010: limit was clamped to _HISTORY_LIMIT_MAX before reaching the service
    fake.get_history.assert_called_once()
    _, kwargs_or_args = fake.get_history.call_args
    limit = kwargs_or_args.args[0] if hasattr(kwargs_or_args, "args") else kwargs_or_args[0]
    assert limit <= 500
```

For tests that only need the registry or dispatch (no service
behaviour), the lighter pattern used by
`TestElectronNotificationFieldValidation` in
`tests/test_bugfix_regressions.py` constructs `IPCServer.__new__(IPCServer)`
and assigns `app` / `service` directly — bypasses the
`VoiceTyperService` construction cost entirely.

#### 7.4.3 Push-event testing

The IPC server fans out push events via the module-level
`_push_event_registry`.  Two helpers — `_set_push_event(fn)` and
`_clear_push_event(fn)` — let a test capture pushed events without
spinning up a real TCP client:

```python
from voice_typer.server.event_bus import subscribe, unsubscribe

captured = []
def _capture(msg): captured.append(msg)
_set_push_event(_capture)
try:
    server._dispatch({"type": "show_electron_notification",
                      "data": {"title": "Hi", "message": "Body"}, "id": "x"})
finally:
    _clear_push_event(_capture)

assert any(m["type"] == "electron_notification" for m in captured)
```

For tests that patch the push hook at the source, target the
*module* the handler is defined in (e.g.
`voice_typer.server.handlers.system_handlers._push_event_now`),
not the re-export in `ipc_server.py` — Python's `from x import y`
binds the name at import time, so patching the re-export won't
affect handlers that already captured the reference.

#### 7.4.4 Bounded-limit assertions (SEC-010)

Several IPC handlers (`get_history`, `get_favorites`, `search_history`)
clamp caller-supplied `limit` / `offset` via `_bound_history_limit`
and `_bound_history_offset` to prevent DoS via huge values.  When
adding a new paginated endpoint, reuse the same helpers and add a
test that dispatches `{"limit": 10**9}` and asserts the service
received a value within `[_HISTORY_LIMIT_MIN, _HISTORY_LIMIT_MAX]`.

---

## 8. Submitting Changes

### 8.1 Branch & commit hygiene

1. **Fork** the repo (if you don't have push access) and create a
   feature branch off `main`:
   ```bash
   git checkout -b feat/my-feature
   # or: fix/sec-018-token-leak, docs/adr-0008-foo, test/recording-edge-cases
   ```
2. **Write tests first** (or alongside) — every bug fix gets a
   regression test, every new feature gets coverage.
3. **Run the full local suite** before pushing:
   ```bash
   pytest tests/ -v
   cd voice_typer/client && npm run test && npm run lint && npm run typecheck && npm run build
   pre-commit run --all-files
   ```
4. **Commit messages** follow Conventional Commits with a tag prefix
   when relevant:
   ```
   feat(asr): add parakeet engine fallback (ARCH-013)

   - adds _init_asr_engine dispatcher
   - falls back to whisper on CUDA OOM
   - tested in tests/test_qwen_engine.py

   SEC-018, ADR-0004
   ```
   - Reference the relevant `SEC-*`, `ADR-*`, `ARCH-*`, `PERF-*`,
     `RELIABILITY-*`, or `UX-*` tag in the body so reviewers can
     trace the change back to its rationale.
   - Breaking changes start with `feat!:` or `fix!:` and include a
     `BREAKING CHANGE:` footer.
5. **Push** and open a PR against `main`.

### 8.2 Pull Request template

When opening a PR, include the following (the repo has a
`.github/PULL_REQUEST_TEMPLATE.md` — fill it in):

```markdown
## Summary
<one-paragraph description of what changed and why>

## Type of change
- [ ] Bug fix (non-breaking)
- [ ] New feature (non-breaking)
- [ ] Refactor / chore
- [ ] Docs / ADR
- [ ] Breaking change (please describe impact below)

## Related tags / issues
- SEC-XXX / ADR-NNNN / ARCH-XXX / PERF-XXX / RELIABILITY-XXX / UX-XXX
- Closes #<issue-number>

## Security impact
<if this touches any SEC-* control, explain why the change is safe
or attaches a new ADR. Otherwise write "None — no SEC-* controls
affected.">

## Testing
- [ ] `pytest tests/ -v` passes locally
- [ ] `cd voice_typer/client && npm run test` passes
- [ ] `npm run lint && npm run typecheck` clean
- [ ] `pre-commit run --all-files` clean
- [ ] New tests added / updated
- [ ] Coverage not reduced

## Screenshots / recordings
<for UI changes — before/after>

## Checklist
- [ ] Code is formatted (ruff + biome)
- [ ] Type hints / types are complete
- [ ] Inline-tag comments added where non-obvious
- [ ] CHANGELOG.md updated (if user-facing)
- [ ] Docs (README / FEATURES / ARCHITECTURE / ADR) updated if needed
```

### 8.3 Review criteria

A maintainer will merge your PR once:

- All CI checks pass (pytest, vitest, biome, tsc, ruff, mypy,
  pre-commit).
- Coverage does not drop below 65 %.
- No `SEC-*` control is bypassed without an ADR.
- The commit history is clean (squash or rebase as needed — maintainers
  will prompt you).
- The CHANGELOG is updated for user-visible changes.

### 8.4 Reporting bugs

Use [GitHub Issues](https://github.com/AbdallahIsDev/voice-typer/issues)
and include:

- Voice Typer version (`python -m voice_typer --version` or the
  About page in the app).
- OS and Python version (`python --version`).
- Steps to reproduce.
- Expected vs. actual behavior.
- Log file — see the **About → Diagnostics** page in the app, or
  `$HOME/.voice-typer/voice-typer.log` on disk.

---

## Questions?

Open an issue with the `question` label on the
[GitHub issue tracker](https://github.com/AbdallahIsDev/voice-typer/issues).
For security-sensitive reports, see `SECURITY.md` — do not open a
public issue for vulnerabilities.
