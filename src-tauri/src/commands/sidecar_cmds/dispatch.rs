#![allow(clippy::unreachable)] // tauri command macro expansion emits `unreachable!()` fallbacks

//! Generic `dispatch` Tauri command + dispatch helpers (ADR-0020 §7).
//! Timeout routing: docs/code-notes/tauri-host.md#dispatch-timeouts
//! C-TAURI-3: FLAT `(cmd: String, data: Option<Value>)` params only.

use crate::commands::require_main_window;
use crate::error::LausuError;
use crate::state::lock as mutex_lock;
use crate::state::SidecarState;
use crate::util::{
    DISPATCH_DOWNLOAD_TIMEOUT_SECS, DISPATCH_SHORT_TIMEOUT_SECS, DISPATCH_TIMEOUT_SECS,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::borrow::Cow;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::oneshot;
use tokio_tungstenite::tungstenite::Message;

use super::allowlist::{is_command_allowed, PENDING_MAX};

// Model-lifecycle commands get the long budget; others 15s.
// Routing: docs/code-notes/tauri-host.md#dispatch-timeouts
// `media_transcribe_start` resolves the pasted URL (yt-dlp extract)
// SYNCHRONOUSLY before acknowledging, which needs tens of seconds on a
// cold network (ADR-0023), so it shares the 120s budget.
const _LONG_RUNNING_COMMANDS: &[&str] = &[
    "download_model",
    "import_model",
    "delete_model",
    "cancel_model_download",
    "pause_model_download",
    "resume_model_download",
    "media_transcribe_start",
];

// Multi-GB transfer commands get the 1h download-scale cap
// (sidecar keeps downloading after a host timeout).
const _DOWNLOAD_COMMANDS: &[&str] = &["download_model", "import_model"];

/// WS envelope headroom subtracted from `MAX_FRAME_BYTES` for the data cap.
pub(crate) const ENVELOPE_HEADROOM_BYTES: usize = 256;

/// Dispatch data cap = transport frame ceiling minus envelope headroom.
/// Pinned by `dispatch_tests.rs`.
pub(crate) const DISPATCH_DATA_MAX_BYTES: usize =
    crate::util::MAX_FRAME_BYTES - ENVELOPE_HEADROOM_BYTES;

/// Dispatch timeout for `cmd` — 1h download / 120s model lifecycle / 15s else.
/// NOTE: see docs/code-notes/tauri-host.md#dispatch-timeouts
fn dispatch_timeout_for(cmd: &str) -> u64 {
    if _DOWNLOAD_COMMANDS.contains(&cmd) {
        DISPATCH_DOWNLOAD_TIMEOUT_SECS
    } else if _LONG_RUNNING_COMMANDS.contains(&cmd) {
        DISPATCH_TIMEOUT_SECS
    } else {
        DISPATCH_SHORT_TIMEOUT_SECS
    }
}

// ─── Tauri command: generic dispatch (ADR-0020 §7) ────────────────────

#[derive(Serialize, Deserialize)]
pub(crate) struct DispatchArgs {
    pub(crate) cmd: String,
    pub(crate) data: Option<Value>,
}

/// Internal dispatch: forwards to the sidecar, NO allowlist check
/// (trusted Rust callers only, e.g. tray `tray_click`). Public
/// `dispatch` wraps this with `is_command_allowed`.
pub(crate) async fn dispatch_inner(
    args: DispatchArgs,
    state: Arc<SidecarState>,
) -> Result<Value, LausuError> {
    dispatch_frame(&state, &args.cmd, args.data).await
}

/// Fire-and-forget WS send (id=0, no pending entry / no await).
/// Used by bubble `toggle_dictation` only (SEC-026 fixed command).
/// NOTE: see docs/code-notes/tauri-host.md#fire-and-forget-id0
pub(crate) fn dispatch_fire_and_forget(
    state: &Arc<SidecarState>,
    cmd: &str,
    data: Option<Value>,
) -> Result<(), LausuError> {
    let frame = json!({
        "type": cmd,
        "data": data.unwrap_or(json!({})),
        "id": 0u64,
    });
    let ws_tx_opt = mutex_lock(&state.ws_tx).clone();
    let ws_tx = ws_tx_opt.ok_or(LausuError::NotConnected)?;
    // try_send: sync path; Full/Closed mirror dispatch_frame errors.
    ws_tx
        .try_send(Message::Text(frame.to_string().into()))
        .map_err(|e| LausuError::SendFailed {
            message: e.to_string(),
        })?;
    Ok(())
}

// ─── pending-entry Drop guard (cancellation cleanup) ───────────────────

/// Drop guard: removes the pending-map entry on cancellation (explicit
/// remove paths miss a dropped future). Double-remove is a HashMap no-op.
/// NOTE: see docs/code-notes/tauri-host.md#pending-entry-drop-guard
struct PendingEntryGuard {
    state: Arc<SidecarState>,
    id: u64,
}

impl PendingEntryGuard {
    /// Owns an `Arc` clone so the detached removal task stays valid.
    fn new(state: &Arc<SidecarState>, id: u64) -> Self {
        Self {
            state: state.clone(),
            id,
        }
    }

    /// C-TOKIO-1: Drop may run on a runtime worker; `async_runtime::spawn`
    /// is submit-only (no block_on). JoinHandle detached on purpose.
    fn remove_pending_async(&self) {
        let state = self.state.clone();
        let id = self.id;
        tauri::async_runtime::spawn(async move {
            let mut pending = state.pending.lock().await;
            // Cancellation path only — normal exits already removed it.
            if pending.remove(&id).is_some() {
                log::debug!("[dispatch] id={} pending entry removed by Drop guard", id);
            }
        });
    }
}

impl Drop for PendingEntryGuard {
    fn drop(&mut self) {
        self.remove_pending_async();
    }
}

/// Shared WS dispatch body: frame → pending oneshot → send → await.
/// Used by public `dispatch` and tray `on_menu_event`.
/// NOTE: see docs/code-notes/tauri-host.md#dispatch-frame
async fn dispatch_frame(
    state: &Arc<SidecarState>,
    cmd: &str,
    data: Option<Value>,
) -> Result<Value, LausuError> {
    // Relaxed is fine: pure unique-id generator; no publish/order need.
    let id = state.next_id.fetch_add(1, Ordering::Relaxed);
    // Correlation id + cmd for WS reader fulfillment logs.
    log::debug!("[dispatch] id={} cmd={}", id, cmd);

    // Routing: docs/code-notes/tauri-host.md#dispatch-timeouts
    let timeout_secs = dispatch_timeout_for(cmd);

    // Bail during shutdown: late responses would timeout after teardown.
    if state.shutting_down.load(Ordering::SeqCst) {
        log::warn!(
            "[dispatch] id={} cmd={} rejected: sidecar shutting down",
            id,
            cmd
        );
        return Err(LausuError::ShuttingDown);
    }

    // Cap data BEFORE pending insert / writer enqueue (writer's 1 MiB
    // check is too late against a burst of oversized frames).
    // Cap = MAX_FRAME_BYTES − envelope headroom (docs/code-notes/tauri-host.md).
    // Serialize once into Cow for size check + frame body.
    let data_str: Cow<'static, str> = match data.as_ref() {
        Some(data_val) => {
            Cow::Owned(serde_json::to_string(data_val).unwrap_or_else(|_| "null".to_string()))
        }
        None => Cow::Borrowed("{}"),
    };
    if data_str.len() > DISPATCH_DATA_MAX_BYTES {
        log::warn!(
            "[dispatch] id={} cmd={} rejected: data payload {} bytes > {} byte cap",
            id,
            cmd,
            data_str.len(),
            DISPATCH_DATA_MAX_BYTES
        );
        return Err(LausuError::DataTooLarge);
    }

    // Manual frame: type quoted via serde_json, data pre-serialized, id numeric.
    let cmd_json = serde_json::to_string(cmd).unwrap_or_else(|_| "\"\"".to_string());
    let frame_str = format!(r#"{{"type":{},"data":{},"id":{}}}"#, cmd_json, data_str, id);

    // Check ws_tx BEFORE pending insert so a None path cannot leak an entry.
    let ws_tx_opt = mutex_lock(&state.ws_tx).clone();
    let ws_tx = match ws_tx_opt {
        Some(tx) => tx,
        None => {
            log::warn!(
                "[dispatch] id={} cmd={} rejected: sidecar not connected (ws_tx is None)",
                id,
                cmd
            );
            return Err(LausuError::NotConnected);
        }
    };

    let (tx, rx) = oneshot::channel::<Value>();
    {
        let mut pending = state.pending.lock().await;
        // Cap inside the lock: renderer treats PendingFull as backpressure.
        if pending.len() >= PENDING_MAX {
            log::warn!(
                "[dispatch] id={} cmd={} rejected: pending map at capacity ({}/{}); \
                 sidecar unresponsive: renderer should back off and retry",
                id,
                cmd,
                pending.len(),
                PENDING_MAX
            );
            return Err(LausuError::PendingFull);
        }
        pending.insert(id, tx);
    }
    // Drop guard owns the entry from insert onward (covers cancellation).
    let _pending_guard = PendingEntryGuard::new(state, id);

    // On send failure remove the pending entry (writer exited; drain may not have run).
    if let Err(e) = ws_tx.try_send(Message::Text(frame_str.into())) {
        let mut pending = state.pending.lock().await;
        pending.remove(&id);
        let err = match &e {
            tokio::sync::mpsc::error::TrySendError::Closed(_) => LausuError::NotConnected,
            tokio::sync::mpsc::error::TrySendError::Full(_) => LausuError::SendFailed {
                message: e.to_string(),
            },
        };
        log::warn!(
            "[dispatch] id={} cmd={} WS send failed: {} (pending entry removed)",
            id,
            cmd,
            e
        );
        return Err(err);
    }

    // Await the response with a timeout.
    match tokio::time::timeout(Duration::from_secs(timeout_secs), rx).await {
        Ok(Ok(mut response)) => {
            // type:"error" → Rust Err so the webview invoke() rejects (ADR-0020 §2).
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
                log::warn!(
                    "[dispatch] id={} cmd={} server error [{}]: {}",
                    id,
                    cmd,
                    code,
                    msg
                );
                // Pass data VERBATIM so renderer fields (errors[], consent_field, …) survive.
                let data = response
                    .get_mut("data")
                    .map(Value::take)
                    .unwrap_or(json!({}));
                return Err(LausuError::server_from_data(data));
            }
            // Value::take: O(1) move, no deep clone on hot path.
            let data = response
                .get_mut("data")
                .map(Value::take)
                .unwrap_or(json!({}));
            Ok(data)
        }
        Ok(Err(_)) => {
            // Sender dropped: WS reader exited mid-response.
            log::warn!(
                "[dispatch] id={} cmd={} response channel closed (WS reader dropped)",
                id,
                cmd
            );
            Err(LausuError::ChannelClosed)
        }
        Err(_) => {
            let mut pending = state.pending.lock().await;
            pending.remove(&id);
            log::error!(
                "[dispatch] id={} cmd={} timed out after {}s (pending entry removed)",
                id,
                cmd,
                timeout_secs
            );
            Err(LausuError::Timeout { secs: timeout_secs })
        }
    }
}

#[tauri::command]
pub async fn dispatch(
    cmd: String,
    data: Option<Value>,
    state: tauri::State<'_, Arc<SidecarState>>,
    window: tauri::Window,
) -> Result<Value, LausuError> {
    // C-TAURI-3: FLAT (cmd, data) params — a struct arg breaks every invoke.
    // NOTE: see docs/code-notes/tauri-host.md#dispatch-arg-shape-c-tauri-3
    let args = DispatchArgs { cmd, data };

    // 64-char cmd cap before guard/allowlist (DoS on writer/serializer).
    if args.cmd.len() > 64 {
        // Log length only (logging a multi-MB cmd is itself a DoS vector).
        log::warn!(
            "rejected dispatch command with length {} (>64 char cap)",
            args.cmd.len()
        );
        return Err(LausuError::Host("command name too long".into()));
    }

    // SEC-026: bubble is sandboxed and must not drive the sidecar command surface.
    // Capabilities do not gate user-defined #[tauri::command]s — runtime label check.
    require_main_window(&window)?;

    // SEC-019: ALLOWED_COMMANDS allowlist before sidecar forward.
    if !is_command_allowed(&args.cmd) {
        log::warn!(
            "[DISPATCH-ALLOWLIST] rejected disallowed dispatch command: {:?} (not in ALLOWED_COMMANDS)",
            args.cmd
        );
        return Err(LausuError::DisallowedCommand);
    }

    // Already allowlisted above; Arc clone makes this callable outside Tauri commands.
    dispatch_inner(args, state.inner().clone()).await
}

// C-TEST-5: sibling test file (no inline tests in production source).
#[cfg(test)]
#[path = "dispatch_tests.rs"]
mod dispatch_tests;
