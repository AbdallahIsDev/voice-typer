# ADR 0013: Desktop Runtime Migration to Tauri v2 + Python Sidecar

## Status

Superseded by [ADR-0020](0020-desktop-runtime-migration-analysis.md) — see ADR-0020 for the current authoritative migration analysis.

## Date

2026-07-13 (decision) — 2026-07-14 (updated to Sidecar-only, actionable migration plan)

## Context

Voice Typer today is **Electron (React UI) + a separate Python backend + a separate prewarm helper** — effectively **three processes**:

1. Electron main process (hosts the React UI).
2. `python -m voice_typer.server.ipc_server --port 9876` — the Python backend, spawned by `electron_launcher.py:13`, reached over a local TCP socket. This process does audio capture + model inference.
3. The `voice_typer/server/prewarm/` package (entry point `__main__.py`) — a standalone helper that warms the OS file cache at startup (kept intentionally separate; see ADR-0011).

This ADR adopts **Tauri v2 + a Python Sidecar** as the replacement desktop runtime, and records the ordered migration plan. The Python backend and React UI are kept substantially as-is; only the shell and the transport change.

**Scope clarification (important for the implementing agent):** the original reasons for exploring a runtime change were a dislike of the layered IPC middleware and a wish for a single process / one exe. **This chosen Sidecar approach meets neither:** the UI still reaches Python through a bridge (Tauri `invoke` + a localhost socket), and the result is still **multiple OS processes, not one**. Those original wishes are intentionally NOT achieved here; they are noted only so the rationale for the migration is not lost.

**Process model (post-migration):** the runtime is **multiple OS processes**, not one:
- Tauri host process (Rust shell + WebView2 renderer child),
- Python sidecar process (spawned & managed by Tauri),
- prewarm helper process (kept separate, per ADR-0011).

These present to the user as **one application** (one icon / one install / Tauri-managed lifecycle), but it is **not a single process**. The "single process" goal is traded away to keep the UI freeze-free and crash-isolated.

> **Plain English:** Today the app is three running programs (the window, the speech brain in Python, and a pre-load helper). We are moving the *window* from Electron to a smaller Tauri program, and bundling the speech brain *next to* it as a "sidecar" that Tauri starts and manages. The user still sees one app. We keep Electron working the whole time, so if the new version misbehaves we just ship Electron again — nothing is lost.

---

## Decision

**Adopt Tauri v2 + Python Sidecar as the desktop runtime, replacing Electron.** Keep the Python backend and React UI substantially as-is; only the shell + transport change.

**Rationale (why Sidecar, not embedding Python in the app):** embedding Python directly inside the Rust/Tauri process would put the speech engine in the *same* process as the UI. For a continuous realtime-audio app that reintroduces a Global Interpreter Lock (GIL) freeze risk on the audio path, adds fragile Windows native DLL/ABI linking, and prevents crash isolation (a speech-engine crash would kill the whole app). The sidecar pattern keeps the speech engine in its own managed process, so the UI never freezes, crashes are isolated, and Windows native loading stays standard.

**Migration is incremental and reversible.** Electron is NOT removed. We build Tauri + Sidecar *alongside* Electron, port the UI/components to Tauri's WebView2, implement the sidecar, then re-point the "wire" (UI → logic) from Electron→Python to Tauri→sidecar. At every phase the Electron app remains buildable, runnable, and shippable. Cutover is a packaging/default switch, not a destructive change.

Three mandatory architecture rules:

1. **Keep prewarm as a SEPARATE boot helper.** Do **not** merge it into the app. Prewarm remains a distinct, intentional boot-time process that warms the OS file cache. The post-migration runtime is **multiple OS processes** (Tauri host + Python sidecar + prewarm); it presents as one application but is not a single process. Preserves ADR-0011.
2. **Preserve the current streaming model.** Background chunking/streaming stays hidden from the user until dictation ends, then pastes at once. Unaffected by the runtime change.
3. **Migration must stay reversible.** Electron code is untouched; the Tauri build is additive. Ability to ship/switch back to Electron at any time, with zero loss.

> **Plain English (the three rules):** Rule 1 — the pre-load helper stays its own little program so the model stays ready in RAM; we only swap the *window* technology. Rule 2 — the way words are collected in the background and shown all at once does not change. Rule 3 — we never delete or break Electron; the new app is added next to it, and we can go back whenever we want.

---

## Target Architecture (post-migration)

```
One Tauri app (ONE icon / install — multiple OS processes, see Process Model note):
  ├─ WebView2 (React UI)          ← ported from Electron, same React components
  ├─ Rust shell (Tauri v2)
  └─ python-sidecar.exe           ← bundled externalBin, spawned & managed by Tauri
        UI → Tauri invoke / HTTP → Rust → localhost WebSocket → Python sidecar
        (sidecar runs faster-whisper / CTranslate2 — same Python backend as today)
Plus: the `voice_typer/server/prewarm/` package (separate boot helper, kept per ADR-0011)

> **Note:** although the sidecar and WebView2 renderer are drawn inside "One Tauri app", they are **separate OS processes**. This is one *application* (one icon/install), not one *process*.
```

The sidecar is a **normal Python program** (your existing `ipc_server` / `handlers/*` logic), compiled/bundled and launched by Tauri — no Python embedded in the Rust binary.

---

## Migration Plan (ordered)

### Phase 0 — Spike (prove before building)
- Decide and prove the sidecar **packaging mechanism** (see Implementation Specification §P1): freeze the Python backend into a **single executable** using `python-build-standalone` as the base interpreter + **Nuitka** (preferred) or PyInstaller `--onedir` (NOT `--onefile`), named `python-sidecar-x86_64-pc-windows-msvc.exe`, registered as a Tauri `externalBin`. (`externalBin` points at one exe with a target-triple suffix — never a folder.)
- Confirm on the user's Windows machine: sidecar spawns on app launch, **WebSocket** comms work (Rust proxies `invoke` ↔ sidecar; sidecar pushes events back over the same socket), sidecar auto-stops with the app, `kill_children` cleans the tree.
- Confirm `faster-whisper` / `CTranslate2` loads and transcribes inside the sidecar, with model paths resolved exactly as today (via `HF_HOME` / config dir env, independent of CWD).
- **Gate:** do not start Phase 1 until this passes.

### Phase 1 — Sidecar packaging
- Freeze the Python backend into a **single executable** (`python-sidecar-x86_64-pc-windows-msvc.exe`) registered as Tauri `externalBin`. Build with `python-build-standalone` (base interpreter) + Nuitka or PyInstaller `--onedir`; **avoid `--onefile`** (runtime unpack + bootloader-child PID/antivirus quirks).
- Code-sign the sidecar **separately** from the main exe (Windows SmartScreen flags an unsigned sidecar even if the main exe is signed); run with hidden console.
- Implement **cooperative shutdown over the WebSocket control channel**: the supervisor sends `{"type":"shutdown"}`; the sidecar releases the mic (`sounddevice`/`pyaudio` terminate) and acks before exiting. `kill_children` is the backstop for unclean exit. (Do NOT use stdin/stdout for lifecycle when transport is WebSocket — see §P2.)

### Phase 2 — Transport bridge
- Replace Electron's TCP IPC (`ipc_server --port 9876` + `electron_launcher` spawn) with a **localhost WebSocket** between Tauri (Rust) and the sidecar. **WebSocket is the single transport** (not HTTP/JSON-RPC): it is full-duplex, which the current server-push (`_push_event_now`) events require.
- **Rust is the only bridge.** UI calls `invoke('dispatch', {cmd, data})` → Rust forwards `{"type": cmd, "data": ...}` over the socket → sidecar dispatches via the existing `_COMMAND_REGISTRY` (`getattr(self, "_handle_<cmd>")`). Sidecar → UI events flow back over the same socket → Rust re-emits them as Tauri events (`app.emit(name, payload)`) the UI already subscribes to. The WebView never talks to Python directly (single trust boundary, no CORS).
- **JSON shape (carried from today's `ipc_server.py`):** request `{"type": <command>, "data": {...}}`; response `{"type": "result"|"error", "data": {...}, "code"?: <error_code>}`. Reuse `_validate_dict_payload` + error codes (`invalid_payload`, `missing_field`, `invalid_field`).
- **Port + auth:** Tauri allocates an **ephemeral port** (bind `127.0.0.1:0`, read back the OS-assigned port) — never a hardcoded `9876`. Pass port + an **HMAC token** to the sidecar via env (`VOICE_TYPER_IPC_PORT`, `VOICE_TYPER_IPC_TOKEN`), reusing today's token scheme.
- **Handler registry:** keep `ipc_server.py`'s dispatch; only the listen/accept loop changes (TCP → WebSocket server). Add `sidecar_main.py` entrypoint (or `--ws` flag). `handlers/*` mixins stay unchanged.
- **Event mapping:** enumerate every `_push_event_now` call site; map each to a Tauri event name the UI already `listen()`s to; document the table.

### Phase 3 — UI port to Tauri WebView2
- Move the React UI from the Electron renderer to the Tauri webview; replace `ipcMain`/`contextBridge` calls with Tauri `invoke`.
- Port tray + global hotkey + settings to Tauri plugins (`tray`, `global-shortcut`, `single-instance`). Keep the existing Windows Task Scheduler `BootTrigger` (`task_scheduler.py`) as the sole autostart mechanism for the app + prewarm; **do NOT enable the Tauri `autostart` plugin for the app** (avoids duplicate autostart entries).
- Keep the same React components — only the shell bridge changes.

### Phase 4 — Wire swap + recovery
- Re-point the "wire" (UI → logic) from Electron→Python to Tauri→sidecar. Keep the Electron build path intact and runnable in parallel.
- Implement **crash isolation** (tracked as supervisor in `.workspace/TASKS.md`): a Rust supervisor respawns the sidecar on unexpected exit, shows a "reconnecting…" state, and falls back to full-app relaunch if respawn fails repeatedly.
- Enable the `single-instance` plugin so only one app instance runs.

### Phase 5 — Validation & cutover
- Verify: one icon/install; UI never freezes (sidecar owns its own GIL); crash isolation works; prewarm still warms the cache; streaming unchanged; global hotkey + tray work.
- Keep the Electron code path intact until satisfied; then make Tauri the default shipping app. Revert at any time by shipping the Electron build.

---

## Implementation Specification (resolved decisions)

This section closes the contradictions and missing specs flagged in implementation review so two implementers build the same app. It is grounded in the actual codebase (`ipc_server.py`, `handlers/*`, `config.py`, `task_scheduler.py`).

### P1 — Sidecar packaging (review §1a)
- **One exe, not a folder.** Sidecar = a single `.exe` registered as Tauri `externalBin` with target-triple suffix: `python-sidecar-x86_64-pc-windows-msvc.exe`. `externalBin` cannot point at a directory.
- **Build stack:** base interpreter = `python-build-standalone` (a raw CPython distro — it has NO `--onedir` flag; it is the runtime we build against). Freeze the backend with **Nuitka** (preferred — single self-contained exe) or **PyInstaller `--onedir`** (folder whose main exe we name with the triple suffix). **Avoid `--onefile`** (runtime unpack + bootloader-child PID/AV quirks).
- **`tauri.conf.json` snippet:** `bundle: { externalBin: ["bin/python-sidecar-x86_64-pc-windows-msvc.exe"] }`. Sidecar launched via Tauri's sidecar API.
- **Resources:** only the Python runtime + `faster-whisper`/`CTranslate2` + app code are frozen in. Model files + HF cache stay outside the exe, resolved at runtime (see P2).

### P2 — Transport, port, auth, handler & event contract (review §1b, §1c, §2)
- **Transport = WebSocket, single choice.** Full-duplex required for server-push. Rust is the only bridge; WebView never talks to Python directly.
- **JSON shape (carried from `ipc_server.py`):** request `{"type": <command>, "data": {...}}` → response `{"type": "result"|"error", "data": {...}, "code"?: <error_code>}`. Reuse `_validate_dict_payload` + error codes (`invalid_payload`, `missing_field`, `invalid_field`).
- **Dispatch unchanged:** `ipc_server._COMMAND_REGISTRY` + `getattr(self, "_handle_<cmd>")` already maps command → `handlers/*` mixin. Only the listen/accept loop changes (TCP → WebSocket server). Add `sidecar_main.py` (or `--ws` flag on `ipc_server.py`).
- **Port:** ephemeral — Rust binds `127.0.0.1:0`, reads the OS-assigned port, passes it to the sidecar via env `VOICE_TYPER_IPC_PORT`. Never hardcode `9876`.
- **Auth:** HMAC token via env `VOICE_TYPER_IPC_TOKEN` (reuses today's scheme). localhost-only; same trust level as today.
- **Events (`_push_event_now`):** sidecar pushes `{"type": <event>, "data": {...}}` over the socket → Rust `app.emit(event, payload)`. Enumerate every push site; keep the SAME event names the UI already `listen()`s to. Document the full table in `sidecar_main.py`/handlers.
- **Lifecycle over WebSocket (not stdin):** supervisor sends `{"type":"shutdown"}`; sidecar releases mic + acks, then exits. `kill_children` = unclean-exit backstop.
- **Model/DLL paths unchanged:** Python resolves `HF_HOME` / config dir via `os.environ` (`config.py` already uses `APPDATA`-based dir + sets `HF_HOME`); CWD-independent, so dev (`target/debug`) and installed (MSI) both work. MSVC runtime + `cublas` DLLs travel with the frozen exe. No `_MEIPASS` rewriting needed.

### P3 — UI port & OS integrations (review §3)
- **Electron API → Tauri plugin map** (inventory from `client/src`):
  - `globalShortcut` → `global-shortcut` plugin (Rust captures → `invoke` → sidecar).
  - `Tray` → `tray` plugin.
  - `clipboard.write` → `clipboard` plugin (or Rust side).
  - paste-at-cursor → **Rust `enigo`** (or `rdev`) simulates Ctrl+V / types; uses `SendInput` on Windows (may prompt AV — code-sign + document).
  - `autoUpdater` → Tauri `updater` plugin (smaller patches without Chromium).
  - `screen` / `powerMonitor` → Tauri window/event APIs as needed.
- **Hotkey latency:** unchanged vs today (Rust global-shortcut → `invoke` → sidecar starts capture). Measure in spike.
- **Autostart dedup:** keep the EXISTING Windows Task Scheduler `BootTrigger` (`task_scheduler.py`) as the sole autostart; **do NOT enable the Tauri `autostart` plugin for the app** (avoids duplicate entries). Prewarm stays on BootTrigger (ADR-0011).

### P4 — Lifecycle, reliability, recovery (review §4 + crash isolation)
- **supervisor state machine:** `running → (unexpected exit) → reconnecting (UI "reconnecting…") → respawn with backoff (500ms → 1s → 2s, cap 5 retries) → running | give up → full-app relaunch`. In-flight audio chunk on crash is discarded (next dictation re-opens capture); acceptable.
- **Mic release:** on `{"type":"shutdown"}` the sidecar calls `sounddevice`/`pyaudio` terminate within a timeout (~3s); if it doesn't exit, `kill_children` force-kills.
- **Zombie prevention:** `kill_children: true` cleans the Tauri-spawned sidecar; the sidecar must NOT spawn its own long-lived subprocesses (CTranslate2 runs in-thread, dies with the process). Any subprocess must be tracked + killed explicitly.
- **Code-signing:** sign BOTH the main exe and `python-sidecar-*.exe` separately (SmartScreen flags an unsigned sidecar even if the main is signed).

### Cross-cutting
- **Config / user-data:** Python keeps resolving its config dir exactly as today (`APPDATA`/voice-typer + `HF_HOME`) — no rewrite, no `sys._MEIPASS` dependency. On first Tauri launch, optionally copy any legacy Electron `userData` into that same dir (one-time migration) so users keep settings/history/model cache. The UI MUST NOT write config directly; all config goes UI → `invoke` → sidecar → `config.py` (single writer).
- **Capabilities (`capabilities/default.json`):** allow `core:default`, `shell:allow-spawn` (or sidecar), `global-shortcut:default`, `tray:default`, `clipboard:default`, `updater` (if used), `window:allow-*`. Without these, `invoke`/shell are blocked.
- **Plugins:** `tray`, `global-shortcut`, `autostart` (prewarm only / disabled for app), `single-instance`, `updater` (optional), `clipboard`, `opener`.
- **Prewarm post-migration:** still spawned by `task_scheduler.py` BootTrigger (not by Tauri). Installer must remove old Electron autostart/launcher tasks; `single-instance` ensures one app instance. Prewarm's separate process is unchanged (ADR-0011).

### Day-1 answers (review §5)
1. **externalBin vs resources:** `externalBin` with a single frozen exe `python-sidecar-x86_64-pc-windows-msvc.exe` (§P1).
2. **WebSocket or HTTP:** WebSocket, Rust proxy, events over same socket (§P2).
3. **Port/token:** ephemeral port + HMAC token via env `VOICE_TYPER_IPC_PORT`/`VOICE_TYPER_IPC_TOKEN` (§P2).
4. **Sidecar entrypoint:** `sidecar_main.py` (or `ipc_server.py --ws`); keeps `_COMMAND_REGISTRY` (§P2).
5. **Model location:** unchanged — `HF_HOME`/config dir via env, CWD-independent (§P2).
6. **Capability + plugin list:** see Cross-cutting.
7. **Prewarm + installer cleanup:** BootTrigger retained; installer removes legacy Electron tasks (Cross-cutting).

### Open questions to close in the Phase 0 spike
- Freeze tool: **Nuitka vs PyInstaller `--onedir`** (pick by EXE size + AV false-positive rate on the user's Windows).
- Paste approach: **Rust `enigo`** vs sidecar-signals-Rust-to-paste (decide by AV behavior).
- Produce the **full `_push_event_now` event-name table** before Phase 2 coding.

---

## Consequences

### Wins (keep)
- **One application (perceived), not one process.** Tauri host + sidecar are bundled and lifecycled together, so the user sees one icon/install and Task Manager groups them under one app. This addresses the *spirit* of the original "one app" wish, but the runtime remains **multiple OS processes** (Tauri host + sidecar + prewarm). The original "single process / one exe" goal is intentionally NOT met — traded for a freeze-free, crash-isolated design. Process count is similar to today (Electron + Python + prewarm ≈ Tauri host + sidecar + prewarm); what changes is the leaner host and Tauri-managed lifecycle, not the count.
- **No hand-rolled launcher.** Tauri owns the Python lifecycle; the `electron_launcher.py` relay behind complaint (A) is removed.
- **No UI freeze.** The sidecar owns its own GIL, so continuous mic capture + inference never block the UI — matches today's smooth behavior.
- **Crash isolation possible (supervisor).** A speech-engine crash can be recovered without killing the whole app — an upgrade over today's whole-app restart.
- **Smaller shell.** Tauri exe ~2–10 MB using system WebView2, vs Electron's ~100 MB+ bundled Chromium.
- **Python stays Python.** No ML rewrite; the existing backend is bundled as a sidecar.

### Costs (documented, with mitigations)
- **Installer size:** Python + CTranslate2 + model adds ~400 MB–1 GB. Mitigation: this is model/data weight, comparable to what the app already ships; far less than Electron + Chromium overhead overall.
- **Startup latency:** 2–5 s cold sidecar start. Mitigation: prewarm file-cache warming + background load.
- **Multiple processes in Task Manager** (Tauri host + sidecar + prewarm): mitigated by Tauri-managed lifecycle + `single-instance`; users perceive one app. (Tauri groups them under one app name, but the Details view shows three separate processes — set expectation accordingly.)
- **Lifecycle/PID bugs** (the child Python process must close cleanly or it lingers as a zombie / blocks reinstall): mitigated by four concrete measures, all to be applied:
  1. **Single frozen exe (`externalBin`)** — build with `python-build-standalone` base + Nuitka / PyInstaller `--onedir`, named `python-sidecar-<triple>.exe`; avoid `--onefile` bootloader-child confusion.
  2. **`python-build-standalone` as the base interpreter** (raw CPython, no PyInstaller bootloader) → simpler process tree, fewer antivirus false positives.
  3. **`kill_children`** — Tauri recursively kills the whole child process tree on exit → no zombies left behind.
  4. **Cooperative shutdown over WebSocket** — sidecar receives `{"type":"shutdown"}`, releases the mic, acks, exits gracefully; not stdin/stdout (transport is WebSocket).
- **Webview consistency:** WebView2 vs Chromium — minor CSS/API guardrails if macOS/Linux is later added.

### Reversibility
Electron code is untouched throughout the migration. The Tauri + Sidecar build is strictly additive. At any phase the Electron app remains the shippable fallback; cutover is a packaging/default switch. No data, config, or model loss on revert.

---

## Risks / Open Questions

1. **Spike must pass on the user's Windows machine** (Phase 0) before any full build — the make-or-break step.
2. **UI port effort (Phase 3)** is the largest unknown; mitigated by keeping React components shell-agnostic.
3. **Transport bridge (Phase 2)** must preserve all current handler behaviors and the event flow exactly.
4. **Recovery supervisor (supervisor)** must be implemented before cutover so a sidecar crash does not strand the user.

## References

- ADR-0003 (Electron + Python Architecture, Accepted) — current architecture, retained as the reversible fallback. ADR-0002 was the initial design, superseded by ADR-0003.
- ADR-0011 (Prewarm & Autostart Architecture) — prewarm / BootTrigger design preserved by this ADR.
- Tauri v2 sidecar guide (v2.tauri.app/develop/sidecar) — first-class `externalBin` sidecar feature.
- Tauri discussion #1645 (github.com/tauri-apps/tauri/discussions/1645) — sidecar trade-offs.
- `python-build-standalone` (by Gregory Szorc) — clean pre-built Python for sidecar bundling.
- Tauri `kill_children` + `single-instance` plugin — lifecycle/cleanup correctness.
- supervisor in `.workspace/TASKS.md` — crash isolation (restart backend only, keep UI alive).
- `electron_launcher.py:13` — current spawn of `python -m voice_typer.server.ipc_server --port 9876` (replaced by sidecar in Phase 2).
- `voice_typer/server/ipc_server.py`, `voice_typer/server/handlers/*` — TCP IPC + handler registry (bridged to sidecar WebSocket in Phase 2).
- `voice_typer/server/prewarm/`, `voice_typer/server/task_scheduler.py` — prewarm + BootTrigger (kept).

*End of document.*
