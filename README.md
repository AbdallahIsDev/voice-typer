# Voice Typer

Premium offline background voice-to-text utility. Runs in your system tray. Press the hotkey, talk, press it again — final text is copied to your clipboard and pasted safely when a text field is focused.

## How It Works

1. App starts in the system tray (tray icon appears quickly, model loads in the background)
2. Press the hotkey anywhere to start recording (configurable in Settings)
3. Talk freely — switch apps, browse, do whatever
4. Press the hotkey again to stop (or let silence/max duration stop it automatically)
5. Audio is transcribed locally (faster-whisper or optional Qwen3-ASR, your GPU if available)
6. Text is cleaned (dedup, misspellings, self-corrections, capitalization)
7. Text is copied to clipboard
8. If a text field is focused, text is auto-pasted there; otherwise it stays in the clipboard

No cloud. No API keys. No rate limits. Fully offline after first model download.

## Runtime Architecture

Voice Typer runs on **two parallel runtime stacks** during the migration from Electron to Tauri v2:

1. **Electron (current default shipping app)** — `voice_typer/client/src/main/index.ts` (Electron main process) spawns the Python backend as a child process and bridges IPC over a local TCP socket on `127.0.0.1:9876`. This is the app users install today from the [Releases page](https://github.com/AbdallahIsDev/voice-typer/releases).

2. **Tauri v2 + Python sidecar (in migration, not yet the default)** — `src-tauri/src/main.rs` is a Rust host that spawns the Python backend as a Nuitka-frozen sidecar via Tauri's `externalBin` mechanism and bridges IPC over a localhost WebSocket (the sidecar binds an ephemeral loopback port and announces it on stdout; the host connects and authenticates with a bearer token). The Rust host also spawns the runtime-pack worker exe (`voice-typer-worker-<triple>`), a second Nuitka-frozen process that owns the heavy offline-ASR stack; the sidecar talks to it over a dedicated second WebSocket hop to serve the `transcribe_offline` command. This stack is being developed per [ADR-0020](docs/adr/0020-desktop-runtime-migration-analysis.md).

The Electron stack remains fully shippable as a **reversible fallback** until each platform's Tauri build is proven and cut over (Windows first → macOS → Linux, per [docs/migration/cutover-playbook.md](docs/migration/cutover-playbook.md)). The Tauri stack is **additive** — the Electron code is untouched and remains buildable, runnable, and shippable at every phase. The React renderer (`voice_typer/client/src/renderer/`) is **shared between both stacks** — the same bundle runs under Electron (via the preload `contextBridge`) and under Tauri (via `voice_typer/client/src/renderer/src/lib/tauri-bridge.ts`, which auto-detects the host at startup and installs the `window.python` / `window.bubble` / `window.window_` namespaces using Tauri's global `__TAURI__` API).

### Developer environment variables

| Variable | Purpose | When to set it |
|---|---|---|
| `TAURI_SIDECAR=1` | Tells the Python backend it is running under the Tauri host. Disables the Python-side heartbeat watchdog (ADR-0018) and the Win32 single-instance mutex — the Tauri host provides both. | Set automatically when the sidecar is launched with `--ws` (i.e. `python -m voice_typer.server.ipc_server --ws`). Set manually only when running the WS server standalone for debugging. |
| `VOICE_TYPER_SIDECAR_DEV=1` | Tells the Tauri Rust host to spawn `python -m voice_typer.server.ipc_server --ws` as a subprocess instead of the Nuitka-frozen `externalBin` binary. Lets you iterate on UI/transport changes in seconds — no ~10-minute Nuitka rebuild required. | Set when running `cargo tauri dev` (see [CONTRIBUTING.md § Tauri Development](CONTRIBUTING.md#tauri-development-migration-in-progress)). Do NOT set for `cargo tauri build` (production builds must use the frozen sidecar). |

### Dev mode

```bash
cd src-tauri
VOICE_TYPER_SIDECAR_DEV=1 cargo tauri dev
```

This runs the Rust host against a `python -m voice_typer.server.ipc_server --ws` subprocess for fast iteration — no Nuitka rebuild needed. See [`docs/migration/`](docs/migration/) for the full set of validation runbooks (Windows / macOS / Linux) and the cutover playbook.

## Quick Install (Windows — Easiest)

1. Go to **[Releases](https://github.com/AbdallahIsDev/voice-typer/releases)**
2. Download the latest `VoiceTyper-Setup-*.exe`
3. Double-click the installer
4. Click Next → Install → Finish
5. Voice Typer starts automatically — look for the microphone icon in your system tray

No Python, no terminal, no commands needed.

> **Note:** The installer does not bundle a `LicenseFile` — Inno Setup
> defaults to showing a standard license wizard page only if one is
> configured.  **Autostart is enabled by default** (the installer
> creates a Windows Scheduled Task).  To disable autostart after
> installation, open the Electron app (tray menu → **Open App**) and
> turn off the **Launch at Login** toggle under Settings → General, or
> delete the scheduled task in Task Scheduler.

## Requirements

- **Windows 10/11**, **macOS 13+** (Ventura or newer), or **Linux** (X11 or Wayland) — Voice Typer is cross-platform. See [docs/PLATFORM_STATUS.md](docs/PLATFORM_STATUS.md) for the full per-OS support matrix and minimum-version rationale.
- A microphone
- Internet on first run (downloads the Whisper model for the selected model size)
- **macOS only**: Accessibility permission for the native key listener (see [Troubleshooting](#troubleshooting))
- **Linux only**: membership in the `input` group so the native key listener can read `/dev/input/event*` (see [Troubleshooting](#troubleshooting))

## For Developers (from source)

Requires **Python 3.10+**. Install from [python.org](https://python.org).

### Using `uv` (recommended for fast setup)

[`uv`](https://docs.astral.sh/uv/) is a fast Python package installer/resolver
by Astral. It is **10-100x faster than pip for cold installs** (parallel
downloads, a global cache, and Rust-based resolution) and is the recommended
way for contributors to get a dev environment running. The project already
declares a `[tool.uv]` section in `pyproject.toml` for uv-native preferences.

```bash
# Install uv (one-time)
curl -LsSf https://astral.sh/uv/install.sh | sh  # macOS / Linux
# or: pip install uv
# or: winget install astral-sh.uv                # Windows

# Create a venv and install deps in one step
cd voice-typer
uv venv
# `dev` extras = ruff, mypy, pre-commit, mutmut
# `test` extras = pytest, pytest-cov, hypothesis, pytest-benchmark, ...
uv pip install -e ".[dev,test]"

# Activate the venv (optional — uv run uses .venv automatically)
source .venv/bin/activate          # macOS / Linux
# or: .venv\Scripts\activate       # Windows

# Run tests
uv run --no-sync pytest
# or just: pytest                   # if you activated the venv above
```

> **Why `uv pip install` and not `uv sync`?** The canonical uv workflow is
> `uv sync --extra dev --extra test` followed by `uv run pytest`, and that
> is what we recommend *once* the upstream `qwen-asr` package publishes a
> `>=0.1` release (only `0.0.6` exists on PyPI today, so the optional
> `[qwen]` extra fails resolution and `uv sync`'s comprehensive lockfile
> can't be generated). Until then, `uv pip install -e ".[dev,test]"`
> uses uv's pip-compatible mode — same speed, same global cache, but
> skips the all-extras lockfile step that triggers the qwen-asr failure.
> You still get all of uv's speed benefits; you just don't get a
> checked-in `uv.lock` file. See `pyproject.toml`'s `[tool.uv]` block
> for the `environments` constraint that limits uv's resolution to
> Python 3.10-3.14 (matching the project's supported window).

For production-only deps (no test/dev tooling):

```bash
uv venv
uv pip install .
```

### Editable install (recommended for development)

```bash
pip install -e ".[test]"
pytest
```

### Production install (simulates end-user setup)

```bash
pip install .
```

> **Note:** the default `pip install .` pulls in the GPU-enabled torch
> wheel (~2 GB) via the `faster-whisper` / `transformers` dependencies.
> If you don't have an NVIDIA GPU and want a smaller CPU-only install,
> use `pip install . --no-deps` followed by manual installation of the
> CPU-only variants, or use the `[cpu]` extra (if available). The
> `pyproject.toml` `dependencies` block lists the full set.

The package must be installed (not just run from source) for autostart to work.

### Reproducible builds (hash-pinned)

For a fully reproducible environment (e.g. for release engineering or
bisecting a regression), install from the hash-pinned lockfile instead
of resolving from `pyproject.toml`:

```bash
pip install --require-hashes -r requirements-lock.txt
```

The lockfile is generated via `uv pip compile --generate-hashes` and
enforces sha256 verification of every wheel and sdist. Its completeness
against `pyproject.toml` is verified by
`tests/test_requirements_lock_completeness.py`. (the legacy
`requirements.txt` mirror file was removed because it drifted out of
sync with `pyproject.toml` — `pyproject.toml` is now the single source
of truth for Python dependencies.)

### Optional: Qwen ASR backend

Voice Typer ships with Whisper by default. To also enable the experimental
Qwen3-ASR-0.6B backend, install the additional dependencies:

```bash
pip install qwen-asr torch --index-url https://download.pytorch.org/whl/cpu
```

For CUDA support, replace `cpu` with `cu118` or `cu121` matching your
NVIDIA driver version. Then set `asr_backend: "qwen"` and `qwen_model_path`
in the config file.

### ASR Auto-Setup

On startup, Voice Typer runs an automatic ASR dependency check (`asr_setup.py`) that detects available GPU hardware, verifies required packages are installed, and downloads model weights if needed. This runs transparently in the background — no manual setup required.

## Run

**If you used the installer:** find "Voice Typer" in your Start Menu and click it.

**If you installed from source:**

```bash
voice-typer
```

Or:

```bash
python -m voice_typer
```

The app runs in the system tray — look for the microphone icon. No terminal window stays open.

### Single Instance

Only one Voice Typer process can run at a time. If you launch a second instance, it will show "Voice Typer is already running" and exit immediately. This prevents duplicate tray icons and hotkey conflicts.

### Desktop Shortcut

A desktop shortcut with a microphone icon is automatically created on first startup. The shortcut uses `pythonw.exe` so no console window appears. (There is no manual shortcut action in the tray menu — the shortcut is auto-created on first run.)

## Fast Startup

The tray icon appears quickly on startup (cold import is measured in tens of milliseconds — ~84 ms observed on Windows — and varies with hardware and OS; the CI-tracked worker-startup metric runs ~0.9 s first-run / ~0.3 s median on CI runners against a ≤600 ms median target; run `python bench/bench_startup.py` to measure on yours). The transcription engine is created in a background thread while the UI becomes immediately responsive. The hotkey is usable once the model finishes loading — cold-start load time varies by model size and disk speed (run `python bench/bench_transcription.py` for measurements). If the model hasn't loaded yet when you press it, you'll see a "Starting up — please wait" message. See `bench/` for benchmark tooling.

## Settings

Open the Electron app from the tray menu → **Open App** to change the hotkey,
microphone, model, start-on-login, and notifications. The Settings page
exposes every configurable field with validation and inline help.

Settings are stored in JSON for troubleshooting:

`<DATA_DIR>/config.json`

On Windows this is `%APPDATA%\voice-typer\config.json` for new installs
(`C:\Users\<you>\AppData\Roaming\voice-typer\config.json`). If you upgraded
from an older version, `%USERPROFILE%\.voice-typer\config.json` is still used.
See `docs/home-directory.md` for the full per-platform layout.

Use Settings for normal changes. Use the advanced settings button to open the raw config file only when troubleshooting.

All configurable fields, their defaults, and descriptions are defined in
`voice_typer/server/config/__init__.py` — that file is the canonical source of truth
for every setting. Key categories:

| Category | Settings |
|---|---|
| Hotkey | `hotkey`, `recording_mode`, `repaste_hotkey` |
| Recording | `microphone`, `sample_rate`, `silence_warning_seconds`, `silence_auto_stop_seconds`, `max_recording_seconds` |
| Transcription | `model_size`, `language`, `device`, `beam_size`, `streaming_transcription` |
| Behavior | `autostart`, `paste_on_stop`, `show_notifications`, `text_cleanup_enabled` |
| ASR backend | `asr_backend` (whisper/qwen/parakeet), `qwen_model_path`, `parakeet_model_path` |
| Audio warnings | `audio_quality_warnings`, `audio_clipping_warning`, `audio_low_volume_warning`, `audio_noise_warning` |
| History | `history_retention_days`, `history_retention_count`, `history_max_entries` |

### Tray Menu Structure

The tray menu is intentionally minimal — most configuration lives in the
Electron app. The actual menu (`build_menu_for_tray` in
`voice_typer/server/tray_menu.py`) is:

```
Open App
Start Dictation (current hotkey)
Force Cancel Stuck Transcription   (only shown while transcribing)
─────────────────────
Models ▸           → downloaded models + "More models..." link
Microphones ▸      → devices (active one checked) + "More microphones..."
─────────────────────
Settings
History
Help
─────────────────────
Restart
Quit
```

The dictation item's label switches to "Stop Dictation" while recording.
There is no Hotkey submenu — hotkey selection lives in the Electron app's
Settings page.

### Hotkey

The dictation hotkey defaults to `Caps Lock` on **ALL platforms** (Windows, macOS, Linux). This is universally present, rarely used in shortcuts, and easy to remap. The `Fn`/Globe key remains available as an alternative on macOS via the Settings dropdown.

You can pick any key or combination via the Settings capture dialog (the **Hotkey** field's **Capture** button). The dialog accepts modifier-only releases as single-key hotkeys — press `Alt` alone and release, and the hotkey becomes `<alt>`.

The `Fn` key is only supported on macOS. On Windows and Linux it is firmware-only (intercepted by the keyboard's own controller before the OS sees it), so the Settings UI hides it on those platforms.

### Custom Hotkeys

Select **Custom** in the Hotkey submenu (or use Settings → Hotkey → Capture) to pick any key combination (e.g., `Ctrl+Shift+K`, `Alt+Q`, or a bare modifier like `Alt`). The app validates the format and applies it immediately.

### Help Overlay (`?`)

Press **`?`** anywhere in the app to open the help overlay (a small modal that lists every keyboard shortcut and a punctuation cheat sheet). The overlay is rendered by `App.tsx` (`showHelpOverlay` state) and uses the `Modal` + `PunctuationCheatSheet` components. Press **`Esc`** (or click outside) to close it.

The overlay lists the active dictation hotkey plus:

| Shortcut | Action |
|---|---|
| Dictation hotkey | Toggle dictation on/off |
| `Esc` | Cancel active dictation |
| Repaste hotkey (default `Ctrl+Alt+V`) | Re-paste the last transcription |
| `Ctrl+B` | Toggle the sidebar |
| `Ctrl+,` | Open Settings |
| `Ctrl+H` | Go to Home |
| `Tab` / `Shift+Tab` | Navigate focusable controls |
| `Space` / `Enter` | Activate the focused control |
| `Ctrl+Plus` / `Ctrl+Minus` | Zoom text size (in / out) |
| `?` | Open this help overlay |
| `Alt+←` / `Alt+→` | Navigate back / forward |

Below the shortcut list, the **Punctuation cheat sheet** shows the spoken-form → character mappings Voice Typer recognizes (the cleanup pipeline in `voice_typer/server/text_cleanup.py` normalizes spacing around these without dropping them):

| Spoken form | Inserted character |
|---|---|
| comma | `,` |
| period | `.` |
| question mark | `?` |
| exclamation point | `!` |
| semicolon | `;` |
| colon | `:` |
| apostrophe | `'` |
| open quote / close quote | `"` |
| new line | ↵ |
| new paragraph | ¶ |

The cheat sheet is rendered by `voice_typer/client/src/renderer/src/components/help/PunctuationCheatSheet.tsx` and the localized labels live under `help.punctuation.*` in `i18n/translations/*.json`.

### Model Selection

Available models (subject to Whisper upstream naming and sizes):

| Model | Notes |
|---|---|
| `tiny.en` | Fastest, lower accuracy |
| `small.en` | Default, best balance of speed and accuracy |
| `medium.en` | Higher accuracy for difficult audio |
| `qwen` | Qwen3-ASR, requires separate installation (`pip install qwen-asr torch`) |
| `parakeet` | NVIDIA Parakeet TDT v3 — English-only, optimized for GPU. Weights are auto-downloaded from HuggingFace on first use. Set `asr_backend = "parakeet"` in config or pick "Parakeet" from the Models submenu. |

## Silence Detection and Auto-Stop

Voice Typer monitors audio input during recording to detect microphone disconnections and extended silence:

### Silence Warning

Uses variance-based analysis to detect when the microphone stops capturing audio. When silence exceeds the configured threshold (default 20s), a safety notification warns you to check your microphone. The warning repeats with exponential backoff (10s, 20s, 40s...) until audio resumes or recording stops. Configure from **Settings → Recording → Silence Warning** (Electron app → Settings).

### Auto-Stop Timeout

Recording automatically stops after a configurable silence period (default 2 minutes). This prevents runaway recordings if you walk away or forget to press the hotkey. Configure from **Settings → Recording → Auto-Stop Timeout** (Electron app → Settings).

### Max Recording Duration

Recording automatically stops after reaching a maximum time limit. Configure from **Settings → Recording → Max Recording** (Electron app → Settings).

All three features fire **safety notifications** that bypass the notification toggle — you will always be alerted when recording stops due to silence or max duration.

## Notification System

Notifications are split into two categories:

- **Safety alerts** (silence warnings, auto-stop, max duration) — always fire regardless of notification settings. You will never miss a safety-critical event.
- **Dictation notifications** (transcription complete, errors, clipboard status) — controlled by the **Dictation Notifications** toggle under **Settings → General** (Electron app → Settings).

## Microphone Selection

Microphone selection lives in the Electron app's **Microphone** page
(open the tray menu → **Open App** → Microphone). The page lists every
input device reported by PortAudio, shows a live level meter, and
remembers your selection across restarts.

The `microphone` config value is the **device index** (a string like `"3"`),
not the display name. This avoids ambiguity when multiple host APIs expose
devices with the same name.

To set manually, open the config file and set `"microphone"` to the device
index string. To find the right index, run:

```bash
python -c "import sounddevice as sd; [print(i, d['name'], sd.query_hostapis(d['hostapi'])['name']) for i,d in enumerate(sd.query_devices()) if d['max_input_channels'] > 0]"
```

## Autostart

Enable or disable from **Settings → General → Launch at Login** (Electron app → Settings).

- **Windows**: registers itself in `HKCU\...\Run` using `pythonw.exe` (no console window).
- **macOS**: installs a `LaunchAgents` plist in `~/Library/LaunchAgents/`.
- **Linux**: drops a `.desktop` file in `~/.config/autostart/`.

Global hotkey detection on every platform uses the out-of-process native binary (see [Hotkey Architecture](#hotkey-architecture)); the legacy `RegisterHotKey`/`GetAsyncKeyState` polling and `pynput` paths remain as fallbacks when the native binary is absent. The package must be installed (`pip install .`) for autostart to work.

## Auto-Paste Behavior

When `paste_on_stop` is enabled, the app detects whether a text input is focused (via Win32 API on Windows; via the focused-window process name on macOS/Linux). Auto-paste only happens when a text field is confirmed focused. If no text input is focused, the keystroke is skipped and the text stays in your clipboard.

The paste keystroke is **terminal-aware**: the app checks the focused window's process name against `_TERMINAL_PROCESS_NAMES` in `voice_typer/server/clipboard/linux.py` (Windows Terminal, Warp, Alacritty, iTerm2, Terminal.app, gnome-terminal, konsole, xfce4-terminal, …). For terminal targets it sends **Shift+Insert** (macOS uses Cmd+V); for every other focus target it sends Ctrl+V. If you're pasting into a terminal that doesn't accept either keystroke, use the terminal's "Paste" menu item.

The clipboard always gets the transcribed text when transcription succeeds. The app never pastes provisional streaming text.

## Text Corrections

### Self-Correction Detection

The cleanup pipeline detects and removes self-corrections in speech (e.g., "I went to the store the shop" → "I went to the shop"). Uses a higher threshold to avoid false positives: requires at least 5 characters or half the word length before matching a correction.

### Case-Preserving Corrections

Phrase corrections preserve the original casing pattern. If you speak in ALL CAPS, corrections stay in ALL CAPS. Title Case and mixed case patterns are also preserved.

### Roman Numeral Detection

Context-aware capitalization of "I" that skips capitalization when followed by Roman numeral context words (e.g., "chapter i of the book" stays lowercase). This prevents false capitalization of the pronoun in academic or numbered contexts.

### External Corrections

Bundled corrections are in `voice_typer/server/corrections.json` (misspellings, phrase corrections, extra-word patterns).
Place a `voice-typer-corrections.json` in the config directory (or set `corrections_path` in config) to override bundled entries.
External file format: `{"misspellings": {...}, "phrase_corrections": [["bad", "good"], ...], "extra_word_patterns": [["bad", "good"], ...]}`.

## Streaming Transcription

Hidden streaming transcription processes audio in overlapping chunks during recording for faster finalization. Key behaviors:

- **Retry counter**: Transient streaming errors no longer permanently disable streaming. Three consecutive failures are required before falling back to batch transcription for the session.
- **Word preservation**: Committed words are preserved during streaming. Deduplication structures are pruned but the output accumulator stays intact, preventing word drops across chunk boundaries.
- **Emergency override**: Set `VOICE_TYPER_STREAMING=0` to disable streaming entirely.

## Hotkey Architecture

Voice Typer detects global hotkeys via an **out-of-process native binary** spawned by the Python backend. The binary speaks a line-delimited stdout wire protocol (`READY`, `KEY_DOWN:<Name>`, `MOD_DOWN:<Name>`, `FN_DOWN` (macOS only), …) that the Python side parses and matches against the registered hotkey. The same binary is reused in record mode for the Settings capture dialog.

- **macOS** — `voice_typer/server/native/macos-key-listener` (Swift) — uses `NSEvent.modifierFlags.function` + a `CGEvent` tap to support the Fn/Globe key.
- **Windows** — `voice_typer/server/native/windows-key-listener.exe` (C) — uses a `WH_KEYBOARD_LL` low-level hook (event-driven, supports key suppression, lower CPU than polling).
- **Linux** — `voice_typer/server/native/linux-key-listener` (C) — reads `/dev/input/event*` (evdev), which works on both X11 and Wayland.

This design gives us crash isolation (a hotkey listener crash can't take down the Python backend), per-platform key suppression (so `Caps Lock` doesn't actually toggle caps state on Windows), and access to platform-specific keys (Fn on macOS) that aren't reachable from Python alone.

If the native binary is missing or fails to start, `create_hotkey_backend()` transparently falls back to the legacy in-process backends (`PynputHotkey`, `WindowsNativeHotkey`, `WaylandHotkey`) so the app still works without a rebuild.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and [`docs/adr/0007-native-hotkey-architecture.md`](docs/adr/0007-native-hotkey-architecture.md) for the full design and rationale.

## Platform Notes

Voice Typer is **cross-platform** — Windows, macOS, and Linux (X11 and Wayland) are all supported. The hotkey backend, autostart adapter, and clipboard/focus backends are selected per-OS at runtime.

- **Windows 10/11**: tested by the maintainer. There is no CI matrix for Win10 vs Win11 yet — contributors on either version are welcome to report issues. Several Win10-specific code paths (notably `taskkill /T /F` and the legacy `wmic` calls) have been removed; the app now uses `psutil` for process introspection on all platforms.
- **macOS 13+** (Ventura): native Swift key listener supports the Fn/Globe key. Requires Accessibility permission (see [Troubleshooting](#troubleshooting)). Autostart uses a `LaunchAgents` plist. macOS 12 may work but is not tested; CI runners pin to `macos-13` (Intel) and `macos-14` (Apple Silicon).
- **Linux (X11 and Wayland)**: native C key listener reads `/dev/input/event*` (evdev), which works on both X11 and Wayland. Requires the user to be in the `input` group (see [Troubleshooting](#troubleshooting)). Autostart uses a `.desktop` file in `~/.config/autostart/`.
- Autostart uses `pythonw.exe` for background execution on Windows (no console window).
- Global hotkey uses the out-of-process native binary on every platform; legacy `RegisterHotKey`/`GetAsyncKeyState` polling (Windows) and `pynput` (macOS/Linux X11) remain as fallbacks.
- Focus detection for safe auto-paste is Windows-only; on macOS/Linux the text is always copied to the clipboard (auto-paste is skipped).
- Win32 console control handler keeps the tray app alive when the console is closed (Windows-only).
- GPU acceleration via CUDA if available (NVIDIA wheel DLL paths configured automatically on Windows).
- Composite hotkeys with modifiers supported on all platforms via the native binary, and via `RegisterHotKey`/`pynput` fallbacks.

## Architecture

```
voice_typer/
├── __init__.py         # Package init, __version__
├── __main__.py         # Entry point (python -m voice_typer)
├── server/             # Python backend (was voice_typer/*.py before refactor)
│   ├── app.py          # VoiceTyperApp orchestrator — startup, state machine, thread safety
│   ├── asr_setup.py    # ASR auto-setup: GPU detection, dependency checking, weight downloading
│   ├── asr_registry.py # Registry of ASR backends (whisper/qwen/parakeet)
│   ├── config/        # Configuration package (platform-aware paths, validation, schema versioning) — see config/__init__.py
│   ├── recording/     # Session-based audio recording (package: recorder, buffer, resampling, exceptions)
│   ├── transcription.py  # faster-whisper engine with GPU→CPU fallback chain
│   ├── qwen_engine.py  # Optional Qwen3-ASR-0.6B backend
│   ├── parakeet_engine.py  # Optional NVIDIA Parakeet TDT v3 backend
│   ├── cloud_engines.py   # Cloud ASR / LLM HTTP transports
│   ├── streaming.py    # Streaming transcription with overlapping audio windows
│   ├── text_cleanup.py # Post-transcription cleanup (dedup, misspellings, capitalization)
│   ├── clipboard/     # Clipboard copy + safe auto-paste (package: manager, linux, windows; terminal-aware: Shift+Insert)
│   ├── hotkeys/        # Hotkey backend abstraction package (Win32 native / pynput / Wayland fallback)
│   ├── hotkey_dispatcher.py  # Owns the 3 hotkey backends (dictation / ESC / repaste)
│   ├── ipc_server.py   # IPC server for the desktop client: JSON-over-TCP (port 9876) under Electron; WebSocket (--ws) as the Tauri sidecar
│   ├── server_platform/  # OS-specific autostart adapters + mic listing + desktop shortcut (package)
│   ├── tray.py         # System tray icon (pystray) + dynamic menu
│   ├── tray_menu.py    # Tray menu builder (extracted from tray.py)
│   ├── tray_types.py   # AppState enum + TrayController Protocol
│   ├── model_manager.py    # Model load/unload lifecycle
│   ├── recording_controller.py  # Recording lifecycle + streaming session
│   ├── dictation_pipeline.py    # Transcription pipeline (cleanup, history, paste)
│   ├── crash_recovery.py    # Crash-recovery buffer for unpasted transcriptions
│   ├── history_db.py    # SQLite history DB
│   ├── vocabulary.py    # Custom vocabulary manager
│   ├── templates.py     # Text templates
│   ├── llm_polish.py    # Optional LLM-based transcription polish
│   ├── vad.py           # Voice activity detection
│   ├── volume_ducker.py / volume_backends/  # System volume ducking (volume_backends is a package: linux/macos/windows)
│   ├── task_scheduler.py  # Pre-warm task scheduler (Windows Task Scheduler / cron)
│   ├── prewarm/        # Pre-warm orchestration (package: pipeline, cli, paths, cache_probe, …)
│   ├── prewarm_scheduler_posix.py  # POSIX pre-warm scheduling (LaunchAgent / systemd user timer)
│   ├── platform_utils.py  # Platform detection helpers (is_windows / is_macos / is_linux)
│   └── corrections.json  # Bundled misspellings, phrase corrections (canonical path: voice_typer/server/corrections.json)
├── client/             # Electron frontend (TypeScript/React/Vite)
│   ├── src/main/       # Electron main process — window lifecycle, IPC bridge
│   ├── src/renderer/   # React renderer — pages (Home, Settings, Models, History, ...)
│   ├── src/preload/    # Context bridge (IPC channel whitelists)
│   ├── electron.vite.config.ts  # electron-vite build config
│   ├── electron-builder.yml     # Distribution config (NSIS / DMG / AppImage)
│   ├── package.json    # Node deps + scripts (dev / build / lint / test)
│   └── vite.config.ts  # Vite aliases (shadcn CLI compatibility shim)
└── tests/              # Pytest suite (E2E + per-module unit tests)
```

Key design decisions:

- **Fast startup**: Tray icon appears quickly. TranscriptionEngine created in background thread.
- **Hidden streaming transcription**: Records the full session while transcribing safe overlapping chunks in the background. On stop, it finalizes the unconfirmed tail and falls back to full-session batch transcription if streaming state is unsafe.
- **Dual ASR backends**: Whisper (default, via faster-whisper) with 4-level GPU->CPU fallback, optional Qwen3-ASR-0.6B, and optional NVIDIA Parakeet. Backend selection via `asr_backend` config key.
- **ASR auto-setup**: GPU detection, dependency verification, and weight downloading at startup.
- **Text cleanup pipeline**: High-confidence adjacent duplicate removal, self-correction cleanup, misspelling correction, phrase substitutions, extra-word removal, sentence capitalization, pronoun-I capitalization with Roman numeral awareness, and case-preserving phrase corrections.
- **Low-audio hallucination guard**: Rejects known boilerplate phrases only when audio evidence indicates near-silence.
- **Fast default decoding**: Greedy decoding with VAD filter and no timestamp decoding for low latency.
- **Safe auto-paste**: Paste keystrokes only sent when a text input is confirmed focused. Terminal emulators get Shift+Insert. Clipboard always populated.
- **Composite hotkey support**: Hotkeys with modifiers via both Win32 RegisterHotKey and pynput fallback. Custom hotkey input via dialog.
- **Microphone fallback chain**: Same-name candidate discovery across host APIs, ranked by reliability. Falls back further to all available input devices if the configured mic fails.
- **Silence detection**: Variance-based mic disconnect detection with repeating warnings (exponential backoff). Auto-stop on prolonged silence.
- **Notification split**: Safety alerts always fire. Dictation notifications controlled by user toggle.
- **Single instance**: Windows named mutex prevents duplicate processes.
- **Desktop shortcut**: Auto-created on first startup with microphone icon.
- **Buffer management**: O(1) deque buffer with hard cap. Telemetry warnings at configurable thresholds.
- **Console survival**: Win32 console control handler lets the tray app survive console closure.
- **Tray-first**: The tray icon is the primary UI. It appears before model loading starts.
- **Graceful degradation**: GPU → CPU → tiny.en fallback chain. If auto-paste fails, clipboard still has the text. If hotkey fails, tray menu still works. If model loading fails, app stays alive and retries.
- **Thread safety**: Busy state guarded by `threading.Event`, streaming session access protected by `threading.Lock`.
- **Config schema versioning**: `schema_version` field enables future migration support.

## Log File

Voice Typer writes **two** log files (one per process). The Python
backend log lives directly under the data directory; the Tauri Rust
host log lives under a `logs/` subdir. **Single-file policy:** each log
is exactly ONE file — when it exceeds 5 MiB it is truncated IN PLACE
(emptied) and writing continues to the same file; numbered backups
(`.1`, `.2`, ...) are NEVER created
(`_SecureTruncatingFileHandler(maxBytes=5_242_880, backupCount=0)` in
`voice_typer/server/log/__init__.py`).

| Platform | Python backend log | Tauri Rust host log |
|----------|--------------------|---------------------|
| Windows (new installs) | `%APPDATA%\voice-typer\voice-typer.log` | `%APPDATA%\voice-typer\logs\voice-typer.log` |
| Windows (existing users) | `%USERPROFILE%\.voice-typer\voice-typer.log` (legacy path honored if it exists) | `%USERPROFILE%\.voice-typer\logs\voice-typer.log` |
| macOS | `~/Library/Application Support/voice-typer/voice-typer.log` | `~/Library/Application Support/voice-typer/logs/voice-typer.log` |
| Linux | `$XDG_DATA_HOME/voice-typer/voice-typer.log` (falls back to `~/.local/share/voice-typer/voice-typer.log`) | `$XDG_DATA_HOME/voice-typer/logs/voice-typer.log` |

Override the location by setting `VOICE_TYPER_CONFIG_DIR` (the Python
log lives directly under the resolved `<DATA_DIR>`; the Rust host log
lives under `<DATA_DIR>/logs/`). See `docs/home-directory.md` §"Log
File Paths" for the canonical per-platform reference and the source-of-
truth constants.

Uses `_SecureTruncatingFileHandler` (5 MiB cap, truncates in place — single-file policy) with structured logging (session ID, component name).

## Troubleshooting

### Hotkey doesn't fire

- **Build the native binary**: the native key listener is a compiled binary, not bundled with the source tree. Build it with:
  ```bash
  bash scripts/build/compile_native.sh        # macOS / Linux
  # or, on Windows:
  powershell -ExecutionPolicy Bypass -File scripts/build/compile_native.ps1
  ```
  The script auto-detects your platform and only builds the binary that matches it. The compiled binary lives in `voice_typer/server/native/`.
- If the binary is missing, Voice Typer falls back to the legacy in-process backends (`PynputHotkey` / `WindowsNativeHotkey` / `WaylandHotkey`) — this is enough to keep the app usable, but you lose Fn-key support on macOS and Wayland support on Linux.
- Check the log file for `[HOTKEY]` messages indicating which backend was selected.

### Hotkey doesn't work on macOS

- Voice Typer needs Accessibility permission to read the keyboard.
- On first launch, it should show a notification with a link to System Settings.
- If you missed it: System Settings → Privacy & Security → Accessibility → add Voice Typer.
- After macOS updates, you may need to re-grant Accessibility.

### Hotkey doesn't work on Linux

- If you installed via `.deb` or `.rpm`: log out and log back in after install (the `input` group change needs a new login session).
- If you're using the AppImage: on first launch, Voice Typer will prompt for your password to install keyboard permissions.
- To check: `groups` should include `input`. If not, run `sudo usermod -aG input $USER` and log out/back in.

### Hotkey stopped working after a while

- This can happen if antivirus software (Windows) or macOS code-signing changes kill the native key-listener binary.
- Voice Typer should automatically fall back to compatibility mode and show a notification.
- To restore full mode: restart Voice Typer.

### macOS: Accessibility permission

The native key listener needs Accessibility permission to observe keyboard events system-wide:

1. Open **System Settings → Privacy & Security → Accessibility**.
2. Enable the toggle next to **Voice Typer** (or the terminal you launched it from, if running from source).
3. If Voice Typer isn't listed, click **+** and add it.

### macOS: Re-grant Accessibility after a macOS update

macOS updates sometimes invalidate the Accessibility grant for previously-trusted apps. If the hotkey stops working immediately after a macOS update, go back to **System Settings → Privacy & Security → Accessibility**, toggle Voice Typer off and back on, or remove it and re-add it.

### Linux: Add yourself to the `input` group

The native key listener reads `/dev/input/event*`, which on most distros is owned by root:`input`. Add yourself to the group, then **log out and back in** (group membership is evaluated at login):

```bash
sudo usermod -aG input $USER
```

If you can't log out, you can run the binary as root for testing — but the proper fix is the group add above.

### Linux: Caps Lock remap

The Linux evdev backend is read-only — it can observe keystrokes but can't suppress them. If you use `Caps Lock` as the hotkey (the default on Linux), pressing Caps Lock will **also** toggle the OS caps-lock state. To neutralize that, add the following to `~/.xprofile` (X11) or your compositor's startup script (Wayland):

```bash
setxkbmap -option caps:none
```

For more permanent behavior across Wayland compositors, consider `keyd` or `kmonad`.

### Windows: Caps Lock remap

The Windows native binary **does** suppress the Caps Lock keydown event so the OS doesn't toggle caps state — but only when Voice Typer is running. If you want Caps Lock to be neutralized even when Voice Typer isn't running (or you want it remapped to a different key entirely), use one of these:

- **PowerToys Keyboard Manager** (recommended): install PowerToys → Keyboard Manager → Remap a key → remap `Caps Lock` to `Disable`.
- **Registry Scancode Map**: add a `Scancode Map` binary value under `HKLM\SYSTEM\CurrentControlSet\Control\Keyboard Layout` to globally remap Caps Lock. (Standard caveat: edits to `HKLM` require admin rights and a reboot.)

### Word drops

- Keep hidden streaming enabled unless diagnosing: it finalizes the tail and falls back to batch transcription if timestamps are unsafe.
- Streaming preserves committed words and tolerates transient errors (3 consecutive failures before fallback).
- Check the log for `[STREAMING]` messages.
- For emergency batch-only mode, run with `VOICE_TYPER_STREAMING=0`.

### Duplicate words

- The cleanup pipeline removes only high-confidence adjacent duplicate words/phrases.
- Intentional short repeats like `no no no`, `very very good`, and `test test one two` are preserved.
- If a real repeated phrase is removed, save the exact raw phrase and the log timestamp.

### No speech detected

- Check the selected microphone in the Electron app (tray menu → **Open App** → Microphone).
- Watch the log line `RMS`, `peak`, and `silence_pct`. Near-zero RMS usually means the wrong mic or muted input.
- If audio is quiet but real, move closer to the mic or choose the non-virtual physical microphone.

### Wrong microphone

- Use the Electron app's Microphone page (tray menu → **Open App** → Microphone). Duplicate names show host APIs where needed.
- If one host API fails, Voice Typer can fall back to another entry with the same physical microphone name and persist the working device index.

### Silence warnings during recording

- If you get silence warnings while actively speaking, your microphone may have a high noise floor or the silence threshold is too aggressive.
- Adjust **Settings → Recording → Silence Warning** (Electron app → Settings) to a higher value (e.g., 15s or 20s).
- Check that the correct microphone is selected and is not being used by another application.

### Recording stops unexpectedly

- Check if **Auto-Stop Timeout** or **Max Recording** triggered. Both fire safety notifications.
- Adjust these from **Settings → Recording** (Electron app → Settings).

### Slow stop after pressing the hotkey

- Current logs include `Stop timing` with stream, concat, stats, resample, and total milliseconds.
- The resampler is warmed at startup. If stop is slow, check whether `Resampler warmed up` appears before the recording.
- CPU fallback can make transcription slower after stop, especially for long recordings.

### CUDA fallback

- Voice Typer tries CUDA first. If CUDA/cuBLAS/cuDNN fails during load or transcription, it falls back to CPU.
- On Windows, NVIDIA wheel DLL paths are added automatically when installed.
- The fallback chain: configured device → CPU/int8 with original model → CPU/int8 with tiny.en → CPU/float32 with tiny.en.

### Autostart

- Install the package first: `pip install .`
- Enable from **Settings → General → Launch at Login** (Electron app → Settings).
- Windows uses `pythonw.exe -m voice_typer` when available so no console window stays open.

### Settings window

- If the settings window does not appear, check the log for errors.
- The window is the Electron app's Settings page (React + shadcn/ui), organized into sections (General, Recording, Audio, Models, AI Enhancement, Privacy, Theme). Open it via the tray menu → **Open App** → Settings.
- Cancelling discards changes; Save validates and applies them immediately.

### Text corrections

- Self-correction detection uses a higher threshold (min 5 chars or half word length) to reduce false positives.
- Phrase corrections preserve ALL-CAPS, Title Case, and mixed case patterns.
- Roman numeral detection prevents false capitalization of "i" in academic/numbered contexts.
- Bundled corrections are in `voice_typer/server/corrections.json`.
- Place a `voice-typer-corrections.json` in the config directory (or set `corrections_path` in config) to override bundled entries.

### Already running

- Only one Voice Typer instance can run at a time (enforced via Windows named mutex on Windows, lockfile on macOS/Linux).
- If you see "Voice Typer is already running", check the system tray for the existing instance.
- Use **Restart** from the tray menu to cleanly restart the app.

## Known Limitations

- Focus detection (for safe auto-paste) only works on Windows; on macOS/Linux the transcription is always copied to the clipboard (auto-paste is skipped, you Ctrl+V manually)
- Key suppression (so a `Caps Lock` hotkey doesn't toggle caps state) only works on Windows and macOS; on Linux the hotkey press reaches the foreground app and should be neutralized at the OS level via `setxkbmap -option caps:none`
- The `Fn` key is supported only on macOS; on Windows/Linux it is firmware-only and never reaches the OS
- First model download requires internet (model sizes vary by backend: Whisper `tiny.en` is ~75 MB, `small.en` ~466 MB, `medium.en` ~1.5 GB; Parakeet TDT v3 is ~2.5 GB; Qwen3-ASR is configured by path)
- Very long recordings (>10 min) may use significant RAM during transcription
- The standalone installer bundles Python + dependencies (no Python installation needed)

## Project Status

Actively maintained. Uses proper type checking via Pyrefly. Cross-platform — standalone Windows installer available for each release, with macOS and Linux running from source.

## License

MIT
