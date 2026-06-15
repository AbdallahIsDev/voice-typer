# Voice Typer

Premium offline background voice-to-text utility for Windows. Runs in your system tray. Press the hotkey, talk, press it again — final text is copied to your clipboard and pasted safely when a text field is focused.

## How It Works

1. App starts in the system tray (appears in <200ms, model loads in the background)
2. Press the hotkey anywhere to start recording (configurable in Settings)
3. Talk freely — switch apps, browse, do whatever
4. Press the hotkey again to stop (or let silence/max duration stop it automatically)
5. Audio is transcribed locally (faster-whisper or optional Qwen3-ASR, your GPU if available)
6. Text is cleaned (dedup, misspellings, self-corrections, capitalization)
7. Text is copied to clipboard
8. If a text field is focused, text is auto-pasted there; otherwise it stays in the clipboard

No cloud. No API keys. No rate limits. Fully offline after first model download.

## Quick Install (Windows — Easiest)

1. Go to **[Releases](https://github.com/AbdallahIsDev/voice-typer/releases)**
2. Download the latest `VoiceTyper-Setup-*.exe`
3. Double-click the installer
4. Click Next → Install → Finish
5. Voice Typer starts automatically — look for the microphone icon in your system tray

No Python, no terminal, no commands needed.

## Requirements

- Windows 10/11 (primary target; platform.py has stubs for macOS/Linux autostart but the app is not tested on those platforms)
- A microphone
- Internet on first run (downloads the Whisper model for the selected model size)

## For Developers (from source)

Requires **Python 3.10+**. Install from [python.org](https://python.org).

### Editable install (recommended for development)

```bash
pip install -e ".[test]"
pytest
```

### Production install (simulates end-user setup)

```bash
pip install .
```

The package must be installed (not just run from source) for autostart to work.

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

A desktop shortcut with a microphone icon is automatically created on first startup. You can also create it manually from **tray menu → Advanced → Create Desktop Shortcut**. The shortcut uses `pythonw.exe` so no console window appears.

## Fast Startup

The tray icon appears in under 200ms. The transcription engine is created in a background thread while the UI becomes immediately responsive. The hotkey is usable in approximately 4 seconds once the model finishes loading. If the model hasn't loaded yet when you press it, you'll see a "Starting up — please wait" message.

## Settings

Open the tray menu -> Settings to change the hotkey, microphone, model, start-on-login, and notifications. The microphone tray submenu is also available for quick device switching.

Settings are stored in JSON for troubleshooting:

`%APPDATA%/voice-typer/config.json`

Use Settings for normal changes. Use the advanced settings button to open the raw config file only when troubleshooting.

All configurable fields, their defaults, and descriptions are defined in
`voice_typer/server/config.py` — that file is the canonical source of truth
for every setting. Key categories:

| Category | Settings |
|---|---|
| Hotkey | `hotkey`, `recording_mode`, `push_to_talk_hotkey`, `repaste_hotkey` |
| Recording | `microphone`, `sample_rate`, `silence_warning_seconds`, `silence_auto_stop_seconds`, `max_recording_seconds` |
| Transcription | `model_size`, `language`, `device`, `beam_size`, `streaming_transcription` |
| Behavior | `autostart`, `paste_on_stop`, `show_notifications`, `text_cleanup_enabled` |
| ASR backend | `asr_backend` (whisper/qwen/parakeet), `qwen_model_path`, `parakeet_model_path` |
| Audio warnings | `audio_quality_warnings`, `audio_clipping_warning`, `audio_low_volume_warning`, `audio_noise_warning` |
| History | `history_retention_days`, `history_retention_count`, `history_max_entries` |

### Tray Menu Structure

```
Toggle Dictation (current hotkey)
─────────────────────
Hotkey          → current, F3, ... F12, Ctrl+1..5, Custom
Microphone      → System Default, [device list]
Model           → tiny.en, small.en, medium.en, qwen
Advanced        → Start on Login
              → Dictation Notifications
              → Silence Warning     → 5s, 10s, 15s, 20s, Custom
              → Auto-Stop Timeout   → 1min, 2min, 3min, 5min, Custom
              → Max Recording       → 5min, 10min, 15min, 20min, Custom
              → Create Desktop Shortcut
─────────────────────
Restart
Quit
```

### Custom Hotkeys

Select **Custom** in the Hotkey submenu to open a dialog where you can type any key combination (e.g., `Ctrl+Shift+K`, `Alt+Q`). The app validates the format and applies it immediately.

### Model Selection

Available models (subject to Whisper upstream naming and sizes):

| Model | Notes |
|---|---|
| `tiny.en` | Fastest, lower accuracy |
| `small.en` | Default, best balance of speed and accuracy |
| `medium.en` | Higher accuracy for difficult audio |
| `qwen` | Qwen3-ASR, requires separate installation (`pip install qwen-asr torch`) |

## Silence Detection and Auto-Stop

Voice Typer monitors audio input during recording to detect microphone disconnections and extended silence:

### Silence Warning

Uses variance-based analysis to detect when the microphone stops capturing audio. When silence exceeds the configured threshold (default 20s), a safety notification warns you to check your microphone. The warning repeats with exponential backoff (10s, 20s, 40s...) until audio resumes or recording stops. Configure from **tray menu → Advanced → Silence Warning**.

### Auto-Stop Timeout

Recording automatically stops after a configurable silence period (default 2 minutes). This prevents runaway recordings if you walk away or forget to press the hotkey. Configure from **tray menu → Advanced → Auto-Stop Timeout**.

### Max Recording Duration

Recording automatically stops after reaching a maximum time limit. Configure from **tray menu → Advanced → Max Recording**.

All three features fire **safety notifications** that bypass the notification toggle — you will always be alerted when recording stops due to silence or max duration.

## Notification System

Notifications are split into two categories:

- **Safety alerts** (silence warnings, auto-stop, max duration) — always fire regardless of notification settings. You will never miss a safety-critical event.
- **Dictation notifications** (transcription complete, errors, clipboard status) — controlled by the **Dictation Notifications** toggle in **tray menu → Advanced**.

## Microphone Selection

The easiest way to change microphone: **tray menu -> Microphone** -> pick from the list.
The tray menu shows device names and disambiguates duplicates by showing the
host API (e.g. "WO Mic (Windows WASAPI)" vs "WO Mic (MME)").

The `microphone` config value is the **device index** (a string like `"3"`),
not the display name. This avoids ambiguity when multiple host APIs expose
devices with the same name.

To set manually, open the config file and set `"microphone"` to the device
index string. To find the right index, run:

```bash
python -c "import sounddevice as sd; [print(i, d['name'], sd.query_hostapis(d['hostapi'])['name']) for i,d in enumerate(sd.query_devices()) if d['max_input_channels'] > 0]"
```

## Autostart

Enable or disable from **Settings -> Advanced -> Start on login**.

The app registers itself in `HKCU\...\Run` (uses `pythonw.exe` for background execution, no console window). Hotkey uses Win32 RegisterHotKey with GetAsyncKeyState polling for reliable detection. The package must be installed (`pip install .`) for autostart to work.

## Auto-Paste Behavior

When `paste_on_stop` is enabled, the app detects whether a text input is focused (via Win32 API). Auto-paste only happens when a text field is confirmed focused. If no text input is focused, the keystroke is skipped and the text stays in your clipboard.

Terminal emulators (Windows Terminal, Warp, Alacritty, etc.) are detected and pasted via Shift+Insert instead of Ctrl+V.

The clipboard always gets the transcribed text when transcription succeeds. The app never pastes provisional streaming text.

## Text Corrections

### Self-Correction Detection

The cleanup pipeline detects and removes self-corrections in speech (e.g., "I went to the store the shop" → "I went to the shop"). Uses a higher threshold to avoid false positives: requires at least 5 characters or half the word length before matching a correction.

### Case-Preserving Corrections

Phrase corrections preserve the original casing pattern. If you speak in ALL CAPS, corrections stay in ALL CAPS. Title Case and mixed case patterns are also preserved.

### Roman Numeral Detection

Context-aware capitalization of "I" that skips capitalization when followed by Roman numeral context words (e.g., "chapter i of the book" stays lowercase). This prevents false capitalization of the pronoun in academic or numbered contexts.

### External Corrections

Bundled corrections are in `voice_typer/corrections.json` (misspellings, phrase corrections, extra-word patterns).
Place a `voice-typer-corrections.json` in the config directory (or set `corrections_path` in config) to override bundled entries.
External file format: `{"misspellings": {...}, "phrase_corrections": [["bad", "good"], ...], "extra_word_patterns": [["bad", "good"], ...]}`.

## Streaming Transcription

Hidden streaming transcription processes audio in overlapping chunks during recording for faster finalization. Key behaviors:

- **Retry counter**: Transient streaming errors no longer permanently disable streaming. Three consecutive failures are required before falling back to batch transcription for the session.
- **Word preservation**: Committed words are preserved during streaming. Deduplication structures are pruned but the output accumulator stays intact, preventing word drops across chunk boundaries.
- **Emergency override**: Set `VOICE_TYPER_STREAMING=0` to disable streaming entirely.

## Platform Notes

Voice Typer is **Windows-only**. The platform.py module has stubs for macOS and Linux autostart
but the app is primarily developed and tested on Windows.

- Tested on Windows 10/11
- Autostart uses `pythonw.exe` for background execution (no console window)
- Global hotkey uses Win32 RegisterHotKey via ctypes (no admin required) with GetAsyncKeyState polling
- Focus detection for safe auto-paste (Win32 API)
- Win32 console control handler keeps the tray app alive when the console is closed
- GPU acceleration via CUDA if available (NVIDIA wheel DLL paths configured automatically)
- Composite hotkeys with modifiers supported via both Win32 RegisterHotKey and pynput fallback

## Architecture

```
voice_typer/
├── __init__.py         # Package init, __version__
├── __main__.py         # Entry point (python -m voice_typer)
├── app.py              # Main orchestrator — startup, state machine, callbacks, thread safety
├── asr_setup.py        # ASR auto-setup: GPU detection, dependency checking, weight downloading
├── config.py           # Configuration with platform-aware paths, validation, and schema versioning
├── recording.py        # Session-based audio recording with device fallback chain and silence detection
├── transcription.py    # faster-whisper engine with 4-level GPU->CPU fallback
├── qwen_engine.py      # Optional Qwen3-ASR-0.6B backend (self-contained, graceful fallback)
├── parakeet_engine.py  # Optional NVIDIA Parakeet backend
├── streaming.py        # Hidden streaming transcription with overlapping audio windows and retry counter
├── text_cleanup.py     # Post-transcription cleanup pipeline (dedup, misspellings, self-corrections, capitalization)
├── clipboard.py        # Clipboard copy + safe auto-paste with terminal detection
├── focus.py            # Win32 text input focus detection (Windows only)
├── hotkeys.py          # Hotkey backend abstraction (Win32 native / pynput fallback)
├── platform.py         # OS-specific autostart adapters + mic listing + desktop shortcut creation
├── settings.py         # Tkinter-based settings window + SettingsController
├── tray.py             # System tray icon (pystray) with dynamic menu, state indication, and safety notifications
├── corrections.json    # Bundled misspellings, phrase corrections, and extra-word patterns
├── client/             # Electron frontend (TypeScript/React/Vite)
│   ├── src/main/       # Electron main process
│   ├── src/renderer/   # React renderer
│   └── src/preload/    # Context bridge (IPC)
└── tests/              # Test suite
```

Key design decisions:

- **Fast startup**: Tray icon appears in <200ms. TranscriptionEngine created in background thread.
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

Debug logs are written to `%APPDATA%/voice-typer/voice-typer.log`.

Uses `RotatingFileHandler` (1MB max, 2 backups) with structured logging (session ID, component name).

## Troubleshooting

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

- Check the selected microphone in the tray menu.
- Watch the log line `RMS`, `peak`, and `silence_pct`. Near-zero RMS usually means the wrong mic or muted input.
- If audio is quiet but real, move closer to the mic or choose the non-virtual physical microphone.

### Wrong microphone

- Use tray menu -> Microphone. Duplicate names show host APIs where needed.
- If one host API fails, Voice Typer can fall back to another entry with the same physical microphone name and persist the working device index.

### Silence warnings during recording

- If you get silence warnings while actively speaking, your microphone may have a high noise floor or the silence threshold is too aggressive.
- Adjust **tray menu → Advanced → Silence Warning** to a higher value (e.g., 15s or 20s).
- Check that the correct microphone is selected and is not being used by another application.

### Recording stops unexpectedly

- Check if **Auto-Stop Timeout** or **Max Recording** triggered. Both fire safety notifications.
- Adjust these from **tray menu → Advanced**.

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
- Enable from Settings -> Advanced -> Start on login.
- Windows uses `pythonw.exe -m voice_typer` when available so no console window stays open.

### Settings window

- If the settings window does not appear, check the log for errors.
- The window uses a tkinter GUI with a collapsible Advanced section.
- Cancelling discards changes; Save validates and applies them immediately.

### Text corrections

- Self-correction detection uses a higher threshold (min 5 chars or half word length) to reduce false positives.
- Phrase corrections preserve ALL-CAPS, Title Case, and mixed case patterns.
- Roman numeral detection prevents false capitalization of "i" in academic/numbered contexts.
- Bundled corrections are in `voice_typer/corrections.json`.
- Place a `voice-typer-corrections.json` in the config directory (or set `corrections_path` in config) to override bundled entries.

### Already running

- Only one Voice Typer instance can run at a time (enforced via Windows named mutex).
- If you see "Voice Typer is already running", check the system tray for the existing instance.
- Use **Restart** from the tray menu to cleanly restart the app.

## Known Limitations

- Focus detection (for safe auto-paste) only works on Windows
- First model download requires internet (the default model is ~466MB)
- Very long recordings (>10 min) may use significant RAM during transcription
- The standalone installer bundles Python + dependencies (no Python installation needed)

## Project Status

Actively maintained. Uses proper type checking via Pyrefly. Standalone Windows installer available for each release.

## License

MIT
