# Voice Typer

Premium offline background voice-to-text utility. Runs in your system tray. Press F2, talk, press F2 again -- final text is copied to your clipboard and pasted safely when a text field is focused.

## How It Works

1. App starts in the system tray (or menu bar on macOS)
2. Press **F2** anywhere to start recording
3. Talk freely — switch apps, browse, do whatever
4. Press **F2** again to stop
5. Audio is transcribed locally (faster-whisper, your GPU if available)
6. Text is copied to clipboard
7. If a text field is focused, text is auto-pasted there; otherwise it stays in the clipboard

No cloud. No API keys. No rate limits. Fully offline after first model download.

## Requirements

- Python 3.10 or later
- A microphone (built-in, USB, or WO Mic on Windows)

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

Settings are stored in a JSON file:

- **Windows**: `%APPDATA%/voice-typer/config.json`
- **macOS**: `~/Library/Application Support/voice-typer/config.json`
- **Linux**: `~/.config/voice-typer/config.json`

Use Settings for normal changes. Use the advanced settings button to open the raw config file only when troubleshooting.

| Setting               | Default     | Description                                                   |
|-----------------------|-------------|---------------------------------------------------------------|
| `hotkey`              | `<f2>`      | Global hotkey to toggle dictation                             |
| `microphone`          | `null`      | Microphone device index (string), or `null` for system default|
| `model_size`          | `small.en`  | User-facing Whisper model: `small.en` or `medium.en`. `tiny.en` may be used internally only as a last-resort fallback |
| `language`            | `en`        | Language for transcription                                    |
| `device`              | `cuda`      | CUDA-first runtime policy. Falls back to CPU automatically if CUDA is unavailable or fails |
| `beam_size`           | `1`         | Decode beam size. `1` is fastest; higher values can improve accuracy but slow transcription |
| `best_of`             | `1`         | Candidate count for decoding. Keep `1` for fastest voice typing |
| `condition_on_previous_text` | `false` | Reuse previous decoded text as context. Disabled by default for lower latency |
| `streaming_transcription` | `true`  | Hidden streaming is enabled for faster long recordings. Emergency override: `VOICE_TYPER_STREAMING=0` |
| `autostart`           | `true`      | Start automatically on login                                  |
| `paste_on_stop`       | `true`      | Auto-paste into focused field after transcription             |
| `show_notifications`  | `true`      | Show desktop notifications                                    |

## Microphone Selection

The easiest way to change microphone: **tray menu → Microphone** → pick from the list.
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

Platform behavior:
- **Windows**: Registry key in `HKCU\...\Run` (uses `pythonw.exe` for background execution, no console window). Hotkey uses Win32 RegisterHotKey with GetAsyncKeyState polling for reliable detection.
- **macOS**: LaunchAgent plist in `~/Library/LaunchAgents/com.voicetyper.plist`
- **Linux**: Desktop entry in `~/.config/autostart/voice-typer.desktop`

All platforms use `python -m voice_typer` with the full path to the current Python interpreter. The package must be installed (`pip install .`) for autostart to work.

## Auto-Paste Behavior

When `paste_on_stop` is enabled:

- **Windows**: The app detects whether a text input is focused (via Win32 API). Auto-paste only happens when a text field is confirmed focused. If no text input is focused, the keystroke is skipped and the text stays in your clipboard.
- **macOS / Linux**: Focus detection is not available. The app will attempt to paste (Ctrl+V / Cmd+V) after every transcription. Set `paste_on_stop` to `false` if you prefer clipboard-only behavior.

On all platforms, the clipboard gets the transcribed text when transcription succeeds. The app never pastes provisional streaming text.

## Platform Notes

### Windows
- Tested on Windows 10/11
- Autostart uses `pythonw.exe` for background execution (no console window)
- Global hotkey uses Win32 RegisterHotKey via ctypes (no admin required, no pynput dependency)
- Focus detection for safe auto-paste
- GPU acceleration via CUDA if available

### macOS
- Requires Python installed (Homebrew recommended: `brew install python`)
- Requires Accessibility permissions for global hotkey (System Preferences → Privacy → Accessibility)
- Cmd+V for auto-paste
- Tested on macOS 12+

### Linux
- Global hotkey works on X11; Wayland support depends on compositor
- Ctrl+V for auto-paste
- Tested on Ubuntu 22.04+ and Fedora 38+

## Architecture

```
voice_typer/
├── __main__.py       # Entry point (python -m voice_typer)
├── app.py            # Main orchestrator -- startup, state machine, callbacks
├── config.py         # Configuration with platform-aware paths
├── recording.py      # Session-based audio recording
├── transcription.py  # faster-whisper engine with GPU fallback
├── clipboard.py      # Clipboard copy + safe auto-paste
├── focus.py          # Platform-aware text input focus detection (Windows only)
├── hotkeys.py        # Hotkey backend abstraction (Win32 native / pynput)
├── platform.py       # OS-specific autostart adapters + mic listing
└── tray.py           # System tray icon with state indication and dynamic menu
```

Key design decisions:

- **Hidden streaming transcription**: Records the full session while transcribing safe overlapping chunks in the background. On stop, it finalizes the unconfirmed tail and falls back to full-session batch transcription if streaming state is unsafe.
- **Fast default decoding**: Uses the same configured model with greedy decoding (`beam_size=1`) and no timestamp decoding for lower voice-typing latency.
- **Conservative cleanup**: Removes high-confidence adjacent Whisper duplicates while preserving common intentional repetitions such as "no no no" and "very very good".
- **Low-audio hallucination guard**: Rejects known boilerplate phrases such as "Thanks for watching" only when audio evidence indicates near-silence or a weak mostly silent long recording.
- **Safe auto-paste**: Paste keystrokes are only sent when a text input is focused (Windows) or when the user opted in (other platforms). Clipboard is always populated.
- **Platform adapters**: Autostart, focus detection, and paste behavior are isolated behind platform-specific code.
- **Tray-first**: The tray icon is the primary UI. It appears before model loading starts so you always know the app is running.
- **Graceful degradation**: If GPU not available, falls back to CPU. If MKL int8 allocation fails, falls back to float32 with tiny.en. If auto-paste fails, clipboard still has the text. If hotkey fails, tray menu still works. If model loading fails entirely, the app stays alive and F2 retries loading.

## Log File

Debug logs are written to:
- **Windows**: `%APPDATA%/voice-typer/voice-typer.log`
- **macOS**: `~/Library/Application Support/voice-typer/voice-typer.log`
- **Linux**: `~/.config/voice-typer/voice-typer.log`

## Troubleshooting

### Word drops

- Keep hidden streaming enabled unless diagnosing: it finalizes the tail and falls back to batch transcription if timestamps are unsafe.
- Check the log for `[STREAMING] Finalizing streaming transcript`, fallback messages, and transcription length.
- For emergency batch-only mode, run with `VOICE_TYPER_STREAMING=0`.

### Duplicate words

- The app removes only high-confidence adjacent duplicate words/phrases.
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

### Autostart

- Install the package first: `pip install .`
- Enable from Settings -> Advanced -> Start on login.
- Windows uses `pythonw.exe -m voice_typer` when available so no console window stays open.

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
|---------|----------|-------|
| Tray startup | Yes | python.exe and pythonw.exe |
| F2 hotkey | Yes | Both launch modes |
| Transcription + clipboard | Yes | Full F2 cycle tested |
| Auto-paste (focused input) | Yes | Chrome, Warp, Windows Terminal, Codex |
| Paste skip (non-text window) | Yes | Explorer, Settings correctly excluded |
| pythonw via Start-Process | Yes | Same launch path as Windows autostart |
| **Reboot/login autostart** | **No** | Registry entry is correct, pythonw survives Start-Process, but actual reboot has not been manually verified |

To verify reboot autostart: reboot, confirm the tray icon appears automatically, press F2 and run one short dictation cycle.

## Known Limitations

- Wayland global hotkey support depends on the compositor
- Focus detection (for safe auto-paste) only works on Windows; on macOS/Linux the app cannot tell if a text field is focused
- First model download requires internet (~466MB)
- Very long recordings (>10 min) may use significant RAM during transcription
- No `.bat` files or setup scripts — the app is installed via `pip install .` and run as `voice-typer` or `python -m voice_typer`

## License

MIT
