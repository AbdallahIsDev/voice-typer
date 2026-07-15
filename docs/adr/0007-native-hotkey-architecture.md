# ADR 0007: Native subprocess hotkey architecture

## Status

Accepted

## Date

2026-06-30

## Context

Voice Typer's hotkey detection lived entirely inside the Python process. On
Windows the backend was `WindowsNativeHotkey`, which used Win32
`RegisterHotKey` plus a `GetAsyncKeyState` polling thread; on macOS and Linux
the backend was `PynputHotkey`, which uses `pynput`'s `GlobalHotKeysListener`
under the hood (Quartz event tap on macOS, Xlib record extension on Linux).

This architecture had three concrete limitations that blocked the
cross-platform roadmap:

1. **No FN key support on macOS.** The Fn / Globe key on modern MacBooks is
   the most ergonomic single-key trigger for dictation, but `pynput` does not
   surface it. The Fn key is reported to user space via
   `NSEvent.modifierFlags.contains(.function)`, not as a regular keyDown
   event — so the only way to observe it is to install a Quartz event tap
   (`CGEvent.tapCreate`) and read the modifier flags off every event. That
   needs to happen from a native binary (or a `pyobjc` bridge that runs its
   own `NSRunLoop`), not from `pynput`.

2. **CPU-wasting polling on Windows.** `GetAsyncKeyState` is a polled API.
   The legacy backend polled every ~10 ms to keep latency below human
   perceptual threshold. That is ~100 wakeups/sec on a thread that does
   nothing useful 99.99% of the time, which is a measurable battery/CPU
   cost on laptops. `WH_KEYBOARD_LL` is event-driven — the OS dispatches
   the low-level hook callback only when a key actually transitions — so
   it has near-zero idle cost.

3. **No Wayland support on Linux.** `pynput` on Linux uses the Xlib record
   extension, which only works under X11. On Wayland the global hotkey simply
   did not work — users had to use the tray menu's "Toggle Dictation" item
   instead. The XDG desktop portal has a global-shortcuts API, but it is
   compositor-dependent, requires the calling app to be a "background"
   portal client, and is awkward to drive from Python. `/dev/input/event*`
   (evdev) is below the display server and works on both X11 and Wayland,
   but reading from it requires raw file I/O and group membership that
   `pynput` does not abstract.

A secondary concern was **crash isolation**: an in-process hotkey backend
that hangs or crashes (e.g. `pynput`'s Xlib thread deadlocks, the
`RegisterHotKey` thread throwing) takes the entire Python backend down with
it, including the tray icon and the recorder. An out-of-process binary can
crash without bringing down the rest of the app — the Python side just sees
the subprocess exit and can either restart it or fall back.

Options considered:

1. **pyobjc in-process** (macOS) — use `pyobjc` to drive `NSEvent` and
   `CGEvent.tapCreate` directly from the Python process.
   Rejected: pyobjc needs to run its own `NSRunLoop` on the main thread,
   which conflicts with `pystray`'s AppKit run loop and the IPC server's
   threading model. The GIL also serializes every event tap callback,
   which can drop events under load. No crash isolation.
2. **Keep `pynput` on macOS/Linux** — accept the limitations.
   Rejected: no FN support on macOS, no Wayland support on Linux. Both
   are project-blocking for the cross-platform roadmap.
3. **Keep `GetAsyncKeyState` polling on Windows** — accept the CPU cost.
   Rejected: the polling thread was the single largest contributor to
   Voice Typer's idle CPU usage on Windows laptops, and it cannot support
   modifier-only hotkeys (e.g. bare `Alt` as a trigger) because
   `RegisterHotKey` requires a non-modifier virtual key.
4. **Out-of-process native binaries** (this ADR) — one binary per
   platform, spawned by Python as a subprocess, communicating via a
   line-delimited stdout wire protocol. Modeled on the Freestyle project's
   architecture.

## Decision

Migrate to **out-of-process native binaries**, one per platform:

- **macOS** — `voice_typer/server/native/macos-key-listener` (Swift).
  Uses `NSEvent.modifierFlags.contains(.function)` to detect the Fn/Globe
  key and a `CGEvent.tapCreate` event tap to observe all keyDown/keyUp
  events system-wide. Suppresses keys by returning `NULL` from the tap
  callback.
- **Windows** — `voice_typer/server/native/windows-key-listener.exe` (C).
  Uses `SetWindowsHookEx(WH_KEYBOARD_LL)` to install a low-level
  keyboard hook and a `GetMessage` message pump. Suppresses keys by
  returning non-zero from the `LowLevelKeyboardProc` callback.
- **Linux** — `voice_typer/server/native/linux-key-listener` (C).
  Discovers `/dev/input/event*` devices that have `EV_KEY` capability,
  opens them read-only, and `poll()`s for events. No suppression is
  possible (evdev is read-only).

All three binaries speak the same line-delimited stdout wire protocol:

```
READY                          # binary initialized successfully
FN_DOWN                        # macOS only — FN/Globe key pressed (edge-detected)
FN_UP                          # macOS only — FN/Globe key released (edge-detected)
KEY_DOWN:<Name>                # non-modifier key pressed (e.g. KEY_DOWN:Space)
KEY_UP:<Name>                  # non-modifier key released
MOD_DOWN:<Name>                # modifier pressed (Ctrl, Shift, Alt, Cmd, RightAlt, etc.)
MOD_UP:<Name>                  # modifier released
ERROR:<message>                # fatal error, binary will exit
```

The binary is spawned with the hotkey spec as `argv[1]`. The Python side
parses lines and matches against the registered hotkey. The same binary
is reused in record mode for the Settings capture dialog.

The `create_hotkey_backend()` factory in `voice_typer/server/hotkeys.py`
prefers the native backend for the current platform and falls back to the
legacy in-process backends (`PynputHotkey`, `WindowsNativeHotkey`,
`WaylandHotkey`) when the native binary is missing or fails to start.
This keeps the app usable from a fresh source checkout before the binary
has been compiled.

The build is centralized in `scripts/build/compile_native.sh`
(`scripts/build/compile_native.ps1` on Windows). The script auto-detects
the platform and only builds the binary that matches it — cross-compilation
is not supported and is delegated to per-platform CI runners.

## Consequences

### Positive
- **FN key support on macOS** — the most ergonomic single-key trigger on
  modern Macs is now usable.
- **Lower idle CPU on Windows** — `WH_KEYBOARD_LL` is event-driven, so
  the hotkey backend uses ~0% CPU when no keys are being pressed, vs the
  ~100 wakeups/sec of the legacy polling thread.
- **Wayland support on Linux** — evdev sits below the display server, so
  the same binary works on both X11 and Wayland without any compositor-
  specific code.
- **Crash isolation** — a misbehaving native binary can't take down the
  Python backend, tray icon, or recorder. The Python side detects the
  subprocess exit and can restart it or fall back to a legacy backend.
- **Key suppression on macOS and Windows** — `Caps Lock` as a hotkey no
  longer toggles the OS caps-lock state on those platforms, because the
  native binary swallows the keydown event before it reaches the
  foreground app. (Linux remains unsuppressable; see Negative below.)
- **Modifier-only hotkeys** — bare `Alt`, `Ctrl`, `Shift`, `Win`/`Cmd`,
  and (on macOS) `Fn` are now usable as single-key triggers, which the
  legacy in-process backends could not express.

### Negative
- **More build complexity** — there are now three native binaries to
  compile (one per platform). The build script handles one platform per
  invocation, so a release that ships pre-built binaries needs three CI
  runners (one per OS) or a cross-compilation pipeline we don't have.
- **More code to maintain** — roughly 1,500 lines of C and Swift across
  the three binary sources, plus the matching wire-protocol parsing on
  the Python side (`native_hotkeys.py`). The three binaries must stay
  byte-for-byte compatible on the wire protocol, which is an ongoing
  discipline burden.
- **macOS code-signing gotchas** — the Swift binary must be ad-hoc
  code-signed (at minimum) for Accessibility permission to be granted
  without a Developer ID. `compile_native.sh` does this automatically
  via `codesign --force --sign -`, but if a user re-builds the binary
  themselves they may need to re-grant Accessibility.
- **Linux `input` group requirement** — evdev requires the user to be
  in the `input` group (or the binary to be `setgid input`, or run as
  root). This is a one-time setup step surfaced in the Settings UI, but
  it is more friction than the legacy `pynput` backend (which worked
  without extra setup on X11, just not on Wayland).
- **No key suppression on Linux** — evdev is read-only, so a `Caps Lock`
  hotkey on Linux still toggles the OS caps-lock state. Users must
  neutralize this at the OS level via `setxkbmap -option caps:none`.
- **Subprocess lifecycle complexity** — the Python side must spawn,
  parse stdout from, and gracefully terminate the subprocess. The
  binaries handle `SIGTERM`/`SIGINT` (Linux/macOS) and
  `SetConsoleCtrlHandler` (Windows) for clean shutdown; on Windows the
  Python parent may need to fall back to `TerminateProcess()` if the
  console-handler path doesn't fire (e.g. when spawned with
  `CREATE_NO_WINDOW`).

## Alternatives considered

- **pyobjc in-process on macOS** — see Context option 1. Rejected for GIL
  issues, run-loop conflicts with `pystray`, and no crash isolation.
- **Keep `pynput` on macOS/Linux** — see Context option 2. Rejected for no
  FN support on macOS and no Wayland support on Linux.
- **Keep `GetAsyncKeyState` polling on Windows** — see Context option 3.
  Rejected for CPU waste and no modifier-only hotkey support.
- **XDG desktop portal `global_shortcuts` on Wayland** — would have
  avoided the `input` group requirement and given us real suppression on
  Wayland. Rejected for compositor coverage (only GNOME and KDE support
  it well as of 2026), portal-client setup complexity from Python, and
  the fact that it would leave X11 on a different backend than Wayland.
  Could be revisited as a future Layer-2 backend on top of the evdev
  fallback.

## References

- Freestyle project — architecture inspiration:
  https://github.com/freestyle-voice/freestyle/
- Apple NSEvent.ModifierFlags.function docs:
  https://developer.apple.com/documentation/appkit/nsevent/modifierflags/function
- Apple CGEvent.tapCreate docs:
  https://developer.apple.com/documentation/coregraphics/1542025-cgeventtapcreate
- Microsoft WH_KEYBOARD_LL docs:
  https://learn.microsoft.com/en-us/windows/win32/winmsg/lowlevelkeyboardproc
- Linux `linux/input-event-codes.h`:
  https://github.com/torvalds/linux/blob/master/include/uapi/linux/input-event-codes.h
- Prior ADRs: ADR 0000 (ADR process), ADR 0004 (TCP IPC protocol)
