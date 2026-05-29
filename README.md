# Voice Typer

Premium offline background voice-to-text utility for Windows. Runs in your system tray. Press F2, talk, press F2 again -- final text is copied to your clipboard and pasted safely when a text field is focused.

## How It Works

1. App starts in the system tray
2. Press **F2** anywhere to start recording
3. Talk freely -- switch apps, browse, do whatever
4. Press **F2** again to stop
5. Audio is transcribed locally (faster-whisper or optional Qwen3-ASR, your GPU if available)
6. Text is cleaned (dedup, misspellings, self-corrections, capitalization)
7. Text is copied to clipboard
8. If a text field is focused, text is auto-pasted there; otherwise it stays in the clipboard

No cloud. No API keys. No rate limits. Fully offline after first model download.

## Requirements

- Python 3.10 or later
- Windows 10/11 (primary target; platform.py has stubs for macOS/Linux autostart but the app is not tested on those platforms)
- A microphone

## Install

```bash
pip install .
```

This installs the `voice-typer` command and all dependencies. The package
must be installed (not just run from source) for autostart to work.

First run downloads the Whisper model (~466MB for small.en). Subsequent runs are instant.

### Development install

```bash
pip install -e ".[test]"
pytest
```

## Run

```bash
voice-typer
```

Or:

```bash
python -m voice_typer
```

The app runs in the system tray. No terminal window is shown (on Windows, it uses `pythonw.exe` automatically when launched via autostart).

## Settings

Open the tray menu -> Settings to change the hotkey, microphone, model, start-on-login, and notifications. The microphone tray submenu is also available for quick device switching.

Settings are stored in JSON for troubleshooting:

`%APPDATA%/voice-typer/config.json`

Use Settings for normal changes. Use the advanced settings button to open the raw config file only when troubleshooting.

| Setting | Default | Description |
|---|---|---|
| `hotkey` | `<f2>` | Global hotkey to toggle dictation |
| `microphone` | `null` | Microphone device index (string), or `null` for system default |
| `model_size` | `small.en` | User-facing Whisper model: `small.en` or `medium.en`. `tiny.en` may be used internally only as a last-resort fallback |
| `language` | `en` | Language for transcription |
| `device` | `cuda` | CUDA-first runtime policy. Falls back to CPU automatically if CUDA is unavailable or fails |
| `beam_size` | `1` | Decode beam size. `1` is fastest; higher values can improve accuracy but slow transcription |
| `best_of` | `1` | Candidate count for decoding. Keep `1` for fastest voice typing |
| `condition_on_previous_text` | `false` | Reuse previous decoded text as context. Disabled by default for lower latency |
| `streaming_transcription` | `true` | Hidden streaming is enabled for faster long recordings. Emergency override: `VOICE_TYPER_STREAMING=0` |
| `autostart` | `true` | Start automatically on login |
| `paste_on_stop` | `true` | Auto-paste into focused field after transcription |
| `show_notifications` | `true` | Show desktop notifications |
| `text_cleanup_enabled` | `true` | Apply post-transcription text cleanup (dedup, misspellings, self-corrections, capitalization) |
| `unsafe_paste_on_unknown_focus` | `false` | Paste even when focus detection can't determine a text input is focused |
| `log_transcriptions` | `false` | Log transcription text to the log file |
| `asr_backend` | `"whisper"` | ASR backend: `"whisper"` or `"qwen"` |
| `qwen_model_path` | `""` | Path to Qwen3-ASR model files (local directory) |
| `corrections_path` | `""` | Path to external corrections.json file (overrides bundled defaults) |

Advanced streaming timing settings (`streaming_chunk_seconds`, `streaming_step_seconds`, `streaming_left_overlap_seconds`, `streaming_right_guard_seconds`, `streaming_min_first_chunk_seconds`, `streaming_silence_threshold`) are also available in the config file.

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
├── config.py           # Configuration with platform-aware paths and validation
├── recording.py        # Session-based audio recording with device fallback chain
├── transcription.py    # faster-whisper engine with 4-level GPU->CPU fallback
├── qwen_engine.py      # Optional Qwen3-ASR-0.6B backend (self-contained, graceful fallback)
├── streaming.py        # Hidden streaming transcription with overlapping audio windows
├── text_cleanup.py     # Post-transcription cleanup pipeline (dedup, misspellings, self-corrections, capitalization)
├── clipboard.py        # Clipboard copy + safe auto-paste with terminal detection
├── focus.py            # Win32 text input focus detection (Windows only)
├── hotkeys.py          # Hotkey backend abstraction (Win32 native / pynput fallback)
├── platform.py         # OS-specific autostart adapters + mic listing
├── settings.py         # Tkinter-based settings window + SettingsController
├── tray.py             # System tray icon (pystray) with dynamic menu and state indication
└── corrections.json    # Bundled misspellings, phrase corrections, and extra-word patterns
```

Key design decisions:

- **Hidden streaming transcription**: Records the full session while transcribing safe overlapping chunks in the background. On stop, it finalizes the unconfirmed tail and falls back to full-session batch transcription if streaming state is unsafe. Overlapping windows with timestamp-based dedup prevent duplicate words across chunk boundaries.
- **Dual ASR backends**: Whisper (default, via faster-whisper) with 4-level GPU->CPU fallback, and optional Qwen3-ASR-0.6B. Backend selection via `asr_backend` config key.
- **Text cleanup pipeline**: High-confidence adjacent duplicate removal (preserving intentional repeats like "no no no", "very very good"), self-correction cleanup ("talk talking" -> "talking"), misspelling correction via bundled `corrections.json`, known Whisper phrase substitutions, extra-word removal, sentence capitalization, and pronoun-I capitalization. No forced terminal punctuation (avoids corrupting URLs, commands, and code).
- **Low-audio hallucination guard**: Rejects known boilerplate phrases ("Thanks for watching", "See you next time") only when audio evidence indicates near-silence or a weak mostly-silent long recording.
- **Fast default decoding**: Uses greedy decoding (`beam_size=1`) with VAD filter and no timestamp decoding for lower voice-typing latency.
- **Safe auto-paste**: Paste keystrokes only sent when a text input is confirmed focused. Terminal emulators get Shift+Insert. Clipboard always populated. With `unsafe_paste_on_unknown_focus: true`, pastes even when focus can't be determined.
- **Composite hotkey support**: Hotkeys with modifiers like `<ctrl>+<alt>+f2` via both Win32 RegisterHotKey and pynput fallback.
- **Microphone fallback chain**: Same-name candidate discovery across host APIs, ranked by reliability (MME > WASAPI > WDM-KS > DirectSound). Falls back further to all available input devices if the configured mic fails.
- **Buffer management**: O(1) deque buffer with hard cap at 30k chunks (~30 min). Telemetry warnings at 1k and 5k chunks.
- **Console survival**: Win32 console control handler lets the tray app survive console closure (FreeConsole + orphan guard with 60s timeout).
- **Tray-first**: The tray icon is the primary UI. It appears before model loading starts so you always know the app is running.
- **Graceful degradation**: If GPU not available, falls back to CPU. If MKL int8 allocation fails, falls back to float32 with tiny.en. If auto-paste fails, clipboard still has the text. If hotkey fails, tray menu still works. If model loading fails entirely, the app stays alive and F2 retries loading.
- **Settings window**: Tkinter-based GUI for all config options, with an "Advanced" collapsible section for autostart and notifications.
- **Thread safety**: Busy state guarded by `threading.Event`, streaming session access protected by `threading.Lock`. Timers tracked and cancelled across recording sessions. Watchdog timer recovers from stuck transcriptions after 60s.

## Log File

Debug logs are written to `%APPDATA%/voice-typer/voice-typer.log`.

Uses `RotatingFileHandler` (1MB max, 2 backups) with structured logging (session ID, component name).

## Troubleshooting

### Word drops

- Keep hidden streaming enabled unless diagnosing: it finalizes the tail and falls back to batch transcription if timestamps are unsafe.
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

- Bundled corrections are in `voice_typer/corrections.json` (misspellings, phrase corrections, extra-word patterns).
- Place a `voice-typer-corrections.json` in the config directory (or set `corrections_path` in config) to override bundled entries.
- External file format: `{"misspellings": {...}, "phrase_corrections": [["bad", "good"], ...], "extra_word_patterns": [["bad", "good"], ...]}`.

## Manual Verification Checklist

Run:

```bash
python -m voice_typer
```

Verify:

- F2 starts recording and tray shows recording.
- F2 stops recording and tray shows transcribing, then idle.
- Short phrase copies to clipboard and pastes into focused text input.
- Duplicate-risk phrase does not duplicate words.
- Intentional repetition stays repeated.
- 30-60s phrase preserves words across streaming chunks.
- Quiet speech either transcribes or reports no speech with microphone guidance.
- 3s silence does not copy/paste boilerplate hallucinations.
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
| **Reboot/login autostart** | **No** | Registry entry correct, pythonw survives Start-Process, but actual reboot has not been manually verified |

To verify reboot autostart: reboot, confirm the tray icon appears automatically, press F2 and run one short dictation cycle.

## Known Limitations

- Focus detection (for safe auto-paste) only works on Windows
- First model download requires internet (~466MB)
- Very long recordings (>10 min) may use significant RAM during transcription
- No `.bat` files or setup scripts -- the app is installed via `pip install .` and run as `voice-typer` or `python -m voice_typer`

## Project Status

Actively maintained. Uses proper type checking via Pyrefly. ~4000 lines of tests across 14 test files.

## License

MIT
