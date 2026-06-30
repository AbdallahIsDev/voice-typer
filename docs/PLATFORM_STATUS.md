# Platform Status

Feature × OS matrix for Voice Typer.  Last updated: 2026-06-30.

| Feature                    | Windows | macOS | Linux (X11) | Linux (Wayland) |
|----------------------------|---------|-------|-------------|-----------------|
| Global hotkey (native)     | ✅ `windows-key-listener.exe` (`WH_KEYBOARD_LL`) | ✅ `macos-key-listener` (Swift, `NSEvent.modifierFlags.function` + `CGEvent` tap) | ✅ `linux-key-listener` (evdev `/dev/input/event*`) | ✅ `linux-key-listener` (evdev `/dev/input/event*`) |
| Global hotkey (legacy fallback) | ✅ `RegisterHotKey` + `GetAsyncKeyState` polling | ⚠️ `pynput` (needs Accessibility) | ✅ `pynput` X11 | ❌ N/A (use native binary) |
| Push-to-talk (on_release)  | ✅ Native or polling fallback | ✅ Native or `pynput` fallback | ✅ Native or `pynput` fallback | ✅ Native |
| Default hotkey             | `Caps Lock` | `Fn` (Globe key) | `Caps Lock` | `Caps Lock` |
| Required permission / setup | OS-level Caps Lock remap (PowerToys / registry Scancode Map) recommended | **Accessibility** permission (System Settings → Privacy & Security → Accessibility) | `input` group (`sudo usermod -aG input $USER`) + `setxkbmap -option caps:none` | `input` group + compositor-level Caps Lock remap (`keyd`/`kmonad`) |
| Key suppression (so the hotkey doesn't reach the foreground app) | ✅ `WH_KEYBOARD_LL` returns non-zero | ✅ `CGEvent` tap returns NULL | ❌ evdev is read-only — neutralize via `setxkbmap` | ❌ Same as X11 |
| Modifier-only hotkeys (Alt / Ctrl / Shift / Win / Fn) | ✅ | ✅ | ✅ | ✅ |
| Fn / Globe key             | ❌ Firmware-only | ✅ Native binary only | ❌ Firmware-only | ❌ Firmware-only |
| System tray icon           | ✅ pystray Win32 | ✅ pystray AppKit | ✅ pystray GTK | ✅ pystray GTK |
| Tray notifications         | ✅ `Shell_NotifyIcon` | ✅ `NSUserNotificationCenter` | ✅ `Notify` (libnotify) | ✅ `Notify` |
| Autostart                  | ✅ Registry `HKCU\...\Run` | ✅ `LaunchAgents` plist | ✅ `.desktop` in `~/.config/autostart/` | ✅ Same |
| Single-instance lock       | ✅ Win32 named mutex | ⚠️ Lockfile (best-effort) | ⚠️ Lockfile (best-effort) | ⚠️ Lockfile |
| Microphone listing         | ✅ sounddevice WASAPI | ✅ sounddevice CoreAudio | ✅ sounddevice ALSA/PulseAudio | ✅ sounddevice PipeWire |
| Clipboard paste            | ✅ `pyperclip` + Win32 API | ✅ `pyperclip` + `pbpaste` | ✅ `pyperclip` + `xclip`/`xsel` | ⚠️ `wl-copy` (if installed) |
| Focus detection (safe auto-paste) | ✅ Win32 API | ❌ N/A (text always copied to clipboard) | ❌ N/A | ❌ N/A |
| Audio recording            | ✅ PortAudio WASAPI | ✅ PortAudio CoreAudio | ✅ PortAudio ALSA | ✅ PortAudio PipeWire |
| Console handler (Ctrl+C)   | ✅ `SetConsoleCtrlHandler` | ❌ N/A | ❌ N/A | ❌ N/A |
| Devnull redirect (pythonw) | ✅ `os.devnull` | ✅ `os.devnull` | ✅ `os.devnull` | ✅ `os.devnull` |
| IPC TCP (loopback)         | ✅ | ✅ | ✅ | ✅ |
| IPC session token auth     | ✅ | ✅ | ✅ | ✅ |
| Config file permissions    | ⚠️ NTFS ACLs (default) | ✅ 0o600/0o700 | ✅ 0o600/0o700 | ✅ 0o600/0o700 |
| Model download (CLI)       | ✅ `voice-typer setup` | ✅ | ✅ | ✅ |
| Model download (UI)        | ❌ Not implemented | ❌ | ❌ | ❌ |
| Native binary build command | `scripts/build/compile_native.sh` (or `.ps1`) | `bash scripts/build/compile_native.sh` | `bash scripts/build/compile_native.sh` | `bash scripts/build/compile_native.sh` |

## Legend

- ✅ — Fully supported and tested
- ⚠️ — Partially supported or has known limitations
- ❌ — Not supported

## Per-platform default hotkey

The `hotkey` config default is platform-aware (`_default_hotkey_for_platform()` in
`voice_typer/server/config.py`):

| Platform      | Default            | Why |
|---------------|--------------------|-----|
| macOS         | `<fn>`             | The Fn/Globe key on modern Macs is ergonomic, rarely conflicts with shortcuts, and is supported only on macOS via the native Swift binary. |
| Windows       | `<caps_lock>`      | Ergonomic single-key trigger. The native `WH_KEYBOARD_LL` binary suppresses the keydown so the OS doesn't toggle caps state while Voice Typer is running. |
| Linux         | `<caps_lock>`      | Same ergonomic rationale. The evdev backend is read-only, so the user is expected to neutralize caps-toggling via `setxkbmap -option caps:none`. |

The legacy `<f2>` default from older releases is preserved as a fallback when the
native binary is missing or the platform default can't be applied. Existing users
with `<f2>` in their config keep it untouched.

## Required permissions per platform

### macOS
- **Accessibility permission** (System Settings → Privacy & Security →
  Accessibility). The native `macos-key-listener` binary uses `CGEvent` taps
  which require Accessibility. macOS updates sometimes invalidate the grant —
  re-grant by toggling Voice Typer off and back on in the Accessibility list.
- The compiled binary is ad-hoc code-signed by `scripts/build/compile_native.sh`
  so it can be trusted for Accessibility without a Developer ID.

### Windows
- **No special permission** for the `WH_KEYBOARD_LL` hook (it is an
  out-of-process hook that does not require admin rights or a DLL injection).
- Recommended but optional: OS-level Caps Lock remap (PowerToys Keyboard Manager
  or a registry Scancode Map) so Caps Lock stays neutralized even when Voice
  Typer isn't running.

### Linux
- **`input` group membership** so the binary can open `/dev/input/event*`:
  ```bash
  sudo usermod -aG input $USER
  ```
  Log out and back in for the change to take effect.
- **Caps Lock neutralization** (if you use the default hotkey): add
  `setxkbmap -option caps:none` to `~/.xprofile` (X11) or your compositor's
  startup script (Wayland). The native binary cannot suppress the keydown on
  Linux (evdev is read-only).

## Known limitations

### macOS
- **Accessibility permission**: global hotkeys require Accessibility permission
  (System Settings → Privacy & Security → Accessibility). The app does not
  detect or prompt for this — users must grant it manually.
  Tracked as **XPLAT-002**. Resolved at the binary level by **NATIVE-001**:
  the Swift binary emits `ERROR:Accessibility permission not granted` and the
  Python backend surfaces a Settings UI prompt with the grant instructions.
- **macOS updates**: macOS updates sometimes invalidate the Accessibility grant
  for previously-trusted apps. Users may need to re-grant after an update.

### Linux (Wayland)
- **Key suppression**: evdev is read-only, so the native binary cannot stop the
  hotkey press from reaching the foreground app the way the Windows
  `WH_KEYBOARD_LL` hook or the macOS `CGEvent` tap can. Users on Wayland
  (and X11) should neutralize Caps Lock at the OS level via
  `setxkbmap -option caps:none` (or `keyd`/`kmonad` for compositor-agnostic
  remapping). Tracked as **NATIVE-002**.
- **Wayland hotkey support is now real**: the previous "Not supported (needs
  portal API)" entry is obsolete — the evdev backend works on both X11 and
  Wayland because evdev sits below the display server. Tracked as
  **XPLAT-004**, resolved by **NATIVE-001**.
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

Native-backend tests live in `tests/test_native_hotkeys.py` and exercise the
Python side of the wire protocol without spawning the real compiled binary
(the binary is mocked).

## Adding a new platform

1. Add the platform to the matrix above.
2. Implement platform-specific adapters in `voice_typer/server/platform.py`.
3. If the platform needs a new native key listener, add the source under
   `voice_typer/server/native/<platform>-key-listener.*` and a build case to
   `scripts/build/compile_native.sh`.
4. Wire the binary lookup into
   `voice_typer/server/native_hotkeys.get_native_binary_path()` and a backend
   subclass of `SubprocessHotkeyBackend` in `voice_typer/server/hotkeys.py`.
5. Add `@pytest.mark.platform_specific` tests in `tests/`.
6. Update this file with the feature×OS status.
