# Permissions per OS

Consolidated reference for the OS-level permissions Voice Typer needs on
each platform. The matrix in [`PLATFORM_STATUS.md`](PLATFORM_STATUS.md)
covers the feature × OS surface; this doc focuses specifically on the
**permissions a user must grant** before Voice Typer can use the global
hotkey, the microphone, and the clipboard-paste path on each OS.

> **TL;DR:** Windows needs **no special permission** for the
> `WH_KEYBOARD_LL` hotkey hook (it's an out-of-process hook that
> requires no admin rights and no DLL injection). macOS needs
> **Accessibility** (for the hotkey) + **Microphone** (for audio
> capture). Linux needs **`input` group membership** + **Caps Lock
> neutralization** (per compositor), plus the usual PulseAudio /
> PipeWire microphone access (no system-level permission dialog).

---

## macOS

### Accessibility permission (required for the global hotkey)

The native `macos-key-listener` binary (Swift) uses `CGEvent` taps to
detect the global hotkey. `CGEvent.tapCreate` requires the
**Accessibility** permission (System Settings → Privacy & Security →
Accessibility).

- **First launch**: Voice Typer detects the missing grant via the
  zero-command onboarding flow (ADR-0008 Gap 2). The native binary
  probes `AXIsProcessTrusted()` on startup; if it returns `false`,
  Voice Typer shows a tray notification with a deep-link to
  `x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility`
  and a 60 s retry timer. The moment the user toggles Voice Typer on in
  the Accessibility list, the retry succeeds and the native backend
  auto-restarts — no app restart required.
- **After macOS updates**: macOS updates sometimes invalidate the
  Accessibility grant for previously-trusted apps. The next Voice Typer
  launch will re-detect the missing grant and re-fire the onboarding
  notification automatically.
- **Code signing**: the compiled binary is ad-hoc code-signed by
  `scripts/build/compile_native.sh` so it can be trusted for
  Accessibility without a Developer ID. If you build from source
  yourself, you may need to grant Accessibility to the unsigned binary
  directly (System Settings will warn about the unsigned origin).

### Microphone permission (required for audio capture)

Voice Typer uses `sounddevice` / PortAudio's CoreAudio backend to
capture microphone audio. macOS requires the **Microphone** permission
(System Settings → Privacy & Security → Microphone) for any process
that opens an audio input device.

- **First launch**: the first time Voice Typer tries to open the
  microphone, macOS shows the standard "Voice Typer wants to access
  the microphone" dialog. The user must click **Allow**.
- **If the user clicked Don't Allow**: Voice Typer cannot reopen the
  microphone — the user must go to System Settings → Privacy & Security
  → Microphone and toggle Voice Typer on manually. Voice Typer shows a
  tray notification with a deep-link when it detects a permission
  failure.
- **TCC.db reset** (advanced): if the permission is in a broken state
  (e.g. after a macOS major-version upgrade), reset it via
  `tccutil reset Microphone com.voice-typer` (substitute the actual
  bundle ID).

### No special permission for clipboard paste

macOS does not gate clipboard access or synthetic keystrokes (pynput's
`keyboard.press`/`release`) behind a system permission. Once
Accessibility is granted for the hotkey, the paste path works without
further setup.

---

## Linux (X11 + Wayland)

### `input` group membership (required for the global hotkey)

The native `linux-key-listener` binary reads `/dev/input/event*` via
evdev. By default, these device nodes are owned by `root:input` with
mode `0660`, so only members of the `input` group can read them.

- **`.deb` / `.rpm` install**: the package's `postinst` /
  `postinst.rpm` script automatically adds the installing user to the
  `input` group via `usermod -aG input $USER` and installs the udev
  rule `99-voice-typer.rules` (which sets `GROUP="input"` on the
  event device nodes). After install, **log out and log back in once**
  so the new group membership takes effect — there is no other manual
  step.
- **AppImage install**: AppImage cannot install udev rules or modify
  group membership without root. The first launch shows a `pkexec` GUI
  prompt (backed by the `voice-typer.polkit` policy) asking for the
  user's sudo password once. Voice Typer itself never prompts for or
  stores the password.
- **Manual setup** (if you're running from source without a package):
  ```bash
  sudo usermod -aG input $USER
  # log out and back in
  sudo cp voice_typer/server/native/99-voice-typer.rules /etc/udev/rules.d/
  sudo udevadm control --reload-rules
  sudo udevadm trigger
  ```

### Caps Lock neutralization (per compositor)

The native evdev backend is **read-only** — it cannot suppress the
hotkey press from reaching the foreground app the way the Windows
`WH_KEYBOARD_LL` hook or the macOS `CGEvent` tap can. If your hotkey is
`Caps Lock` (the default on Linux), the OS will toggle caps state every
time you trigger Voice Typer, which is undesirable.

The mitigation is to neutralize Caps Lock at the OS level so the key
still fires the evdev listener but doesn't toggle anything:

| Compositor / display server | Neutralization method |
|------------------------------|-----------------------|
| X11 (any window manager) | `setxkbmap -option caps:none` (run from `~/.xprofile` or your WM's autostart). The `.deb` / `.rpm` `postinst` script detects an X11 session and runs this for you. |
| GNOME (Wayland) | `gsettings set org.gnome.desktop.input-sources xkb-options "['caps:none']"` (or via Tweaks → Keyboard & Mouse → Additional Layout Options → Caps Lock behavior → Disabled). |
| KDE Plasma (Wayland) | System Settings → Keyboard → Advanced → Caps Lock behavior → Disabled. Writes to `~/.config/kxkbrc`. |
| Sway / wlroots (Wayland) | `input * xkb_options caps:none` in `~/.config/sway/config`. |
| Hyprland (Wayland) | `input { kb_options = caps:none }` in `~/.config/hypr/hyprland.conf`. |
| Compositor-agnostic (any) | Install `keyd` or `kmonad` and remap Caps Lock at the kernel-input layer. This works regardless of the display server. |

The `.deb` / `.rpm` `postinst` script detects the session type (X11 /
GNOME / KDE / Sway / Wayland-other / headless) and applies the right
neutralization for that compositor automatically. The AppImage `pkexec`
path does the same. If you're running from source, you must apply the
neutralization yourself.

### Microphone access (no system-level permission dialog)

Linux does not have a system-level "Microphone" permission dialog like
macOS / Windows. Microphone access is governed by:

- **PulseAudio** (X11): the default config allows any user in the
  `audio` group to read from the default source. Modern PulseAudio
  installs use ACLs (Kit) or module-device-restore defaults; in
  practice, any local user can capture audio.
- **PipeWire** (Wayland + modern X11): same — PipeWire's default
  config exposes the `alsa_input.*` source nodes to any local user
  via the `access=unrestricted` permission on the client. Some
  distributions (notably Fedora 38+) ship with a WirePlumber policy
  that asks the user (via `xdg-desktop-portal`) before granting
  microphone access to a new client; the prompt appears on first
  recording.
- **ALSA direct** (rare, only when PulseAudio / PipeWire are absent):
  requires membership in the `audio` group, same as PulseAudio.

If Voice Typer reports "no microphone found", check:

1. `pactl list sources short` (PulseAudio) or `pw-cli list-objects`
   (PipeWire) — the source must be listed.
2. `groups $USER` must include `audio` (or the equivalent ACL grant).
3. On PipeWire + WirePlumber + a portal-enabled desktop, the portal
   prompt may have been dismissed — re-launch Voice Typer to re-trigger
   it.

### No special permission for clipboard paste

Linux does not gate clipboard access (X11: `xclip` / `xsel` / `Xfixes`;
Wayland: `wl-copy`). Synthetic keystrokes (pynput's `keyboard.press` /
`release`) work without a permission on X11; on Wayland, pynput's
uinput backend requires the same `input` group membership as the
hotkey listener (covered above).

---

## Windows

### No special permission for the `WH_KEYBOARD_LL` hotkey hook

The native `windows-key-listener.exe` uses the `WH_KEYBOARD_LL` low-level
keyboard hook. This is an **out-of-process** hook (registered via
`SetWindowsHookExW` with `WH_KEYBOARD_LL`) that:

- Does NOT require administrator rights.
- Does NOT require a DLL injection (the hook callback lives in the
  listener exe's own address space — Windows routes the keyboard events
  to it via the hook chain).
- Does NOT require any UAC prompt or system-settings toggle.

**Zero-command out of the box** — no onboarding prompt is shown on
Windows. The only prerequisite is that the listener exe is registered
as an auto-start entry (handled by the installer) or launched manually.

### No special permission for microphone access

Windows does not have a system-level "Microphone" permission toggle for
desktop (Win32) apps the way it does for UWP apps. The first time a
Win32 process opens the default audio capture endpoint via WASAPI /
DirectSound, Windows may show a one-time "Voice Typer wants to use your
microphone" toast notification (Windows 10 1903+), but the access is
granted automatically — there is no Settings toggle to deny.

If the user has globally disabled microphone access via Settings →
Privacy → Microphone → "Allow apps to access your microphone" (which
affects Win32 apps too on Win10 1903+), Voice Typer will fail to open
the device with `E_ACCESSDENIED`. The mitigation is to re-enable that
global toggle.

### No special permission for clipboard paste

Windows does not gate clipboard access (`OpenClipboard` /
`SetClipboardData`) or synthetic keystrokes (`SendInput`) behind a
system permission. The only relevant setting is "Let apps use the
clipboard" (Settings → Privacy → Clipboard history), which controls
the Clipboard History feature, not direct clipboard access by desktop
apps.

### Recommended but optional: OS-level Caps Lock remap

Not a permission — but a recommended ergonomic setup. The default
hotkey on Windows is `Caps Lock`. The native `WH_KEYBOARD_LL` binary
suppresses the keydown event so the OS doesn't toggle caps state while
Voice Typer is running, but when Voice Typer isn't running, Caps Lock
still toggles normally. To neutralize Caps Lock permanently (so it
never toggles caps state, even when Voice Typer isn't running):

- **PowerToys Keyboard Manager** (recommended): remap Caps Lock to
  "Disable" — survives OS updates and is per-user (no admin needed).
- **Registry Scancode Map** (alternative):
  ```reg
  Windows Registry Editor Version 5.00
  [HKEY_CURRENT_USER\Keyboard Layout]
  "Scancode Map"=hex:00,00,00,00,00,00,00,00,02,00,00,00,00,00,3a,00,00,00,00,00
  ```
  Requires logoff / logon to take effect. Survives OS updates.

---

## Verifying your setup

After granting the permissions above, verify with:

- **macOS**: open System Settings → Privacy & Security and confirm
  Voice Typer is listed (and toggled ON) under both Accessibility and
  Microphone. Then trigger the global hotkey — if it works, the
  Accessibility grant landed. Then start a dictation — if the recording
  indicator lights up, the Microphone grant landed.
- **Linux**: `groups` should list `input` (and `audio` if you needed
  it). `ls -l /dev/input/event*` should show `crw-rw---- root input`.
  Press Caps Lock outside Voice Typer — if the caps state doesn't
  toggle, the neutralization landed.
- **Windows**: no verification step is needed — if the installer
  completed successfully, the hotkey works on next login.

## See also

- [`docs/PLATFORM_STATUS.md`](PLATFORM_STATUS.md) — the feature × OS
  matrix (which features ship on which platforms).
- [`docs/adr/0008-zero-command-hotkey-architecture.md`](adr/0008-zero-command-hotkey-architecture.md) —
  the zero-command onboarding ADR (the auto-grant flow on macOS +
  Linux).
- [`docs/adr/0007-native-hotkey-architecture.md`](adr/0007-native-hotkey-architecture.md) —
  the native-binary architecture ADR (`{windows,macos,linux}-key-listener`).
- [`docs/migration/tauri-build-runbook.md`](migration/tauri-build-runbook.md) —
  per-OS build requirements for the Tauri host (display server + toolchain).
