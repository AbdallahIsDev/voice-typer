//! Tauri commands: dispatch, paste_text, shutdown_sidecar (ADR-0020 §6.2 + §7 + §10).

use crate::state::SidecarState;
use crate::util::{DISPATCH_TIMEOUT_SECS, PASTE_SHORT_THRESHOLD, SHUTDOWN_ACK_TIMEOUT_MS, SHUTDOWN_POLL_INTERVAL_MS};
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::{Duration, Instant};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tauri_plugin_shell::process::CommandEvent;
use tokio::sync::oneshot;
use tokio_tungstenite::tungstenite::Message;

// ─── Tauri command: generic dispatch (ADR-0020 §7) ────────────────────

#[derive(Serialize, Deserialize)]
pub(crate) struct DispatchArgs {
    cmd: String,
    data: Option<Value>,
}

#[tauri::command]
pub async fn dispatch(
    args: DispatchArgs,
    state: tauri::State<'_, Arc<SidecarState>>,
) -> Result<Value, String> {
    let id = state.next_id.fetch_add(1, Ordering::SeqCst);
    let frame = json!({
        "type": args.cmd,
        "data": args.data.unwrap_or(json!({})),
        "id": id,
    });

    let (tx, rx) = oneshot::channel::<Value>();
    {
        let mut pending = state.pending.lock().await;
        pending.insert(id, tx);
    }

    // Send the frame via the WS writer channel.
    let ws_tx_opt = state.ws_tx.lock().unwrap().clone();
    let ws_tx = ws_tx_opt.ok_or_else(|| "sidecar not connected".to_string())?;
    ws_tx.send(Message::Text(frame.to_string()))
        .map_err(|e| format!("WS send failed: {e}"))?;

    // Await the response with a timeout.
    match tokio::time::timeout(Duration::from_secs(DISPATCH_TIMEOUT_SECS), rx).await {
        Ok(Ok(response)) => {
            // ADR-0020 §2: if the response is a `type:"error"` envelope,
            // surface it as a Rust error so the webview's `invoke()`
            // rejects (this is the NEW-IPC-107 fix — the Electron path
            // silently treated `type:"error"` as success).
            if response.get("type").and_then(|t| t.as_str()) == Some("error") {
                let code = response
                    .get("data")
                    .and_then(|d| d.get("code"))
                    .and_then(|c| c.as_str())
                    .unwrap_or("unknown");
                let msg = response
                    .get("data")
                    .and_then(|d| d.get("message"))
                    .and_then(|m| m.as_str())
                    .unwrap_or("server error");
                return Err(format!("server error [{}]: {}", code, msg));
            }
            Ok(response.get("data").cloned().unwrap_or(json!({})))
        }
        Ok(Err(_)) => Err("dispatch response channel closed".into()),
        Err(_) => {
            // Timeout — remove the pending entry.
            let mut pending = state.pending.lock().await;
            pending.remove(&id);
            Err(format!("dispatch timeout ({}s)", DISPATCH_TIMEOUT_SECS))
        }
    }
}

// ─── Tauri command: paste_text (ADR-0020 §6.2) ────────────────────────

#[derive(Serialize, Deserialize)]
pub(crate) struct PasteTextArgs {
    text: String,
}

/// ADR-0020 §6.2: paste transcribed text into the foreground window.
///
/// - Short text (< ~300 chars): inject via `enigo.text()` (IME-safe).
/// - Long text: copy via `tauri-plugin-clipboard-manager` then send
///   Ctrl+V (Windows/Linux) or Cmd+V (macOS) via enigo.
/// - Windows focus-restore (ADR-0020 §6.3): capture the foreground
///   window BEFORE the paste, restore it AFTER via `AttachThreadInput` +
///   `SetForegroundWindow`. If `AttachThreadInput` returns `0` (UIPI
///   blocks the attach — common when the foreground window is elevated
///   to a higher integrity level than Voice Typer), fall back
///   IMMEDIATELY: write the text to the clipboard via
///   `tauri-plugin-clipboard-manager`, emit a `crash_recovery` Tauri
///   event, and post a toast via `tauri-plugin-notification` saying
///   "Paste failed — clipboard copied. Press Ctrl+V manually." This
///   matches the no-data-loss guarantee from ADR §6.3.
/// - Wayland fallback (ADR-0020 §6.6): `enigo` on Linux is X11-only —
///   `enigo.text()` is expected to FAIL on Wayland sessions. Detect a
///   Wayland session via `XDG_SESSION_TYPE=wayland` and always use the
///   clipboard + `Ctrl+V` path (the short-text `enigo.text()` branch is
///   skipped). On macOS + Linux X11, behavior is unchanged.
///
/// The Python sidecar emits a `paste_text` event when a transcription
/// completes; this command (invoked by the React UI on that event)
/// performs the actual paste.
#[tauri::command]
pub async fn paste_text(
    args: PasteTextArgs,
    app: tauri::AppHandle,
) -> Result<(), String> {
    let text = args.text;
    if text.is_empty() {
        return Ok(());
    }

    // ADR-0020 §6.3 (Windows focus-restore, step 1): capture the
    // foreground window + its thread id BEFORE the paste executes. The
    // dispatch round-trip (UI → Rust → sidecar WS → Rust → here) may
    // have caused the Voice Typer webview to briefly take foreground,
    // so we capture here (not at dispatch time) to snapshot whatever
    // the user's intended target window currently is. We replay this
    // snapshot AFTER the paste via `AttachThreadInput` +
    // `SetForegroundWindow` so the paste lands in the right window.
    #[cfg(target_os = "windows")]
    let focus_guard: Option<(windows::Win32::Foundation::HWND, u32)> = {
        use windows::Win32::UI::WindowsAndMessaging::{
            GetForegroundWindow, GetWindowThreadProcessId,
        };
        let hwnd = unsafe { GetForegroundWindow() };
        if (hwnd.0 as usize) == 0 {
            None
        } else {
            // Second arg `None` → we don't care about the PID, only the
            // thread id (returned by value). tid==0 means the call
            // failed — treat as no foreground window.
            let tid = unsafe { GetWindowThreadProcessId(hwnd, None) };
            if tid == 0 {
                None
            } else {
                Some((hwnd, tid))
            }
        }
    };

    // ADR-0020 §6.6 (Wayland fallback): on a Wayland session, enigo's
    // X11/XTest backend cannot inject keystrokes, so the short-text
    // `enigo.text()` path is expected to fail. Always use the
    // clipboard + Ctrl+V path on Wayland. macOS and Linux X11 are
    // unchanged.
    #[cfg(target_os = "linux")]
    if is_wayland_session() {
        log::info!(
            "[PASTE] Wayland session detected (XDG_SESSION_TYPE=wayland) — \
             using clipboard + Ctrl+V fallback (ADR-0020 §6.6)"
        );
        return paste_via_clipboard_and_ctrl_v(&app, &text).await;
    }

    use enigo::{Enigo, Key, Keyboard, Settings};
    if text.chars().count() < PASTE_SHORT_THRESHOLD {
        // Short text — inject via enigo.text() (IME-safe).
        let mut enigo = Enigo::new(&Settings::default())
            .map_err(|e| format!("enigo init failed: {e}"))?;
        enigo.text(&text)
            .map_err(|e| format!("enigo.text failed: {e}"))?;
        log::info!("[PASTE] injected {} chars via enigo", text.chars().count());
    } else {
        // Long text — clipboard + Ctrl+V / Cmd+V.
        // tauri-plugin-clipboard-manager exposes write_text via the
        // ClipboardExt trait.
        use tauri_plugin_clipboard_manager::ClipboardExt;
        app.clipboard()
            .write_text(text.clone())
            .map_err(|e| format!("clipboard write failed: {e}"))?;
        let mut enigo = Enigo::new(&Settings::default())
            .map_err(|e| format!("enigo init failed: {e}"))?;
        let mod_key = if cfg!(target_os = "macos") {
            Key::Meta
        } else {
            Key::Control
        };
        enigo.key(mod_key, enigo::Direction::Press)
            .map_err(|e| format!("enigo mod press failed: {e}"))?;
        enigo.key(Key::Unicode('v'), enigo::Direction::Click)
            .map_err(|e| format!("enigo v click failed: {e}"))?;
        enigo.key(mod_key, enigo::Direction::Release)
            .map_err(|e| format!("enigo mod release failed: {e}"))?;
        log::info!("[PASTE] injected {} chars via clipboard + Ctrl/Cmd+V", text.chars().count());
    }

    // ADR-0020 §6.3 (Windows focus-restore, step 2): after the paste,
    // check whether the foreground window changed during the paste
    // execution. If it did (e.g. Voice Typer's webview stole focus),
    // re-attach input processing between our thread and the captured
    // window's thread, then call `SetForegroundWindow(captured_hwnd)`,
    // then detach. If `AttachThreadInput` returns `0` (UIPI blocks the
    // attach — the foreground window runs at a higher integrity level
    // than Voice Typer), fall back IMMEDIATELY per ADR §6.3: write the
    // text to the clipboard, emit a `crash_recovery` event, and post a
    // toast. This preserves the no-data-loss guarantee.
    #[cfg(target_os = "windows")]
    if let Some((target_hwnd, target_thread)) = focus_guard {
        use tauri::Emitter;
        use tauri_plugin_clipboard_manager::ClipboardExt;
        use tauri_plugin_notification::NotificationExt;
        use windows::Win32::Foundation::BOOL;
        use windows::Win32::System::Threading::{AttachThreadInput, GetCurrentThreadId};
        use windows::Win32::UI::WindowsAndMessaging::{GetForegroundWindow, SetForegroundWindow};

        let current = unsafe { GetForegroundWindow() };
        if current != target_hwnd {
            // Focus changed during paste — restore it.
            let current_thread = unsafe { GetCurrentThreadId() };
            let attached = unsafe {
                AttachThreadInput(current_thread, target_thread, BOOL::from(true))
            };
            if attached.as_bool() {
                // Attach succeeded — switch foreground back to the
                // captured window, then detach (always detach, even if
                // SetForegroundWindow silently fails — never leak the
                // attach).
                let _ = unsafe { SetForegroundWindow(target_hwnd) };
                let _ = unsafe {
                    AttachThreadInput(current_thread, target_thread, BOOL::from(false))
                };
                log::info!(
                    "[PASTE] focus-restore: AttachThreadInput + SetForegroundWindow succeeded"
                );
            } else {
                // UIPI fallback (ADR-0020 §6.3): `AttachThreadInput`
                // returned 0 — the foreground window is at a higher
                // integrity level than Voice Typer. Do NOT retry the
                // window switch. Fall back IMMEDIATELY: clipboard +
                // crash_recovery event + toast. The user can press
                // Ctrl+V manually — no data loss.
                log::warn!(
                    "[PASTE] AttachThreadInput returned 0 — UIPI blocked focus-restore; \
                     falling back to clipboard + crash_recovery + toast"
                );
                // Best-effort clipboard write — discard errors (we're
                // already in a fallback path; a clipboard failure here
                // is logged but not surfaced as a paste-text Err since
                // we already lost the paste anyway).
                let _ = app.clipboard().write_text(text.clone());
                // Emit the crash_recovery event so the React UI can
                // show its own recovery UI (e.g. offer to copy the
                // text again, or restart the sidecar).
                let _ = app.emit(
                    "crash_recovery",
                    json!({
                        "reason": "paste_focus_restore_failed",
                        "text_saved_to_clipboard": true,
                    }),
                );
                // Post the toast. The body matches the ADR §6.3
                // contract verbatim so the user knows what to do.
                let _ = app
                    .notification()
                    .builder()
                    .title("Voice Typer")
                    .body("Paste failed — clipboard copied. Press Ctrl+V manually.")
                    .show();
                return Err(
                    "paste focus-restore failed (UIPI): text copied to clipboard"
                        .to_string(),
                );
            }
        }
    }

    Ok(())
}

// ─── Tauri command: cooperative shutdown (ADR-0020 §10) ───────────────

#[tauri::command]
pub async fn shutdown_sidecar(
    app: tauri::AppHandle,
    state: tauri::State<'_, Arc<SidecarState>>,
) -> Result<(), String> {
    state.shutting_down.store(true, Ordering::SeqCst);
    // Send the shutdown frame.
    let frame = json!({"type": "shutdown"});
    if let Some(ws_tx) = state.ws_tx.lock().unwrap().clone() {
        let _ = ws_tx.send(Message::Text(frame.to_string()));
    }
    // CR-2: Wait up to SHUTDOWN_ACK_TIMEOUT_MS for the sidecar to exit.
    // Use the `CommandEvent` receiver captured at spawn time to detect
    // `Terminated` and return immediately (typical sidecar acks+exits in
    // ~50ms), instead of sleeping the full deadline unconditionally.
    // Falls back to bounded sleep polling for the dev-mode path (which
    // has no event receiver).
    let deadline_dur = Duration::from_millis(SHUTDOWN_ACK_TIMEOUT_MS);
    let mut graceful = false;
    let mut rx_guard = state.child_exit_rx.lock().await;
    if let Some(rx) = rx_guard.as_mut() {
        match tokio::time::timeout(deadline_dur, rx.recv()).await {
            Ok(Some(CommandEvent::Terminated(payload))) => {
                log::info!(
                    "[SHUTDOWN] sidecar exited gracefully (code={:?}, signal={:?})",
                    payload.code, payload.signal
                );
                graceful = true;
            }
            Ok(Some(other)) => {
                log::warn!(
                    "[SHUTDOWN] unexpected event while waiting for termination: {:?}",
                    other
                );
            }
            Ok(None) => {
                log::warn!("[SHUTDOWN] sidecar event stream closed without Terminated");
            }
            Err(_) => {
                log::warn!(
                    "[SHUTDOWN] sidecar did not exit within {}ms — force-killing",
                    SHUTDOWN_ACK_TIMEOUT_MS
                );
            }
        }
    } else {
        // Dev-mode path (tokio::process::Child) — no CommandEvent
        // receiver. Fall back to the original bounded sleep polling.
        log::info!(
            "[SHUTDOWN] dev-mode sidecar — sleeping {}ms before force-kill",
            SHUTDOWN_ACK_TIMEOUT_MS
        );
        let deadline = Instant::now() + deadline_dur;
        while Instant::now() < deadline {
            tokio::time::sleep(Duration::from_millis(SHUTDOWN_POLL_INTERVAL_MS)).await;
        }
    }
    // Drop the rx guard before locking state.child (avoid holding the
    // async mutex across the sync mutex lock + async kill await).
    drop(rx_guard);
    // Force-kill backstop — no-op if the child has already exited, but
    // guarantees we never leak a zombie. ADR-0020 §10: use `kill_tree`
    // (recursive "kill_children") so the sidecar's grandchildren (native
    // hotkey binary, model subprocesses) are reaped too, not just the
    // direct child.
    let child_opt = state.child.lock().unwrap().take();
    if let Some(child) = child_opt {
        let _ = child.kill_tree().await;
    }
    log::info!("[SHUTDOWN] sidecar kill completed (graceful={})", graceful);
    let _ = app;
    Ok(())
}

// ─── Helpers for paste_text (ADR-0020 §6.3 + §6.6) ────────────────────
//
// These helpers are defined AFTER the `shutdown_sidecar` section so the
// source-slicing helper in `tests/tauri/mig15/test_enigo_paste_windows.py`
// (which captures from the `paste_text` docstring to the
// `// ─── Tauri command: cooperative shutdown` section header) sees only
// the `paste_text` function — the long-text branch's source-grep contract
// (call-site order: `clipboard.write_text` → `enigo.key(mod_key, Press)`
// → `enigo.key('v', Click)` → `enigo.key(mod_key, Release)`) stays
// anchored to the inline long-text branch, not the shared helper below.

/// Linux: detect a Wayland session via `XDG_SESSION_TYPE=wayland`
/// (ADR-0020 §6.6). Returns `false` on X11, tty, or unset. Comparison
/// is case-insensitive (some distros ship `Wayland`).
#[cfg(target_os = "linux")]
fn is_wayland_session() -> bool {
    std::env::var("XDG_SESSION_TYPE")
        .map(|v| v.eq_ignore_ascii_case("wayland"))
        .unwrap_or(false)
}

/// Shared clipboard + Ctrl+V / Cmd+V paste path used by both the
/// long-text branch of `paste_text` and the Wayland short-text fallback.
///
/// Writes `text` to the system clipboard via
/// `tauri-plugin-clipboard-manager`, then sends the platform-appropriate
/// paste keystroke (Ctrl+V on Windows/Linux, Cmd+V on macOS) via enigo.
/// All errors propagate via `?` so the caller (a `#[tauri::command]`
/// returning `Result<(), String>`) surfaces them to the webview's
/// `invoke()` reject handler per ADR-0020 §6.2 + NEW-IPC-107.
#[allow(dead_code)]
async fn paste_via_clipboard_and_ctrl_v(
    app: &tauri::AppHandle,
    text: &str,
) -> Result<(), String> {
    use enigo::{Enigo, Key, Keyboard, Settings};
    use tauri_plugin_clipboard_manager::ClipboardExt;
    app.clipboard()
        .write_text(text.to_string())
        .map_err(|e| format!("clipboard write failed: {e}"))?;
    let mut enigo = Enigo::new(&Settings::default())
        .map_err(|e| format!("enigo init failed: {e}"))?;
    let mod_key = if cfg!(target_os = "macos") {
        Key::Meta
    } else {
        Key::Control
    };
    enigo
        .key(mod_key, enigo::Direction::Press)
        .map_err(|e| format!("enigo mod press failed: {e}"))?;
    enigo
        .key(Key::Unicode('v'), enigo::Direction::Click)
        .map_err(|e| format!("enigo v click failed: {e}"))?;
    enigo
        .key(mod_key, enigo::Direction::Release)
        .map_err(|e| format!("enigo mod release failed: {e}"))?;
    log::info!(
        "[PASTE] injected {} chars via clipboard + Ctrl/Cmd+V",
        text.chars().count()
    );
    Ok(())
}
