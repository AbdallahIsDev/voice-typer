# Voice Typer

Premium offline background voice-to-text utility for Windows. Runs in your system tray. Press F2, talk, press F2 again -- final text is copied to your clipboard and pasted safely when a text field is focused.

## How It Works

1. App starts in the system tray (appears in <200ms, model loads in the background)
2. Press **F2** anywhere to start recording
3. Talk freely -- switch apps, browse, do whatever
4. Press **F2** again to stop (or let silence/max duration stop it automatically)
5. Audio is transcribed locally (faster-whisper or optional Qwen3-ASR, your GPU if available)
6. Text is cleaned (dedup, misspellings, self-corrections, capitalization)
7. Text is copied to clipboard
8. If a text field is focused, text is auto-pasted there; otherwise it stays in the clipboard

No cloud. No API keys. No rate limits. Fully offline after first model download.

## Quick Install (Windows — Easiest)

1. Go to **[Releases](https://github.com/AbdallahIsDev/voice-typer/releases)**
2. Download `VoiceTyper-Setup-1.0.0.exe`
3. Double-click the installer
4. Click Next → Install → Finish
5. Voice Typer starts automatically — look for the microphone icon in your system tray

No Python, no terminal, no commands needed.

## Requirements

- Windows 10/11 (primary target; platform.py has stubs for macOS/Linux autostart but the app is not tested on those platforms)
- A microphone
- Internet on first run (downloads the Whisper model ~466MB)

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

The tray icon appears in under 200ms. The transcription engine is created in a background thread while the UI becomes immediately responsive. F2 is usable in approximately 4 seconds once the model finishes loading. If the model hasn't loaded yet when you press F2, you'll see a "Starting up — please wait" message.

## Settings

Open the tray menu -> Settings to change the hotkey, microphone, model, start-on-login, and notifications. The microphone tray submenu is also available for quick device switching.

Settings are stored in JSON for troubleshooting:

`%APPDATA%/voice-typer/config.json`

Use Settings for normal changes. Use the advanced settings button to open the raw config file only when troubleshooting.

### Tray Menu Structure

```
Toggle Dictation (F2)
─────────────────────
Hotkey          → F2, F3, ... F12, Ctrl+1..5, Custom
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

All four models are available from the tray menu:

| Model | Size | Speed | Notes |
|---|---|---|---|
| `tiny.en` | ~75MB | Fastest | Lower accuracy, good for quick notes |
| `small.en` | ~466MB | Fast | Default, best balance of speed and accuracy |
| `medium.en` | ~1.5GB | Slow | Higher accuracy for difficult audio |
| `qwen` | varies | varies | Qwen3-ASR, requires separate installation |

### Config Reference

| Setting | Default | Description |
|---|---|---|
| `hotkey` | `<f2>` | Global hotkey to toggle dictation |
| `microphone` | `null` | Microphone device index (string), or `null` for system default |
| `model_size` | `small.en` | User-facing Whisper model: `tiny.en`, `small.en`, `medium.en`, or `qwen` |
| `language` | `en` | Language for transcription |
| `device` | `cuda` | CUDA-first runtime policy. Falls back to CPU automatically if CUDA is unavailable or fails |
| `beam_size` | `1` | Decode beam size. `1` is fastest; higher values can improve accuracy but slow transcription |
| `best_of` | `1` | Candidate count for decoding. Keep `1` for fastest voice typing |
| `condition_on_previous_text` | `false` | Reuse previous decoded text as context. Disabled by default for lower latency |
| `streaming_transcription` | `true` | Hidden streaming is enabled for faster long recordings. Emergency override: `VOICE_TYPER_STREAMING=0` |
| `autostart` | `true` | Start automatically on login |
| `paste_on_stop` | `true` | Auto-paste into focused field after transcription |
| `show_notifications` | `true` | Show desktop notifications for dictation events (completion, errors). Safety alerts always fire regardless |
| `text_cleanup_enabled` | `true` | Apply post-transcription text cleanup (dedup, misspellings, self-corrections, capitalization) |
| `unsafe_paste_on_unknown_focus` | `false` | Paste even when focus detection can't determine a text input is focused |
| `log_transcriptions` | `false` | Log transcription text to the log file |
| `asr_backend` | `"whisper"` | ASR backend: `"whisper"` or `"qwen"` |
| `qwen_model_path` | `""` | Path to Qwen3-ASR model files (local directory) |
| `corrections_path` | `""` | Path to external corrections.json file (overrides bundled defaults) |
| `silence_warning_seconds` | `20.0` | Seconds of silence before warning notification. Configurable from tray (5s, 10s, 15s, 20s, Custom) |
| `silence_auto_stop_seconds` | `120.0` | Seconds of silence before auto-stopping recording. Configurable from tray (1min, 2min, 3min, 5min, Custom) |
| `max_recording_seconds` | `0` | Max recording duration override in seconds. `0` = use device-specific default. Configurable from tray (5min, 10min, 15min, 20min, Custom) |
| `max_recording_seconds_gpu` | `1200` | Default max recording for GPU mode (20 minutes) |
| `max_recording_seconds_cpu` | `600` | Default max recording for CPU mode (10 minutes) |
| `schema_version` | `1` | Config schema version for migration support |

Advanced streaming timing settings (`streaming_chunk_seconds`, `streaming_step_seconds`, `streaming_left_overlap_seconds`, `streaming_right_guard_seconds`, `streaming_min_first_chunk_seconds`, `streaming_silence_threshold`) are also available in the config file.

## Silence Detection and Auto-Stop

Voice Typer monitors audio input during recording to detect microphone disconnections and extended silence:

### Silence Warning

Uses variance-based analysis to detect when the microphone stops capturing audio. When silence exceeds the configured threshold (default 20s), a safety notification warns you to check your microphone. The warning repeats with exponential backoff (10s, 20s, 40s...) until audio resumes or recording stops. Configure from **tray menu → Advanced → Silence Warning**.

### Auto-Stop Timeout

Recording automatically stops after a configurable silence period (default 2 minutes). This prevents runaway recordings if you walk away or forget to press F2. Configure from **tray menu → Advanced → Auto-Stop Timeout**.

### Max Recording Duration

Recording automatically stops after reaching a maximum time limit. GPU default is 20 minutes; CPU default is 10 minutes (longer recordings may use significant RAM during transcription). Configure from **tray menu → Advanced → Max Recording**.

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

Alternatively, set `"autostart": true` in the config file and restart.

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
- Composite hotkeys with modifiers (`<ctrl>+<alt>+f2`) supported via both Win32 RegisterHotKey and pynput fallback

## Architecture

```
voice_typer/
├── __init__.py         # Package init
├── __main__.py         # Entry point (python -m voice_typer)
├── app.py              # Main orchestrator -- startup, state machine, callbacks, thread safety
├── asr_setup.py        # ASR auto-setup: GPU detection, dependency checking, weight downloading
├── config.py           # Configuration with platform-aware paths, validation, and schema versioning
├── recording.py        # Session-based audio recording with device fallback chain and silence detection
├── transcription.py    # faster-whisper engine with 4-level GPU->CPU fallback
├── qwen_engine.py      # Optional Qwen3-ASR-0.6B backend (self-contained, graceful fallback)
├── streaming.py        # Hidden streaming transcription with overlapping audio windows and retry counter
├── text_cleanup.py     # Post-transcription cleanup pipeline (dedup, misspellings, self-corrections, capitalization)
├── clipboard.py        # Clipboard copy + safe auto-paste with terminal detection
├── focus.py            # Win32 text input focus detection (Windows only)
├── hotkeys.py          # Hotkey backend abstraction (Win32 native / pynput fallback)
├── platform.py         # OS-specific autostart adapters + mic listing + desktop shortcut creation
├── settings.py         # Tkinter-based settings window + SettingsController
├── tray.py             # System tray icon (pystray) with dynamic menu, state indication, and safety notifications
└── corrections.json    # Bundled misspellings, phrase corrections, and extra-word patterns
```

Key design decisions:

- **Fast startup**: Tray icon appears in <200ms. TranscriptionEngine created in background thread. F2 usable in ~4s.
- **Hidden streaming transcription**: Records the full session while transcribing safe overlapping chunks in the background. On stop, it finalizes the unconfirmed tail and falls back to full-session batch transcription if streaming state is unsafe. Overlapping windows with timestamp-based dedup prevent duplicate words across chunk boundaries. A retry counter tolerates transient errors (3 consecutive failures before fallback). Committed words are preserved during streaming.
- **Dual ASR backends**: Whisper (default, via faster-whisper) with 4-level GPU->CPU fallback, and optional Qwen3-ASR-0.6B. Backend selection via `asr_backend` config key.
- **ASR auto-setup**: `asr_setup.py` handles GPU detection, dependency verification, and weight downloading at startup.
- **Text cleanup pipeline**: High-confidence adjacent duplicate removal (preserving intentional repeats like "no no no", "very very good"), self-correction cleanup with improved thresholds (min 5 chars or half word length), misspelling correction via bundled `corrections.json`, known Whisper phrase substitutions, extra-word removal, sentence capitalization, pronoun-I capitalization with Roman numeral awareness, and case-preserving phrase corrections. No forced terminal punctuation (avoids corrupting URLs, commands, and code).
- **Low-audio hallucination guard**: Rejects known boilerplate phrases ("Thanks for watching", "See you next time") only when audio evidence indicates near-silence or a weak mostly-silent long recording.
- **Fast default decoding**: Uses greedy decoding (`beam_size=1`) with VAD filter and no timestamp decoding for lower voice-typing latency.
- **Safe auto-paste**: Paste keystrokes only sent when a text input is confirmed focused. Terminal emulators get Shift+Insert. Clipboard always populated. With `unsafe_paste_on_unknown_focus: true`, pastes even when focus can't be determined.
- **Composite hotkey support**: Hotkeys with modifiers like `<ctrl>+<alt>+f2` via both Win32 RegisterHotKey and pynput fallback. Custom hotkey input via dialog.
- **Microphone fallback chain**: Same-name candidate discovery across host APIs, ranked by reliability (MME > WASAPI > WDM-KS > DirectSound). Falls back further to all available input devices if the configured mic fails.
- **Silence detection**: Variance-based mic disconnect detection with repeating warnings (exponential backoff). Auto-stop on prolonged silence. Max recording duration with device-specific defaults (GPU: 20min, CPU: 10min). Safety notifications bypass the notification toggle.
- **Notification split**: Safety alerts (silence, auto-stop, max duration) always fire. Dictation notifications (completion, errors) controlled by user toggle.
- **Single instance**: Windows named mutex prevents duplicate processes. Restart flow uses env var to bypass the check.
- **Desktop shortcut**: Auto-created on first startup with microphone icon. Uses `pythonw.exe` for console-free execution.
- **Buffer management**: O(1) deque buffer with hard cap at 30k chunks (~30 min). Telemetry warnings at 1k and 5k chunks.
- **Console survival**: Win32 console control handler lets the tray app survive console closure (FreeConsole + orphan guard with 60s timeout).
- **Tray-first**: The tray icon is the primary UI. It appears before model loading starts so you always know the app is running.
- **Graceful degradation**: If GPU not available, falls back to CPU. If MKL int8 allocation fails, falls back to float32 with tiny.en. If auto-paste fails, clipboard still has the text. If hotkey fails, tray menu still works. If model loading fails entirely, the app stays alive and F2 retries loading.
- **Settings window**: Tkinter-based GUI for all config options, with an "Advanced" collapsible section for autostart and notifications.
- **Thread safety**: Busy state guarded by `threading.Event`, streaming session access protected by `threading.Lock`. Timers tracked and cancelled across recording sessions. Watchdog timer recovers from stuck transcriptions after 60s.
- **Config schema versioning**: `schema_version` field in config file enables future migration support.

## Log File

Debug logs are written to `%APPDATA%/voice-typer/voice-typer.log`.

Uses `RotatingFileHandler` (1MB max, 2 backups) with structured logging (session ID, component name).

## Troubleshooting

### Word drops

- Keep hidden streaming enabled unless diagnosing: it finalizes the tail and falls back to batch transcription if timestamps are unsafe.
- Streaming now preserves committed words and tolerates transient errors (3 consecutive failures before fallback).
- Check the log for `[STREAMING] Finalizing streaming transcript`, fallback messages, and transcription length.
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
- Auto-stop defaults to 2 minutes of silence. Max recording defaults to 20min (GPU) or 10min (CPU).
- Adjust these from **tray menu → Advanced**.

### Slow stop after F2

- Current logs include `Stop timing` with stream, concat, stats, resample, and total milliseconds.
- The resampler is warmed at startup. If stop is slow, check whether `Resampler warmed up` appears before the recording.
- CPU fallback can make transcription slower after stop, especially for long recordings.

### CUDA fallback

- Voice Typer tries CUDA first. If CUDA/cuBLAS/cuDNN fails during load or transcription, it falls back to CPU.
- On Windows, NVIDIA wheel DLL paths are added automatically when installed.
- The fallback chain: configured device -> CPU/int8 with original model -> CPU/int8 with tiny.en -> CPU/float32 with tiny.en (last resort avoiding MKL int8 path).

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
- Bundled corrections are in `voice_typer/corrections.json` (misspellings, phrase corrections, extra-word patterns).
- Place a `voice-typer-corrections.json` in the config directory (or set `corrections_path` in config) to override bundled entries.
- External file format: `{"misspellings": {...}, "phrase_corrections": [["bad", "good"], ...], "extra_word_patterns": [["bad", "good"], ...]}`.

### Already running

- Only one Voice Typer instance can run at a time (enforced via Windows named mutex).
- If you see "Voice Typer is already running", check the system tray for the existing instance.
- Use **Restart** from the tray menu to cleanly restart the app.

## Manual Verification Checklist

Run:

```bash
python -m voice_typer
```

Verify:

- Tray icon appears in <200ms
- F2 starts recording and tray shows recording.
- F2 stops recording and tray shows transcribing, then idle.
- Short phrase copies to clipboard and pastes into focused text input.
- Duplicate-risk phrase does not duplicate words.
- Intentional repetition stays repeated.
- 30-60s phrase preserves words across streaming chunks.
- Quiet speech either transcribes or reports no speech with microphone guidance.
- 3s silence does not copy/paste boilerplate hallucinations.
- Silence warning fires after configured threshold (default 20s).
- Auto-stop fires after configured silence period (default 2min).
- Max recording duration stops recording at limit (20min GPU, 10min CPU).
- Safety notifications fire even when dictation notifications are disabled.
- Custom hotkey dialog accepts and applies new key combos.
- Restart launches new instance and quits current one.
- Second instance shows "already running" and exits.
- Desktop shortcut created on first run and works from tray menu.
- All four models selectable from tray (tiny.en, small.en, medium.en, qwen).
- Settings opens and saves hotkey/microphone/model changes.
- Quit exits cleanly with no orphan app process.

## Verification Status

| Feature | Verified | Notes |
|---|---|---|
| Tray startup | Yes | `python.exe` and `pythonw.exe` |
| F2 hotkey | Yes | Both launch modes |
| Transcription + clipboard | Yes | Full F2 cycle tested |
| Auto-paste (focused input) | Yes | Chrome, Warp, Windows Terminal, Codex |
| Paste skip (non-text window) | Yes | Explorer, Settings correctly excluded |
| pythonw via Start-Process | Yes | Same launch path as Windows autostart |
| Single instance enforcement | Yes | Windows named mutex |
| Desktop shortcut | Yes | Auto-created + manual from tray |
| Fast startup (<200ms tray) | Yes | Background model loading |
| Silence detection | Yes | Variance-based with exponential backoff warnings |
| Auto-stop timeout | Yes | Configurable from tray |
| Max recording duration | Yes | Device-specific defaults |
| Safety notifications | Yes | Bypass notification toggle |
| Custom hotkey input | Yes | Dialog-based key combo entry |
| Restart button | Yes | New instance + clean quit |
| Model selection (4 models) | Yes | Including qwen backend |
| Streaming retry counter | Yes | 3 consecutive failures before fallback |
| Streaming word preservation | Yes | Committed words kept across chunks |
| Case-preserving corrections | Yes | ALL-CAPS, Title Case, mixed |
| Roman numeral detection | Yes | Context-aware "i" capitalization |
| Config schema versioning | Yes | Migration-ready |
| ASR auto-setup | Yes | GPU detection + dependency check |
| **Reboot/login autostart** | **No** | Registry entry correct, pythonw survives Start-Process, but actual reboot has not been manually verified |

To verify reboot autostart: reboot, confirm the tray icon appears automatically, press F2 and run one short dictation cycle.

## Known Limitations

- Focus detection (for safe auto-paste) only works on Windows
- First model download requires internet (~466MB for small.en)
- Very long recordings (>10 min) may use significant RAM during transcription
- The standalone installer bundles Python + dependencies (no Python installation needed)

## Project Status

Actively maintained. Uses proper type checking via Pyrefly. ~4000 lines of tests across 14 test files. Standalone Windows installer available.

## License

MIT
