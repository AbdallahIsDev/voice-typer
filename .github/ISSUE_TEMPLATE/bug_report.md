---
name: Bug Report
about: Report a bug or unexpected behavior
title: "[BUG] "
labels: bug
assignees: ''
---

## Describe the Bug

A clear and concise description of what the bug is.

## Steps to Reproduce

1. 
2. 
3. 

## Expected Behavior

What you expected to happen.

## Actual Behavior

What actually happened.

## Environment

- **Voice Typer version:** (run `python -m voice_typer --version`)
- **OS:** (e.g. Windows 11 23H2, macOS 14, Ubuntu 24.04)
- **Python version:** (if running from source)
- **Node version:** (if running from source)
- **ASR backend:** (Whisper / Qwen / Parakeet / Cloud)
- **Model size:** (tiny.en / small.en / medium.en)
- **Microphone:** (built-in / USB / Bluetooth)

## Logs

If applicable, paste relevant log output. The log file location is shown
in the app's About/Diagnostics page, or check the per-platform location:

| Platform | Python host log | Rust / Tauri host log |
|---|---|---|
| Windows (new installs) | `%APPDATA%\voice-typer\voice-typer.log` | `%APPDATA%\voice-typer\logs\voice-typer.log` |
| Windows (existing users) | `%USERPROFILE%\.voice-typer\voice-typer.log` (legacy path, honored if exists) | `%USERPROFILE%\.voice-typer\logs\voice-typer.log` |
| macOS | `~/Library/Application Support/voice-typer/voice-typer.log` | `~/Library/Application Support/voice-typer/logs/voice-typer.log` |
| Linux | `$XDG_DATA_HOME/voice-typer/voice-typer.log` (falls back to `~/.local/share/voice-typer/voice-typer.log`) | `$XDG_DATA_HOME/voice-typer/logs/voice-typer.log` |

See `docs/home-directory.md` §"Log File Paths" for the canonical reference.

```
<paste logs here>
```

## Diagnostic Bundle

Please attach a diagnostic bundle to help us reproduce the issue. Run:

```
python scripts/diagnostics.py export
```

This produces a timestamped zip file under the working directory
(`voice-typer-diagnostics-<UTC timestamp>.zip`). Attach it to this
issue (drag-and-drop onto the GitHub editor).

**The bundle contains:**

- `system_info.json` — OS, Python version, architecture, GPU / CUDA
  info (if `torch` is installed), `voice-typer` app version.
- `config_redacted.json` — your `~/.voice-typer/config.json` with
  secret fields redacted (API keys, cloud credentials, etc., via the
  canonical `_SECRET_CONFIG_FIELDS` frozenset from
  `voice_typer/server/ipc_server.py`).
- `voice-typer.log` — the Python host log (last 1 MiB if larger).
- `rust-voice-typer.log[.N]` — the Rust / Tauri host log (+ any
  rotated variants) from `<config_dir>/logs/`.
- `model_info.json` — which ASR models are currently downloaded.

**The bundle EXCLUDES (PII never leaves your machine):**

- Transcription text (your dictated content is NOT included).
- API keys and secrets (redacted in `config_redacted.json`).
- Crash recovery buffer contents (recent clipboard / dictation
  scratch space).

If you would rather not share any of the above files, you may redact
further or omit the bundle entirely — but please still fill in the
Environment section below.

## Reproduction Hint (for crashes)

If this bug is a crash (the app quit unexpectedly, the tray icon
disappeared, the bubble froze, etc.), please add the following so we
can reproduce it quickly:

- **Last user action:** what did you do immediately before the crash?
  (e.g. "pressed the dictation hotkey", "clicked the tray menu →
  Settings", "switched ASR backend to Qwen".)
- **Last IPC command visible in the log:** open `voice-typer.log` and
  copy the last `ipc.command=` line (or the last 20 log lines if no
  IPC command is visible).
- **Crash diagnostics file:** if a `crash_diagnostics.<PID>.txt` file
  was written next to the log (only emitted by the Windows VEH crash
  handler on hard crashes), attach it here. It contains the crash
  address, register dump, and stack trace — no transcription text.

## Additional Context

Add any other context about the problem here (screenshots, config, etc.).
