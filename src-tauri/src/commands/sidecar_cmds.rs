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
    // guarantees we never leak a zombie.
    let child_opt = state.child.lock().unwrap().take();
    if let Some(child) = child_opt {
        let _ = child.kill().await;
    }
    log::info!("[SHUTDOWN] sidecar kill completed (graceful={})", graceful);
    let _ = app;
    Ok(())
}
