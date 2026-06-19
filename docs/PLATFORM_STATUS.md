# Platform Status

Feature × OS matrix for Voice Typer.  Last updated: 2026-06-18.

| Feature                    | Windows | macOS | Linux (X11) | Linux (Wayland) |
|----------------------------|---------|-------|-------------|-----------------|
| Global hotkey              | ✅ Win32 `RegisterHotKey` + polling | ⚠️ `pynput` (needs Accessibility permission) | ✅ `pynput` X11 | ❌ Not supported (needs portal API) |
| Push-to-talk (on_release)  | ✅ Polling fallback | ⚠️ `pynput` fallback | ✅ `pynput` fallback | ❌ |
| System tray icon           | ✅ pystray Win32 | ✅ pystray AppKit | ✅ pystray GTK | ✅ pystray GTK |
| Tray notifications         | ✅ `Shell_NotifyIcon` | ✅ `NSUserNotificationCenter` | ✅ `Notify` (libnotify) | ✅ `Notify` |
| Autostart                  | ✅ Registry `HKCU\...\Run` | ✅ `LaunchAgents` plist | ✅ `.desktop` in `~/.config/autostart/` | ✅ Same |
| Single-instance lock       | ✅ Win32 named mutex | ⚠️ Lockfile (best-effort) | ⚠️ Lockfile (best-effort) | ⚠️ Lockfile |
| Microphone listing         | ✅ sounddevice WASAPI | ✅ sounddevice CoreAudio | ✅ sounddevice ALSA/PulseAudio | ✅ sounddevice PipeWire |
| Clipboard paste            | ✅ `pyperclip` + Win32 API | ✅ `pyperclip` + `pbpaste` | ✅ `pyperclip` + `xclip`/`xsel` | ⚠️ `wl-copy` (if installed) |
| Audio recording            | ✅ PortAudio WASAPI | ✅ PortAudio CoreAudio | ✅ PortAudio ALSA | ✅ PortAudio PipeWire |
| Console handler (Ctrl+C)   | ✅ `SetConsoleCtrlHandler` | ❌ N/A | ❌ N/A | ❌ N/A |
| Devnull redirect (pythonw) | ✅ `os.devnull` | ✅ `os.devnull` | ✅ `os.devnull` | ✅ `os.devnull` |
| IPC TCP (loopback)         | ✅ | ✅ | ✅ | ✅ |
| IPC session token auth     | ✅ | ✅ | ✅ | ✅ |
| Config file permissions    | ⚠️ NTFS ACLs (default) | ✅ 0o600/0o700 | ✅ 0o600/0o700 | ✅ 0o600/0o700 |
| Model download (CLI)       | ✅ `voice-typer setup` | ✅ | ✅ | ✅ |
| Model download (UI)        | ❌ Not implemented | ❌ | ❌ | ❌ |

## Legend

- ✅ — Fully supported and tested
- ⚠️ — Partially supported or has known limitations
- ❌ — Not supported

## Known limitations

### macOS
- **Accessibility permission**: global hotkeys require Accessibility permission
  (System Settings → Privacy & Security → Accessibility).  The app does not
  detect or prompt for this — users must grant it manually.
  Tracked as **XPLAT-002**.

### Linux (Wayland)
- **Global hotkeys**: `pynput` uses X11 APIs which don't work on Wayland.
  The app falls back to polling `GetAsyncKeyState` on Windows, but on
  Wayland there's no equivalent.  Users on Wayland should use the tray
  menu's "Toggle Dictation" option instead of a hotkey.
  Tracked as **XPLAT-004**.
- **Clipboard**: `pyperclip` may not work on Wayland without `wl-copy`
  installed.  Users should install `wl-clipboard` package.

### Windows
- **`wmic` deprecation**: `wmic` is deprecated since Win10 21H1 and may be
  removed in future Windows builds.  The server-side code (`_another_voice_typer_alive`)
  was removed (DEAD-013) and the client-side `killStalePython` was removed
  (RELIABILITY-002), so `wmic` is no longer used by Voice Typer.
  The remaining `wmic`-like operations use `psutil` or `tasklist`.

## Testing

Platform-specific tests are marked with `@pytest.mark.platform_specific`.
To run only platform-specific tests:

```bash
pytest -m platform_specific
```

To skip platform-specific tests:

```bash
pytest -m "not platform_specific"
```

## Adding a new platform

1. Add the platform to the matrix above.
2. Implement platform-specific adapters in `voice_typer/server/platform.py`.
3. Add `@pytest.mark.platform_specific` tests in `tests/`.
4. Update this file with the feature×OS status.
