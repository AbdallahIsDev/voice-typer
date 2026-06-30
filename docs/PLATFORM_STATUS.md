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
- **Zero-command onboarding (ADR 0006, Gap 2)**: when the native binary detects
  a missing Accessibility grant, Voice Typer automatically shows a tray
  notification and deep-links to System Settings → Privacy & Security →
  Accessibility via the `x-apple.systempreferences:` scheme. A 60s retry timer
  polls for the grant and auto-restarts the native backend the moment the user
  toggles Voice Typer on in the Accessibility list — no app restart required.

### Windows
- **No special permission** for the `WH_KEYBOARD_LL` hook (it is an
  out-of-process hook that does not require admin rights or a DLL injection).
  Zero-command out of the box — no onboarding prompt needed.
- Recommended but optional: OS-level Caps Lock remap (PowerToys Keyboard Manager
  or a registry Scancode Map) so Caps Lock stays neutralized even when Voice
  Typer isn't running.

### Linux
- **Zero-command setup (ADR 0006, Gap 3)**: `.deb` and `.rpm` packages ship
  `postinst` / `postinst.rpm` scripts that automatically:
  - install the udev rule `99-voice-typer.rules` (grants the `input` group
    read access to `/dev/input/event*`) and reload udev,
  - add the installing user to the `input` group via `usermod -aG input`,
  - detect the session type (X11 / GNOME / KDE / Sway / Wayland-other /
    headless) and configure Caps Lock neutralization for that compositor
    (`setxkbmap -option caps:none` on X11, XKB config drop-in on libinput
    compositors),
  - write a manifest at `/var/lib/voice-typer/permissions-manifest.json` so
    `prerm` / `prerm.rpm` can cleanly uninstall every change.
- **AppImage users** get a `pkexec` GUI prompt (backed by the
  `voice-typer.polkit` policy) on first launch. The OS asks for the user's
  sudo password once; Voice Typer itself never prompts for or stores a
  password.
- After installing a `.deb`/`.rpm`, log out and log back in once so the new
  `input` group membership takes effect — there is no other manual step.
- The compiled binary is the native `linux-key-listener` (evdev), which works
  on both X11 and Wayland because evdev sits below the display server.

## Known limitations

### macOS
- **Accessibility permission**: global hotkeys require Accessibility permission
  (System Settings → Privacy & Security → Accessibility). As of **ADR 0006
  (Gap 2)**, the app detects the missing grant, shows a tray notification with
  an "Open Settings" deep-link, and auto-restarts the native backend via a
  60s retry timer once the user toggles Voice Typer on in the Accessibility
  list. Tracked as **XPLAT-002**, resolved at the binary level by
  **NATIVE-001** and at the UX level by **ADR 0006**.
- **macOS updates**: macOS updates sometimes invalidate the Accessibility grant
  for previously-trusted apps. Users may need to re-grant after an update
  (the onboarding notification will re-fire automatically on the next launch).

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
