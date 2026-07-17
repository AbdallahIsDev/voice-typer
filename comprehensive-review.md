# Voice Typer — Open Findings (Comprehensive Review)

Only unresolved findings are listed below. Completed/verified items have been removed.

## Findings

### CR-11 — IPC per-connection rate limiter resets on every reconnect
- **Category**: Security
- **Severity**: Low
- **Status**: Pending (deferred)
- **Description**: `_RateLimiter` is instantiated fresh per TCP/WS connection. A local attacker can burst 200 messages, disconnect, reconnect, and burst another 200 — bypassing the sustained cap.
- **Recommended fix**: Maintain a per-process (or per-token) rate limiter keyed by the auth token, decayed over a 10-minute sliding window.
- **Files**: `voice_typer/server/ipc_server.py` (lines 215-278, 977-998), `voice_typer/server/sidecar_ws.py` (lines 228-280)

### CR-16 — `tray.py` reaches into pystray private `_icon_handle` attribute
- **Category**: Cross-platform / Code Quality
- **Severity**: Low
- **Status**: Pending (deferred)
- **Description**: `TrayIcon._apply_state` catches `OSError` from `self._icon.icon = _make_icon(state)` and, on failure, sets `self._icon._icon_handle = None` to force pystray to re-create the icon handle. `_icon_handle` is a private attribute.
- **Recommended fix**: Pin pystray to a known-good minor version and file an upstream issue to expose a public `reset_icon_handle()` method.
- **Files**: `voice_typer/server/tray.py` (lines 475-487), `pyproject.toml`

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
- **Fix status**: Pending — not addressed. Recommended priority: P4.
