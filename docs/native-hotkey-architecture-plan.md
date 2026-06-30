# The complete plan

## Phase 0 — Reconcile the design decisions (no code)

1. Default trigger key per platform: Fn on macOS, Caps Lock on Windows, Caps Lock on Linux (with Alt as fallback)
2. Detection architecture: out-of-process native binary per platform, line-delimited stdout protocol (Freestyle's model)
3. Settings UI: single capture input, no dropdown (your plan)
4. Remove the F1–F12 / Home / Page Up / etc. dropdown entirely

## Phase 1 — macOS Swift binary + FN support (highest value)

Port Freestyle's macos-key-listener.swift, trimmed:

**Keep:**
- `NSEvent.addGlobalMonitorForEvents(.flagsChanged)` with `.function` flag edge-detection
- `CGEvent.tapCreate` for key-up + suppression
- `setActivationPolicy(.accessory)`
- SIGTERM teardown

**Drop (for now):**
- Mouse button 4/5 hooks, right-modifier distinction, F13–F24

**Wire into:**
- A new `MacNativeHotkey(HotkeyBackend)` in `hotkeys.py` that spawns the binary via `subprocess.Popen` and reads stdout
- Update `create_hotkey_backend()` to prefer this on macOS
- Add Accessibility permission check + onboarding prompt (deep-link to `x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?Privacy_Accessibility`)

**Build:**
- `swiftc -O macos-key-listener.swift -framework Cocoa -o macos-key-listener ` in your build pipeline
- Bundle in PyInstaller spec

## Phase 2 — Windows C binary (replaces `GetAsyncKeyState` polling)

Port Freestyle's windows-key-listener.c, trimmed:

**Keep:**
- `SetWindowsHookEx(WH_KEYBOARD_LL, ...)`
- Modifier-only hotkey support
- Key suppression via `return 1`

**Drop:**
- `WH_MOUSE_LL` (unless you want mouse button triggers)

**Add:**
- A new `WindowsNativeHotkey` (replacing the current polling implementation) that spawns the binary
- Keep the existing pynput fallback as `WindowsPynputFallback` for emergency use only — surface it in logs but not in UI

**Validate this fixes:**
- Modifier-only Caps Lock / Alt detection
- No missed presses
- Zero idle CPU

## Phase 3 — Linux C binary (replaces non-functional pynput)

Port Freestyle's linux-key-listener.c:

- `/dev/input/event*` polling via `poll()`
- Add udev rule installation in your setup/installer: write `/etc/udev/rules.d/99-voice-typer.rules` granting `input` group access to event devices, or document that users must `sudo usermod -aG input $USER`
- This makes Linux actually work for the first time — currently it's vaporware
- Update README to remove "Windows-only" disclaimer

## Phase 4 — Unify Python side

- Refactor `HotkeyBackend` so all three backends share a common subprocess-spawning, stdout-parsing base class (`SubprocessHotkeyBackend`)
- The existing `capture_hotkey()` polling function gets replaced with "spawn binary in record mode, forward events to UI"
- `hotkey_dispatcher.py` stays mostly unchanged — it already abstracts over `HotkeyBackend` instances

## Phase 5 — Settings UI cleanup

- Remove the dropdown of "guaranteed" keys entirely (your plan)
- Single capture input that accepts any key, including:
  - Caps Lock (with a guided OS-remap step to neutralize the caps toggle)
  - Alt as standalone (modifier-only release detection — works now via the new binaries)
  - Fn on macOS only (validates as Fn token, rejected on Win/Linux with a friendly message)
- Default per platform: Fn (macOS) / Caps Lock (Win) / Caps Lock (Linux)
- Onboarding step for Caps Lock OS remap:
  - **macOS:** guide to System Settings → Keyboard → Modifier Keys → Caps Lock = No Action
  - **Windows:** offer to install the registry Scancode Map automatically (or guide to PowerToys)
  - **Linux:** write `setxkbmap -option caps:none` to `~/.xprofile` / XKB config

## Phase 6 — Documentation & permissions

- README updated to reflect "cross-platform" status (no more "Windows-only")
- Per-platform permission requirements table:
  - **macOS:** Microphone + Accessibility
  - **Windows:** Microphone (no extra permission for WH_KEYBOARD_LL)
  - **Linux:** Microphone + input group membership (with udev rule for auto-setup)
- Troubleshooting section covering: Accessibility re-grant after macOS update, input group setup on Linux, Caps Lock OS remap per platform
