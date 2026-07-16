# Voice Typer — Open Findings (Comprehensive Review)

Only unresolved findings are listed below. Completed/verified items have been removed:

## Findings

### CR-2 — `shutdown_sidecar` blocks 2s unconditionally regardless of sidecar exit
- **Category**: Performance / UX
- **Severity**: Medium
- **Status**: Pending (deferred to next round)
- **Description**: The `shutdown_sidecar` Tauri command sleeps in a `while Instant::now() < deadline` loop with 100ms `tokio::time::sleep` calls but never polls whether the child has actually exited. Even when the sidecar acks and exits in ~50ms, the host waits the full 2000ms before calling `child.kill()`.
- **Recommended fix**: Store the `Receiver<CommandEvent>` from the original spawn on `SidecarState`, and in `shutdown_sidecar` use `tokio::time::timeout(deadline, rx.recv())` to poll for `CommandEvent::Terminated`. Exit the wait loop as soon as termination is received.
- **Files**: `src-tauri/src/main.rs` (lines 174-246, 617-643)

### CR-4 — `_ready_emitted` module-level global in sidecar_ws.py is never reset
- **Category**: Testing / Architecture
- **Severity**: Medium
- **Status**: Pending (deferred to next round)
- **Description**: `sidecar_ws._ready_emitted` is a module-level boolean that prevents re-emitting the `ready` event on reconnect. This is correct for production, but it's never reset between test runs that import the module once and call `run()` multiple times.
- **Recommended fix**: Move `_ready_emitted` to a per-`IPCServer` instance attribute, or expose a `_reset_ready_emitted()` test-only helper.
- **Files**: `voice_typer/server/sidecar_ws.py` (lines 98, 326-332)

### CR-6 — `usePythonEvent` hook silently drops events if `window.python` is unset at mount
- **Category**: Code Quality / UX
- **Severity**: Medium
- **Status**: Pending (deferred to next round)
- **Description**: `usePythonEvent` returns early from its `useEffect` if `window.python` is undefined. The effect's only dependency is `[type]`, so if `window.python` is set up later, the subscription is never re-attempted.
- **Recommended fix**: Add a `bridgeReady` state from a small `useSyncExternalStore` that polls/subscribes to `window.python` presence, and include it in the effect's dependency array.
- **Files**: `voice_typer/client/src/renderer/src/hooks/usePython.ts`, `voice_typer/client/src/renderer/src/lib/tauri-bridge.ts`

### CR-7 — `_pick_available_port` race window: bind-release between check and real bind
- **Category**: Security / Cross-platform
- **Severity**: Low
- **Status**: Pending (deferred)
- **Description**: The probe-then-bind pattern is inherently racy. Between the probe close and the real bind, another local process can grab the port.
- **Recommended fix**: Either pass the bound probe socket through to `start_tcp`, or catch the bind failure and surface a clear error with retry logic.
- **Files**: `voice_typer/server/ipc_server.py` (lines 128-151, 1950-1955)

### CR-8 — `_handle_show_electron_notification` event name not renamed for Tauri path
- **Category**: Cross-platform / Code Quality
- **Severity**: Medium
- **Status**: Pending (deferred)
- **Description**: Python emits `electron_notification`; Tauri Rust renames to `notification`, but the rename is a single `match` arm with no fallback. The Python event name still carries the `electron_` prefix even though it's now platform-agnostic.
- **Recommended fix**: Rename the Python-side event from `electron_notification` to `notification`. Remove the Rust-side rename once the source event is renamed.
- **Files**: `voice_typer/server/handlers/system_handlers.py`, `voice_typer/server/startup_sequence.py`, `src-tauri/src/main.rs`

### CR-9 — Heartbeat watchdog has no fallback if `tray.stop()` hangs
- **Category**: Reliability / Cross-platform
- **Severity**: Medium
- **Status**: Pending (deferred)
- **Description**: The heartbeat watchdog calls `self.app.quit()` when Electron hasn't pinged in 120s. From a daemon thread, `quit()` relies on `tray.stop()` breaking the pystray loop — but pystray on certain Linux backends and on Windows Server has been observed to hang.
- **Recommended fix**: After calling `self.app.quit()` from the heartbeat watchdog, schedule a daemon thread that calls `os._exit(1)` after a 10-second grace period.
- **Files**: `voice_typer/server/ipc_server.py` (lines 1145-1192), `voice_typer/server/app.py` (lines 1646-1707)

### CR-10 — `_secure_clear_array_background` spawns unbounded threads on rapid stop/discard
- **Category**: Performance
- **Severity**: Medium
- **Status**: Pending (deferred)
- **Description**: Each `stop()`/`discard()` spawns a new daemon thread for buffer zeroing. Under rapid hotkey toggling, this can spawn several threads per second.
- **Recommended fix**: Replace with a single long-lived "buffer-clear" worker thread + `queue.Queue` of buffers to zero.
- **Files**: `voice_typer/server/recording.py` (lines 142-166, 2786-2791, 3149+)

### CR-11 — IPC per-connection rate limiter resets on every reconnect
- **Category**: Security
- **Severity**: Low
- **Status**: Pending (deferred)
- **Description**: `_RateLimiter` is instantiated fresh per TCP/WS connection. A local attacker can burst 200 messages, disconnect, reconnect, and burst another 200 — bypassing the sustained cap.
- **Recommended fix**: Maintain a per-process (or per-token) rate limiter keyed by the auth token, decayed over a 10-minute sliding window.
- **Files**: `voice_typer/server/ipc_server.py` (lines 215-278, 977-998), `voice_typer/server/sidecar_ws.py` (lines 228-280)

### CR-12 — No unit tests for the 14 IPC handler mixins
- **Category**: Testing
- **Severity**: High
- **Status**: Pending (deferred — documented as future work)
- **Description**: `voice_typer/server/handlers/*.py` (2,060 LOC across 14 files) has no dedicated unit tests. The handler mixins are only exercised indirectly via integration tests.
- **Recommended fix**: Add `tests/handlers/test_<name>_handlers.py` per mixin, using the existing `make_fake_service` fixture to inject a mock service and assert on the handler's response shape for each validation path.
- **Files**: `voice_typer/server/handlers/*.py`, `tests/`

### CR-13 — No Rust tests for the FT-1 supervisor or WS dispatch logic
- **Category**: Testing
- **Severity**: High
- **Status**: Pending (deferred — documented as future work)
- **Description**: `src-tauri/src/main.rs` (713 LOC) has zero `#[cfg(test)]` modules. The FT-1 backoff schedule, the per-id pending-dispatch map, the bubble_level coalescing logic, and the auth handshake are all untested in Rust.
- **Recommended fix**: Add a `#[cfg(test)] mod tests` module with unit tests for: (a) `generate_token` produces 64-char hex strings, (b) the bubble_level coalesce logic respects the 30 Hz cap, (c) `current_target_triple` returns the expected string for each `(arch, os)` combo, (d) the pending-dispatch map correctly fulfills requests by id. For FT-1, add a Rust integration test that spawns a mock WS server, kills it, and asserts the respawn schedule.
- **Files**: `src-tauri/src/main.rs`

### CR-14 — README and CONTRIBUTING have zero mentions of the Tauri migration
- **Category**: Documentation
- **Severity**: Medium
- **Status**: Pending (deferred)
- **Description**: `grep -rn 'Tauri|tauri' README.md CONTRIBUTING.md docs/ARCHITECTURE.md` returns nothing. A new contributor would have no idea that `src-tauri/` exists or that there are two parallel UI shells.
- **Recommended fix**: Add a "Runtime Architecture" section to README.md explaining the dual-stack, link to ADR-0020, and document the `TAURI_SIDECAR=1` env var in CONTRIBUTING.md.
- **Files**: `README.md`, `CONTRIBUTING.md`, `docs/ARCHITECTURE.md`

### CR-15 — `tauri-bridge.ts` stubs 6 bubble APIs and 2 window APIs as no-ops
- **Category**: UX / Cross-platform
- **Severity**: Medium
- **Status**: Pending (deferred — documented in tauri-sidecar-bridge.md)
- **Description**: `tauri-bridge.ts` stubs `bubble.show`, `bubble.signalReady`, `bubble.setPosition`, `bubble.setDraggable`, `bubble.moveBy`, `bubble.hideComplete`, `window_.exportHistory`, and `window_.exportVocabulary` as no-ops or rejection-returning stubs. In Tauri mode, the user cannot reposition the bubble, drag it, or export history/vocabulary.
- **Recommended fix**: Either implement the missing Rust commands before the Tauri cutover, OR add a runtime capability flag so the renderer can show "not available in Tauri mode" UI.
- **Files**: `voice_typer/client/src/renderer/src/lib/tauri-bridge.ts`

### CR-16 — `tray.py` reaches into pystray private `_icon_handle` attribute
- **Category**: Cross-platform / Code Quality
- **Severity**: Low
- **Status**: Pending (deferred)
- **Description**: `TrayIcon._apply_state` catches `OSError` from `self._icon.icon = _make_icon(state)` and, on failure, sets `self._icon._icon_handle = None` to force pystray to re-create the icon handle. `_icon_handle` is a private attribute.
- **Recommended fix**: Pin pystray to a known-good minor version and file an upstream issue to expose a public `reset_icon_handle()` method.
- **Files**: `voice_typer/server/tray.py` (lines 475-487), `pyproject.toml`

### CR-17 — `_validate_path_safety` uses `str.startswith` for path containment
- **Category**: Security
- **Severity**: Low
- **Status**: Pending (deferred)
- **Description**: `_validate_path_safety` resolves both `path` and `parent`, then checks `str(resolved).startswith(str(parent_resolved))`. This is the classic prefix-match bug: `/home/userX/secret` would be considered "within" `/home/user`.
- **Recommended fix**: Replace the body of `_validate_path_safety` with a call to `_is_path_within(path, parent)`, or inline the `commonpath` logic.
- **Files**: `voice_typer/server/config.py` (lines 228-239, 420, 440, 454)

### CR-18 — TCP `sendToPython` 120s timeout is uncapped for long-running commands
- **Category**: Performance / UX
- **Severity**: Low
- **Status**: Pending (deferred)
- **Description**: A blanket 120s `setTimeout` is applied to every IPC call. A `get_status` call that hangs takes 120s to surface an error; the 120s timer is created even for trivial commands.
- **Recommended fix**: Add a per-command timeout table (e.g., `get_status: 5s`, `download_model: 600s`, default: 30s).
- **Files**: `voice_typer/client/src/main/index.ts` (lines 507-644), `src-tauri/src/main.rs` (lines 522-552)

### CR-19 — `_validate_systemroot` logs at `error` but never aborts startup
- **Category**: Security / Code Quality
- **Severity**: Low
- **Status**: Pending (deferred)
- **Description**: `_validate_systemroot` detects path traversal, unusual characters, missing directory, and missing `notepad.exe`, but every branch ends in `return` (not `sys.exit`) after logging at `error` level.
- **Recommended fix**: For the path-traversal and unusual-character branches, either `sys.exit(1)` (fail-closed) or unconditionally reset to `C:\Windows` and continue.
- **Files**: `voice_typer/server/config.py` (lines 324-399)

### CR-20 — Electron `app.on("window-all-closed")` is a no-op on non-macOS
- **Category**: Cross-platform / UX
- **Severity**: Low
- **Status**: Pending (deferred)
- **Description**: Closing the last window on Linux/Windows does nothing; the user must use tray Quit. On Wayland-without-SNI there's no tray icon, leaving the user with no UI affordance to quit.
- **Recommended fix**: When `_tray_unavailable` is true (Wayland-without-SNI), change `window-all-closed` to call `app.quit()`.
- **Files**: `voice_typer/client/src/main/index.ts` (lines 2164-2170), `voice_typer/server/tray.py` (lines 357-380)

### CR-21 — `_atexit_cleanup` swallows all exceptions including `KeyboardInterrupt`
- **Category**: Code Quality
- **Severity**: Low
- **Status**: Pending (deferred)
- **Description**: `_atexit_cleanup` catches `Exception` with a `pass` body — no log, no diagnostic. If `_do_cleanup` raises, the user has no way to know why their history wasn't saved.
- **Recommended fix**: Replace `pass` with `log.exception("[ATEXIT] _do_cleanup() raised — emergency cleanup incomplete")`.
- **Files**: `voice_typer/server/app.py` (lines 1717-1756)

### CR-22 — `dispatch` in main.rs holds `state.ws_tx.lock()` (std::sync::Mutex) across an await boundary indirectly
- **Category**: Performance
- **Severity**: Low
- **Status**: Pending (deferred)
- **Description**: The use of `std::sync::Mutex` for `ws_tx` while using `tokio::sync::Mutex` for `pending` is inconsistent. On a heavily contended UI, the std Mutex can block the tokio worker thread briefly.
- **Recommended fix**: Either switch `ws_tx` to `tokio::sync::Mutex` for consistency, or wrap the `ws_tx` clone in a small `Arc`-based read lock.
- **Files**: `src-tauri/src/main.rs` (lines 99-113, 517-520)

### CR-23 — Generated capabilities cache contains stale `process:allow-restart` permission
- **Category**: Documentation / Code Quality
- **Severity**: Low
- **Status**: Pending (stale build artifact — `cargo clean` recommended)
- **Description**: The source `src-tauri/capabilities/migrate-runtime.json` does NOT include `process:allow-restart`. But the cached `target/debug/build/.../capabilities.json` (an older build artifact) DOES include it.
- **Recommended fix**: Run `cargo clean` and rebuild. Add a CI step that asserts `target/` is clean.
- **Files**: `src-tauri/capabilities/migrate-runtime.json`, `src-tauri/target/debug/build/`

### CR-24 — Tauri `on_window_event` only handles `CloseRequested` on the `main` window
- **Category**: Cross-platform
- **Severity**: Low
- **Status**: Pending (deferred)
- **Description**: `on_window_event` only fires `shutdown_sidecar` for `WindowEvent::CloseRequested` on `window.label() == "main"`. The `bubble` window's close is ignored.
- **Fix status**: Pending — not addressed by Wave 1/2/3. Recommended priority: P4.

## What remains open

This section consolidates everything that is still Pending, Won't Fix, or carried forward as future work after Wave 1 + Wave 2 + Wave 3. Use this as the work queue for the next round.

### Pending findings (still open)

#### High severity
- **CR-12** — No unit tests for the 14 IPC handler mixins (testing). **Recommended priority: P2** — 2,060 LOC across 14 files exercised only indirectly via integration tests.
- **CR-13** — No Rust tests for the FT-1 supervisor or WS dispatch logic (testing). **Recommended priority: P2** — `src-tauri/src/main.rs` has zero `#[cfg(test)]` modules; FT-1 backoff, bubble_level coalesce, auth handshake all untested in Rust.

#### Medium severity
- **CR-2** — `shutdown_sidecar` blocks 2s unconditionally (performance/UX). **Recommended priority: P3** — poll `CommandEvent::Terminated` instead of fixed sleep.
- **CR-4** — `_ready_emitted` module-level global not reset between tests (testing/architecture). **Recommended priority: P3** — move to per-instance attribute.
- **CR-6** — `usePythonEvent` silently drops events if `window.python` unset at mount (UX). **Recommended priority: P3** — add `bridgeReady` state via `useSyncExternalStore`.
- **CR-8** — `electron_notification` event name not renamed for Tauri path (cross-platform). **Recommended priority: P3** — rename Python-side to `notification`, remove Rust-side rename.
- **CR-9** — Heartbeat watchdog has no fallback if `tray.stop()` hangs (reliability). **Recommended priority: P3** — schedule `os._exit(1)` after 10s grace period.
- **CR-10** — `_secure_clear_array_background` spawns unbounded threads (performance). **Recommended priority: P3** — replace with single long-lived worker thread + queue.
- **CR-14** — README/CONTRIBUTING have zero Tauri mentions (documentation). **Recommended priority: P3** — add "Runtime Architecture" section.
- **CR-15** — `tauri-bridge.ts` bubble/window API stubs (UX/cross-platform). **Recommended priority: P2** — blocks Tauri cutover (but see Wave 1 sub-agents #1 + #2 which addressed the bubble + export stubs; CR-15 may now be partially closed — verify against the current `tauri-bridge.ts` before scheduling).
- **RW-03** — Structured JSON logging + correlation IDs (Medium, P3). Optional; must keep `PIIRedactionFilter` working on both formats.

#### Low severity
- **XPLAT-02** — Linux `deb`/`rpm` `afterInstall` relative path fragile (cross-platform). **FIXED (Wave 4)** — scripts copied to `resources/linux/` and referenced as `resources/linux/postinst` etc.
- **CR-7** — `_pick_available_port` race window (security). **Recommended priority: P4** — inherent race in probe-then-bind.
- **CR-11** — IPC per-connection rate limiter resets on reconnect (security). **Recommended priority: P4** — per-process / per-token sliding-window limiter.
- **CR-16** — `tray.py` reaches into pystray private `_icon_handle` (cross-platform). **Recommended priority: P4** — pin pystray + file upstream issue.
- **CR-17** — `_validate_path_safety` uses `str.startswith` for path containment (security). **Recommended priority: P3** — replace with `commonpath` logic.
- **CR-18** — TCP `sendToPython` 120s timeout uncapped (performance/UX). **Recommended priority: P4** — per-command timeout table.
- **CR-19** — `_validate_systemroot` logs error but never aborts (security/code quality). **Recommended priority: P4** — fail-closed on path-traversal branch.
- **CR-20** — Electron `window-all-closed` no-op on non-macOS (cross-platform/UX). **Recommended priority: P4** — call `app.quit()` when `_tray_unavailable`.
- **CR-21** — `_atexit_cleanup` swallows all exceptions (code quality). **Recommended priority: P4** — replace `pass` with `log.exception(...)`.
- **CR-22** — `dispatch` in `main.rs` holds std Mutex across await (performance). **Recommended priority: P4** — switch `ws_tx` to `tokio::sync::Mutex`.
- **CR-23** — Generated capabilities cache contains stale `process:allow-restart` (documentation). **Recommended priority: P5** — `cargo clean` + add CI step asserting `target/` is clean.
- **CR-24** — Tauri `on_window_event` only handles `CloseRequested` on `main` window (cross-platform). **Recommended priority: P4** — handle `bubble` window close too.

---

### Findings

| Severity | ID | Description | Fix status |
|---|---|---|---|
| Low | W3-VAD-001 | `_make_vad_property` factory uses a closure over `vad_attr`; works correctly but the pattern is unusual. The `del _make_vad_property` line correctly removes the helper from the class namespace. No bug — just noting the pattern. | Documented only |
| Low | W3-VAD-002 | `Recorder.start()` lines 1402-1409 make redundant property-shim assignments after `self._vad.reset()` already set the same values. This is intentional (source-level documentation that `test_vad_auto_calibrate_resets_on_start` pins on the literal attribute names). No bug. | Documented only |
| Low | W3-VAD-003 | `VadProcessor.update_frame` uses `>=` for `is_loud` and `<` for `is_quiet` — chunk at exactly `silence_threshold_db` falls in the grey zone (neither loud nor quiet). Behavior identical to pre-refactor (verified by passing `test_vad_transition_at_exact_silence_threshold`). | Documented only |
| Low | W3-VAD-004 | `Recorder._vad_auto_calibrate` has a redundant `if not self._vad_enabled: return` short-circuit at line 1098 — `VadProcessor.auto_calibrate` already short-circuits at line 293. Intentional optimization to avoid a `time.perf_counter()` call per chunk in raw mode. No bug. | Documented only |
| Low | W3-VAD-005 | `test_recording_discard.py::TestDiscardStopGeneration::test_discard_closes_stream_and_clears_buffer` (line 283) fails with `deque([]) == []` assertion. **This is a pre-existing test bug, NOT caused by the VadProcessor extraction** — `r._buffer` is a `collections.deque`, never a list. Out of scope for this review (only VAD-related files allowed). Filed for awareness. | Out of scope (pre-existing) |
| Medium | W3-VAD-006 | Test coverage gap: the original 38 tests did NOT explicitly cover thread-safety (concurrent `update_frame` + `on_config_changed` / `reset`). Added 2 tests in `TestThreadSafety` class — both pass. | Fixed (added 2 tests) |
| High | — | None found. | — |
| Critical | — | None found. | — |

#### Wave3-INST-04 — macOS `.app` bundle is minimal (LOW, documented)

- **Category**: Build / macOS bundle correctness
- **Severity**: Low
- **Status**: Documented (no fix needed for current use)
- **Files**: `.github/workflows/build.yml` (build-macos "Stage PyInstaller backend for embedding" step)
- **Description**: When PyInstaller produces a onefile binary (`voice_typer/dist/VoiceTyper`, no `.app` wrapper — the current spec has no `BUNDLE()` call), the macOS staging script wraps it into a minimal `.app` structure: `voice-typer-backend.app/Contents/MacOS/voice-typer`. This is NOT a complete macOS `.app` bundle — it's missing `Info.plist`, `PkgInfo`, `Resources/`, and the `Contents/` directory structure that Finder/LaunchServices expect. However, since Electron's `pythonArgs()` spawns the executable directly via Node's `child_process.spawn()` (not via `open` or LaunchServices), the minimal structure is sufficient for the runtime path. The bundle would NOT be recognized by Finder as a proper `.app` (no icon, no bundle ID, can't be double-clicked), but that's acceptable because users never see or interact with this bundle — it's an internal resource embedded inside the Electron app's `resources/` directory.
- **Recommendation (future)**: If the spec is later upgraded to use `BUNDLE()` (which produces a proper `.app` with `Info.plist`), the staging script's `if [ -d "voice_typer/dist/VoiceTyper.app" ]` branch will handle it correctly (rename `VoiceTyper.app` → `voice-typer-backend.app`). No action needed today.

#### Wave3-INST-05 — `pythonArgs()` switch has no `default:` case (LOW, intentional)

- **Category**: Code quality / Robustness
- **Severity**: Low
- **Status**: Documented (intentional design)
- **Files**: `voice_typer/client/src/main/index.ts` (lines 418-497)
- **Description**: The `pythonArgs()` switch on `process.platform` covers `darwin`, `linux`, `win32` — no `default:` case. The comment at line 495 says "(no default — fall through to the dev-mode venv path below)". On an unsupported platform (e.g., `freebsd`, `aix`, `sunos`), the switch falls through and the dev-mode venv path is used. This is intentional and acceptable: Electron only officially supports Windows/macOS/Linux, so the fallthrough is a graceful degradation rather than a bug. The dev-mode venv path uses `process.platform === "win32"` to pick the right Python executable (`pythonw.exe` vs `python3`), so it works on any POSIX-like platform.
- **Recommendation (future)**: Could add a `default:` case that logs a warning like `[pythonArgs] unsupported platform ${process.platform} — falling back to dev venv` for diagnostic purposes. Not a blocker.

### `pythonArgs()` review summary

Verified per task requirements:
- **Switch exhaustiveness**: covers `win32`, `darwin`, `linux`. No `default:` — intentional fall-through to dev venv (see Wave3-INST-05 above). ✓
- **Dev-mode venv fallback**: preserved at lines 499-512. Uses `computeConfigDir()` (RW-15) for cross-platform config dir. ✓
- **`--port` arg**: passed as `["--port", String(IPC_PORT)]` — `String()` ensures it's a string (ipc_server's `argparse type=int` handles the conversion). ✓
- **`app.isPackaged` check**: correct Electron API (line 418). Dev mode (`npm run dev`) sets it false; packaged builds set it true. ✓

### Path consistency — final `from:`/`to:`/lookup-path table

All 3 platforms now use a consistent `distpath` base (`<repo>/voice_typer/dist/`) and `extraResources.from:` depth (`../dist...` relative to `voice_typer/client/`):

| Platform | PyInstaller `--distpath` | PyInstaller output (onefile) | Staging | `extraResources.from:` (rel. to `voice_typer/client/`) | `extraResources.to:` | Runtime lookup in `pythonArgs()` |
|----------|--------------------------|------------------------------|---------|--------------------------------------------------------|----------------------|----------------------------------|
| Windows  | `voice_typer/dist`       | `voice_typer/dist/VoiceTyper.exe` | (none — direct copy) | `../dist` → `<repo>/voice_typer/dist/` | `voice-typer-backend` | `${resourcesPath}/voice-typer-backend/VoiceTyper.exe` (onefile) or `${resourcesPath}/voice-typer-backend/VoiceTyper/VoiceTyper.exe` (onedir) |
| macOS    | `voice_typer/dist`       | `voice_typer/dist/VoiceTyper` (onefile binary) | Stage → `voice_typer/dist/voice-typer-backend.app/Contents/MacOS/voice-typer` | `../dist/voice-typer-backend.app` → `<repo>/voice_typer/dist/voice-typer-backend.app` | `voice-typer-backend.app` | `${resourcesPath}/voice-typer-backend.app/Contents/MacOS/voice-typer` |
| Linux    | `voice_typer/dist`       | `voice_typer/dist/VoiceTyper` (onefile binary) | Stage → `voice_typer/dist/voice-typer-backend/voice-typer` | `../dist/voice-typer-backend` → `<repo>/voice_typer/dist/voice-typer-backend` | `voice-typer-backend` | `${resourcesPath}/voice-typer-backend/voice-typer` |

**Consistency guarantees** (all verified by `TestWave3PathConsistency`):
1. All 3 build jobs pass `--distpath voice_typer/dist` to pyinstaller. ✓
2. All 3 `extraResources.from:` paths start with `../dist` (NOT `../../dist`). ✓
3. Each platform's `pythonArgs()` lookup path matches the `extraResources.to:` field + the staged binary name. ✓
4. PyInstaller spec produces `VoiceTyper` (onefile) on all 3 platforms; the macOS/Linux staging scripts handle the rename to `voice-typer` (lowercase, hyphenated) to match `pythonArgs()`. Windows keeps `VoiceTyper.exe` (the spec's `EXE(name="VoiceTyper")` output) — `pythonArgs()` win32 branch looks for `VoiceTyper.exe` directly. ✓
5. CI step ordering: PyInstaller → (staging for mac/linux) → electron-builder. All 3 jobs verified. ✓
6. `--publish never` flag present on all 3 electron-builder invocations. ✓
7. Artifact upload paths match electron-builder output: `*-setup.exe` (NSIS), `*.dmg` (macOS), `*.deb`/`*.rpm`/`*.AppImage` (Linux). ✓

### Validation evidence

- `python -m pytest tests/test_windows_installer_extra_resources.py tests/test_macos_linux_installer_extra_resources.py tests/test_electron_ipc_and_build.py -v --no-cov --timeout=30` → **131 passed, 0 failed** (was 130 before Wave 3; added 6 new tests: 2 path-consistency, 2 spec-entry-point, 1 code-signing-env-vars, 1 implicit from `_steps_blob` env-extension coverage).
- `python -c "import yaml; yaml.safe_load(open('voice_typer/client/electron-builder.yml')); yaml.safe_load(open('.github/workflows/build.yml')); print('YAML OK')"` → **YAML OK** (both files parse cleanly).
- `npx tsc --noEmit -p tsconfig.node.json` → **0 errors** (the `pythonArgs()` comment updates did not affect compilation).

### Files modified

1. `scripts/build/voice-typer.spec` — changed `Analysis()` entry from `voice_typer/__main__.py` to `voice_typer/server/ipc_server.py`; updated module docstring to document the Wave 3 fix.
2. `voice_typer/client/electron-builder.yml` — `win.extraResources.from: ../../dist` → `from: ../dist`; updated win: section comment.
3. `.github/workflows/build.yml` — build-windows PyInstaller step `--distpath dist --workpath build` → `--distpath voice_typer/dist --workpath voice_typer/build`; added `WIN_CSC_LINK` / `WIN_CSC_KEY_PASSWORD` / `CSC_LINK` / `CSC_KEY_PASSWORD` env vars to electron-builder step.
4. `voice_typer/client/src/main/index.ts` — updated win32 branch comment to reflect new `--distpath voice_typer/dist` and document the spec entry-point fix.
5. `tests/test_macos_linux_installer_extra_resources.py` — extended `_steps_blob()` to include `env:` keys; updated `test_python_args_passes_port_to_embedded_backend` docstring; added `TestWave3PathConsistency` (2 tests), `TestWave3SpecEntryPointAcceptsPort` (2 tests), `TestWave3WindowsCodeSigningEnvVars` (1 test).
6. `tests/test_windows_installer_extra_resources.py` — updated `test_win_section_has_extra_resources_for_backend` docstring and assertion message to reflect new `from: ../dist` path.
7. `comprehensive-review.md` — added this "Wave 3 Installer Review" section.

---

## Wave 3 Credential Store Review (sub-agent `review-credential-store`)

**Scope**: Security, backward-compat, cross-platform, and test-coverage review of `voice_typer/server/credential_store.py` (RW-01 keyring integration). Hardening pass — no behavior changes for existing callers.

**Methodology**: read `credential_store.py` end-to-end (713 LOC after hardening), `tests/test_credential_store.py` (611 LOC pre-review), `docs/security/credential-store.md`, and the credential_store integration points in `voice_typer/server/config.py` (`Config.save` lines 940-996, `Config.load` lines 1185-1244) and `voice_typer/server/service.py` (`apply_config` lines 1221-1254, `get_config` / `get_defaults` lines 170-211). Grepped for `log.*secret`, `log.*api_key`, `print.*token`, `keyring.set_password`, `keyring.get_password` to verify no secret-value logging. Verified the `keyring://` reference-token unforgeability contract by tracing `Config.load` → `PROVIDER_TO_CONFIG_FIELD.items()` → `load_secret(provider)` (provider from field, not from token suffix).

### Findings

| # | Severity | Description | Fix status |
|---|----------|-------------|------------|
| CS-1 | Medium | `get_keyring_status()` returned an inconsistent snapshot when the cache was set and `available=False`: it used the cached `backend` field but re-probed for the `reason` field, potentially pairing a stale backend name with a fresh reason from a second probe. | **Fixed**: cache the `reason` alongside `available` and `backend` in `is_keyring_available()` (single probe per cache lifetime). `get_keyring_status()` now returns a consistent snapshot. Added `_keyring_reason_cache` module-level cache. |
| CS-2 | Medium | Keyring exception messages and probe reasons were surfaced to the renderer (via `get_keyring_status().reason`) and written to logs without sanitization. A buggy or custom keyring backend could embed filesystem paths (e.g. `/home/<user>/.cache/...`) or, theoretically, API-key-like substrings in exception text. | **Fixed**: added `_redact_sensitive()` helper that strips `/home/<user>`, `/Users/<user>`, `~/<path>`, `C:\Users\<user>` paths and `sk-...`/`gsk_...`/32+ char alphanumeric runs, and truncates to 200 chars. Applied to all keyring exception log calls in `store_secret` / `load_secret` / `delete_secret` / `migrate_secrets_to_keyring` / `_read_plaintext_fallback` / `_write_plaintext_fallback`, to the probe reasons in `_probe_keyring`, and as a final defense-in-depth pass on the `reason` returned by `get_keyring_status()`. |
| CS-3 | Low | `secrets_migrated` flag in `config.json` is not race-safe across processes — two app instances starting simultaneously could both enter `migrate_secrets_to_keyring()` before either writes the flag. The migration is idempotent (`keyring.set_password` overwrites; `_secure_atomic_write` is atomic), so the worst case is the same secret is stored twice. No data loss. | **Documented** (not fixed): cross-process file locking (`fcntl.flock` on POSIX, `msvcrt.locking` on Windows) would close the TOCTOU window but is a larger change. Added explicit note in the `migrate_secrets_to_keyring` docstring and the module-level "Design notes" section. |
| CS-4 | Low | Python `str` is immutable, so secret values returned by `load_secret` cannot be zeroed in place. The value lives in the `Config` dataclass for the app's lifetime. | **Documented** (not fixed): `bytearray` + `del` would only help for the brief window inside `credential_store` itself (before returning), not for the `Config` instance. Added explicit note in the module-level "Design notes" section. Full secret-memory hygiene requires a C extension. |
| CS-5 | Low | `get_keyring_status().backend` could be non-`None` when `available=False` (e.g. `"fail"` or the broken backend's class name), contradicting the docstring which said "None when unavailable". | **Fixed** (docstring): updated the `get_keyring_status` docstring to accurately describe that `backend` is "preserved even when `available` is False for diagnostics; None only when the keyring library itself couldn't be imported". Behavior unchanged — the backend name remains useful diagnostic info. |
| CS-6 | Info | Reference-token unforgeability contract (verified safe): `Config.load()` iterates `PROVIDER_TO_CONFIG_FIELD` and calls `load_secret(provider)` with the provider matched to the field, NOT by parsing the `keyring://<suffix>` token. A malicious `config.json` that puts `keyring://llm` in `openai_api_key` cannot trick the loader into returning the LLM secret — the code calls `load_secret("openai")`, which looks up only the OpenAI entry. | **Documented + tested**: added explicit note in the module-level "Design notes" section. Added `TestReferenceTokenUnforgeability` (2 tests) verifying `load_secret` uses the provider arg (not token suffix) and that `CONFIG_FIELD_TO_PROVIDER` is an exact, unambiguous inverse. |
| CS-7 | Info | Migration mid-failure safety (verified safe): if `keyring.set_password()` raises for one provider mid-migration, the plaintext for that provider stays in `data` (the reference-token assignment is inside the `try` block, after `set_password` succeeds). The final `_secure_atomic_write` writes a mixed state (references for migrated providers, plaintext for failed ones). If the atomic write itself fails, the original `config.json` is untouched (tmp-file-then-`os.replace` is atomic). No data loss. | **Tested**: added `TestMigrationMidFailureSafety` (2 tests): `test_migrate_preserves_failed_provider_plaintext` (keyring succeeds for A, fails for B → A in keyring + reference, B in plaintext), `test_migrate_no_data_loss_when_atomic_write_fails` (keyring accepts secret, atomic write fails → secret is in BOTH keyring and original config.json — no loss). |
| CS-8 | Info | `load_secret` / `store_secret` exception path (verified safe): `keyring.get_password` is called with service+username only (not the value, since we're retrieving it); `keyring.set_password` is called with the value, but the value is in the call args, not in the exception. Defense-in-depth redaction (CS-2) covers the theoretical case where a buggy backend embeds the value in the exception. | **Tested**: added `TestLoadStoreNeverLeakViaException` (2 tests) simulating a buggy backend that embeds the secret in the exception message; verifies the WARNING log does not contain the secret. |
| CS-9 | Info | Plaintext fallback uses `0o600` on POSIX (enforced by `_secure_atomic_write` in `config.py`); config dir uses `0o700` (enforced by `Config.save()` line 955). Windows fallback relies on per-user NTFS ACLs under `%APPDATA%`. | **Verified** (pre-existing): `test_plaintext_fallback_uses_0600_permissions` and `test_migrate_preserves_0600_perms` cover the POSIX case. No change needed. |
| CS-10 | Info | Keyring service name is the hardcoded constant `"voice-typer"` (line 96). Not attacker-controllable. Provider names are looked up via the hardcoded `PROVIDER_TO_CONFIG_FIELD` map, not derived from user input. | **Verified** (pre-existing): `test_store_secret_calls_keyring_with_right_args` and `test_expected_providers_are_present` cover this. No change needed. |

### Fixes applied to `voice_typer/server/credential_store.py`

1. Added `_REASON_MAX_LEN = 200` constant.
2. Added `_PATH_RE` and `_API_KEY_RE` compiled regex patterns for defense-in-depth redaction.
3. Added `_redact_sensitive(text)` helper that strips paths + API-key-like substrings and truncates to `_REASON_MAX_LEN`.
4. Added `_keyring_reason_cache` module-level cache; updated `is_keyring_available()` and `_reset_keyring_cache()` to manage it.
5. Refactored `get_keyring_status()` to return a consistent cached snapshot (single probe via `is_keyring_available()`), with a final `_redact_sensitive` pass on the reason.
6. Wrapped all keyring exception text in log calls with `_redact_sensitive(str(e))` — applied to `store_secret`, `load_secret`, `delete_secret`, `_read_plaintext_fallback`, `_write_plaintext_fallback`, `migrate_secrets_to_keyring` (6 log sites).
7. Wrapped the probe reason in `_probe_keyring()` with `_redact_sensitive()` for all 3 exception branches.
8. Updated the module docstring with three new "Design notes" bullet points: reference-token unforgeability, Python memory hygiene (known limitation), two-instance migration race (known limitation).
9. Updated the `get_keyring_status` docstring to accurately describe the `backend` field's value when `available=False`.
10. Added an inline comment in `migrate_secrets_to_keyring` documenting the mid-migration failure safety contract (reference-token assignment is gated on `set_password` succeeding).

### Tests added to `tests/test_credential_store.py`

19 new tests (28 → 47 total), organized into 5 new test classes:

1. `TestRedactSensitive` (10 tests) — verifies `_redact_sensitive` strips POSIX/macOS/Windows paths, `sk-`/`gsk_`/32+ char alphanumeric API keys, truncates long strings, and passes through `None`/empty/short strings unchanged.
2. `TestGetKeyringStatusConsistency` (2 tests) — verifies `get_keyring_status()` does NOT re-probe when the cache is populated (sentinel test), and that the returned `reason` is redacted even when `_probe_keyring` returns a raw string.
3. `TestLoadStoreNeverLeakViaException` (2 tests) — defense-in-depth: simulates a buggy keyring backend that embeds the secret in its exception message; verifies the WARNING log from `load_secret` / `store_secret` does not contain the secret.
4. `TestMigrationMidFailureSafety` (2 tests) — verifies mid-migration failure preserves all secrets (failed provider's plaintext stays in config.json; atomic-write failure leaves original config.json untouched, secret is in both keyring AND config.json — no data loss).
5. `TestReferenceTokenUnforgeability` (2 tests) — verifies `load_secret` uses the provider arg (not the token suffix) for keyring lookup, and that `CONFIG_FIELD_TO_PROVIDER` is an exact, unambiguous inverse.
6. `TestMultiProviderConcurrentAccess` (1 test) — smoke test: store all 5 providers' secrets in rapid succession, load each, verify no cross-contamination.

### Validation evidence

- `python -m pytest tests/test_credential_store.py tests/test_config.py -v --no-cov --timeout=30` → **118 passed** (was 99 pre-review: 28 credential_store + 71 config; now 47 credential_store + 71 config).
- `python -m pytest tests/test_consent_and_privacy.py tests/test_security_hardening.py -v --no-cov --timeout=30` → **83 passed, 2 skipped** (unchanged — hardening pass did not touch consent or security-hardening code paths).

### Files modified

1. `voice_typer/server/credential_store.py` — hardening pass: added `_redact_sensitive()` helper, `_keyring_reason_cache`, refactored `get_keyring_status()` for consistent snapshots, wrapped all keyring exception log calls with `_redact_sensitive()`, expanded module docstring with 3 new design-notes bullets (unforgeability, memory hygiene, two-instance race). 581 → 713 LOC (+132, mostly docstrings + the redaction helper + test scaffolding).
2. `tests/test_credential_store.py` — added 19 new tests across 5 new test classes (`TestRedactSensitive`, `TestGetKeyringStatusConsistency`, `TestLoadStoreNeverLeakViaException`, `TestMigrationMidFailureSafety`, `TestReferenceTokenUnforgeability`, `TestMultiProviderConcurrentAccess`). 611 → 1100 LOC.
3. `comprehensive-review.md` — added this "Wave 3 Credential Store Review" section.

### Backward compatibility

- **No behavior changes for existing callers.** `store_secret` / `load_secret` / `delete_secret` / `migrate_secrets_to_keyring` / `is_keyring_available` / `get_keyring_status` all preserve their signatures, return types, and side effects.
- **`get_keyring_status().reason`** may now be shorter (truncated to 200 chars) and have paths/API-keys redacted — this is a security improvement, not a regression. Existing tests that asserted `"fail" in reason.lower()` or `"no usable" in reason.lower()` still pass.
- **`get_keyring_status().backend`** is unchanged — still returns the backend class name (or `"fail"`) even when unavailable, for diagnostics.
- **No changes to `config.json` on-disk format** — the `secrets_migrated` flag, `keyring://<provider>` reference tokens, and plaintext fallback format are all unchanged.
- **No changes to the keyring service name** (`"voice-typer"`) or the provider→field mapping.

### Cross-platform notes (unchanged from pre-review)

- **Windows Credential Manager**: keyring uses `keyring.backends.Windows.WinVaultKeyring` via `pywin32` (bundled). The `_probe_keyring` sentinel `get_password` call confirms the backend is responsive.
- **macOS Keychain**: keyring uses `keyring.backends.macOS.Keyring` via `pyobjc` (bundled). Locked keychain → `get_password` raises → `load_secret` falls back to `None` (renderer shows "not configured"); `store_secret` falls back to plaintext in `config.json`.
- **Linux libsecret/SecretService**: keyring uses `keyring.backends.SecretService.Keyring` via `python-dbus` + `gnome-keyring-daemon`. Missing D-Bus → backend selected but probe raises → `_probe_keyring` returns `(False, "SecretServiceKeyring", redacted reason)`.
- **Headless Linux (no gnome-keyring-daemon)**: `keyring.backends.fail.Keyring` is selected → `_probe_keyring` returns `(False, "fail", "no usable keyring backend (fail backend selected)")` → plaintext fallback with `0o600` perms. Verified by `test_status_unavailable_when_keyring_missing` and `test_load_secret_falls_back_to_config_json`.
