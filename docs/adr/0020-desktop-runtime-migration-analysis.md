# ADR 0020: Desktop Runtime Migration to Tauri v2 + Python Sidecar (Cross-Platform Edition)

## Status

Accepted — migration in progress. Electron is retained intact as a reversible fallback until Tauri + Sidecar is proven and cut over on **all three supported platforms** (Windows, macOS, Linux). Cutover is per-platform, not all-at-once: Windows first, then macOS, then Linux, each gated on its own Phase 0 spike.

## Date

2026-07-13 (decision) — 2026-07-14 (updated to Sidecar-only, actionable migration plan) — 2026-07-16 (cross-platform rewrite: verified against `AbdallahIsDev/voice-typer` `main`, reconciled with ADRs 0003/0007/0008/0009/0011/0014/0015/0016/0017/0018/0019, expanded to cover Windows + macOS + Linux + Wayland + Apple Silicon + Linux ARM64).

---

## 0. How to read this document

This is a **migration contract**, not a high-level proposal. Every section is written so that two implementers working independently produce the same app. The previous version of this ADR was Windows-only and made several incorrect references to other ADRs and source files; this rewrite:

1. **Fixes the ADR cross-references.** The previous version cited "ADR-0009 (Prewarm & Autostart Architecture)" — that is wrong. ADR-0009 is the **Audio Filter Chain Architecture**. The actual prewarm ADR is **ADR-0011**. Every reference has been re-verified against `docs/adr/`.
2. **Fixes the source-file line references.** The previous version cited `prewarm.py:17` for the prewarm helper; `prewarm.py` no longer exists (it was decomposed into the `prewarm/` package — see `prewarm/pipeline.py` for the entrypoint `run` function), and line 17 was part of the module docstring anyway. References now point to **symbols** (function/class names) — line numbers drift, and entire files get split into packages, so symbol names are the only stable anchor.
3. **Adds full cross-platform coverage.** Voice Typer today ships on Windows, macOS, and Linux (X11 + Wayland), per `README.md`, `docs/PLATFORM_STATUS.md`, `pyproject.toml` classifiers, `electron-builder.yml`, the `scripts/linux/` postinst/prerm/udev/polkit set, and the `prewarm_scheduler_posix.py` module. The previous ADR's Nuitka flags, code-signing commands, paste logic, autostart, prewarm scheduling, and path resolution were Windows-only and would silently produce broken macOS/Linux builds. Each technical section now has a Windows / macOS / Linux sub-section.
4. **Reconciles with the existing build assets.** The repo already contains `scripts/build/voice-typer.spec` (PyInstaller), `scripts/build/compile_native.sh` + `.ps1` (native hotkey binaries), `scripts/linux/{postinst,prerm,postinst.rpm,prerm.rpm,99-voice-typer.rules,00-voice-typer-capslock.conf,voice-typer.polkit,install_permissions.py,uninstall_permissions.py}`, and `voice_typer/client/electron-builder.yml` (NSIS + DMG x64/arm64 + AppImage/deb/rpm with notarization enabled). (The historical `scripts/build/installer.iss` Inno Setup script is no longer present in the source tree.) The migration must reuse or explicitly replace each of these — none were referenced by the previous ADR.
5. **Reconciles with the existing ADRs.** Voice Typer has 20 prior ADRs (0000–0019). The previous ADR (ADR-0013) referenced 0002 (the *initial* Electron + Python ADR, superseded by ADR-0003 — the current architecture), 0009 (wrong, actually audio filters), and 0011 (the actual prewarm ADR). The migration touches concerns governed by ADRs 0003, 0007, 0008, 0009 (real), 0011, 0014, 0015, 0016, 0017, 0018, 0019. Each is cited where relevant.

> **Plain English:** This is the rewritten migration plan. The old plan only really worked on Windows and got a few file references wrong. This version works on Windows, macOS, and Linux, fixes the references, and tells you exactly which existing build scripts and ADRs the migration interacts with.

---

## Context

Voice Typer today is **Electron (React UI) + a separate Python backend + a separate prewarm helper** — effectively **three OS processes**, plus the Electron renderer GPU child:

1. **Electron main process** (hosts the React UI). Source: `voice_typer/client/src/main/index.ts` (209 lines — wiring-only; logic in `./state/`, `./python/`, `./ipc/`, `./windows/`, `./bootstrap`). Entry: `package.json` `main: ./out/main/index.js`. Build: `electron-vite build` → `electron-builder` (NSIS on Windows, DMG on macOS x64+arm64, AppImage+deb+rpm on Linux).
2. **Python backend** — `python -m voice_typer.server.ipc_server --port 9876` (see the file directly for the current size — earlier drafts of this ADR disagreed on the line count, and the module has continued to grow since). Spawned by `electron_launcher.py:launch_electron_frontend` (the inverse path — Python-as-parent — also exists) and reached over a local TCP socket on `127.0.0.1:9876`. Audio capture + ASR inference + tray + hotkeys + volume ducking + clipboard all live here. The IPC dispatch layer is `_COMMAND_REGISTRY` (locate by the `_COMMAND_REGISTRY = {` assignment near the top of `ipc_server.py`; 63 commands — see §2 IPC-1 reconciliation) → `_handle_<cmd>` mixins in `voice_typer/server/handlers/*`. Server-initiated events flow through `event_bus.publish(...)` (`event_bus.py`, the modern successor to `ipc_server._push_event_now`).
3. **prewarm helper** — `prewarm/` package (entry point `prewarm/__main__.py`, dispatched to `prewarm/pipeline.py::run`). A standalone boot-time process that warms the OS file cache (~7 GB of torch + transformers + model weights) before the app's cold imports contend for disk. Kept intentionally separate per ADR-0011. Scheduling is platform-specific:
   - **Windows**: `task_scheduler.py` (708 lines) registers a `LogonTrigger` Scheduled Task (`schtasks`) with an HKCU `Run` registry-key fallback.
   - **macOS**: `prewarm_scheduler_posix.py` registers a LaunchAgent at `~/Library/LaunchAgents/com.voicetyper.prewarm.plist` with `RunAtLoad=true`.
   - **Linux**: `prewarm_scheduler_posix.py` registers a systemd user timer at `~/.config/systemd/user/voice-typer-prewarm.{service,timer}` with `OnBootSec=10s`.

The codebase is **already cross-platform**. The README explicitly states: "**Windows 10/11**, **macOS 11+**, or **Linux** (X11 or Wayland) — Voice Typer is cross-platform". `pyproject.toml` declares platform-conditional deps for volume ducking on each OS (`pycaw`/`comtypes` on Win32, `pyobjc-core`/`pyobjc-framework-CoreAudio`/`pyobjc-framework-Cocoa` on Darwin). `docs/PLATFORM_STATUS.md` enumerates a 30-row feature × OS matrix. Native hotkey binaries exist for all three platforms (`voice_typer/server/native/{windows-key-listener.c, macos-key-listener.swift, linux-key-listener.c}`), compiled by `scripts/build/compile_native.sh` (Linux/macOS) and `scripts/build/compile_native.ps1` (Windows). The CI matrix in `.github/workflows/build.yml` runs tests on `windows-2022`, `ubuntu-22.04`, `macos-13`, plus `macos-14` for Apple Silicon native builds.

Two pain points drive this migration:

- **(A) IPC middleware dislike.** The UI ↔ Python path is Electron `ipcMain`/`ipcRenderer` (in `client/src/main/index.ts` and `client/src/preload/index.ts`) → TCP socket → Python `IPCServer._dispatch` → handler mixins. We want to remove the hand-rolled Electron `child_process.spawn` + `electron_launcher.py` relay layer (318 lines) and the `autostart_launcher.py` Electron-aware spawn logic (801 lines).
- **(B) "Two things in Task Manager."** Electron + Python ship as two separate programs. We want **one application** the user launches (one icon/install), not two unrelated programs.

This ADR adopts **Tauri v2 + a Python Sidecar** as the replacement desktop runtime, and records the ordered migration plan. The Python backend and React UI are kept substantially as-is; only the shell and the transport change. The migration is **per-platform incremental and reversible**: each platform has its own Phase 0 spike and its own cutover gate.

> **Plain English:** Today the app is three running programs (the window, the speech brain in Python, and a pre-load helper). We are moving the *window* from Electron to a smaller Tauri program, and bundling the speech brain *next to* it as a "sidecar" that Tauri starts and manages. The user still sees one app. We keep Electron working the whole time, so if the new version misbehaves we just ship Electron again — nothing is lost. The migration happens one OS at a time: Windows first, then macOS, then Linux.

---

## Decision

**Adopt Tauri v2 + Python Sidecar as the desktop runtime, replacing Electron on all three platforms.** Keep the Python backend and React UI substantially as-is; only the shell + transport change.

**Rationale (why Sidecar, not embedding Python in the app):** embedding Python directly inside the Rust/Tauri process (PyO3) would put the speech engine in the *same* process as the UI. For a continuous realtime-audio app that reintroduces a Global Interpreter Lock (GIL) freeze risk on the audio path, adds fragile native DLL/ABI linking (Windows MSVC, macOS .dylib @rpath, Linux glibc version constraints), and prevents crash isolation (a speech-engine crash would kill the whole app). The sidecar pattern keeps the speech engine in its own managed process, so the UI never freezes, crashes are isolated, and native loading stays standard.

**Migration is incremental and reversible.** Electron is NOT removed. We build Tauri + Sidecar *alongside* Electron, port the UI/components to Tauri's WebView (WebView2 on Windows, WKWebView on macOS, webkit2gtk on Linux), implement the sidecar, then re-point the "wire" (UI → logic) from Electron→Python to Tauri→sidecar. At every phase the Electron app remains buildable, runnable, and shippable. Cutover is a packaging/default switch per platform, not a destructive change.

Three mandatory architecture rules:

1. **Keep prewarm as a SEPARATE boot helper.** Do **not** merge it into the app. Prewarm remains a distinct, intentional boot-time process that warms the OS file cache. Net: **3 OS processes → 2 OS processes per session** (one Tauri app + one invisible boot helper). Preserves ADR-0011 (the actual prewarm ADR — the previous version of this document mis-cited ADR-0009, which is the Audio Filter Chain ADR).
2. **Preserve the current streaming model.** Background chunking/streaming stays hidden from the user until dictation ends, then pastes at once. Unaffected by the runtime change.
3. **Migration must stay reversible.** Electron code is untouched; the Tauri build is additive. Ability to ship/switch back to Electron at any time, with zero loss. **Per-platform**, not global: Windows can cutover while macOS still ships Electron.

> **Plain English (the three rules):** Rule 1 — the pre-load helper stays its own little program so the model stays ready in RAM; we only swap the *window* technology. Rule 2 — the way words are collected in the background and shown all at once does not change. Rule 3 — we never delete or break Electron; the new app is added next to it, and we can go back whenever we want, one OS at a time.

### Locked implementation decisions (resolved in planning)

These choices were decided before the Phase 0 spike and are fixed for the build:

- **Sidecar freeze tool: Nuitka.** The Python backend is compiled to a native single-file executable via **Nuitka** (not PyInstaller `--onedir`). Rationale: smaller/faster-start binary, no PyInstaller bootloader PID/antivirus quirks on Windows, better fit for a Tauri `externalBin` sidecar (which requires a single executable per target triple, not a folder). Build uses a clean **`python-build-standalone`** interpreter as the Nuitka target. **Per-platform:** each target triple gets its own Nuitka build — Nuitka does not cross-compile, so the CI matrix must run one Nuitka build per target. The existing `scripts/build/voice-typer.spec` (PyInstaller, Windows-focused) is **retained as the fallback path** for platforms where Nuitka proves impractical (e.g., macOS Apple Silicon ABI issues); the sidecar entrypoint is identical, only the freeze tool changes.
- **Transport: WebSocket, single choice.** UI → Rust (`invoke`) → sidecar over a **localhost WebSocket**. No HTTP/JSON-RPC alternative. The sidecar is reached at an **ephemeral `127.0.0.1:0`** port it binds itself and reports to Rust via a `server_started` JSON line on stdout (not the hardcoded `9876`; see §1). Auth is the existing **HMAC session token** scheme via env `VOICE_TYPER_IPC_TOKEN`, reused from ADR-0014 (TCP IPC session-token auth).
- **Paste/keystroke injection: `enigo` + `tauri-plugin-clipboard-manager`.** The Rust bridge uses the **`enigo`** crate (cross-platform: Windows via `SendInput`, macOS via CGEvent, Linux via X11/XTest) for keystroke injection of transcribed text into the foreground window, **plus** `tauri-plugin-clipboard-manager` for the clipboard copy + `Ctrl+V`/`Cmd+V` long-text path. `enigo` is keyboard/mouse ONLY — it does NOT do toast notifications (see §6 of the Implementation Specification). The previous ADR's Win32-only focus-restore dance (`AttachThreadInput`, `SetForegroundWindow`, `GetForegroundWindow`) is the Windows implementation; macOS and Linux each need their own equivalent (see §6).
- **Cooperative shutdown over the WebSocket**, not stdin/stdout. The Rust supervisor sends `{"type":"shutdown"}`; the sidecar releases the mic, acks, and exits. `kill_children` is the backstop only.
- **Crash isolation (supervisor)** is a hard requirement before cutover on each platform: Rust respawns the sidecar only, shows "reconnecting…", with backoff 500 ms → 1 s → 2 s (cap 5 retries) then full-app relaunch. Treat supervisor as a label defined by this ADR, not an external task ID.
- **Heartbeat is removed on BOTH sides** (replaces ADR-0018's TCP heartbeat watchdog). Under Tauri, Rust is the supervisor: it detects sidecar death via WS-close / process exit, so the app→backend heartbeat is redundant. This invalidates ADR-0018 for the Tauri build path; ADR-0018 stays in force for the Electron fallback path until that fallback is removed. See §10.

> **Honest process-model note:** post-migration the OS still runs **multiple processes** — Tauri host + Tauri WebView renderer child + Python sidecar + prewarm (3 → 2 net per session: one app + one boot helper). The user sees **one app** (one icon/install/start menu entry), but Task Manager / Activity Monitor / `ps` will show more than one entry. This migration resolves complaint (B) as "one app to launch", NOT "one OS process". Embedding Python (PyO3) was rejected precisely because it *would* yield one process but reintroduces GIL-freeze risk and kills crash isolation.

---

## Target Architecture (post-migration, per-platform)

```
One Tauri app per platform (ONE icon / install — one app to launch; multiple OS processes under the hood):

  Windows:  VoiceTyper.exe (Rust shell) + WebView2 (React UI) + python-sidecar-x86_64-pc-windows-msvc.exe + prewarm-x86_64-pc-windows-msvc.exe
  macOS:    Voice Typer.app (Rust shell + WKWebView React UI) + python-sidecar-{aarch64|x86_64}-apple-darwin + prewarm-{aarch64|x86_64}-apple-darwin
  Linux:    voice-typer (Rust shell) + webkit2gtk (React UI) + python-sidecar-{aarch64|x86_64}-unknown-linux-gnu + prewarm-{aarch64|x86_64}-unknown-linux-gnu

  UI → Tauri invoke('dispatch', {cmd,data}) → Rust → localhost WebSocket → Python sidecar _COMMAND_REGISTRY
  Sidecar → event_bus.publish(event) → Rust subscriber → app.emit(name, payload) → React UI
  (sidecar runs faster-whisper / CTranslate2 / Qwen3-ASR / Parakeet — same Python backend as today)
```

The sidecar is a **normal Python program** (your existing `ipc_server` / `handlers/*` logic), compiled/bundled and launched by Tauri — no Python embedded in the Rust binary. The `externalBin` mechanism requires one binary per Rust target triple; Tauri selects the right one at runtime by matching `std::env::consts::ARCH` + `OS`.

---

## Cross-Platform Capability Matrix (before → after)

Verified against `docs/PLATFORM_STATUS.md` (last updated 2026-06-30) and the actual source tree. The "Today (Electron)" column reflects the shipping app; the "After (Tauri)" column reflects the target of this ADR. **Any cell where After is worse than Today is a regression that must be explicitly accepted or blocked.**

| Capability | Today (Electron) | After (Tauri) | Notes |
|---|---|---|---|
| Global hotkey (native) — Windows | `windows-key-listener.exe` (`WH_KEYBOARD_LL`) | **Keep native binary** (do NOT switch to `tauri-plugin-global-shortcut`) | Tauri's plugin lacks key suppression + modifier-only hotkeys. See §6.4. |
| Global hotkey (native) — macOS | `macos-key-listener` (Swift, `CGEvent` tap, **Fn/Globe key**) | **Keep native binary** | Tauri's plugin cannot detect Fn/Globe key. Accessibility permission still required. |
| Global hotkey (native) — Linux | `linux-key-listener` (evdev, X11 **and** Wayland) | **Keep native binary** | Tauri's plugin uses X11 only on Linux; **breaks Wayland**. evdev is the only Wayland-capable path. |
| Global hotkey (legacy fallback) | `pynput` (Win32/Quartz/Xlib) | Remove (Tauri plugin is the new fallback) | Native binary remains primary; legacy fallback is rarely used. |
| Key suppression (so hotkey doesn't reach foreground) | Win ✅ (hook returns non-zero) · macOS ✅ (CGEvent tap returns NULL) · Linux ❌ (evdev read-only) | Unchanged | Native binaries already handle this; Tauri plugin would regress Windows + macOS. |
| Tray icon | `pystray` (Win32 / AppKit / GTK) | `tauri-plugin-tray` (Win32 / AppKit / GTK via `gtk-3.0`) | Tray menu structure (locale, dynamic items) must be preserved 1:1 — see §6.5. |
| Tray notifications | `Shell_NotifyIcon` / `NSUserNotificationCenter` / libnotify | `tauri-plugin-notification` (cross-platform backend) | `electron_notification` event renames; payload unchanged. |
| Autostart — Windows | Task Scheduler `LogonTrigger` + HKCU Run key fallback | **Keep existing** `task_scheduler.py` (do NOT enable Tauri `autostart` plugin — avoids duplicate entries) | Per Phase 3 of the prior ADR; still correct. |
| Autostart — macOS | LaunchAgent plist (`com.voicetyper.plist`) | **Keep existing** `server_platform._enable_autostart_macos()` | Tauri `autostart` plugin uses LaunchAgent too but with a different label; switching would orphan the old one. |
| Autostart — Linux | `.desktop` in `~/.config/autostart/` | **Keep existing** `server_platform._enable_autostart_linux()` | Same rationale. |
| Prewarm scheduling — Windows | `task_scheduler.py` → `schtasks` LogonTrigger | **Keep existing** (prewarm exe replaces `pythonw.exe -m voice_typer.server.prewarm` via `resolve_prewarm_exe`) | See §5. |
| Prewarm scheduling — macOS | `prewarm_scheduler_posix.py` → LaunchAgent `com.voicetyper.prewarm.plist` | **Keep existing** (prewarm exe replaces Python module via `resolve_prewarm_exe`) | The previous ADR's claim that prewarm is Windows-only was **wrong** — `prewarm_scheduler_posix.py` already exists. |
| Prewarm scheduling — Linux | `prewarm_scheduler_posix.py` → systemd user timer | **Keep existing** (prewarm exe replaces Python module) | Same. |
| Single-instance lock | Win32 named mutex (`Local\VoiceTyperSingleInstance` — locate by `class VoiceTyperSingleInstance` in `app.py`) · POSIX lockfile (best-effort) | `tauri-plugin-single-instance` (Win mutex / macOS NSApplication activation / Linux lockfile) | The Python-side `VoiceTyperSingleInstance` mutex becomes redundant on Windows once Tauri's plugin is active; remove it from the sidecar to avoid double-locking. |
| Microphone listing | `sounddevice` (WASAPI / CoreAudio / ALSA / PulseAudio / PipeWire) | Unchanged (stays in Python sidecar) | No change. |
| Microphone hot-plug detection | Win `WM_DEVICECHANGE` · Linux `/dev/snd` poll · macOS TTL fallback (NATIVE-001) | Unchanged | `microphone_watcher.py` stays in Python. |
| Clipboard paste — Windows | `pyperclip` + Win32 `SendInput` (atomic 4-event batch) | `enigo` `text()` (short) + `tauri-plugin-clipboard-manager` + `Ctrl+V` (long) | Focus-restore via `AttachThreadInput` + `SetForegroundWindow` — see §6.2. |
| Clipboard paste — macOS | `pyperclip` + `pbpaste`/`pbpaste` + `Cmd+V` via `CGEvent` | `enigo` `text()` (short) + clipboard-manager + `Cmd+V` (long) | macOS paste is generally simpler (no UIPI). See §6.2. |
| Clipboard paste — Linux | `pyperclip` + `xclip`/`xsel` (X11) / `wl-copy` (Wayland) + `Ctrl+V` | `enigo` `text()` (short) + clipboard-manager + `Ctrl+V` (long) | **Wayland caveat:** `enigo` on Linux is X11-only; on Wayland the clipboard + `Ctrl+V` path is the only reliable option. See §6.2 + §6.6. |
| Audio recording | PortAudio (WASAPI / CoreAudio / ALSA / PipeWire) | Unchanged | `recording/` package stays in Python. |
| Volume ducking — Windows | `pycaw` + `comtypes` (ISimpleAudioVolume) | Unchanged (stays in Python sidecar) | No change. |
| Volume ducking — macOS | `pyobjc-framework-CoreAudio` | Unchanged | No change. |
| Volume ducking — Linux | (no native backend today — `volume_backends/linux.py` returns a no-op) | Unchanged | Documented limitation; out of scope. |
| Config file permissions | Win NTFS ACLs (default) · POSIX `0o600`/`0o700` | Unchanged | `config.py::_secure_atomic_write` stays in Python. |
| IPC auth | TCP session token (ADR-0014, bearer-token literal-match — historically referred to as "HMAC"; see §3 ZR-56 reconciliation, first-frame `{"type":"auth","token":...}`) | WebSocket session token (same scheme, same env var `VOICE_TYPER_IPC_TOKEN`) | See §3. |
| IPC rate limit | Per-connection rate limiter (ADR-0019, 200 burst / 60 sustained msg/s) | **Must port** to the WebSocket server side | ADR-0019 was written for TCP; the limiter logic in `ipc_server.py` must be reused on the WS accept path. See §10. |
| Heartbeat watchdog | ADR-0018: Electron sends `heartbeat` every 5s, Python watchdog quits after 120s of silence | **Removed** on Tauri path (Rust supervisor replaces it). Stays on Electron fallback path. | See §2 + §10. |
| Code signing — Windows | Authenticode (`signtool`) via electron-builder `WIN_CSC_LINK` | Authenticode (`signtool`) for both `python-sidecar-*.exe` and `prewarm-*.exe` + Tauri host | See §13. |
| Code signing — macOS | Developer ID + notarization (`notarize: true` in `electron-builder.yml`) | Developer ID + notarization + stapling for the `.app` bundle and both sidecar exes | See §13. macOS notarization requires the sidecar exes to be signed **before** they enter the `.app` bundle. |
| Code signing — Linux | None (deb/rpm are unsigned by default; AppImage is GPG-optional) | None (Tauri deb/rpm/AppImage also unsigned by default) | Optional: GPG-sign the .deb / .rpm. Out of scope for v1. |
| Auto-update | **NOT IMPLEMENTED** today (`docs/auto-update-feature.md` is design-only — the file's own header says so) | `tauri-plugin-updater` (cross-platform: Windows replaces MSI, macOS replaces DMG, Linux replaces AppImage) | See §15. **Do not assume auto-update works today** — it does not. |
| Diagnostics export | `export_diagnostics` command (redacted bundle) | Unchanged (stays in Python sidecar) | No change. |
| Crash recovery | `crash_recovery.py` (BG thread, bounded queue, `flush()` on quit) | Unchanged (stays in Python sidecar) | No change. |
| Streaming dictation | `streaming.py` + `dictation_pipeline.py` | Unchanged (stays in Python sidecar) | No change. |
| Models path | Win `%APPDATA%/voice-typer/models` · macOS `~/Library/Application Support/voice-typer/models` · Linux `$XDG_DATA_HOME/voice-typer/models` | Unchanged (`_paths.config_dir()` already handles this) | See §8. |
| Logs path | `<config_dir>/logs/` (rotating) | Unchanged | See §11. |
| WebView | Chromium (Electron-bundled, ~100 MB) | WebView2 (Win) / WKWebView (macOS, system) / webkit2gtk (Linux, system) | Tauri shell ~2–10 MB. CSS guardrails for webkit2gtk quirks. |

---

## Migration Plan (ordered, per-platform)

The plan runs **Windows → macOS → Linux** in sequence. Each platform has its own Phase 0 gate. Phase 5 cutover is per-platform. The Electron fallback remains shippable on every platform throughout.

### Phase 0 — Spike (prove before building, per platform)

**Phase 0-W (Windows):**
- Freeze a working Python backend with **Nuitka** against a `python-build-standalone` interpreter, producing a single `python-sidecar-x86_64-pc-windows-msvc.exe`, and bundle it as a Tauri `externalBin`.
- Confirm on a Windows 10 + Windows 11 test machine: sidecar spawns on app launch, **localhost WebSocket** comms work over an **ephemeral `127.0.0.1:0` port** + **bearer-token auth** (historically referred to as "HMAC token" — see §3 ZR-56 reconciliation), sidecar auto-stops with the app, `kill_children` cleans the tree.
- Confirm `faster-whisper` / `CTranslate2` loads and transcribes inside the sidecar.
- Confirm `enigo` injects transcribed text into a foreground window (Notepad).
- Confirm `tauri-plugin-notification` shows a toast.
- **Gate:** do not start Phase 1-W until this passes.

**Phase 0-M (macOS):**
- Freeze the same backend with Nuitka against `python-build-standalone` `cpython-3.12.x+aarch64-apple-darwin` (Apple Silicon) and `cpython-3.12.x+x86_64-apple-darwin` (Intel). Produce two sidecar binaries.
- Confirm on macOS 13 (Intel) + macOS 14 (Apple Silicon): sidecar spawns, WS comms work, sidecar auto-stops.
- Confirm `faster-whisper` / `CTranslate2` loads inside the sidecar on both archs.
- Confirm `enigo` types text into TextEdit on both archs.
- Confirm `tauri-plugin-notification` posts a notification (requires `NSUserNotifications` entitlement — see §13).
- **Confirm the existing native hotkey binary (`macos-key-listener`, Swift, CGEvent tap) still works** when spawned from the Tauri host (the binary requires Accessibility permission — see §6.4 for the permission flow).
- **Gate:** do not start Phase 1-M until this passes on both archs.

**Phase 0-L (Linux):**
- Freeze the same backend with Nuitka against `python-build-standalone` `cpython-3.12.x+x86_64-unknown-linux-gnu` (built against glibc 2.35 — Ubuntu 22.04 baseline) and `cpython-3.12.x+aarch64-unknown-linux-gnu` (ARM64). Produce two sidecar binaries.
- Confirm on Ubuntu 22.04 (X11) + Ubuntu 22.04 (Wayland) + Fedora 40 (Wayland): sidecar spawns, WS comms work, sidecar auto-stops.
- Confirm `faster-whisper` / `CTranslate2` loads inside the sidecar.
- Confirm `enigo` types text into `gnome-text-editor` on X11. **On Wayland, `enigo` is expected to fail** — confirm the clipboard + `Ctrl+V` fallback works (see §6.6).
- Confirm the existing `linux-key-listener` (evdev) still works when spawned from the Tauri host (requires `input` group membership — already set up by the existing `scripts/linux/postinst`).
- **Gate:** do not start Phase 1-L until this passes on X11 + Wayland + both archs.

### Phase 1 — Sidecar packaging (per platform)

- Freeze the Python backend with **Nuitka** into a single sidecar executable per target triple. See §4 for the per-platform Nuitka command and flags.
- **Code-sign the sidecar exe separately** on Windows (Authenticode) and macOS (Developer ID + notarization). Linux sidecar is unsigned (matches today's deb/rpm). See §13.
- Run with hidden console on Windows (`--windows-disable-console`), as a background app on macOS (`LSUIElement=true` in `Info.plist`), and with no terminal on Linux (`nohup`-style spawn).
- Implement **cooperative shutdown over the WebSocket** (`{"type":"shutdown"}` → sidecar releases mic, acks, exits); `kill_children` is the backstop, not the primary path.

### Phase 2 — Transport bridge (cross-platform)

- Replace Electron's TCP IPC (`ipc_server --port 9876` + `electron_launcher` spawn) with a **localhost WebSocket** between Tauri (Rust) and the sidecar. Rust is the only bridge: UI `invoke('dispatch',{cmd,data})` → Rust → WebSocket `{"type":cmd,"data":...}` → sidecar `_COMMAND_REGISTRY` (`getattr(self,"_handle_<cmd>")`). The WebView never talks to Python directly.
- Port is **ephemeral `127.0.0.1:0`**, chosen by the sidecar and reported to Rust over stdout (see §1); auth is the existing **HMAC token** via `VOICE_TYPER_IPC_TOKEN`. Reuse `ipc_server._validate_dict_payload` (locate by `def _validate_dict_payload` in `voice_typer/server/ipc/validation.py`) + error codes (`invalid_payload`, `missing_field`, `invalid_field`).
- JSON shapes (carried from `ipc_server.py`): request `{"type":<command>,"data":{...}}`; response `{"type":"result"|"error","data":{...},"code"?:<error_code>}`; sidecar→UI events flow over the same socket → Rust `app.emit(name,payload)`.
- Map the existing handler registry (`handlers/*`) to sidecar commands; keep **one generic dispatch** to minimize Python changes.
- Map Tauri events ↔ the current `event_bus.publish` / `ipc_server._push_event_now` event flow (see **Sidecar→UI Event Table** below) so UI updates behave unchanged. Rename Electron-specific events (`electron_notification` → native toast, `relaunch_electron` → Tauri app relaunch) without changing payloads.
- **Port the per-connection rate limiter** (ADR-0019) from the TCP accept path to the WebSocket accept path. The limiter logic lives in `log_rate_limit.py`; the WS server must call it on every incoming frame.

### Phase 3 — UI port to Tauri WebView (cross-platform)

- Move the React UI from the Electron renderer to the Tauri webview; replace `ipcMain`/`contextBridge` calls (`client/src/preload/index.ts`) with Tauri `invoke`.
- Port tray, **but keep the native hotkey binaries** (do NOT replace with `tauri-plugin-global-shortcut` — see §6.4 for the feature-parity analysis), settings, and autostart UX to Tauri plugins (`tray`, `autostart`, `single-instance`).
- Keep the same React components — only the shell bridge changes.
- **WebView differences:** Windows uses WebView2 (Chromium-based, modern), macOS uses WKWebView (Safari-based, mostly modern), Linux uses webkit2gtk (Safari-ish, lags Chromium by 1–2 years). Audit the React UI for: CSS `backdrop-filter`, `:has()` selector, `grid-template-*` shorthand, `Array.at()`, `Object.has()`, `structuredClone()`. Add polyfills or guard with `@supports` where needed. The existing `client/csp-plugin.ts` CSP enforcer should be ported to Tauri's `tauri.conf.json` `app.security.csp` field — Tauri v2 enforces CSP at the WebView level.

### Phase 4 — Wire swap + recovery (per platform)

- Re-point the "wire" (UI → logic) from Electron→Python to Tauri→sidecar. Keep the Electron build path intact and runnable in parallel.
- Implement **crash isolation** (supervisor): a Rust supervisor respawns the sidecar on unexpected exit, shows a "reconnecting…" state, and falls back to full-app relaunch if respawn fails repeatedly.
- Enable the `single-instance` plugin so only one app instance runs. **On Windows, also remove the `VoiceTyperSingleInstance` Win32 mutex from `app.py` (locate by `class VoiceTyperSingleInstance`)** when running under Tauri — the Tauri plugin already provides the mutex, and double-locking would block the second-instance focus path.

### Phase 5 — Validation & cutover (per platform)

- Verify: one icon/install; UI never freezes (sidecar owns its own GIL); crash isolation works; prewarm still warms the cache; streaming unchanged; global hotkey + tray work.
- Keep the Electron code path intact until satisfied; then make Tauri the default shipping app **for that platform**. Revert at any time by shipping the Electron build.
- **Cutover order:** Windows first (largest user base, smallest Tauri unknowns), then macOS (Apple Silicon + Intel), then Linux (X11 then Wayland).

---

## Sidecar→UI Event Table (extracted from current code)

The sidecar pushes UI events through `event_bus.publish(event)` (`event_bus.py`, the modern successor to `ipc_server._push_event_now` — locate by `def _push_event_now`); the Rust bridge subscribes and re-emits each as a Tauri event. This is **channel (2)** — server-initiated events, distinct from the command/response envelope (channel 1). Every event below is delivered as `{"type":<name>,"data":{...}}`. Payloads are carried unchanged from today's code so the React UI needs no reshaping.

**Verified against the live `voice_typer/server/` tree (2026-07-16; IPC-2 reconciliation 2026-07-18).** The table below lists **24 events** — 3 more than the prior 21-event draft (`paste_failed`, `state_changed`, `status_change` were previously undocumented; all three are emitted via `event_bus.publish` or `IPCServer.push` and flow through the same channel). Line numbers drift as the code grows, so the table anchors on **symbols** (function/class names) — locate each event by `event_bus.publish({"type": "<name>"})` (or `IPCServer.push` for the two `push`-only events) rather than by line number. `ready` is emitted via `IPCServer.push` (not `event_bus.publish` — locate by the `{"type": "ready"}` push call in `sidecar_ws.py` and `ipc_server.py`) but flows through the same channel. `state_changed` and `status_change` are emitted via `IPCServer.push` (the former on TCP connect, the latter on every tray state transition via the `_hook_tray_set_state` wrapper); they are server-initiated events on channel (2), NOT command responses — the prior draft's parenthetical "command responses on this channel" was wrong. Empty `data` is written as `{}` (never `null`) so the Rust subscriber can always do `event.data ?? {}`.

| Event `type` | Source (file:symbol) | `data` payload | Notes |
|---|---|---|---|
| `ready` | `ipc_server.py` / `sidecar_ws.py` — locate by `{"type": "ready"}` `IPCServer.push` call | `{}` | emitted on server start (Electron defers window creation; Tauri should likewise defer UI hydration) |
| `bubble_show` | `waveform_bubble_wiring.py` — locate by `event_bus.publish({"type": "bubble_show"})` | `{}` | show waveform bubble |
| `bubble_hide` | `waveform_bubble_wiring.py` — locate by `event_bus.publish({"type": "bubble_hide"})` | `{}` | hide waveform bubble |
| `bubble_level` | `waveform_bubble_wiring.py` — locate by `_push_bubble_level` | `{rms:float, peak:float}` | ~60 Hz source → Rust coalesce ≤30 Hz (see §9) |
| `bubble_set_state` | `waveform_bubble_wiring.py` — locate by `_push_bubble_set_state` | `{state:str}` | |
| `transcription_final` | `dictation_pipeline.py` — locate by `event_bus.publish({"type": "transcription_final"})` | `{text:str (≤200 chr)}` | UI preview / refresh |
| `vocabulary_suggestion` | `dictation_pipeline.py` — locate by `event_bus.publish({"type": "vocabulary_suggestion"})` | `{suggestions:[{original,corrected,confidence,context,timestamp}]}` | |
| `hotkey_capture_cancel` | `hotkey_dispatcher.py` — locate by `event_bus.publish({"type": "hotkey_capture_cancel"})` | `{}` | |
| `config_changed` | `config_handlers.py` + `service.py` — locate by `event_bus.publish({"type": "config_changed"})` | `{validated config updates}` | |
| `history_changed` | `history_handlers.py` — locate by `event_bus.publish({"type": "history_changed"})` | `{reason:str}` | **added** — missed in earlier draft |
| `microphone_test_complete` | `level_monitor.py` — locate by `event_bus.publish({"type": "microphone_test_complete"})` | `{duration:float}` | |
| `microphones_changed` | `startup_tasks.py` — locate by `event_bus.publish({"type": "microphones_changed"})` | `{count:int}` | |
| `audio_clip` | `recording/` package — locate by `event_bus.publish({"type": "audio_clip"})` | `{peak:float, count:int}` | |
| `recording_started` | `recording_controller.py` — locate by `event_bus.publish({"type": "recording_started"})` | `{}` | **added** — missed in earlier draft |
| `recording_stopped` | `recording_controller.py` — locate by `event_bus.publish({"type": "recording_stopped"})` | `{}` | **added** — missed in earlier draft |
| `download_progress` | `service.py` — locate by `event_bus.publish({"type": "download_progress"})` | `{model, progress(0-100), status, +optional downloaded_bytes, total_bytes, speed_bytes_per_sec, eta_seconds, paused, resumed}` | |
| `electron_notification` | `system_handlers.py` + `startup_sequence.py` — locate by `event_bus.publish({"type": "notification"})` | `{title, message, duration_ms, critical}` | → **native toast** under Tauri (`tauri-plugin-notification`); the Python side now publishes this directly as `notification` (CR-8) |
| `navigate` | `tray.py` — locate by `event_bus.publish({"type": "navigate"})` | `{path:str}` | tray → UI route |
| `show_window` | `tray_window.py` — locate by `event_bus.publish({"type": "show_window"})` | `{}` | |
| `quit_app` | `app.py` — locate by `event_bus.publish({"type": "quit_app"})` | `{}` | sidecar requests app quit |
| `relaunch_electron` | `app.py` — locate by `event_bus.publish({"type": "relaunch_app"})` | `{}` | → **Tauri app relaunch** under Tauri; the Python side now publishes this directly as `relaunch_app` (canonical name) |
| `paste_failed` | `dictation_pipeline.py` — locate by `event_bus.publish({"type": "paste_failed"})` | `{message:str, recovery_path:str\|null}` | **added 2026-07-18 (IPC-2)** — emitted when clipboard paste fails (NEW-UX-006); renderer shows a sonner toast with "Open recovery file" action when `recovery_path` is non-null. Tray notification is also fired for redundancy (visible when the user is on another app). |
| `state_changed` | `ipc_server.py` — locate by `{"type": "state_changed"}` `IPCServer.push` call (ERR-017) | `{status:str, message:str}` | **added 2026-07-18 (IPC-2)** — emitted ONCE per TCP/WS client connect so the renderer immediately knows the current app state (was previously stale until the next state transition). NOT a command response — it is a server-initiated push on channel (2). |
| `status_change` | `ipc_server.py` — locate by `{"type": "status_change"}` `IPCServer.push` call (`_hook_tray_set_state` wrapper) | `{status:str}` | **added 2026-07-18 (IPC-2)** — emitted on EVERY tray state transition (the wrapper monkey-patches `app.tray.set_state` in `IPCServer.start()`). Distinct from `state_changed`: `state_changed` is the connect-time snapshot with a `message` field; `status_change` is the per-transition signal with just `status`. NOT a command response — it is a server-initiated push on channel (2). |

**Channel (1) — command/response envelope** (not in the table above): requests `{"type":<command>,"data":{...}}` → responses `{"type":"result"|"error","data":{...},"code"?:<error_code>}`.

**Renames under Tauri (payloads unchanged):** `electron_notification` → `notification` (Tauri emits via `tauri-plugin-notification`, which uses `NSUserNotificationCenter` on macOS, `Shell_NotifyIcon` / WinRT toast on Windows, libnotify on Linux); `relaunch_electron` → `relaunch_app` (Tauri `app.restart()`); `quit_app` → `quit_app` (Tauri `app.exit(0)`). All other event names and payloads are preserved 1:1. `heartbeat` is **removed from both sides** — see §2 + §10.

---

## Implementation Specification (detailed, cross-platform)

Closes the gaps called out in review: port-bind direction, command table, token lifecycle, per-platform Nuitka command, prewarm packaging, toast/paste per OS, Tauri config + capabilities, paths, throttling, error handling, logging, single-instance, signing, **plus per-platform considerations for Windows / macOS / Linux / Wayland / Apple Silicon / Linux ARM64**. All referenced against the real `voice_typer/server/` code (registry + handlers + event sites) and the existing build assets under `scripts/`.

### 1. Ephemeral port — locked bind direction (cross-platform)

- **Sidecar is the WebSocket SERVER; Rust is the WebSocket CLIENT.** No ambiguity. This is the same on all three platforms.
- **Token:** Rust generates the HMAC token (`secrets.token_bytes(32)`, see §3) and passes it to the sidecar via env `VOICE_TYPER_IPC_TOKEN` at spawn. Rust does **not** choose the port.
- **Port is chosen by the OS at bind time — no TOCTOU race.** Rust spawns the sidecar (`externalBin`) as a child process and captures its `stdout` pipe. The sidecar binds `websockets.serve(...)` to **`127.0.0.1:0`**; the OS assigns a free ephemeral port. **Before** the accept loop starts, the sidecar writes exactly one structured line to `stdout`:
  `{"event":"server_started","port":<n>}`
  Every other sidecar log goes to **stderr** or the rotating file (see §11) — **never `stdout`** — so Rust's parser is unambiguous.
  - **Unbuffered stdout is mandatory (Phase 0 blocker, all platforms).** When the host pipes the sidecar's `stdout`, CPython switches to **block buffering**, so the `server_started` JSON is held in the buffer and Rust hangs forever waiting. Force a flush at the very top of `ipc_server.py`: `sys.stdout.reconfigure(line_buffering=True)` (or have the host spawn the sidecar with `PYTHONUNBUFFERED=1`). Without this the launch freezes with no error.
- **Rust connects:** Rust blocks reading `stdout` until it parses the `server_started` JSON, then opens the WS client to `ws://127.0.0.1:<port>` and performs the HMAC handshake (§3). There is **no** probe→close→rebind window, so this is no `EADDRINUSE` race and **no exit-code-10 respawn dance** is needed.
- **Loopback lock (hard rule, all platforms):** the bind address must be `127.0.0.1` (Windows/Linux) or `127.0.0.1` (macOS — `::1` would also work but stick to IPv4 loopback for parity). Binding `0.0.0.0`/`::` would:
  - **Windows:** pop a Windows Defender Firewall dialog on first run (same as today's TCP `9876` would if it were `0.0.0.0`).
  - **macOS:** trigger an Application Firewall prompt (System Settings → Network → Firewall) on first run.
  - **Linux:** be reachable from other hosts if `net.ipv4.conf.all.localhost_allow` is set; also no firewall prompt but a real exposure.
  Fail the launch if the configured bind is not loopback.
- On respawn Rust generates a **new** token and respawns the sidecar (which binds a fresh `:0`); token rotation per §3.

#> **Frozen command contract (65 commands):** the sidecar IPC command table in §2 enumerates exactly **65 commands** — the baseline established after the Tauri/Rust allowlist narrowing (S3-CR-3) and the subsequent IPC-1 reconciliation (ZR-45 cleanup + `relaunch_ack` add), extended by the §16 addenda. The frozen set lives in `tests/tauri/mig19/test_phase4_validation.py::EXPECTED_COMMANDS` and MUST NOT grow without (1) a new `_handle_<cmd>` mixin, (2) an ADR addendum, (3) a `_validate_dict_payload` schema, and (4) a dispatch-errors test. (DT-19 reconciliation 2026-07-24: earlier drafts of this ADR cited "61 commands"; the frozen-table count is 65 as of the 2026-08-14 §16 addendum — the registry total is 69, see `docs/ipc-reference.md` and `tests/test_security_doc_command_count.py`.)

> **TS-only exceptions parity contract:** The renderer TS `ALLOWED_COMMANDS`
> set contains two commands that are intentionally absent from the Rust
> host's `allowed_commands()` literal: `heartbeat` and `relaunch_ack`.
> These commands are dispatched directly by the Rust host (the WS-reader
> task sends `heartbeat` to the Python backend; the `relaunch_app` Tauri
> command sends `relaunch_ack`) rather than flowing through the generic
> `invoke('dispatch', ...)` path from the renderer. Keeping them out of
> the Rust `allowed_commands()` literal closes the attack surface where a
> compromised renderer could spoof watchdog ticks or prematurely release
> the relaunch-wait event. The +2 TS-only delta is asserted by the
> `_TS_ONLY_EXCEPTIONS` frozenset in
> `tests/test_security_doc_command_count.py`. **Contract:** when a new
> command is added that the Rust host dispatches directly (not via
> `dispatch`), it MUST be (1) added to the TS `ALLOWED_COMMANDS` set,
> (2) NOT added to the Rust `allowed_commands()` literal, (3) added to
> the `_TS_ONLY_EXCEPTIONS` frozenset with a rationale comment, and (4)
> documented in this ADR. When a TS-only exception is removed (the
> command is deleted or routed through `dispatch` instead), it MUST be
> removed from `_TS_ONLY_EXCEPTIONS` in the same PR.

## 2. Sidecar←UI Command Table (channel 1, extracted from `ipc_server._COMMAND_REGISTRY`)

**69 commands registered (verified against `ipc_server._COMMAND_REGISTRY` — IPC-1 reconciliation; ZR-45 removed 14 stale entries that were never in the registry, plus the prior 71→61 reconciliation reflects the post-cleanup state; +2 restored 2026-08-14 for the Cache Status card — `get_prewarm_status` / `open_prewarm_log`, plan §6.3 addendum, `run_prewarm` stays retired)**; each maps to a `_handle_<cmd>` mixin in `handlers/*` (or, for the two IPC-server-resident handlers `heartbeat` and `relaunch_ack`, a method on `IPCServer` itself). Dispatch is generic: `getattr(self, _COMMAND_REGISTRY[cmd])` → `(data, resp)`. Request `{"type":<cmd>,"data":{...}}`; response `{"type":"result"|"error","data":{...},"code"?}`. Exact `data` fields per command are defined inside each `_handle_*` and re-validated by `ipc_server._validate_dict_payload` (locate by `def _validate_dict_payload` in `voice_typer/server/ipc/validation.py`) — **that function is the source of truth for command-payload shape and must be ported 1:1, not redesigned or relaxed**; line numbers drift, so locate each handler by `def _handle_<cmd>` in `handlers/*`.

> **IPC-1 reconciliation (2026-07-18):** the prior draft of this ADR stated "68 commands" — that count predated PERF-005, which added `relaunch_ack` (the UI's ack that it received `relaunch_electron`, so `restart_app` can drop its fixed 300 ms sleep in favour of an event-driven wait bounded by a 2 s timeout). `relaunch_ack` is intentional and stays in the registry; the prior "68 commands" claim was stale. The 69th command is `relaunch_ack` — locate the registry entry by `relaunch_ack:` in `_COMMAND_REGISTRY` and the handler by `def _handle_relaunch_ack` (resident on `IPCServer`, not in `handlers/`).

> **UX-23 reconciliation (2026-07-19):** `repaste_last` is the 70th registered command. Its handler (`_handle_repaste_last`) already existed in `handlers/repaste_handlers.py` and the renderer `ALLOWED_COMMANDS` set (`client/src/main/index.ts`) already permitted it, but the `_COMMAND_REGISTRY` dispatch route was missing — so renderer/tray calls silently failed with `unknown_command`. Added `"repaste_last": "_handle_repaste_last"` to the registry (no payload; reads the latest history entry server-side). This closes the UX-23 gap.

| Command | Handler module (`handlers/`) | Purpose |
|---|---|---|
| `get_status` | status_handlers | app/engine status snapshot |
| `get_rms_level` | status_handlers | live RMS level |
| `get_volume_backend_status` | status_handlers | volume-duck backend state |
| `get_audio_status` | status_handlers | audio device state |
| `get_model_status` | status_handlers | model load state |
| `get_prewarm_status` | status_handlers | ADR-0011 cache status |
| `run_prewarm` | status_handlers | trigger prewarm run (detached) |
| `open_prewarm_log` | status_handlers | open prewarm log in editor |
| `toggle_dictation` | dictation_handlers | start/stop dictation |
| `undo_last` | dictation_handlers | undo last transcription |
| `force_cancel_transcription` | dictation_handlers | recover stuck transcription |
| `get_history` | history_handlers | paginated history |
| `get_today_stats` | history_handlers | today's stats |
| `delete_history` | history_handlers | delete record(s) |
| `restore_history` | history_handlers | restore deleted record |
| `clear_history` | history_handlers | erase all history |
| `toggle_favorite` | history_handlers | favorite toggle |
| `get_favorites` | history_handlers | list favorites |
| `search_history` | history_handlers | search records |
| `get_config` | config_handlers | current config |
| `get_defaults` | config_handlers | default config |
| `set_config` | config_handlers | update config (validated) |
| `get_vocabulary` | vocabulary_handlers | list vocabulary entries |
| `save_vocabulary` | vocabulary_handlers | save vocabulary entries |
| `get_vocabulary_suggestions` | vocabulary_automation_handlers | pending suggestions |
| `apply_vocabulary_suggestion` | vocabulary_automation_handlers | apply a suggestion |
| `dismiss_vocabulary_suggestion` | vocabulary_automation_handlers | dismiss a suggestion |
| `get_templates` | templates_handlers | list templates |
| `save_templates` | templates_handlers | save templates |
| `onboarding_is_first_run` | onboarding_handlers | first-run check |
| `onboarding_start` | onboarding_handlers | begin wizard |
| `onboarding_get_step` | onboarding_handlers | current step |
| `onboarding_next_step` | onboarding_handlers | advance |
| `onboarding_prev_step` | onboarding_handlers | back |
| `onboarding_set_microphone` | onboarding_handlers | set mic |
| `onboarding_set_hotkey` | onboarding_handlers | set hotkey |
| `onboarding_set_model` | onboarding_handlers | set model |
| `onboarding_skip` | onboarding_handlers | skip wizard |
| `onboarding_apply` | onboarding_handlers | apply selections |
| `onboarding_get_microphones` | onboarding_handlers | mic list |
| `onboarding_get_model_options` | onboarding_handlers | model options |
| `onboarding_get_hotkey_presets` | onboarding_handlers | hotkey presets |
| `get_microphones` | microphone_handlers | mic list |
| `refresh_microphones` | microphone_handlers | re-enumerate mics |
| `microphone_test_start` | microphone_test_handlers | start test (duration) |
| `microphone_test_stop` | microphone_test_handlers | stop test |
| `microphone_test_cancel` | microphone_test_handlers | cancel test |
| `microphone_test_status` | microphone_test_handlers | test state |
| `microphone_test_get_level` | microphone_test_handlers | test level |
| `level_monitor_start` | level_monitor_handlers | start level monitor |
| `level_monitor_stop` | level_monitor_handlers | stop level monitor |
| `level_monitor_status` | level_monitor_handlers | monitor state |
| `download_model` | model_handlers | download model |
| `cancel_model_download` | model_handlers | cancel download |
| `pause_model_download` | model_handlers | pause download |
| `resume_model_download` | model_handlers | resume download |
| `get_model_catalog` | model_handlers | full catalog metadata |
| `test_llm_connection` | model_handlers | test LLM endpoint |
| `import_model` | model_handlers | import local model |
| `delete_model` | model_handlers | delete model |
| `restart_app` | system_handlers | request relaunch (→ Tauri `app.restart()`) |
| `quit_app` | system_handlers | request quit (→ Tauri `app.exit(0)`) |
| `export_diagnostics` | system_handlers | redacted diag bundle |
| `check_accessibility` | system_handlers | macOS accessibility check |
| `set_tray_locale` | system_handlers | set tray locale |
| `set_esc_cancel_paused` | system_handlers | pause ESC-cancel hotkey |
| `show_electron_notification` | system_handlers | → **Tauri notification** (renamed) |
| `heartbeat` | ipc_server (RW-10 / ADR-0018) | liveness ping — **REMOVED** under Tauri (Rust owns liveness; see §10) |
| `relaunch_ack` | ipc_server (PERF-005) | UI ack of `relaunch_electron` so `restart_app` can drop its fixed 300 ms sleep — **REMOVED** under Tauri (Rust owns the restart handshake) |

> **`heartbeat` is removed from BOTH sides on the Tauri path.** The registry currently contains **69 commands** (incl. `heartbeat` — locate the registry entry by `heartbeat:` in `_COMMAND_REGISTRY`, handler `_handle_heartbeat` resident on `IPCServer`; and `relaunch_ack` — locate by `relaunch_ack:` in `_COMMAND_REGISTRY`, handler `_handle_relaunch_ack` resident on `IPCServer`, NOT in `handlers/`). The current Electron UI *still sends* `heartbeat` every 5 s (`client/src/main/index.ts`, ADR-0018) and `relaunch_ack` once per restart cycle (`client/src/main/python/relaunch-app.ts`). Under Tauri: (1) the Tauri UI port must **delete** the heartbeat interval (Rust is the supervisor — it detects death via WS-close / process exit, so no app→backend heartbeat is needed) and the `relaunch_ack` send (Rust owns the restart via `app.restart()`); (2) the Rust bridge must **not** forward `heartbeat` or `relaunch_ack` to Python (treat as no-op + debug log); (3) `_handle_heartbeat` and `_handle_relaunch_ack` stay in Python until the UI removal lands, so a stray frame never hits `_handle_unknown_command` and returns `unknown_command`. Verification task: `rg "heartbeat|relaunch_ack" voice_typer/client` → zero hits after the UI port. `show_electron_notification` renames to a Tauri notification emit. The 67 surviving commands (69 − `heartbeat` − `relaunch_ack`) keep their `data` schemas 1:1 — enumerate each `_handle_*` payload from `handlers/*` (do NOT redesign); `_validate_dict_payload` re-validates on the sidecar.

> **Note on ADR-0018 reconciliation:** ADR-0018 (Electron-Alive Heartbeat Watchdog) stays in force for the Electron fallback path. Under Tauri, Rust supervisor + WS-close detection + backoff) replaces the 120-second-heartbeat-timeout watchdog. The two paths are mutually exclusive per build: the Tauri build defines `TAURI_SIDECAR=1` (or equivalent) and the Python sidecar, on seeing that env var, **disables** the `_heartbeat_loop` thread at startup so the watchdog does not false-positive during a slow WS-only reconnect. The Electron build keeps ADR-0018 unchanged.

### 3. Bearer token lifecycle (cross-platform)

> **ZR-56 reconciliation (2026-07-24):** this section was originally titled
> "HMAC token lifecycle". The implementation never used HMAC — the Rust host
> generates a 256-bit bearer token via `secrets.token_bytes(32)` and the
> Python sidecar compares it via `hmac.compare_digest` (constant-time
> *comparison only* — no key derivation, no signing). The `hmac`/`sha2`
> crates are explicitly NOT pulled into the Rust host (see `src-tauri/Cargo.toml`
> comment). The historical "HMAC" wording was carried over from ADR-0014's
> original design and propagated through the runbooks; the heading is corrected
> here to match the actual implementation. Downstream docs (`docs/migration/*`,
> `CONTRIBUTING.md`) have been similarly reconciled.

- Generated by Rust at startup: `secrets.token_bytes(32)` → hex. **Not** reused from any file. Same scheme as ADR-0014 (TCP IPC session-token auth) — the env var name `VOICE_TYPER_IPC_TOKEN` is reused verbatim so no Python code change is needed.
- Passed to the sidecar **only** via env `VOICE_TYPER_IPC_TOKEN` at spawn (not CLI, not a file).
- **Visibility concern per platform:**
  - **Windows:** env readable via WMI / Process Explorer / `wmic process get processid,commandline` (deprecated but still present on many installs).
  - **macOS:** env readable via `ps eww` (POSIX) by the same user.
  - **Linux:** env readable via `/proc/<pid>/environ` by the same user.
  Acceptable for a localhost single-user desktop app on all three platforms — the token only authorizes loopback WS connections; it is regenerated per launch and per respawn, the port is ephemeral + loopback-only (`127.0.0.1`), so a stolen token is useless after the process exits. If stronger isolation is later required, the per-platform options are: **Windows** — pass via a deleted temp file or a named-pipe handshake; **macOS** — pass via a Unix domain socket handshake or `launchctl setenv` scoped to the process; **Linux** — pass via an inherited file descriptor (`systemd` socket activation style) or a Unix domain socket.
- WS handshake: client's first frame must be `{"type":"auth","token":"<token>"}`; sidecar compares with `hmac.compare_digest` (constant-time) against the env value; on mismatch it closes the socket. Subsequent frames skip re-auth (matches today's TCP handshake-once model from ADR-0014).
- **Rotation:** on every respawn Rust generates a new token + new port.
- **Never log the token.** Redact `VOICE_TYPER_IPC_TOKEN` from every sink (`tauri.log`, `sidecar.log`, `stdout`/`stderr`). Log at most `token_present=true` or a short hash. A token written to a log file defeats the per-launch rotation and is readable by any local user, so it must never appear verbatim.

### 4. Nuitka build (actionable, per platform)

Nuitka does **not** cross-compile. Each target triple gets its own Nuitka build, run on a CI runner of the matching OS + arch. The CI matrix in `.github/workflows/build.yml` already runs `windows-2022`, `ubuntu-22.04`, `macos-13` (Intel), `macos-14` (Apple Silicon) — extend it with one Nuitka build job per target triple.

#### 4.1 Target triples (mandatory set)

The Tauri `externalBin` mechanism resolves each binary by the Rust target triple (no `.exe` on macOS/Linux; `.exe` on Windows) at runtime via `std::env::consts::ARCH` + `std::env::consts::OS`. The table below is the set of **shipped** binaries you must place in `src-tauri/bin/` — it is NOT the `externalBin` config entry (that is the single base name `bin/python-sidecar`; see §7 for the correction). Voice Typer's CI today ships:

| Platform | Target triple | CI runner | Binary name |
|---|---|---|---|
| Windows x86_64 | `x86_64-pc-windows-msvc` | `windows-2022` | `python-sidecar-x86_64-pc-windows-msvc.exe` |
| Windows aarch64 | `aarch64-pc-windows-msvc` | `windows-11-arm` (when available) | `python-sidecar-aarch64-pc-windows-msvc.exe` |
| macOS Intel | `x86_64-apple-darwin` | `macos-13` | `python-sidecar-x86_64-apple-darwin` |
| macOS Apple Silicon | `aarch64-apple-darwin` | `macos-14` | `python-sidecar-aarch64-apple-darwin` |
| Linux x86_64 | `x86_64-unknown-linux-gnu` | `ubuntu-22.04` | `python-sidecar-x86_64-unknown-linux-gnu` |
| Linux aarch64 | `aarch64-unknown-linux-gnu` | `ubuntu-22.04-arm` (when available) or cross-compile + qemu | `python-sidecar-aarch64-unknown-linux-gnu` |

The prewarm binary follows the same pattern (`prewarm-<triple>[.exe]`). If you are not yet shipping Windows-on-ARM or Linux-ARM in the Electron build, defer those target triples to a follow-up — but document the deferral explicitly.

#### 4.2 Windows Nuitka command

Base interpreter: `python-build-standalone` `cpython-3.12.x+x86_64-pc-windows-msvc` (matches `faster-whisper`/CTranslate2 wheels). The previous ADR's command is correct in spirit but had broken glob `*.dll` lines — Nuitka does not expand globs.

```bat
:: Pin the build interpreter to python-build-standalone cpython-3.12.x
:: (matches faster-whisper / ctranslate2 wheel tags — do NOT use 3.13+ yet).
set PYBS=C:\path\to\python-build-standalone\cpython-3.12.x+x86_64-pc-windows-msvc
set SITE=%PYBS%\Lib\site-packages
python -m nuitka --standalone --onefile ^
  --assume-yes-for-downloads ^
  --enable-plugin=numpy ^
  --include-package=faster_whisper --include-package=ctranslate2 ^
  --include-package=voice_typer --include-package=websockets ^
  --include-data-dir=%SITE%\ctranslate2\lib=%SITE%\ctranslate2\lib ^
  --include-dll=%SITE%\ctranslate2\lib\ctranslate2.dll ^
  --windows-disable-console ^
  --output-filename=python-sidecar-x86_64-pc-windows-msvc.exe ^
  voice_typer/server/ipc_server.py
```

- **Nuitka does NOT expand globs** like `*.dll` in `--include-dll` — those `*.dll` lines above will NOT work as written; use explicit paths or `--include-data-dir` (which copies a whole folder verbatim). The reliable pattern is `--include-data-dir=%SITE%\ctranslate2\lib=%SITE%\ctranslate2\lib` plus an explicit `--include-dll` for `ctranslate2.dll` itself. `ctranslate2/lib` holds `ctranslate2.dll` + optional CUDA DLLs (`cublas64_*`, `cudnn64_*`, `cuda_runtime64_*`) — include the CUDA ones only if the frozen env has CUDA enabled; otherwise CTranslate2 runs CPU-only and those files are absent.
- `--include-package=websockets` is **required** (added to `requirements-lock.txt` — see §14); the sidecar is a WS *server* and the stdlib has no WS implementation. `--enable-plugin=numpy` pulls numpy's hidden imports; if Nuitka warns about missing `numpy.*` submodules, add `--include-package=numpy`.
- **Discover the exact DLL set at build time**, do not guess: `dir "%SITE%\ctranslate2\lib\*.dll"` and list every file; re-run after any `faster-whisper`/`ctranslate2` version bump.
- **CPU inference runtimes (easy to miss, instant crash if absent):** `ctranslate2` links Intel MKL / OpenMP for fast x86 CPU inference even with no GPU. If `libiomp5md.dll` (OpenMP) or the MKL redistributables are missing, the frozen exe **builds fine but crashes instantly on `import ctranslate2`** at launch. Verify with `python -c "import ctranslate2"` in the build env, then enumerate loaded companion DLLs (`listdlls`, Sysinternals Process Explorer, or `tasklist /m`) and copy every runtime next to `ctranslate2.dll` via `--include-data-dir` (or an explicit `--include-dll`). At minimum include `libiomp5md.dll`; add any `libiomp*.dll` / `mkl*.dll` / `libgomp*.dll` present. Nuitka does **not** auto-collect these.
- **Do NOT** bundle model weights — models live in `%APPDATA%/voice-typer/models` (see §8), loaded at runtime. Include only code + native DLLs.
- **`--onefile` temp-dir bloat:** Nuitka `--onefile` extracts to `%TEMP%\onefile_*` on every launch; frequent launches/crashes accumulate gigabytes. Pin a deterministic extract dir with `--onefile-tempdir-spec=%LOCALAPPDATA%\voice-typer\onefile-tmp` and have the installer/uninstaller purge that dir (match by the Voice Typer binary signature) so stale extracts are cleaned.

#### 4.3 macOS Nuitka command (Apple Silicon + Intel)

Base interpreter: `python-build-standalone` `cpython-3.12.x+aarch64-apple-darwin` (Apple Silicon) or `cpython-3.12.x+x86_64-apple-darwin` (Intel). Run separate builds on `macos-14` and `macos-13` respectively.

```bash
PYBS=/path/to/python-build-standalone/cpython-3.12.x+aarch64-apple-darwin
SITE=$PYBS/lib/python3.12/site-packages
python -m nuitka --standalone --onefile \
  --assume-yes-for-downloads \
  --enable-plugin=numpy \
  --include-package=faster_whisper --include-package=ctranslate2 \
  --include-package=voice_typer --include-package=websockets \
  --include-data-dir=$SITE/ctranslate2/lib=$SITE/ctranslate2/lib \
  --include-data-dir=$SITE/ctranslate2/libs=$SITE/ctranslate2/libs \
  --macos-create-bundle \
  --macos-app-name=VoiceTyperSidecar \
  --macos-signed-app-name=com.voicetyper.sidecar \
  --macos-app-mode=background \
  --output-filename=python-sidecar-aarch64-apple-darwin \
  voice_typer/server/ipc_server.py
```

- `--macos-app-mode=background` sets `LSUIElement=true` in the bundle's `Info.plist` — the sidecar runs with no Dock icon, no menu bar item. This is the macOS equivalent of Windows `--windows-disable-console`.
- `--macos-signed-app-name` must match the `CFBundleIdentifier` used for code signing (see §13).
- **CTranslate2 on macOS:** the wheels ship `libctranslate2.dylib` + `libiomp5.dylib` (OpenMP) under `$SITE/ctranslate2/lib/`. Apple Silicon wheels do NOT ship CUDA — CPU-only inference. Verify with `otool -L $SITE/ctranslate2/lib/libctranslate2.dylib` that every `@rpath` dependency resolves in the build env.
- **`pyobjc` deps:** `pyobjc-core`, `pyobjc-framework-CoreAudio`, `pyobjc-framework-Cocoa` are required (volume ducking + tray). Add `--include-package=pyobjc` (and the framework sub-packages). Nuitka's `--include-package=pyobjc` does not always pick up the framework bridges — run the sidecar once in dev mode and watch for `ImportError: pyobjc-...` to discover missing pieces.
- **Apple Silicon vs Intel:** Nuitka cannot produce a universal binary. Build separately per arch and let Tauri pick the right `externalBin` at runtime via `std::env::consts::ARCH`.
- **`--onefile` temp-dir on macOS:** extracts to `$TMPDIR/onefile_*`. Pin with `--onefile-tempdir-spec=$HOME/Library/Application Support/voice-typer/onefile-tmp`.

#### 4.4 Linux Nuitka command (x86_64 + aarch64)

Base interpreter: `python-build-standalone` `cpython-3.12.x+x86_64-unknown-linux-gnu` (built against glibc 2.35 — Ubuntu 22.04 baseline, matching the CI runner pin in `PLATFORM_STATUS.md`). Build on `ubuntu-22.04` for x86_64 and `ubuntu-22.04-arm` (or qemu-emulated) for aarch64.

```bash
PYBS=/path/to/python-build-standalone/cpython-3.12.x+x86_64-unknown-linux-gnu
SITE=$PYBS/lib/python3.12/site-packages
python -m nuitka --standalone --onefile \
  --assume-yes-for-downloads \
  --enable-plugin=numpy \
  --include-package=faster_whisper --include-package=ctranslate2 \
  --include-package=voice_typer --include-package=websockets \
  --include-data-dir=$SITE/ctranslate2/lib=$SITE/ctranslate2/lib \
  --include-data-dir=$SITE/ctranslate2/libs=$SITE/ctranslate2/libs \
  --output-filename=python-sidecar-x86_64-unknown-linux-gnu \
  voice_typer/server/ipc_server.py
```

- **No `--windows-disable-console` equivalent is needed on Linux** — the sidecar is spawned by Tauri with `stdio` piped; no terminal window appears.
- **CTranslate2 on Linux:** the wheels ship `libctranslate2.so` + `libiomp5.so` + `libgomp.so` (OpenMP) under `$SITE/ctranslate2/lib/`. CPU-only on most installs; CUDA wheels exist but are large. Verify with `ldd $SITE/ctranslate2/lib/libctranslate2.so` that every `NEEDED` dependency resolves in the build env.
- **glibc version pinning:** the `python-build-standalone` Linux builds are compiled against a specific glibc. Pin to a build linked against glibc 2.35 (Ubuntu 22.04) so the sidecar runs on Ubuntu 22.04+ / Debian 12+ / Fedora 36+. Newer glibc builds (e.g., Ubuntu 24.04 baseline) would break older distributions. **This is the same baseline as the existing Linux native binary build** (`PLATFORM_STATUS.md`: "The native binary is compiled on `ubuntu-22.04` and links against glibc 2.35").
- **`--onefile` temp-dir on Linux:** extracts to `/tmp/onefile_*`. Pin with `--onefile-tempdir-spec=$HOME/.cache/voice-typer/onefile-tmp` or `$XDG_CACHE_HOME/voice-typer/onefile-tmp`.
- **AppImage considerations:** if shipping as AppImage, the sidecar binary is extracted at mount time to `/tmp/.mount_VoiceTy<XXXX>/usr/bin/`. The `externalBin` mechanism handles this transparently, but the prewarm binary (a `bundle.resource`, not an `externalBin`) must be looked up via `resolve_prewarm_exe()` (see §5) because AppImage mount paths are not stable across launches.

#### 4.5 Common Nuitka caveats (all platforms)

- Path resolution inside the compiled exe: `os.path.dirname(sys.argv[0])` for the exe dir; the OS-specific data dir for config/models/logs (see §8). Tauri passes `VOICE_TYPER_IPC_TOKEN` (+ optionally `appConfigDir`/`appLogDir`) via env; the port is self-selected by the sidecar (`:0`) and reported via stdout (see §1). Dev mode (§14) instead passes `VOICE_TYPER_IPC_PORT` to the plain-Python server, which still reads it from env.
- **Verify step (Phase 0 gate per platform):** run the sidecar binary with a one-shot command that loads `faster_whisper` (`WhisperModel("tiny")`), transcribes a 3-second WAV, prints the text, exits 0. This proves CTranslate2 + DLLs + model load all work inside Nuitka. Run this on every target triple — a Windows-success does NOT imply macOS-success.
- **Existing PyInstaller spec (`scripts/build/voice-typer.spec`) is the fallback.** If Nuitka proves impractical on a target (e.g., macOS Apple Silicon ABI issues, Linux aarch64 missing wheels), the existing PyInstaller `--onedir` spec already bundles the native hotkey binaries, Linux permission scripts, data files, and platform-specific hidden imports. The sidecar entrypoint is identical; only the freeze tool changes. PyInstaller `--onedir` produces a folder, not a single file — Tauri `externalBin` cannot point at a folder, so the folder must be wrapped: on Windows, a thin launcher `.exe` that `CreateProcess`es the real entrypoint inside the folder; on macOS/Linux, a shell-script launcher with the executable bit set. The launcher must be named with the target-triple suffix.

### 5. Prewarm packaging (cross-platform)

- The `prewarm/` package (entry point `prewarm/__main__.py`, dispatched to `prewarm/pipeline.py::run`) is frozen the **same Nuitka way** into `prewarm-<target-triple>[.exe]` (kept separate per Rule 1 — not via the sidecar's Python). One prewarm binary per target triple.
- **Resource, NOT `externalBin`.** Prewarm is launched by the platform-specific scheduler (Windows Task Scheduler, macOS LaunchAgent, Linux systemd user timer) — NOT spawned by Tauri as a managed child — so it must be a `bundle.resource` extracted to `resourceDir`, **not** an `externalBin` (externalBin is only for the Tauri-spawned sidecar). Consequence: `shell:allow-spawn` does **not** apply to prewarm. Tauri extracts the resource next to the app; first launch (or the installer) registers the scheduled task / LaunchAgent / systemd timer (see `resolve_prewarm_exe` below).
- **Path resolution after install:** today `task_scheduler._prewarm_command()` (Windows) and `prewarm_scheduler_posix._prewarm_python()` (macOS/Linux) return the Python interpreter + module path. Post-migration they must point at the frozen exe. Add one resolver used by all three schedulers and the `run_prewarm` / `get_prewarm_status` handlers:
  ```python
  def resolve_prewarm_exe() -> str | None:
      """Resolve the prewarm executable path, post-Tauri-migration.

      Order:
        1. VOICE_TYPER_PREWARM_EXE env var (set by Tauri to resourceDir/prewarm-<triple>).
        2. Tauri resource dir (tauri::api::path::resource_dir) — heuristically:
           next to the sidecar binary, in ../Resources/ (macOS .app bundle).
        3. App install dir (Windows: %LOCALAPPDATA%\Programs\VoiceTyper\).
        4. Dev fallback: plain python module (works without a frozen exe).
      """
      import os, sys
      from pathlib import Path
      from voice_typer.server.platform_utils import is_windows, is_macos, is_linux

      # 1) Rust passes the extracted resource path (preferred).
      if (env := os.environ.get("VOICE_TYPER_PREWARM_EXE")) and Path(env).exists():
          return env

      # 2) Tauri resource dir (best-effort).
      triple = _target_triple()  # e.g. "x86_64-pc-windows-msvc"
      exe_suffix = ".exe" if is_windows() else ""
      candidates = []
      if is_macos():
          # .app bundle: Voice Typer.app/Contents/Resources/prewarm-<triple>
          candidates.append(Path(sys.argv[0]).resolve().parent.parent / "Resources" / f"prewarm-{triple}{exe_suffix}")
      elif is_linux():
          # AppImage: /tmp/.mount_VoiceTy*/usr/resources/prewarm-<triple>
          # .deb/.rpm: /usr/lib/voice-typer/resources/prewarm-<triple>
          candidates.append(
              Path(os.environ.get("APPDIR", "/usr/lib/voice-typer/resources")) / f"prewarm-{triple}{exe_suffix}"
          )
      elif is_windows():
          # %LOCALAPPDATA%\Programs\VoiceTyper\resources\prewarm-<triple>.exe
          candidates.append(
              Path(os.environ.get("LOCALAPPDATA", ""))
              / "Programs"
              / "VoiceTyper"
              / "resources"
              / f"prewarm-{triple}{exe_suffix}"
          )

      for c in candidates:
          if c.exists():
              return str(c)

      # 4) Dev fallback: plain python module (works without a frozen exe).
      return f"{sys.executable} -m voice_typer.server.prewarm"
  ```
  - `task_scheduler._prewarm_command()` (Windows) and `prewarm_scheduler_posix._prewarm_python()` + `_prewarm_args()` (macOS/Linux) return `resolve_prewarm_exe()` when the sidecar build is active; `run_prewarm` / `get_prewarm_status` shell out via the same resolver. Rust sets `VOICE_TYPER_PREWARM_EXE` to the resource path at startup.
  - The sidecar does **not** spawn prewarm; prewarm remains an independent boot helper (ADR-0011). `get_prewarm_status` / `run_prewarm` still work via `resolve_prewarm_exe()`.
  - **`_target_triple()` helper:** must produce the same string Rust uses to name `externalBin`:
    ```python
    def _target_triple() -> str:
        import sys

        if sys.platform == "win32":
            arch = "x86_64" if sys.maxsize > 2**32 else "x86"
            return f"{arch}-pc-windows-msvc"
        elif sys.platform == "darwin":
            import platform

            arch = "arm64" if platform.machine() == "arm64" else "x86_64"
            return f"{arch}-apple-darwin"
        else:
            import platform

            arch = platform.machine() or "x86_64"
            return f"{arch}-unknown-linux-gnu"
    ```
- **Uninstall cleanup per platform:**
  - **Windows:** the MSI/installer must **deregister** the `VoiceTyperPrewarm` Task Scheduler entry (`schtasks /delete /tn VoiceTyperPrewarm /f`) and the HKCU Run key on uninstall/upgrade. Otherwise an orphaned scheduled task will try to launch a deleted exe after the app is removed, spamming Task Scheduler failures.
  - **macOS:** the `.pkg`/DMG installer must `launchctl unload` and delete `~/Library/LaunchAgents/com.voicetyper.prewarm.plist` on uninstall.
  - **Linux:** the .deb/.rpm `prerm` scripts must `systemctl --user disable --now voice-typer-prewarm.timer` and delete `~/.config/systemd/user/voice-typer-prewarm.{service,timer}` on uninstall. The existing `scripts/linux/prerm` and `scripts/linux/prerm.rpm` already handle the udev-rule and input-group cleanup — extend them, do not replace them.

### 6. Toast + paste (cross-platform)

#### 6.1 Toast (notifications) — cross-platform

`electron_notification` → **`tauri-plugin-notification`**, NOT `enigo`. `enigo` is keystroke/mouse injection only. Add `notification:allow-notify` to capabilities. The Tauri notification plugin uses:
- **Windows:** WinRT `ToastNotification` (Windows 10+) or `Shell_NotifyIcon` balloon (legacy fallback).
- **macOS:** `NSUserNotificationCenter` (deprecated in macOS 11+) or `UNUserNotificationCenter` (requires bundle entitlement).
- **Linux:** libnotify (`notify-send`).

**macOS entitlement:** `Info.plist` must declare `NSUserNotificationsUsageDescription` and the app must request authorization on first launch via `UNUserNotificationCenter.current().requestAuthorization(...)`. Without this, notifications silently no-op on macOS 11+.

#### 6.2 Paste strategy (`enigo` + clipboard) — cross-platform

- **Short text (< ~300 chars):** inject via **`enigo.text()`** (Unicode string injection). Do **NOT** simulate discrete `key_down`/`key_up` virtual-key events — that breaks non-English layouts, dead keys, and punctuation. The only discrete keys simulated are `Ctrl+V` (Windows/Linux) or `Cmd+V` (macOS) in the long-text path below.
  - **Windows:** `enigo.text()` uses `SendInput` with `KEYEVENTF_UNICODE`. IME-safe.
  - **macOS:** `enigo.text()` uses `CGEventCreateKeyboardEvent` with `kCGKeyboardEventKeycode = 0` + `CGEventKeyboardSetUnicodeString`. Requires Accessibility permission (same as the native hotkey binary — see §6.4).
  - **Linux:** `enigo.text()` on Linux uses X11 `XTestFakeKeyEvent` per-character. **Does not work on Wayland** — see §6.6 for the Wayland fallback.
- **Long text (≥ ~300 chars) / text-field target:** copy via `tauri-plugin-clipboard-manager` then send `Ctrl+V` (Windows/Linux) or `Cmd+V` (macOS) via `enigo`, then optionally restore the previous clipboard. Matches today's "paste on stop" logic in `clipboard/manager.py`.

#### 6.3 Focus restore — Windows (the dance the prior ADR described)

Before injecting, capture the current foreground window with `GetForegroundWindow()` + its thread id (`GetWindowThreadProcessId`); after `SendInput`, re-attach via `AttachThreadInput(our_thread, target_thread, TRUE)` then `SetForegroundWindow(hwnd)` + `AttachThreadInput(..., FALSE)`. This is the standard `win32` focus-steal dance (see `clipboard/windows.py`, which already does foreground-attachment for paste) so the user's window is not permanently stolen.

**Elevated / focus-attach failure (UIPI):** `SendInput` and the focus-restore dance are blocked by UIPI when the target runs as Administrator (or at a higher integrity level than Voice Typer). The restore path calls `AttachThreadInput(our_thread, target_thread, TRUE)`; **if it returns `0`, do NOT retry the window-switch** — fall back immediately: write the text to the system clipboard, push it to crash-recovery (`crash_recovery.add`), and surface a **toast** "Could not paste — text copied to clipboard" (via `tauri-plugin-notification`). The same fallback applies if `SetForegroundWindow` silently fails. This matches today's no-data-loss guarantee and removes the ambiguity an implementer would otherwise hit.

**Global hotkeys + UIPI:** the dictation toggle is registered via `tauri-plugin-global-shortcut`. On Windows, UIPI blocks a standard-user process from receiving global keyboard hooks while an **elevated (Administrator)** window has focus — the hotkey silently will not fire. This is an OS limitation, not a bug: log it and (optionally) surface a one-time toast "Hotkeys unavailable while an admin window is focused" so an implementer does not waste hours "debugging" a working hook. **This UIPI issue also applies to the native `windows-key-listener.exe` binary** — the existing behavior is the same, so switching to Tauri's plugin would not regress this, but switching to Tauri's plugin WOULD regress key suppression (see §6.4).

#### 6.4 Global hotkeys — DO NOT switch to `tauri-plugin-global-shortcut` (keep native binaries)

The previous ADR said "Port tray, global hotkey, settings, and autostart UX to Tauri plugins (`tray`, `global-shortcut`, `autostart`, `single-instance`)." **This is wrong for the global hotkey.** Voice Typer today uses three native binaries (`voice_typer/server/native/{windows-key-listener.exe, macos-key-listener, linux-key-listener}`) compiled by `scripts/build/compile_native.sh` / `compile_native.ps1`, documented in ADR-0007 and ADR-0008. The Tauri global-shortcut plugin **cannot replace them** without regressing critical features:

| Feature | Native binary (today) | `tauri-plugin-global-shortcut` |
|---|---|---|
| Key suppression (so the hotkey doesn't reach the foreground app) | Win ✅ (`WH_KEYBOARD_LL` returns non-zero) · macOS ✅ (CGEvent tap returns NULL) · Linux ❌ (evdev read-only) | **Win ❌ · macOS ❌ · Linux ❌** (Tauri's plugin is read-only on all platforms) |
| Modifier-only hotkeys (bare `Caps Lock`, `Alt`, `Fn`) | Win ✅ · macOS ✅ · Linux ✅ | **Win partial** (some) · **macOS partial** · **Linux ❌** |
| Fn / Globe key on macOS (default hotkey per `config._default_hotkey_for_platform`) | ✅ (Swift binary uses `NSEvent.modifierFlags.function`) | **❌** (Tauri plugin does not surface Fn/Globe) |
| Wayland support | ✅ (evdev sits below the display server) | **❌** (Tauri plugin uses X11 only on Linux) |
| Crash isolation | ✅ (subprocess; Python restarts on exit per ADR-0007 fallback chain) | ❌ (in-process; a plugin crash kills the host) |

**Decision: keep the native hotkey binaries, spawned by the Python sidecar (not by Tauri).** The existing `hotkeys/factory.py::create_hotkey_backend()` factory already handles binary discovery + spawn + fallback. The sidecar (post-migration) does exactly what the Python backend does today: spawn the native binary as a subprocess, parse its line-delimited stdout wire protocol (READY / FN_DOWN / KEY_DOWN / MOD_DOWN / etc.), and match against the registered hotkey. **Tauri does not touch the hotkey subsystem at all.** This preserves ADR-0007 + ADR-0008 unchanged.

**macOS Accessibility permission flow (preserved):** the native `macos-key-listener` binary requires Accessibility (System Settings → Privacy & Security → Accessibility). ADR-0008 Gap 2 documents the zero-command onboarding flow: when the binary detects a missing Accessibility grant, the sidecar publishes `electron_notification` (→ `notification` under Tauri) with a deep-link to `x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility`. A 60s retry timer polls for the grant and auto-restarts the native backend. **This entire flow stays in the Python sidecar** — Tauri just forwards the notification event to the system toast. The `permissions.py` module and `check_accessibility` command (`system_handlers.py`) are unchanged.

**Linux udev rule + input group (preserved):** the native `linux-key-listener` binary requires `input` group membership to read `/dev/input/event*`. The existing `scripts/linux/postinst` (and `postinst.rpm`) already install the udev rule `99-voice-typer.rules` and add the installing user to the `input` group via `usermod -aG input`. The Tauri `.deb`/`.rpm` packages must **reuse these postinst scripts verbatim** — they are not Tauri-specific. The `scripts/linux/install_permissions.py` script and the AppImage `pkexec` + `voice-typer.polkit` flow also stay unchanged.

#### 6.5 Tray icon — port to `tauri-plugin-tray`, preserve menu structure

Today the tray icon is `pystray` (Win32 / AppKit / GTK), with menu logic in `tray.py`, `tray_menu.py`, `tray_icon.py`, `tray_models.py`, `tray_hotkey.py`, `tray_window.py`. The menu has locale support (`set_tray_locale` command), dynamic items (record/stop, model status, etc.), and deep-links to UI routes via the `navigate` event.

**Under Tauri:** the tray icon moves to `tauri-plugin-tray` (Win32 / AppKit / GTK via `gtk-3.0`), but the **menu structure and locale logic stay in the Python sidecar** — the sidecar computes the menu items and emits them as a `tray_menu` event; the Rust host renders them via the Tauri tray API. This preserves the existing `tray.py` / `tray_menu.py` logic unchanged. Click events flow back from Rust → sidecar via `invoke('dispatch', {cmd: 'tray_click', data: {item_id}})` (added to `_COMMAND_REGISTRY` per §16; locate the entry by `tray_click:`).

#### 6.6 Wayland — special considerations

- **Global hotkey:** works (evdev native binary, §6.4).
- **Paste:** `enigo.text()` does NOT work on Wayland (X11-only). The clipboard + `Ctrl+V` path via `tauri-plugin-clipboard-manager` is the only reliable option. `tauri-plugin-clipboard-manager` on Linux uses `wl-copy`/`wl-paste` (Wayland) or `xclip`/`xsel` (X11) — detect at runtime via `WAYLAND_DISPLAY` env var. **Document this in the user-facing troubleshooting:** on Wayland, short-text injection falls back to clipboard + `Ctrl+V` always, so the user's previous clipboard contents are temporarily replaced. The `clipboard_snapshot.py` borrow/restore logic from ADR-0012 handles this.
- **Window focus detection:** Wayland does not expose the foreground window to other apps (by design — "focus stealing prevention"). The current `clipboard/windows.py` Windows-only focus detection (`GetForegroundWindow`) has no Wayland equivalent. Today the app already handles this on non-Windows by always copying to clipboard (per `PLATFORM_STATUS.md` row "Focus detection (safe auto-paste)"). Under Tauri this stays the same — no regression, no improvement.
- **AppImage on Wayland:** the AppImage runs in a sandboxed environment that may restrict `wl-copy` access. Test the AppImage on a Wayland session (Fedora 40 default, Ubuntu 22.04 with `GNOME` session) before cutover.

### 7. Tauri config + capabilities (cross-platform)

`tauri.conf.json` essentials:
```json
{
  "bundle": {
    "externalBin": [
      "bin/python-sidecar"
    ],
    "resources": [
      "resources/prewarm-x86_64-pc-windows-msvc.exe",
      "resources/prewarm-aarch64-pc-windows-msvc.exe",
      "resources/prewarm-x86_64-apple-darwin",
      "resources/prewarm-aarch64-apple-darwin",
      "resources/prewarm-x86_64-unknown-linux-gnu",
      "resources/prewarm-aarch64-unknown-linux-gnu",
      "resources/native/windows-key-listener.exe",
      "resources/native/macos-key-listener",
      "resources/native/linux-key-listener"
    ],
    "windows": { "signCommand": "..." },
    "macOS": { "signingIdentity": "...", "entitlements": "entitlements.plist" },
    "linux": { "deb": { "depends": ["libnotify4", "libxtst6", "libwebkit2gtk-4.1-0"] } }
  },
  "plugins": {
    "tray": {},
    "global-shortcut": {},
    "autostart": {"targets": []},
    "notification": {},
    "clipboard-manager": {},
    "single-instance": {},
    "updater": {"endpoints": ["https://github.com/AbdallahIsDev/voice-typer/releases/latest/download/latest.json"], "pubkey": "..."}
  },
  "app": { "security": { "capabilities": ["migrate-runtime"], "csp": "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'" } }
}
```

> **Tauri v2 capabilities are mandatory (not optional).** Unlike v1, Tauri v2 ships zero IPC/shell/plugin permissions by default — every `invoke`, `shell:spawn`, `notification:notify`, `clipboard` read/write, and `global-shortcut` must be explicitly whitelisted in `src-tauri/capabilities/*.json` or Tauri **silently blocks it at runtime** (no compile error). The implementer must confirm each capability below is present, or paste/clipboard/notification will mysteriously no-op.

> **`externalBin` uses a BASE NAME, not per-triple file names (MANDATORY — 2026-07-18 correction).** Tauri v2's `sidecar()` API appends the **Rust target triple automatically** at runtime, so you register the base name once: `"bin/python-sidecar"`. Tauri then resolves it to `bin/python-sidecar-x86_64-pc-windows-msvc.exe` (Windows), `bin/python-sidecar-aarch64-apple-darwin` (macOS Apple Silicon), etc., by matching `std::env::consts::ARCH` + `OS` against the files you placed in `src-tauri/bin/`. **Do NOT list six triple-suffixed entries** in `externalBin` (an earlier draft of this ADR did — that is wrong and will fail to bundle). The build must still *ship* one frozen binary per target triple under `src-tauri/bin/` (see §4.1 for the required filenames), but the `tauri.conf.json` entry is the single base name. The same base-name rule applies to the `shell.scope` entry below.

> **Native hotkey binaries are `resources`, not `externalBin`:** the native hotkey binaries are spawned by the **Python sidecar** (not by Tauri), so they must be `bundle.resources` extracted to `resourceDir`, then the sidecar discovers them via the existing `native_hotkeys.get_native_binary_path()` lookup (which already checks `VOICE_TYPER_NATIVE_BINARY` env var, `voice_typer/server/native/` dev path, next to `sys.executable`, and `_MEIPASS`). Add a fifth lookup path: `os.environ.get("VOICE_TYPER_NATIVE_DIR")` set by Tauri to `resourceDir/native/`. Do NOT change the existing four paths — they are used by the PyInstaller fallback build.

`migrate-runtime.capability` (least privilege):
- `core:default`, `core:event:default`, `core:window:allow-show`/`hide`/`set-focus`
- `shell:allow-spawn` **scoped to the sidecar binary** — Tauri v2 rejects an unconstrained spawn, so the capability must name the sidecar base name (Tauri appends the target triple at runtime — same rule as `externalBin`):
  ```json
  { "identifier": "shell:allow-spawn",
    "allow": [
      { "name": "bin/python-sidecar" }
    ] }
  ```
  `shell:allow-kill-children` for the force-kill backstop (§10).
- `global-shortcut:allow-register` / `unregister` — **only if** you decide to also register hotkeys via Tauri (e.g., for a global "show settings" shortcut). The dictation toggle stays on the native binary (§6.4).
- `clipboard-manager:allow-read-text` / `write-text` / `clear`
- `notification:allow-notify`
- `single-instance:default`
- **Exactly ONE generic Rust command** bridges the webview to the sidecar — do **not** write a per-command `tauri::command` for each of the 69 commands. The webview calls `invoke('dispatch',{cmd,data})`; Rust forwards the envelope over WS, awaits the response keyed by a per-request id, and returns it:

  ```rust
  #[tauri::command]
  async fn dispatch(cmd: String, data: serde_json::Value) -> Result<serde_json::Value, String> {
      // assign a request id; send {"type": cmd, "data": data, "id": id} over the WS client;
      // await the matching {"id": id, ...} response; return it.
  }
  ```

  Because there is a single command, no per-command `ipc:` capability entry is needed — Rust maps `dispatch` to the WS connection.

### 8. Path resolution (cross-platform)

Voice Typer today uses the platform-aware `_paths.config_dir()` (which delegates to `config._config_dir()`) — the single source of truth. The previous ADR's Windows-only `%LOCALAPPDATA%` reference was incomplete. The actual resolution per `_paths.py`:

| Platform | Path | Notes |
|---|---|---|
| Windows | `%APPDATA%/voice-typer` | `APPDATA` = `C:\Users\<user>\AppData\Roaming` |
| macOS | `~/Library/Application Support/voice-typer` | per Apple File System conventions |
| Linux | `$XDG_DATA_HOME/voice-typer` (default `~/.local/share/voice-typer`) | per XDG Base Directory Spec |
| Override | `$VOICE_TYPER_CONFIG_DIR` | dev/test override |
| Migration | `~/.voice-typer` checked first | existing installs keep their data in place |

- Config: `<config_dir>/config.json` (same as today's `config.py` `APPNAME` dir). Sidecar reads it via `_paths.config_dir()` (CWD-independent, unchanged).
- Models: `<config_dir>/models` (`HF_HOME` redirected here via `asr_setup.py`). `prewarm` warms this path.
- Logs: `<config_dir>/logs/` (see §11).
- History DB: `<config_dir>/history.db` (SQLite WAL, `0o600` on POSIX, NTFS ACLs on Windows).
- Crash recovery: `<config_dir>/voice-typer-recovery.json`.
- **Electron `userData` migration:** on first Tauri launch, if `<config_dir>` is absent but the old Electron `userData/voice-typer` exists, copy it once (config + models + history) — one-time, idempotent. Off by default until validated.
  - **Both exist (merge rule):** if both `<config_dir>` and the old Electron `userData/voice-typer` exist and differ, do **not** blindly overwrite: (a) `config.json` — merge key-by-key, **newest mtime wins** per key; (b) `models/` — copy only files **absent** from the target (never clobber a newer download); (c) `history.db` — **append**, never replace (history is append-only and irreplaceable); (d) log a summary of what was merged. Prevents silently destroying user data on a revert-then-relaunch.
  - **Ordering (write-conflict trap):** run the migration/merge **before** the sidecar starts. If the sidecar boots first it initializes a fresh empty `config.json` / `history.db`; the later merge then hits a file lock / write conflict or silently ignores the old data. Migrate → then spawn.
  - **Per-platform `userData` location:** Electron's `app.getPath('userData')` is `%APPDATA%/Voice Typer` (Windows, with a space), `~/Library/Application Support/Voice Typer` (macOS, with a space), `~/.config/Voice Typer` (Linux, with a space). Note the **space** in the dir name — different from the Python side's `voice-typer` (hyphen). The migration code must handle both.

### 9. `bubble_level` throttling

- Sidecar emits `bubble_level` at source ~60 Hz. **Rust coalesces**: keep only the latest `{rms,peak}` and emit a Tauri event at ≤ 30 Hz (or on `requestAnimationFrame`). Prevents WebView jank. The ~60 Hz source throttle in `app.py` stays; Rust adds a second coalescing throttle.

### 10. WebSocket disconnect / error handling + supervisor + rate limiter

- **Clean shutdown:** Rust sends `{"type":"shutdown"}`; sidecar releases mic, acks `{"type":"result"}`, exits 0. **Hard timeout:** if the sidecar has not exited within **2.0 s** of the ack (e.g. it is stuck inside a native CTranslate2 call and cannot service the WS message), Rust force-kills the process tree via Tauri's `kill_children` handle. Never wait indefinitely on a blocked Python thread.
- **Sidecar crash / WS close without shutdown:** Rust treats it as a crash → respawn (backoff 500→1000→2000 ms, cap 5). In-flight chunk discarded.
- **Token validation failure:** sidecar closes the socket immediately; Rust logs + shows "connection rejected", retries with a fresh token (counts toward backoff).
  - **Transient loopback blip:** Rust attempts one immediate reconnect; on failure, falls into backoff.
  - **Frame-size limit:** cap WS messages at **1 MiB** (`tokio-tungstenite` `max_frame_size` on the Rust client; `websockets` `max_size` on the server). `download_progress` and `vocabulary_suggestion` can carry large payloads; without a limit a malformed/huge frame can OOM the client. Reject oversized frames with a clean error rather than buffering unbounded.
  - **Malformed frames:** a WS frame that is not valid JSON (or fails `_validate_dict_payload`) must yield `{"type":"error","code":"invalid_payload","data":{...}}` and leave the connection open — the sidecar must **never** crash on a bad frame. The Rust client treats a non-`result`/`error` response as a protocol error, not a crash.
- **supervisor state machine:** `running → (unexpected exit) → reconnecting (UI "reconnecting…") → respawn with backoff (500ms → 1s → 2s, cap 5 retries) → running | give up → full-app relaunch`. In-flight audio chunk on crash is discarded (next dictation re-opens capture); acceptable.
- **Per-connection rate limiter (ADR-0019 port):** the existing limiter in `log_rate_limit.py` (200 burst / 60 sustained msg/s) was written for the TCP accept path. The WS server accept path must call the same limiter on every incoming frame. A client that exceeds the limit gets `{"type":"error","code":"rate_limited","data":{"retry_after_ms":...}}` and the connection stays open. **This is a hard porting requirement** — without it, a misbehaving UI (or a buggy Rust bridge that re-sends on timeout) can DoS the sidecar's dispatch loop.
- **Heartbeat removal (replaces ADR-0018 on Tauri path):** the `_heartbeat_loop` daemon thread in `ipc_server.py` and the `_handle_heartbeat` command are **disabled on the Tauri path** via an env-var check at sidecar startup (`TAURI_SIDECAR=1` → skip `_heartbeat_loop` start). They stay enabled on the Electron fallback path. The Rust supervisor's WS-close detection replaces the 120-second heartbeat timeout. See §2.

### 11. Logging / diagnostics (cross-platform)

- Sidecar runs with no console on all platforms (Windows `--windows-disable-console`, macOS `LSUIElement=true`, Linux piped stdio). Rust reads the sidecar `stdout` pipe for the `server_started` JSON (see §1) and **tees both streams** to `<config_dir>/logs/sidecar.log` (Tauri `appLogDir`). Rust also writes `tauri.log`.
- **Rotation:** `log.py` must use a `RotatingFileHandler` (e.g. 5 MB × 5 files ≈ 25 MB cap) instead of an unbounded `FileHandler`, or `sidecar.log` grows without limit across sessions.
- **Exclude `bubble_level` from the file log.** At ~60 Hz it would fill disk fast even with rotation. Ensure `bubble_level` publishes are logged at `DEBUG` only (or suppressed in `log.py` / the `event_bus`→file forwarder) so file logs capture events/errors, not the level stream. Rust already coalesces the event to ≤30 Hz for the UI (§9); the file path must drop it entirely.
- Keep the Python `logging` config (`log.py`) otherwise unchanged; it writes to the file resolved from `_paths.config_dir()`. Do NOT rely on console output post-migration.
- **Per-platform log location:**
  - Windows: `%APPDATA%/voice-typer/logs/`
  - macOS: `~/Library/Application Support/voice-typer/logs/` (NOT `~/Library/Logs/` — keep consistency with the existing app data dir)
  - Linux: `$XDG_DATA_HOME/voice-typer/logs/` (default `~/.local/share/voice-typer/logs/`)

### 12. Single-instance behavior (cross-platform)

- `single-instance` plugin enabled. Second launch → existing instance focused (`show` + `setFocus` on main window) and a `second-instance` event emitted so tray/UI can surface. No second sidecar spawned. Matches "one app" expectation.
- **Ordering is critical:** the `single-instance` duplicate check must run at the **absolute entry point of `main.rs` — before any sidecar initialization** (token gen, `stdout` port handshake, `shell:spawn`). If a second launch reaches the spawn code before the duplicate is detected, you get a **zombie sidecar** (and a competing mic holder) on every double-click of the desktop shortcut. Detect the duplicate first; only the surviving instance starts the sidecar.
- **CLI args / deep links:** forward the second instance's argv to the running instance via the `single-instance` `args` payload → re-emit as a Tauri event (or internal Rust message). The current `app.py` mutex (`VoiceTyperSingleInstance` — locate by `class VoiceTyperSingleInstance`) only blocks duplicates; under Tauri the args must be delivered so deep links / `voice-typer:` URIs still open the right view.
- **Per-platform single-instance mechanism used by Tauri's plugin:**
  - **Windows:** Win32 named mutex (same approach as today's `VoiceTyperSingleInstance`, different name — Tauri uses the app identifier). **Remove the Python-side `VoiceTyperSingleInstance` mutex when running under Tauri** (`TAURI_SIDECAR=1` → skip mutex acquire) to avoid double-locking.
  - **macOS:** `NSApplication` activation policy — the second launch activates the first via the macOS app-activation protocol. No file-based lock needed.
  - **Linux:** lockfile in `<config_dir>/.single-instance.lock` (Tauri plugin default). Best-effort, like today's POSIX lockfile.

### 13. Code-signing order (per platform)

#### 13.1 Windows (Authenticode)

1. Nuitka-produced `python-sidecar-x86_64-pc-windows-msvc.exe` and `prewarm-x86_64-pc-windows-msvc.exe` are **Authenticode-signed** immediately after build (before bundling) — unsigned sidecars trigger SmartScreen / AV.
2. Tauri builds the MSI/EXE; the bundler signs the main executable + installer.
3. Optionally re-sign the final bundle. Keep cert + timestamp server configured in CI.

**Signing specifics:**
- Tool: `signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /a <exe>`. Use an RFC-3161 timestamp server (DigiCert shown) so the signature survives cert expiry.
- **`--onefile` self-extraction caveat:** Nuitka `--onefile` bundles an inner exe that extracts to a temp dir at runtime. **Only the outer `.exe` is signed** — the extracted inner exe is transient and not separately signed, which is fine; do not attempt to sign the inner payload. AV may briefly flag the temp extraction; that is expected and benign.
- **Antivirus / SmartScreen QA note:** during `--onefile` self-extraction the inner exe briefly appears in a temp dir *unsigned*; procmon / AV consoles will show an "unsigned" child process. That is the expected transient stage, **not** a packaging bug — do not flag it in QA. The outer `.exe` is what is Authenticode-signed and what SmartScreen validates.
- Sign both `python-sidecar-*` and `prewarm-*` exes before they enter the Tauri bundle; the bundler then signs the host + MSI.
- **Match the existing `electron-builder.yml` config:** today the Windows build uses `WIN_CSC_LINK` / `CSC_LINK` env vars for the cert. The Tauri build should reuse the same cert + env vars to avoid cert duplication in CI.

#### 13.2 macOS (Developer ID + notarization + stapling)

1. Nuitka-produced sidecar + prewarm binaries are **code-signed with Developer ID Application** (`codesign --force --options runtime --sign "Developer ID Application: <name>" <binary>`) immediately after build.
2. The binaries are added to the `.app` bundle's `Contents/Resources/` (or `Contents/MacOS/` for the sidecar).
3. The entire `.app` bundle is code-signed with `--deep` (or, preferably, signed leaf-to-root manually).
4. The bundle is **notarized** (`xcrun notarytool submit ... --wait`), then **stapled** (`xcrun stapler staple <app>`).
5. The DMG is built from the stapled `.app`, then the DMG itself is signed + notarized + stapled.

**Required `Info.plist` keys for the `.app`:**
- `CFBundleIdentifier`: `com.voicetyper.desktop` (matches today's `electron-builder.yml` `appId`).
- `LSMinimumSystemVersion`: `13.0` (matches `PLATFORM_STATUS.md` minimum).
- `LSUIElement`: `false` (the main app shows in the Dock; the sidecar sets `LSUIElement=true` separately).
- `NSMicrophoneUsageDescription`: required for `sounddevice` to access the mic.
- `NSUserNotificationsUsageDescription`: required for `tauri-plugin-notification` on macOS 11+.
- **Hardened runtime** (`com.apple.security.cs.allow-jit` for CTranslate2 if it uses JIT; `com.apple.security.cs.disable-library-validation` if Nuitka's `--onefile` extracts unsigned dylibs at runtime — coordinate with Apple's notarization docs).

**Match the existing `electron-builder.yml` config:** today the macOS build uses `MAC_SIGNING_IDENTITY` env var + `notarize: true` + `hardenedRuntime: true`. The Tauri build should reuse the same identity + notarization credentials.

**Apple Silicon + Intel:** build separately, produce two `.app` bundles, ship as two DMGs (or one universal DMG). The `electron-builder.yml` today ships `dmg` with `arch: [x64, arm64]` — two separate DMGs. Mirror this.

#### 13.3 Linux (no signing by default)

Linux packages are unsigned by default in both Electron (today) and Tauri. Optional improvements (out of scope for v1):
- **GPG-sign the .deb**: `dpkg-sig --sign builder <deb>`. Users verify with `apt-key`.
- **GPG-sign the .rpm**: `rpm --addsign <rpm>`. Users verify with `rpm --checksig`.
- **AppImage GPG signature**: AppImage supports `zsync` + GPG; documented at the AppImage spec.

**Reuse the existing `scripts/linux/postinst`, `prerm`, `postinst.rpm`, `prerm.rpm`** verbatim — they install the udev rule, add the user to `input`, configure Caps Lock neutralization, and write a manifest at `/var/lib/voice-typer/permissions-manifest.json` for clean uninstall. **These scripts are not Tauri-specific** and must be wired into the Tauri `deb` and `rpm` bundle config:
```json
"bundle": {
  "linux": {
    "deb": {
      "depends": ["libnotify4", "libxtst6", "libwebkit2gtk-4.1-0", "python3"],
      "desktopTemplate": "voice-typer.desktop.template",
      "postInstallScript": "../../scripts/linux/postinst",
      "preRemoveScript": "../../scripts/linux/prerm"
    },
    "rpm": {
      "depends": ["libnotify", "libXtst", "webkit2gtk3", "python3"],
      "postInstallScript": "../../scripts/linux/postinst.rpm",
      "preRemoveScript": "../../scripts/linux/prerm.rpm"
    }
  }
}
```

### 14. Dev workflow + WebSocket dependency (cross-platform)

- **WebSocket library:** the current IPC is raw TCP — there is **no** WS library in `requirements-lock.txt`. Add **`websockets`** (server-capable) and pin it; Nuitka must `--include-package=websockets` (§4). The sidecar is the WS *server* (`websockets.serve`); Rust is the WS *client* (`tokio-tungstenite`).
- **`cargo tauri dev` without recompiling Nuitka per platform:** add a dev mode where the sidecar runs as a plain Python process instead of the frozen exe. Rust reads `VOICE_TYPER_SIDECAR_DEV=1`; when set it spawns `python -m voice_typer.server.ipc_server` (with the same `VOICE_TYPER_IPC_PORT` / `VOICE_TYPER_IPC_TOKEN` env) instead of the `externalBin`. UI/transport iterate in seconds, not minutes; the frozen exe is still used in `release`/bundled builds. **On macOS/Linux dev mode**, also set `VOICE_TYPER_NATIVE_DIR` to the `voice_typer/server/native/` source path so the sidecar finds the dev-mode native binaries.
- **Per-platform dev gotchas:**
  - **Windows dev:** `python -m voice_typer.server.ipc_server` opens a console window. Use `pythonw.exe` instead, or accept the console for dev (it shows logs).
  - **macOS dev:** the native `macos-key-listener` binary requires Accessibility permission for the **terminal** that spawned it (Terminal.app / iTerm / VS Code). The permission does NOT transfer to the binary alone. Grant Accessibility to the terminal, then the binary inherits.
  - **Linux dev:** the native `linux-key-listener` binary requires `input` group membership. If running from source without install, run `sudo usermod -aG input $USER` and log out/in once.

### 15. Auto-update (NOT IMPLEMENTED today — explicit non-goal for v1)

The previous ADR did not address auto-update. **Today, auto-update is NOT IMPLEMENTED** — `docs/auto-update-feature.md`'s own header states: "STATUS: NOT IMPLEMENTED. This is a design-only spec. None of the referenced files exist." The `electron-builder.yml` does declare a `publish: github` config, but no code reads it.

**Under Tauri:** the `tauri-plugin-updater` is the cross-platform auto-updater (Windows replaces MSI via `nsis`; macOS replaces DMG via `sparkle`-style; Linux replaces AppImage via `AppImageUpdate`). It requires a `latest.json` manifest hosted at a stable URL + a signing keypair.

**Decision: auto-update is out of scope for the v1 Tauri migration.** Ship the Tauri build as a manual-download release (matching today's Electron release model — there is no working auto-update today). Track auto-update as a separate follow-up ADR after the Tauri cutover stabilizes. Do NOT wire up `tauri-plugin-updater` in the v1 migration — it adds a signing-key distribution problem and a manifest-hosting problem that are orthogonal to the runtime migration.

### 16. New commands / events process

The 65-command frozen table (§2 — the registry total is 69 with the §16 addenda) and 24-event table are the **frozen contract** for the v1 migration. If the migration introduces new commands (e.g., `tray_click` for §6.5 — already added; or `notification_clicked` for toast interactions), they MUST be:
1. Added to `_COMMAND_REGISTRY` in `ipc_server.py` with a corresponding `_handle_<cmd>` mixin in `handlers/*`.
2. Documented in this ADR as an addendum (not a quiet addition).
3. Validated by `_validate_dict_payload` with an explicit schema.
4. Tested in `tests/test_ipc_dispatch_errors.py` (or a new test file).

Do NOT silently add commands/events during implementation — every addition widens the wire contract and must be tracked.

#### §16 addendum 2026-08-14 — prewarm IPC surface (retirement + restoration)

- **Retired (plan-runtime-pack-split.md §6.2 P-1):** `run_prewarm` — the standalone-prewarm subprocess (`prewarm-<triple>.exe`, `process_tracker.py`, sentinel/PID machinery) was deleted; start/stop is now the `fast_startup` toggle gating the worker warm phase. `run_prewarm` stays OUT of the registry.
- **Restored (plan-runtime-pack-split.md §6.3 addendum — user-facing feature re-opened):** `get_prewarm_status` + `open_prewarm_log` restored verbatim from commit 5a319872 (Settings → About "Cache Status" card). Adaptations: status reads the worker status file `prewarm_status.json` (written by the worker warm phase) instead of the deleted sentinel; `open_prewarm_log` opens `worker.log`. `EXPECTED_COMMANDS` grew 63 → 65; registry 67 → 69 (TS 67, Rust 65).

---

### Phase 0 validation gate (concrete, per platform)

**Phase 0-W (Windows) — required before Phase 1-W:**
- [ ] Nuitka exe builds from `python-build-standalone` (x86_64-pc-windows-msvc).
- [ ] `externalBin` sidecar spawns via Tauri; Rust reads `server_started` JSON from sidecar `stdout`, connects WS to the reported port.
- [ ] HMAC handshake: wrong token rejected; correct token accepted.
- [ ] **`faster-whisper` `WhisperModel("tiny")` loads + transcribes a WAV inside the Nuitka exe** (proves CTranslate2/DLLs/models).
- [ ] `enigo` types text into Notepad; clipboard+Ctrl+V path verified.
- [ ] `tauri-plugin-notification` shows a toast.
- [ ] Cooperative `{"type":"shutdown"}` exits cleanly; `kill_children` cleans on hard kill.
- [ ] Prewarm exe registered as a `LogonTrigger` scheduled task (via `resolve_prewarm_exe`) and warms cache.
- [ ] Native `windows-key-listener.exe` still spawns from the sidecar and toggles dictation.

**Phase 0-M (macOS) — required before Phase 1-M, on BOTH Apple Silicon and Intel:**
- [ ] Nuitka exe builds from `python-build-standalone` (aarch64-apple-darwin AND x86_64-apple-darwin).
- [ ] `externalBin` sidecar spawns via Tauri on both archs; Rust reads `server_started` JSON, connects WS.
- [ ] HMAC handshake works on both archs.
- [ ] `faster-whisper` `WhisperModel("tiny")` loads + transcribes inside both Nuitka exes.
- [ ] `enigo` types text into TextEdit on both archs.
- [ ] `tauri-plugin-notification` posts a notification (after `UNUserNotificationCenter` authorization).
- [ ] Cooperative `{"type":"shutdown"}` exits cleanly; `kill_children` cleans on hard kill.
- [ ] Prewarm exe registered as a LaunchAgent (via `resolve_prewarm_exe`) and warms cache.
- [ ] Native `macos-key-listener` (Swift) still spawns from the sidecar on both archs and toggles dictation. Accessibility permission flow (ADR-0008 Gap 2) works end-to-end.
- [ ] Codesign + notarize + staple the `.app` bundle on both archs.

**Phase 0-L (Linux) — required before Phase 1-L, on BOTH X11 and Wayland, on x86_64 (aarch64 may follow):**
- [ ] Nuitka exe builds from `python-build-standalone` (x86_64-unknown-linux-gnu, glibc 2.35 baseline).
- [ ] `externalBin` sidecar spawns via Tauri on X11 and Wayland; Rust reads `server_started` JSON, connects WS.
- [ ] HMAC handshake works on both.
- [ ] `faster-whisper` `WhisperModel("tiny")` loads + transcribes inside the Nuitka exe.
- [ ] `enigo` types text into `gnome-text-editor` on X11. On Wayland, `enigo.text()` is expected to fail — confirm the clipboard + `Ctrl+V` fallback works.
- [ ] `tauri-plugin-notification` posts a notification via libnotify on both X11 and Wayland.
- [ ] Cooperative `{"type":"shutdown"}` exits cleanly; `kill_children` cleans on hard kill.
- [ ] Prewarm exe registered as a systemd user timer (via `resolve_prewarm_exe`) and warms cache.
- [ ] Native `linux-key-listener` (evdev) still spawns from the sidecar on both X11 and Wayland and toggles dictation. `input` group membership is set by the existing `scripts/linux/postinst`.
- [ ] `.deb` and `.rpm` build with the existing `postinst` / `prerm` scripts and the udev rule is installed.
- [ ] AppImage builds and runs on Wayland (test on Fedora 40).

---

### Wins (keep)

- **One app / one icon per platform.** Tauri host + sidecar bundle into one app, installed/launched/stopped together. The user launches **one app** — directly addresses complaint (B) as "one thing to open", not "one OS process". Process count: today's 3 (Electron + Python + prewarm) → 2 (Tauri app + prewarm).
- **No hand-rolled launcher.** Tauri owns the Python lifecycle; the `electron_launcher.py` (318 lines) + `autostart_launcher.py` (801 lines) relay behind complaint (A) is removed. (Note: the Rust↔sidecar bridge is still a thin IPC layer — complaint (A) is addressed by removing Electron's `ipcMain`/`contextBridge` middleware, replaced by a single Tauri `invoke`→WebSocket path.)
- **No UI freeze.** The sidecar owns its own GIL, so continuous mic capture + inference never block the UI — matches today's smooth behavior.
- **Crash isolation possible (supervisor).** A speech-engine crash can be recovered without killing the whole app — an upgrade over today's whole-app restart.
- **Smaller shell.** Tauri exe ~2–10 MB using system WebView (WebView2 / WKWebView / webkit2gtk), vs Electron's ~100 MB+ bundled Chromium.
- **Python stays Python.** No ML rewrite; the existing backend is bundled as a sidecar (Nuitka-compiled).
- **Cross-platform parity preserved.** The native hotkey binaries (Win/macOS/Linux), the platform-specific autostart, the platform-specific prewarm schedulers, the Linux udev/polkit scripts, and the macOS Accessibility flow all stay unchanged — the migration does not regress any feature in the `PLATFORM_STATUS.md` matrix.

### Costs (documented, with mitigations)

- **Installer size:** Python + CTranslate2 + model adds ~400 MB–1 GB. Mitigation: this is model/data weight, comparable to what the app already ships; far less than Electron + Chromium overhead overall.
- **Startup latency:** 2–5 s cold sidecar start. Mitigation: prewarm file-cache warming + background load. The existing `prewarm/` package + `prewarm_scheduler_posix.py` + `task_scheduler.py` already handle this — they keep working post-migration.
- **Multiple processes in Task Manager** (app + sidecar + prewarm): this is expected and honest — the migration does NOT yield a single OS process. Mitigated by Tauri-managed lifecycle + `single-instance`; users perceive one app. Do not promise "one process" to the user.
- **Lifecycle/PID bugs** (the child Python process must close cleanly or it lingers as a zombie / blocks reinstall): mitigated by four concrete measures, all to be applied:
   1. **Nuitka single-exe** (not PyInstaller `--onedir`/`--onefile`) — a clean native `python-sidecar-<triple>[.exe]` built from `python-build-standalone` → no PyInstaller bootloader-child confusion, simpler process tree, fewer antivirus false positives.
   2. **`python-build-standalone`** — a clean prebuilt Python as the Nuitka target → standard native loading, no embedded-PE contradiction.
   3. **`kill_children`** — Tauri recursively kills the whole child process tree on exit → no zombies left behind.
   4. **Cooperative shutdown over WebSocket** — Rust sends `{"type":"shutdown"}`; sidecar releases the mic and exits gracefully, rather than being force-killed. `kill_children` is backstop only.
- **Webview consistency:** WebView2 (Win) vs WKWebView (macOS) vs webkit2gtk (Linux) — minor CSS/API guardrails. webkit2gtk lags Chromium by 1–2 years; audit the React UI for modern CSS/JS features (see Phase 3).
- **Per-platform Nuitka build complexity:** six target triples × one Nuitka build each = six CI jobs. Each job takes ~5–15 minutes. Mitigation: cache Nuitka build artifacts in CI; only rebuild when `voice_typer/server/` changes.
- **macOS notarization friction:** notarization adds ~5–10 minutes per arch to the release pipeline + requires Apple Developer account + careful entitlement management. Mitigation: reuse the existing `MAC_SIGNING_IDENTITY` + `notarize: true` CI config from `electron-builder.yml`.
- **Wayland paste UX regression:** short-text injection via `enigo.text()` does not work on Wayland; the clipboard + `Ctrl+V` fallback replaces the user's clipboard temporarily. Mitigation: `clipboard_snapshot.py` borrow/restore logic (ADR-0012) preserves the original clipboard contents.

### Reversibility

Electron code is untouched throughout the migration. The Tauri + Sidecar build is strictly additive. At any phase the Electron app remains the shippable fallback **per platform**; cutover is a packaging/default switch per OS. No data, config, or model loss on revert. **Reverting one platform does not revert the others** — Windows can ship Tauri while macOS still ships Electron, and the two are independently revertible.

---

## Consequences

The migration's consequences are itemized below as the "What stays / what moves / what is removed" scope boundary, followed by the "Risks / Open Questions" section. Positive consequences: leaner host shell (~2–10 MB Tauri exe vs ~100 MB+ Electron + Chromium), freeze-free UI (sidecar owns its own GIL), crash-isolation via a Rust supervisor, no hand-rolled launcher relay (`electron_launcher.py` removed on the Tauri path), and the user perceives one application (one icon/install) despite the multi-process runtime. Negative consequences: installer size grows by Python + CTranslate2 + model weight (~400 MB–1 GB), 2–5 s cold sidecar startup latency (mitigated by prewarm), multiple processes visible in Task Manager, lifecycle/PID cleanup complexity (mitigated by `kill_children` + cooperative WebSocket shutdown), and WebView2/WKWebView/webkit2gtk rendering differences (minor CSS/API guardrails). Neutral: per-platform incremental cutover (Windows → macOS → Linux), each gated on its own Phase 0 spike; Electron code is retained intact throughout as a reversible fallback; cutover is a packaging/default switch, not a destructive change.

## What stays / what moves / what is removed

To prevent an implementer from accidentally touching the wrong layer, here is the explicit scope boundary.

### Stays in the Python sidecar (DO NOT REWRITE)

These modules / behaviors are unchanged by the migration. They live in the Python sidecar exactly as they do today. The migration only changes the *shell* (Electron → Tauri) and the *transport* (TCP → WebSocket).

- `ipc_server.py` — dispatch layer (`_COMMAND_REGISTRY`, `_dispatch`, `_validate_dict_payload`). The listen/accept loop changes (TCP → WS server), but the dispatch + handler invocation is unchanged.
- `event_bus.py` — the publish/subscribe event bus. Unchanged.
- `handlers/*` — all 69 command handlers. Unchanged.
- `app.py`, `service.py` — the `VoiceTyperApp` and `VoiceTyperService` domain layer. Unchanged.
- `recording/` package, `recording_controller.py`, `streaming.py`, `dictation_pipeline.py`, `transcription.py` — audio capture + ASR pipeline. Unchanged.
- `audio_processor.py`, `audio_filters/*`, `audio_chain_builder.py`, `audio_presets.py` — audio filter chain (ADR-0009, the real one). Unchanged.
- `vad.py`, `silero_vad.onnx` — voice activity detection. **Changed by the ONNX migration (ADR-0005, `PLAN_ONNX_INTEGRATION.md` §2):** `vad.py` now uses an `onnxruntime.InferenceSession` against the bundled `silero_vad.onnx` (replacing the legacy `torch.jit.load` + `silero_vad.jit` path). The hidden-state buffer is threaded across calls (`_state` numpy array, not torch tensors) so streaming chunk detection preserves context. The legacy `silero_vad.jit` artifact and the `--module-parameter=torch-disable-jit=no` Nuitka flag are retired at Phase 1c (see `plan-runtime-pack-split.md` §3.3).
- `model_manager.py`, `model_registry.py`, `asr_registry.py`, `asr_setup.py`, `parakeet_engine.py`, `qwen_engine.py`, `cloud_engines.py` — ASR engine management. Unchanged by the Electron→Tauri migration. **Subsequent ONNX migration:** `parakeet_engine.py` is rewritten (Phase 1b, `PLAN_ONNX_INTEGRATION.md` §3) from `transformers + torch` to `onnx_asr.Model(...)` (ORT backend). `qwen_engine.py` still uses `transformers + torch` until Phase 1d (deferred — see `PLAN_ONNX_INTEGRATION.md` §4). `cloud_engines.py` and `llm_polish.py` are unaffected (zero torch/transformers/onnxruntime imports — verified).
- `config.py`, `config_validators.py`, `_paths.py` — config + path resolution. Unchanged.
- `history_db.py` — SQLite WAL history. Unchanged.
- `crash_recovery.py`, `crash_handler.py`, `duck_crash_recovery.py` — crash recovery. Unchanged.
- `clipboard/` package (`manager.py` + `linux.py`/`windows.py` platform branches + sibling `clipboard_snapshot.py` / `clipboard_target_safety.py`) — clipboard borrow/restore logic (ADR-0006, ADR-0012). **The paste *transport* moves to Rust (`enigo` + `tauri-plugin-clipboard-manager`), but the clipboard *management* (snapshot, restore, security checks) stays in Python.** The sidecar emits a `paste_text` event with the text; Rust receives it and performs the actual paste.
- `text_cleanup.py`, `hallucination.py`, `vocabulary.py`, `vocabulary_automation.py`, `templates.py`, `llm_polish.py`, `ai_enhancement.py` — text post-processing. Unchanged.
- `hotkeys/` package, `hotkey_dispatcher.py`, `hotkey_spec.py`, `native_hotkeys/` package, `native/*` — global hotkey subsystem (ADR-0007, ADR-0008). **Stays in Python; spawns the native binaries as today.** Tauri does NOT touch this.
- `tray.py`, `tray_menu.py`, `tray_icon.py`, `tray_models.py`, `tray_hotkey.py`, `tray_window.py` — tray menu logic. **Stays in Python; emits `tray_menu` events to Rust, which renders them via `tauri-plugin-tray`.**
- `volume_ducker.py`, `volume_backends/` package (PVT-24 split — `windows.py` / `macos.py` / `linux.py`), `volume_backend_base.py` — volume ducking. Unchanged.
- `level_monitor.py`, `microphone_watcher.py`, `microphone_watcher_coreaudio.py`, `microphone_test.py` — microphone subsystem. Unchanged.
- `permissions.py`, `security.py`, `_secrets.py`, `_security_attributes.py`, `log_rate_limit.py` — security + rate limiting. Unchanged.
- `prewarm/` package, `task_scheduler.py`, `prewarm_scheduler_posix.py` — prewarm + scheduling (ADR-0011). **Stays in Python; `resolve_prewarm_exe()` is the only addition.**
- `autostart_launcher.py`, `electron_launcher.py`, `_electron_build.py` — **REMOVED on the Tauri path** (the Tauri host replaces them). Stays for the Electron fallback path.
- `startup_sequence.py`, `startup_tasks.py`, `thread_registry.py`, `log.py`, `branding.py`, `container_detect.py`, `_lazy_import.py`, `providers.py`, `server_platform/` package, `platform_utils.py` — infrastructure. Unchanged.

### Moves to Rust (Tauri host)

- WebSocket client (connects to the sidecar's WS server).
- `dispatch` command bridge (UI `invoke` → WS frame → response).
- Event subscriber (subscribes to `event_bus` via the WS, re-emits as Tauri events).
- supervisor (respawn sidecar on crash, backoff, full-app relaunch).
- Sidecar lifecycle (spawn via `externalBin`, `kill_children` backstop, cooperative `{"type":"shutdown"}`).
- HMAC token generation + rotation.
- `server_started` JSON parsing from sidecar stdout.
- `bubble_level` coalescing (60 Hz → 30 Hz).
- Paste transport (`enigo.text()` for short, `tauri-plugin-clipboard-manager` + `Ctrl+V`/`Cmd+V` for long).
- Toast (`tauri-plugin-notification`).
- Tray rendering (`tauri-plugin-tray` — the menu *structure* comes from Python).
- Single-instance check (`tauri-plugin-single-instance`).
- Window lifecycle (show/hide/focus the main window + bubble window).
- `VOICE_TYPER_IPC_TOKEN` env var injection at sidecar spawn.
- `VOICE_TYPER_PREWARM_EXE` env var injection (points at `resourceDir/prewarm-<triple>`).
- `VOICE_TYPER_NATIVE_DIR` env var injection (points at `resourceDir/native/`).
- Electron `userData` → Tauri `<config_dir>` one-time migration (before sidecar spawn).

### Moves to Tauri plugins

- Tray icon: `pystray` → `tauri-plugin-tray`.
- Toast: `electron_notification` event + `Shell_NotifyIcon` / `NSUserNotificationCenter` / libnotify → `tauri-plugin-notification`.
- Clipboard: `pyperclip` (Python) for the *paste transport* → `tauri-plugin-clipboard-manager` (Rust). `pyperclip` stays in Python for snapshot/restore.
- Single-instance: Win32 mutex + POSIX lockfile → `tauri-plugin-single-instance`.

### Removed on the Tauri path (stays on Electron fallback)

- `electron_launcher.py` (318 lines) — the Electron spawn relay.
- `autostart_launcher.py` (801 lines) — the Electron-aware autostart launcher.
- `_electron_build.py` — Electron build helpers.
- `client/src/main/index.ts` (209 lines, plus sibling modules under `client/src/main/{windows,python,ipc,bootstrap,state}/`) — the Electron main process, refactored from the historical monolithic `index.ts` into multiple submodules. Replaced by Rust `main.rs` + Tauri plugins.
- `client/src/preload/index.ts`, `client/src/preload/bubble.ts` — Electron preload bridges. Replaced by Tauri `invoke`.
- `client/electron-builder.yml` — the Electron builder config. Replaced by Tauri `tauri.conf.json`.
- `client/electron.vite.config.ts`, `client/electron.vite.main.ts`, `client/electron.vite.renderer.ts` — Electron-specific Vite configs. Replaced by a single Vite config for the Tauri WebView.
- `client/csp-plugin.ts` — Electron CSP enforcer. Replaced by `tauri.conf.json` `app.security.csp`.
- `scripts/build/installer.iss` — Inno Setup script (no longer present in the source tree; was the legacy Windows installer). Replaced by Tauri's NSIS bundler.
- `scripts/build/voice-typer.manifest` — Windows app manifest. Tauri generates its own.
- The `_handle_heartbeat` path in `ipc_server.py` — disabled via `TAURI_SIDECAR=1` env var (not deleted, so the Electron fallback still works).
- The `VoiceTyperSingleInstance` Win32 mutex in `app.py` (locate by `class VoiceTyperSingleInstance`) — disabled via `TAURI_SIDECAR=1` (Tauri's `single-instance` plugin replaces it).

### Kept verbatim (NOT Tauri-specific — reuse as-is)

- `scripts/build/voice-typer.spec` — PyInstaller spec (fallback if Nuitka fails on a target).
- `scripts/build/compile_native.sh` + `compile_native.ps1` — native hotkey binary build.
- `scripts/linux/postinst`, `prerm`, `postinst.rpm`, `prerm.rpm` — Linux package scripts (udev + input group + Caps Lock).
- `scripts/linux/99-voice-typer.rules` — udev rule.
- `scripts/linux/00-voice-typer-capslock.conf` — X11 Caps Lock config.
- `scripts/linux/voice-typer.polkit` — polkit policy for AppImage `pkexec`.
- `scripts/linux/install_permissions.py`, `uninstall_permissions.py` — Linux permission setup.
- `voice_typer/server/native/*` — native hotkey binaries source.
- `voice_typer/stubs/*` — type stubs for platform-only deps.

---

## Risks / Open Questions

1. **Spike must pass on each platform's Phase 0** before any full build — the make-or-break step. Windows first, then macOS (both archs), then Linux (X11 + Wayland).
2. **UI port effort (Phase 3)** is the largest unknown; mitigated by keeping React components shell-agnostic.
3. **Transport bridge (Phase 2)** must preserve all current handler behaviors and the event flow exactly — the **Sidecar→UI Event Table** above enumerates every event so nothing is missed. The per-connection rate limiter (ADR-0019) must be ported to the WS path.
4. **Recovery supervisor (supervisor)** must be implemented before cutover so a sidecar crash does not strand the user.
5. **macOS notarization + entitlements** for the sidecar (which uses `pyobjc` + native dylibs) is the highest-risk unknown — Nuitka + `pyobjc` + hardened runtime + notarization has historical friction. Mitigation: Phase 0-M explicitly validates `faster-whisper` loads inside the notarized sidecar on both archs.
6. **Wayland paste UX regression** — `enigo.text()` does not work on Wayland. Mitigation: clipboard + `Ctrl+V` fallback with `clipboard_snapshot.py` borrow/restore. User-facing doc must explain the clipboard-replacement behavior.
7. **Linux ARM64 (aarch64-unknown-linux-gnu)** — the `python-build-standalone` aarch64 Linux builds + CTranslate2 aarch64 wheels + glibc pinning are less tested than x86_64. Mitigation: defer aarch64 Linux to a follow-up if Phase 0-L on x86_64 passes; document the deferral.
8. **WebView2 / WKWebView / webkit2gtk CSS+JS differences** — the React UI was built for Chromium (Electron). Audit for webkit2gtk-specific quirks (notably: `backdrop-filter` is partial in webkit2gtk; `:has()` is supported only in webkit2gtk ≥ 2.40; `structuredClone` requires webkit2gtk ≥ 2.36). Mitigation: add `@supports` guards or polyfills.
9. **Auto-update is out of scope for v1** (see §15). Users upgrade by downloading the new release manually, same as today. Track auto-update as a separate follow-up ADR.
10. **Tauri v2 maturity** — Tauri v2 was released in late 2024; some plugins (`tray`, `updater`) have had bugs in early 2.x releases. Mitigation: pin Tauri v2 to a known-stable minor version; monitor the Tauri issue tracker for sidecar / `externalBin` regressions.

### Resolved in planning (no longer blocking)

- **Sidecar freeze tool:** Nuitka (not PyInstaller `--onedir`). Single executable per target triple via `python-build-standalone`. PyInstaller `voice-typer.spec` retained as fallback.
- **Paste/keystroke injection crate:** `enigo` (not `rdev`) + `tauri-plugin-clipboard-manager` for the long-text path.
- **Transport:** WebSocket only (not WebSocket/HTTP ambiguity); ephemeral port + HMAC token (ADR-0014 reuse).
- **Cooperative shutdown:** over WebSocket (not stdin/stdout).
- **Sidecar→UI event table:** extracted above from `event_bus.publish` / `_push_event_now` call sites — 24 events mapped, payloads carried 1:1 (see §2 IPC-2 reconciliation).
- **Global hotkey:** KEEP native binaries (do NOT switch to `tauri-plugin-global-shortcut`). Tauri plugin regresses key suppression, modifier-only hotkeys, Fn/Globe key, and Wayland support.
- **Tray:** port to `tauri-plugin-tray`, but menu structure stays in Python (emit `tray_menu` event, Rust renders).
- **Prewarm scheduling:** stays platform-specific (Windows Task Scheduler, macOS LaunchAgent, Linux systemd user timer) via the existing `task_scheduler.py` + `prewarm_scheduler_posix.py`. Only the prewarm *binary* changes (`pythonw -m voice_typer.server.prewarm` → `prewarm-<triple>[.exe]` via `resolve_prewarm_exe`).
- **Heartbeat:** removed on Tauri path (supervisor replaces ADR-0018). Stays on Electron fallback path. Disabled via `TAURI_SIDECAR=1` env var.
- **Auto-update:** out of scope for v1 (not implemented today).

---

## References

### Voice Typer ADRs (verified against `docs/adr/` on 2026-07-16)

- **ADR-0000** — ADR process.
- **ADR-0001** — Record architecture decisions.
- **ADR-0002** — Electron migration (the *first* Electron migration, from PyInstaller-only to Electron + Python).
- **ADR-0003** — Electron + Python Architecture (Accepted). **The current architecture, retained as the reversible fallback.** (The previous version of this ADR mis-cited this as "ADR-0001".)
- **ADR-0004** — IPC protocol.
- **ADR-0005** — Silero VAD.
- **ADR-0006** — Clipboard security.
- **ADR-0007** — Native hotkey architecture. **Source of truth for the native hotkey binaries — this ADR explicitly preserves them.**
- **ADR-0008** — Zero-command hotkey architecture. **Source of truth for the macOS Accessibility + Linux udev + runtime fallback chain. This ADR preserves all four gaps closed by ADR-0008.**
- **ADR-0009** — Audio Filter Chain Architecture (NOT prewarm — the previous version of this ADR mis-cited this as the prewarm ADR).
- **ADR-0010** — Dependency Injection Boundary (`providers.py`, `AppProtocol`, `ServiceProtocol`).
- **ADR-0011** — Prewarm & Autostart Architecture. **The actual prewarm ADR. This ADR preserves ADR-0011 — prewarm stays a separate boot helper, scheduled per-platform by `task_scheduler.py` (Windows) and `prewarm_scheduler_posix.py` (macOS + Linux).**
- **ADR-0012** — Clipboard borrow/restore architecture. Preserved (clipboard_snapshot.py).
- **ADR-0013** — Prior version of this desktop-runtime-migration ADR. **This document supersedes the analysis in ADR-0013 + the prior 0020 draft.**
- **ADR-0014** — TCP IPC session-token auth. **Source of the HMAC token scheme — this ADR reuses `VOICE_TYPER_IPC_TOKEN` verbatim.**
- **ADR-0015** — Electron command allowlist.
- **ADR-0016** — Granular consent flags.
- **ADR-0017** — Cloud URL allowlist + HTTPS.
- **ADR-0018** — Electron-alive heartbeat watchdog. **Removed on the Tauri path (supervisor replaces it). Stays on the Electron fallback path. Disabled via `TAURI_SIDECAR=1` env var.**
- **ADR-0019** — Per-connection rate limiter. **Must be ported from the TCP accept path to the WS accept path.**

### External references

- Tauri v2 sidecar guide (`v2.tauri.app/develop/sidecar`) — first-class `externalBin` sidecar feature.
- Tauri v2 capabilities guide (`v2.tauri.app/security/capabilities`) — mandatory permission whitelisting.
- Tauri discussion #1645 (`github.com/tauri-apps/tauri/discussions/1645`) — sidecar trade-offs.
- `python-build-standalone` (by Gregory Szorc) — clean pre-built Python for sidecar bundling. Per-target-triple builds available for Windows x86_64/aarch64, macOS x86_64/aarch64, Linux x86_64/aarch64.
- Nuitka user manual (`nuitka.net/user-documentation/user-manual.html`) — cross-platform flags reference.
- `enigo` crate (`docs.rs/enigo`) — cross-platform keystroke/mouse injection.
- `tauri-plugin-notification`, `tauri-plugin-clipboard-manager`, `tauri-plugin-single-instance`, `tauri-plugin-tray` — Tauri v2 plugin docs.
- `kill_children` + `single-instance` plugin — Tauri lifecycle/cleanup correctness.
- Apple Notarization guide (`developer.apple.com/documentation/security/notarizing_macos_software_before_distribution`).
- Apple Developer ID signing (`developer.apple.com/developer-id/`).
- Linux udev rules documentation (`www.freedesktop.org/software/systemd/man/udev.html`).
- systemd user units (`www.freedesktop.org/software/systemd/man/systemd.unit.html`).
- macOS LaunchAgent plist reference (`developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html`).

### Voice Typer source files (locate symbols by name; line numbers drift)

- `voice_typer/server/ipc_server.py` (see the file directly — earlier drafts of this ADR disagreed on the line count, and the module has continued to grow since) — `_COMMAND_REGISTRY` (locate by the `_COMMAND_REGISTRY = {` assignment near the top of the file; 69 commands — see §2 IPC-1 reconciliation), `_validate_dict_payload` (locate by `def _validate_dict_payload` in `voice_typer/server/ipc/validation.py` — extracted from `ipc_server.py` during the Phase 4.5 split), `_push_event_now` (locate by `def _push_event_now`), `push` (locate by `def push`), the `ready` emit (locate by the `{"type": "ready"}` `IPCServer.push` call site — also re-emitted from `sidecar_ws.py`), `_handle_heartbeat` (locate by `def _handle_heartbeat` — resident on `IPCServer`, not in `handlers/`), `_handle_relaunch_ack` (locate by `def _handle_relaunch_ack` — resident on `IPCServer`, not in `handlers/`).
- `voice_typer/server/event_bus.py` (see the file directly) — the publish/subscribe singleton. The module docstring's "Canonical event catalogue" section lists all 24 event names (IPC-2 reconciliation, 2026-07-18).
- `voice_typer/server/electron_launcher.py` (318 lines) — `launch_electron_frontend` (REMOVED on Tauri path).
- `voice_typer/server/autostart_launcher.py` (801 lines) — `launch` (REMOVED on Tauri path).
- `voice_typer/server/prewarm/` package — `run` (in `prewarm/pipeline.py`), `_already_warmed` and `_mark_warmed` (both in `prewarm/paths.py`). (The previous ADR's `prewarm.py:17` reference was wrong on two counts: line 17 was part of the module docstring, AND the monolithic `prewarm.py` has since been decomposed into the `prewarm/` package — locate the entrypoint by `def run` in `prewarm/pipeline.py`, not by line number.)
- `voice_typer/server/task_scheduler.py` (see the file directly) — Windows Task Scheduler registration (`_prewarm_command` — locate by `def _prewarm_command`; `_build_task_xml`; `_register_prewarm_registry`).
- `voice_typer/server/prewarm_scheduler_posix.py` (431 lines) — macOS LaunchAgent + Linux systemd user timer registration. **The previous ADR's claim that prewarm is Windows-only was wrong — this module already exists.**
- `voice_typer/server/server_platform/` package — `enable_autostart`/`disable_autostart`/`is_autostart_enabled` cross-platform autostart (Win Task Scheduler + HKCU Run, macOS LaunchAgent, Linux `.desktop`). (The historical `server_platform.py` was split into a package — `autostart.py`, `autostart_{linux,macos,windows}.py`, `microphone_list.py`, `desktop_shortcut.py`, `platform_flags.py`, `remote_session.py`, `volume_factory.py`.)
- `voice_typer/server/_paths.py` (129 lines) — `config_dir`, `pid_file`, `prewarm_sentinel`, `prewarm_log`, `prewarm_launchagent_log`, `autostart_log`, `venv_pythonw`. **Single source of truth for platform-aware paths.**
- `voice_typer/server/platform_utils.py` (141 lines) — `is_windows`, `is_macos`, `is_linux`, `platform_name`, `_set_windows_process_metadata`.
- `voice_typer/server/clipboard/` package (PVT-23 split — see `clipboard/manager.py` for `ClipboardManager`, plus `clipboard/linux.py` / `clipboard/windows.py` platform branches and sibling `clipboard_snapshot.py` / `clipboard_target_safety.py`) — Win32 `SendInput` paste path, terminal-detection (`_TERMINAL_PROCESS_NAMES`), UIPI handling.
- `voice_typer/server/hotkeys/` package — `create_hotkey_backend` factory, native + fallback backends. (The historical `hotkeys.py` was split into a package — `base.py`, `factory.py`, `native_adapter.py`, `pynput_backend.py`, `wayland.py`, `windows_native.py`, `win32_vk.py`.)
- `voice_typer/server/native_hotkeys/` package — `get_native_binary_path`, native binary subprocess management. (The historical `native_hotkeys.py` was split into a package — `base.py`, `binary_path.py`, `factory.py`, `linux_backend.py`, `mac_backend.py`, `modifiers.py`, `recorder.py`, `spec_parser.py`, `windows_backend.py`.)
- `voice_typer/server/native/{windows-key-listener.c, macos-key-listener.swift, linux-key-listener.c}` — the three native hotkey binaries (preserved by this ADR).
- `voice_typer/server/app.py` (see the file directly) — `VoiceTyperApp`. The `VoiceTyperSingleInstance` Win32 mutex (`"Local\\VoiceTyperSingleInstance"` — locate by `class VoiceTyperSingleInstance`) is disabled via `TAURI_SIDECAR=1` (single-instance enforcement is handled by Tauri's `tauri-plugin-single-instance` on the Tauri path). **Note: an earlier version of this ADR claimed `app.py:2086` for `VoiceTyperSingleInstance` — that was wrong; line numbers drift, so the class is located by name, not line number.**
- `voice_typer/client/src/main/index.ts` (209 lines, plus sibling modules under `voice_typer/client/src/main/{windows,python,ipc}/`) — Electron main process, refactored from the historical monolithic `index.ts` into multiple submodules (REMOVED on Tauri path).
- `voice_typer/client/electron-builder.yml` — Electron builder config (Windows NSIS + macOS DMG x64/arm64 + Linux AppImage/deb/rpm with notarization). **Source of signing-config reuse for the Tauri build.**
- `scripts/build/voice-typer.spec` (382 lines) — PyInstaller spec (fallback for Nuitka).
- `scripts/build/compile_native.sh` (270 lines) + `scripts/build/compile_native.ps1` — native hotkey binary build.
- `scripts/build/installer.iss` — Inno Setup script (no longer present in the source tree; was the legacy Windows installer script before the Tauri migration removed it).
- `scripts/build/voice-typer.manifest` — Windows app manifest (REMOVED on Tauri path — Tauri generates its own).
- `scripts/linux/{postinst,prerm,postinst.rpm,prerm.rpm,99-voice-typer.rules,00-voice-typer-capslock.conf,voice-typer.polkit,install_permissions.py,uninstall_permissions.py}` — Linux packaging + permission scripts (REUSED verbatim by Tauri .deb/.rpm).
- `docs/PLATFORM_STATUS.md` — the 30-row feature × OS matrix. **Authoritative for what must not regress.**
- `docs/ARCHITECTURE.md` — the current (Electron) architecture overview.
- `docs/auto-update-feature.md` — design-only (NOT IMPLEMENTED). **Do not assume auto-update works today.**
- `.github/workflows/build.yml` — CI matrix (`windows-2022`, `ubuntu-22.04`, `macos-13`, `macos-14`).

### Errata in the previous version of this ADR (fixed here)

1. **"ADR-0009 (Prewarm & Autostart Architecture)"** — wrong. ADR-0009 is the Audio Filter Chain Architecture. The actual prewarm ADR is **ADR-0011**. Fixed throughout.
2. **"ADR-0001 (Electron + Python Architecture)"** — wrong. ADR-0001 is "Record architecture decisions". The Electron + Python ADR is **ADR-0003**. Fixed.
3. **`prewarm.py:17`** — wrong line. Line 17 was part of the module docstring, and `prewarm.py` has since been decomposed into the `prewarm/` package (entrypoint `run` is now in `prewarm/pipeline.py`). Fixed to use the symbol name + the package layout.
4. **"Prewarm is Windows-only" (implied)** — wrong. `prewarm_scheduler_posix.py` already implements macOS LaunchAgent + Linux systemd user timer scheduling. Fixed in §5 + the Capability Matrix.
5. **"Port global hotkey to `tauri-plugin-global-shortcut`"** — wrong. The Tauri plugin regresses key suppression, modifier-only hotkeys, Fn/Globe key, and Wayland support. Fixed in §6.4 — keep the native binaries.
6. **Windows-only Nuitka flags** — `--windows-disable-console` is Windows-only. macOS uses `--macos-app-mode=background`; Linux needs no equivalent. Fixed in §4.3 + §4.4.
7. **Windows-only code signing** — `signtool` + Authenticode is Windows-only. macOS needs Developer ID + notarization + stapling; Linux is unsigned by default. Fixed in §13.
8. **Windows-only paste logic** — `AttachThreadInput` + `SetForegroundWindow` is Windows-only. macOS uses CGEvent (via `enigo`); Linux uses X11 XTest (via `enigo`, X11 only) or clipboard + `Ctrl+V` (Wayland fallback). Fixed in §6.2 + §6.6.
9. **Windows-only path resolution** — `%LOCALAPPDATA%` is Windows-only. The actual `_paths.config_dir()` uses `%APPDATA%` (Windows), `~/Library/Application Support` (macOS), `$XDG_DATA_HOME` (Linux). Fixed in §8.
10. **Windows-only prewarm scheduling** — "Windows Task Scheduler (`schtasks`)" is Windows-only. macOS uses LaunchAgent, Linux uses systemd user timer. Fixed in §5.
11. **Missing auto-update discussion** — the previous ADR did not mention auto-update at all. Today auto-update is NOT IMPLEMENTED (`docs/auto-update-feature.md`'s own header says so). Fixed in §15.
12. **Missing rate-limiter porting** — ADR-0019 (per-connection rate limiter) was not mentioned. The TCP-side limiter must be ported to the WS accept path. Fixed in §10.
13. **Missing ADR-0018 reconciliation** — the previous ADR said "remove heartbeat" but did not reference ADR-0018 (the heartbeat watchdog ADR) or explain how the Rust supervisor replaces it. Fixed in §2 + §10.
14. **Missing existing build assets** — `voice-typer.spec`, `installer.iss`, `compile_native.sh`, `electron-builder.yml`, `scripts/linux/*` were not referenced. Fixed in §4.5 + §13.3 + the "Kept verbatim" section.
15. **`externalBin` naming** — the previous ADR only mentioned `python-sidecar-x86_64-pc-windows-msvc.exe`. macOS + Linux + ARM need their own target triples. Fixed in §4.1 + §7.

---

*End of document. This is the cross-platform rewrite of ADR-0020, verified against `AbdallahIsDev/voice-typer` `main` on 2026-07-16. The previous Windows-only version is superseded.*
