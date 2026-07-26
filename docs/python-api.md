# Voice Typer — Public API Reference

## Overview

Voice Typer exposes its functionality through Python classes in
`voice_typer.server/` and an IPC server for cross-process communication.
This document covers the primary public API surfaces.

---

## VoiceTyperApp

**Module:** `voice_typer.server.app`

The main application class. Owns the tray icon, recording lifecycle,
model management, and IPC server.

### Key Methods

| Method | Parameters | Returns | Description |
|--------|-----------|---------|-------------|
| `__init__()` | — | — | Initializes app state, does NOT start services. |
| `run()` | — | `None` | Starts the tray icon, IPC server, and main event loop. Blocks until `quit()`. |
| `quit()` | — | `None` | Graceful shutdown: stops recording, restores volume, closes IPC, releases mutex. |
| `toggle_dictation()` | — | `None` | Toggle dictation on/off. Thread-safe (serialized via `_toggle_lock`). |
| `restart()` | — | `None` | Spawns a new process and quits the current one. Uses restart token for mutex bypass. |

### Key Properties

| Property | Type | Description |
|----------|------|-------------|
| `config` | `Config` | Current configuration object. |
| `recorder` | `Recorder` | Audio recorder instance. |
| `tray` | `TrayManager` | System tray manager. |
| `models` | `ModelManager` | Model loading/selection manager. |

---

## Recorder

**Module:** `voice_typer.server.recording`

Captures audio from the microphone using sounddevice/PortAudio, applies
filtering and VAD (Voice Activity Detection), and provides chunks to the
transcription pipeline.

### Key Methods

| Method | Parameters | Returns | Description |
|--------|-----------|---------|-------------|
| `start(device_index)` | `device_index: int \| None` | `None` | Start recording from the specified device (or default). |
| `stop()` | — | `np.ndarray` | Stop recording and return captured audio as float32 array. |
| `cancel()` | — | `None` | Discard recorded audio without returning it. |

### Key Properties

| Property | Type | Description |
|----------|------|-------------|
| `last_rms` | `float` | Most recent RMS level from the audio callback. |
| `is_recording` | `bool` | Whether recording is active. |
| `effective_sample_rate` | `int` | Actual sample rate after resampling decisions. |

---

## TranscriptionEngine

**Module:** `voice_typer.server.transcription`

Wraps faster-whisper CTranslate2 models for speech-to-text transcription.

### Key Methods

| Method | Parameters | Returns | Description |
|--------|-----------|---------|-------------|
| `transcribe(audio, sample_rate)` | `audio: np.ndarray`, `sample_rate: int` | `str` | Transcribe audio to text. |
| `is_loaded` | — | `bool` | Whether a model is currently loaded in memory. |
| `unload()` | — | `None` | Release the model from memory. |

---

## Config

**Module:** `voice_typer.server.config`

Application configuration with type-safe access and atomic persistence.

### Key Methods

| Method | Parameters | Returns | Description |
|--------|-----------|---------|-------------|
| `load()` | — | `None` | Load config from disk (secure read). |
| `save()` | — | `None` | Persist config to disk (atomic write). |
| `get(key, default)` | `key: str`, `default: Any` | `Any` | Get a config value with optional default. |
| `set(key, value)` | `key: str`, `value: Any` | `None` | Set a config value and persist immediately. |

### Key Configuration Keys

The defaults below are read from the `Config` dataclass in
`voice_typer/server/config.py` and the enum validators in
`voice_typer/server/config_validators.py`.  A CI test
(`tests/test_api_doc_accuracy.py`) parses this table and asserts each
row matches the actual `Config` default — if you change a default in
`Config`, update this table in the same commit or CI will fail.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `recording_mode` | `str` | `"toggle"` | One of: `toggle`, `push_to_talk`. |
| `model_size` | `str` | `"small.en"` | Whisper model name (one of `ALLOWED_USER_MODELS`) or `"qwen"` / `"parakeet"`. |
| `language` | `str` | `"en"` | ISO-639-1 language code for transcription (e.g. `"en"`, `"fr"`, `"de"`). |
| `paste_on_stop` | `bool` | `True` | Whether to auto-paste transcribed text when recording stops. |
| `log_transcriptions` | `bool` | `False` | Whether to log transcription text (privacy-sensitive — see SEC-009). |
| `silence_warning_seconds` | `float` | `20.0` | Seconds of silence before the silence-warning tray notification fires. |
| `stop_on_silence_seconds` | `float` | `60.0` | Seconds of silence before auto-stop. |
| `clipboard_restore_delay_ms` | `int` | `150` | Delay (ms) between the paste keystroke and restoring the previous clipboard contents (ADR-0010). |
| `max_recording_time_seconds` | `int` | `900` | Hard cap on recording length (clamped to `[300, 3600]` — 5 to 60 minutes). |

Removed / renamed fields (documented for searchability — do NOT re-add):

- `paste_enabled` → renamed to `paste_on_stop`.
- `clipboard_clear_delay_seconds` → removed in ADR-0010 §8.2 (was dead
  code — only read by the deleted `schedule_clipboard_clear`).
- `check_updates` → never existed on `Config` (the auto-update flow is
  driven by Electron's `electron-updater`, not a Python config flag).
- `voice_activity` recording mode → never implemented; the enum is
  `{toggle, push_to_talk}` only.
- `model` → renamed to `model_size` (the IPC `set_config` allowlist key
  is `model_size`, not `model`).

---

## ClipboardManager

**Module:** `voice_typer.server.clipboard`

Handles copying text to clipboard and pasting into the focused application.

### Key Methods

| Method | Parameters | Returns | Description |
|--------|-----------|---------|-------------|
| `copy(text)` | `text: str` | `None` | Copy text to system clipboard. On Windows, empties clipboard first. |
| `paste()` | — | `None` | Send paste keystroke (Ctrl+V or Shift+Insert for terminals). |

### Security Features

- **EmptyClipboard** before copy (PLAT-006): clears stale formats
- **GetClipboardSequenceNumber** (PLAT-CLIPRACE): detects clipboard modification between copy and paste
- **Password field detection** (PLAT-014): refuses to paste into password fields
- **Elevated target detection** (PLAT-013): warns when targeting an elevated process
- **Clipboard save/restore** (PLAT-SECURE): saves previous clipboard content and restores after delay
- **Clipboard auto-clear** (SEC-012): clears transcription text from clipboard after configurable delay

---

## IPC Server

**Module:** `voice_typer.server.ipc_server`

TCP-based IPC server for communication between the Electron frontend and the
Python backend.

### Protocol

- **Transport:** TCP on `127.0.0.1:9876` (loopback only). The port defaults to `9876` and `_pick_available_port()` in `voice_typer/server/ipc_server.py` falls forward to `9877`, `9878`, … (up to 100 tries) if `9876` is already taken by another Voice Typer instance — in practice the default install binds `9876`. The Tauri sidecar path uses an ephemeral localhost WebSocket instead — see `voice_typer/server/sidecar_ws.py` and ADR-0020.
- **Framing:** Newline-delimited JSON
- **Auth:** Per-connection token. The **first** message on a connection must be a JSON `auth` object whose `token` field matches the `VOICE_TYPER_IPC_TOKEN` env var (constant-time comparison via `hmac.compare_digest`). Once the handshake succeeds, subsequent messages on that authenticated connection bypass the token check and go straight to dispatch. See `SEC-018` in `SECURITY.md` for the threat model.

### Key Endpoints

| Command | Parameters | Returns | Description |
|----------|-----------|---------|-------------|
| `get_status` | — | `{status, xruns_since_start, ...}` | Get current app state. |
| `toggle_dictation` | — | `ack` | Start/stop dictation. |
| `force_cancel_transcription` | — | `force_cancel_transcription_result` | Force-cancel a stuck transcription (resets busy flag immediately). |
| `set_config` | `{<field>: value, ...}` | `ack` | Update config values (validated against allowlist). |
| `get_config` | — | `{<field>: value, ...}` | Get current config (secret fields redacted). |
| `get_model_status` | — | `{<model>: {downloaded, deps_ok}, ...}` | Get on-disk status of each model. |
| `get_model_catalog` | — | `{models: [...]}` | Full model catalog with metadata (VRAM, languages, ratings). |

---

## Model Manager

**Module:** `voice_typer.server.model_manager`

Manages loading, unloading, and selection of transcription models (Whisper,
Qwen, Parakeet).

### Key Methods

| Method | Parameters | Returns | Description |
|--------|-----------|---------|-------------|
| `ensure_active_engine_loaded()` | — | `None` | Load the active engine if not already loaded. |
| `start_background_load()` | — | `None` | Start background model loading (non-blocking). |
| `unload_all()` | — | `None` | Unload all models from memory. |

---

## Security Module

**Module:** `voice_typer.server.security`

Security utilities including restart token verification, PII redaction,
and model integrity checking.

### Key Functions

| Function | Parameters | Returns | Description |
|----------|-----------|---------|-------------|
| `generate_restart_token()` | — | `str` | Generate and persist a restart token. |
| `verify_restart_token()` | — | `bool` | Verify the VOICE_TYPER_RESTART env var against the stored token. |
| `compute_file_sha256(path)` | `path: Path` | `str` | Compute SHA-256 hash of a file. |
| `verify_model_integrity(dir, repo_id)` | `dir: str`, `repo_id: str` | `bool` | Verify model files against the hash manifest. |

### PIIRedactionFilter

A `logging.Filter` that redacts email addresses, phone numbers, SSNs, and
credit card numbers from log messages. Installed on the root logger by default.
