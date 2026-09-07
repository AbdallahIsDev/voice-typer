#![allow(clippy::unreachable)] // tauri command macro expansion emits `unreachable!()` fallbacks

//! Generic `dispatch` Tauri command + dispatch helpers (ADR-0020 §7) —
//! extracted from the former single-file `commands/sidecar_cmds.rs`.

use crate::commands::require_main_window;
use crate::error::VoiceTyperError;
use crate::state::SidecarState;
// state::lock (aliased `mutex_lock`): poison-safe Mutex helper. Replaces inline
// `.lock().unwrap()` so a poisoned mutex (a prior panic while holding
// the lock) does not re-panic and permanently brick the dispatch path.
use crate::state::lock as mutex_lock;
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

// per-command dispatch timeout routing ──────────────────────
//
// Previously every `dispatch` call used the uniform 120s
// `DISPATCH_TIMEOUT_SECS` timeout. That let a hung `get_status` poll
// (median response <50ms) block the UI for 2 minutes before
// rejecting. `dispatch_timeout_for` below routes model-lifecycle
// commands (which can
// legitimately take >15s) to the long 120s timeout, and everything
// else to the new 15s `DISPATCH_SHORT_TIMEOUT_SECS`.
//
// Model lifecycle commands are the 6 entries below — they involve
// network I/O (download), filesystem I/O (import/delete), or
// subprocess management (cancel/pause/resume) that can each take
// 10s+ on a slow connection / cold disk.
const _LONG_RUNNING_COMMANDS: &[&str] = &[
    "download_model",
    "import_model",
    "delete_model",
    "cancel_model_download",
    "pause_model_download",
    "resume_model_download",
];

// Commands that stream a multi-GB model file and can legitimately run
// for tens of minutes. They get the download-scale 1h cap instead of the
// 120s long-running cap — the sidecar keeps the download running after
// a host-side timeout, so a premature cap produced a false-failure UI
// (Retry button) over an in-flight download.
const _DOWNLOAD_COMMANDS: &[&str] = &["download_model", "import_model"];

/// Returns the dispatch timeout (in seconds) for `cmd`.
///
/// - 1h (`DISPATCH_DOWNLOAD_TIMEOUT_SECS`) for the multi-GB transfer
///   commands (`download_model` / `import_model`).
/// - 120s (`DISPATCH_TIMEOUT_SECS`) for the remaining model lifecycle
///   commands listed in [`_LONG_RUNNING_COMMANDS`] — delete / cancel /
///   pause / resume complete in seconds but can stall on slow disk.
/// - 15s (`DISPATCH_SHORT_TIMEOUT_SECS`) for everything else — the
///   sidecar's median response time is <50ms, so 15s is generous
///   while still bounding the worst-case UI freeze.
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

/// Internal dispatch path: forwards a command to the Python sidecar
/// over WS and awaits the per-id response. Performs NO allowlist check
/// — callers are trusted Rust-internal code (e.g. the tray menu click
/// handler, which routes `tray_click` — a Rust-only command that is
/// NOT in the renderer `ALLOWED_COMMANDS` set because the renderer
/// never invokes it; the Rust↔TS allowlist parity tests would fail
/// if it were added).
///
/// The public `dispatch` Tauri command wraps this with the allowlist
/// gate (`is_command_allowed`) before delegating. Trusted Rust callers that need to
/// send a non-allowlisted command (currently only `tray_click` from
/// `tray.rs::on_menu_event`) call this directly.
///
/// `state` is taken as `Arc<SidecarState>` (not `tauri::State`) so
/// this function is callable from contexts that aren't Tauri command
/// invocations (e.g. an `async_runtime::spawn` block in the tray
/// handler).
pub(crate) async fn dispatch_inner(
    args: DispatchArgs,
    state: Arc<SidecarState>,
) -> Result<Value, VoiceTyperError> {
    // The dispatch body is extracted into the shared
    // `dispatch_frame` helper below so the tray menu handler
    // (`tray.rs::on_menu_event`) can call it directly instead of
    // emitting a Tauri "dispatch" event that has no listener.
    dispatch_frame(&state, &args.cmd, args.data).await
}

// dispatch_fire_and_forget: fire-and-forget dispatch helper.
///
/// Builds a WS frame `{"type": cmd, "data": data, "id": 0}` and sends it
/// via `state.ws_tx.try_send` WITHOUT inserting a pending oneshot entry
/// or awaiting a response. Used by `commands::bubble::bubble_toggle_dictation`
/// (a sandboxed-window command that must NOT use the full `dispatch`
/// path — the bubble renderer is allowed to send only the fixed
/// `toggle_dictation` command — see the sanctioned-bypass rationale
/// in `commands/bubble/commands.rs::bubble_toggle_dictation`).
///
/// The synthetic `id: 0` is NOT special-cased server-side.
/// The Python sidecar's `dispatch` coroutine (in
/// `voice_typer/server/sidecar_ws.py`) treats `id=0` like any other
/// request id: it runs the handler, and if the handler returns a
/// non-`None` response envelope (which `_handle_toggle_dictation`
/// always does — it sets `resp["type"] = "ack"`), the server echoes
/// the response back over the WS with `"id": 0` attached. The Rust
/// WS reader (`sidecar/ws/reader.rs`) then looks up `id=0` in
/// `state.pending`, finds no entry (because `dispatch_fire_and_forget`
/// never inserted one), and drops the frame after a single
/// DEBUG-level `[WS-READER] RX response id=0 had NO pending entry`
/// line — no WARN fires (the `pending.remove(&id)` call returns
/// `None`, the `if let Some(tx) = ...` fulfillment branch is
/// skipped, and the loop continues).
///
/// The previous doc text here was internally contradictory: it
/// claimed BOTH "server does NOT echo `id=0` back" AND "one-line
/// `[WS-READER] unknown id` warning per toggle" — two mutually
/// exclusive statements. The actual behavior is: server echoes the
/// response back, reader silently drops it. The net effect (no
/// response delivered to the caller, no warning noise) is what the
/// fire-and-forget semantics require, but the mechanism is "drop on
/// the reader side" rather than "suppress on the server side".
///
/// Replaces the inline `json!` + `lock` + `try_send` block that used
/// to live in `commands/bubble/commands.rs::bubble_toggle_dictation`
/// (a noted cleanup TODO called for this extraction). Keeps the poison-safe `mutex_lock` helper so a
/// poisoned mutex doesn't brick the bubble's mic button permanently.
///
/// Returns `Err` if `ws_tx` is `None` (sidecar disconnected) or if
/// `try_send` fails (channel full or writer task exited). Both error
/// variants mirror the shape used by `dispatch_frame` so the renderer's
/// existing reject path handles them identically.
pub(crate) fn dispatch_fire_and_forget(
    state: &Arc<SidecarState>,
    cmd: &str,
    data: Option<Value>,
) -> Result<(), VoiceTyperError> {
    let frame = json!({
        "type": cmd,
        "data": data.unwrap_or(json!({})),
        "id": 0u64,
    });
    let ws_tx_opt = mutex_lock(&state.ws_tx).clone();
    let ws_tx = ws_tx_opt.ok_or(VoiceTyperError::NotConnected)?;
    // `ws_tx` is a bounded `mpsc::Sender` — use `try_send`
    // (synchronous) rather than `.send().await` (which would require an
    // async context AND block on the writer-task consumer). Returns
    // `TrySendError::Full` if the writer is overwhelmed (256-cap) or
    // `TrySendError::Closed` if the writer task exited.
    ws_tx
        .try_send(Message::Text(frame.to_string().into()))
        .map_err(|e| VoiceTyperError::SendFailed {
            message: e.to_string(),
        })?;
    Ok(())
}

/// Shared dispatch body used by both the `dispatch` Tauri command
/// (renderer `invoke('dispatch', {cmd, data})` calls) and the tray menu
/// event handler in `tray.rs::on_menu_event` (which previously emitted
/// a Tauri event named "dispatch" that had no listener — the click was
/// silently dropped).
///
/// Builds a WS frame `{"type": cmd, "data": data, "id": <next_id>}`,
/// inserts a pending oneshot entry, sends the frame via `state.ws_tx`,
/// and awaits the response (or times out).
///
/// The pending entry is inserted AFTER confirming `ws_tx` is
/// `Some`. Previously the entry was inserted first and the early-return
/// Err branch on `ws_tx == None` leaked the entry — the WS reader never
/// fulfilled it (no frame was sent), so the map accumulated stale
/// senders across reconnects. We also remove the pending entry on
/// `ws_tx.send` failure (writer task has exited; the reader's drain
/// loop is the only other remover and may not have run yet).
///
/// Every `Err(...)` return is logged (4 sites: WS send
/// failed, dispatch response channel closed, dispatch timeout, server
/// error). A `log::debug!` at entry gives correlation (id + cmd) for
/// tracing dispatch lifetimes across the WS reader/writer tasks.
///
/// Bail out early if `state.shutting_down` is set. After
/// `shutdown_sidecar` sends the shutdown frame the WS may stay alive
/// briefly (up to `SHUTDOWN_ACK_TIMEOUT_MS`); dispatches initiated in
/// that window would send the frame but their response hits the
/// shutdown-suppress branch in the WS reader and is
/// dropped — the client then awaits its full per-command dispatch
/// timeout before rejecting. Short-circuit here instead.
///
/// Re-check `state.ws_tx` AFTER inserting the pending
/// entry. A reconnect racing in the window between the outer
/// `mutex_lock(&state.ws_tx).clone()` and the pending insert could leave
/// us holding a stale `ws_tx` (the old writer task has exited; the new
/// reader has no record of this id). Detect by re-checking
/// `state.ws_tx` under a tight critical section; if it's now `None`,
/// drop the pending entry and reject.
///
/// Demoted from `pub(crate) async fn` to `async fn` — the
/// only caller is `dispatch_inner` in this same file (the tray menu
/// handler in `tray.rs` calls `dispatch_inner`, not `dispatch_frame`
/// directly).
async fn dispatch_frame(
    state: &Arc<SidecarState>,
    cmd: &str,
    data: Option<Value>,
) -> Result<Value, VoiceTyperError> {
    // Relaxed is sufficient here: `next_id` is a pure unique-id
    // generator. Uniqueness comes from the atomic RMW itself, not from
    // the ordering — no other data is published through this operation
    // and no other thread reasons about the counter's history (the id
    // is used only as a pending-map key + log-correlation tag + frame
    // field). Matches the Relaxed fetch_add already used by the
    // exit-shutdown id path in `sidecar/shutdown.rs`.
    let id = state.next_id.fetch_add(1, Ordering::Relaxed);
    // Debug-level entry log for correlation. The WS reader
    // logs the matching `id` on fulfillment so a slow / dropped
    // dispatch can be traced end-to-end.
    log::debug!("[dispatch] id={} cmd={}", id, cmd);

    // Per-command timeout. Model lifecycle commands (download /
    // import / delete / cancel / pause / resume) get 120s; everything
    // else gets 15s. See `dispatch_timeout_for` for the rationale.
    let timeout_secs = dispatch_timeout_for(cmd);

    // Short-circuit if the host is shutting down. Avoids
    // the orphaned-pending-then-timeout window described above.
    if state.shutting_down.load(Ordering::SeqCst) {
        log::warn!(
            "[dispatch] id={} cmd={} rejected: sidecar shutting down",
            id,
            cmd
        );
        return Err(VoiceTyperError::ShuttingDown);
    }

    // Cap `args.data` serialized size BEFORE
    // constructing the WS frame. The WS layer's
    // `max_message_size=1 MiB` check fires in the writer task AFTER
    // the frame has been serialized and enqueued on the bounded
    // `mpsc::channel(256)` — a compromised renderer could send 256
    // concurrent ~5 MB dispatches (~1.28 GB peak) before the writer
    // task ever broke a single frame. Capping at 256 KiB (4× the WS
    // layer's 1 MiB / 4 cap to allow for cmd + id + JSON envelope
    // overhead while still preventing the OOM vector) rejects
    // oversized payloads at the dispatch entry point. The check runs
    // BEFORE the pending-map insert so an oversized payload never
    // consumes a pending slot.
    //
    // SINGLE-SERIALIZE OPTIMIZATION: the data Value is serialized
    // ONCE here into ``data_str``. The same ``data_str`` is then
    // reused for both the size check AND the manual frame
    // construction below — previously the size check called
    // ``serde_json::to_string(data_val).map(|s| s.len())`` (bytes
    // discarded) and ``frame.to_string()`` re-serialized the cloned
    // data Value, plus the data Value was CLONED into the frame.
    // For a 256 KiB ``set_config`` payload, that was ~512 KiB of
    // wasted serialization CPU + ~256 KiB of wasted heap allocation
    // + a deep Value clone per dispatch.
    const DISPATCH_DATA_MAX_BYTES: usize = 256 * 1024;
    // `Cow<'static, str>` for `data_str` so the `data: None` case
    // uses `Cow::Borrowed("{}")` (a zero-allocation static slice — no
    // heap-allocated `String` for the common 2-byte literal). The
    // `data: Some(v)` case stays `Cow::Owned(serde_json::to_string(v)...)`
    // (one heap allocation for the serialized payload, same as before).
    // The size check below (`data_str.len()`) and the `format!` frame
    // construction further down both work unchanged with a `Cow<str>`
    // (it derefs to `&str`).
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
        // The `DataTooLarge` variant's Display AND wire string are the
        // same envelope JSON this branch used to inline — single-sourced
        // in `error.rs`.
        return Err(VoiceTyperError::DataTooLarge);
    }

    // Build the WS frame manually using the pre-serialized
    // ``data_str``. ``serde_json::to_string(cmd)`` serializes the
    // ``&str`` as a JSON string (with quotes), which is the correct
    // shape for the ``"type"`` field. ``id`` is a ``u64`` and
    // formats directly as a JSON number. The resulting ``frame_str``
    // is byte-for-byte identical to what ``frame.to_string()`` would
    // have produced, but the data Value is serialized exactly once
    // (in the size check above) instead of twice.
    let cmd_json = serde_json::to_string(cmd).unwrap_or_else(|_| "\"\"".to_string());
    let frame_str = format!(r#"{{"type":{},"data":{},"id":{}}}"#, cmd_json, data_str, id);

    // Confirm `ws_tx` is Some BEFORE inserting into the pending
    // map so the early-return Err path doesn't leak a stale entry.
    let ws_tx_opt = mutex_lock(&state.ws_tx).clone();
    let ws_tx = match ws_tx_opt {
        Some(tx) => tx,
        None => {
            log::warn!(
                "[dispatch] id={} cmd={} rejected: sidecar not connected (ws_tx is None)",
                id,
                cmd
            );
            return Err(VoiceTyperError::NotConnected);
        }
    };

    let (tx, rx) = oneshot::channel::<Value>();
    {
        let mut pending = state.pending.lock().await;
        // Pending-map size cap. Reject new dispatches when
        // the map is at `PENDING_MAX` entries so an unresponsive
        // sidecar + rapid tray clicks / renderer retries can't grow
        // the map unbounded. The renderer treats `pending_full` as a
        // transient backpressure signal (distinct from "sidecar not
        // connected" / "sidecar shutting down") and backs off / retries.
        // The check is INSIDE the pending lock so the size read is
        // consistent with the insert (no TOCTOU window between a
        // racing `len()` read and `insert()`).
        if pending.len() >= PENDING_MAX {
            log::warn!(
                "[dispatch] id={} cmd={} rejected: pending map at capacity ({}/{}); \
                 sidecar unresponsive — renderer should back off and retry",
                id,
                cmd,
                pending.len(),
                PENDING_MAX
            );
            // Match the JSON-envelope error shape used by the
            // `disallowed_command` branch in `dispatch` (line ~687) so
            // the renderer's existing error-envelope switch can branch
            // on `code === "pending_full"` without a special case.
            //
            // The `PendingFull` variant's Display AND wire string are
            // the same envelope JSON this branch used to inline —
            // single-sourced in `error.rs`.
            return Err(VoiceTyperError::PendingFull);
        }
        pending.insert(id, tx);
    }

    // Optimization: a prior version of this dispatch path took a second
    // `state.ws_tx` lock here (the "needs_cleanup" check) to detect a
    // WS-disconnect that happened between the outer clone above and the
    // pending-entry insert. That second lock was redundant — the
    // `try_send` error path below already handles the WS-disconnected
    // case (TrySendError::Closed removes the pending entry and returns
    // the same "sidecar not connected" error). Removing the second lock
    // halves mutex contention per dispatch with no behavior change.

    // Send the frame via the WS writer channel. On send failure, remove
    // the pending entry too — the writer task has exited so the WS
    // reader's drain loop is the only other remover and it may not have
    // run yet (race window).
    if let Err(e) = ws_tx.try_send(Message::Text(frame_str.into())) {
        let mut pending = state.pending.lock().await;
        pending.remove(&id);
        let err = match &e {
            tokio::sync::mpsc::error::TrySendError::Closed(_) => VoiceTyperError::NotConnected,
            tokio::sync::mpsc::error::TrySendError::Full(_) => VoiceTyperError::SendFailed {
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
            // ADR-0020 §2: if the response is a `type:"error"` envelope,
            // surface it as a Rust error so the webview's `invoke()`
            // rejects (the Electron path silently treated
            // `type:"error"` as success — surfacing it as a rejection is
            // the host-side fix).
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
                // Pass the sidecar's error envelope through VERBATIM:
                // the `Server` variant serializes to
                // `{"type":"error","data":<data>}` so structured
                // payload fields the renderer branches on
                // (`data.errors[]`, `consent_field`, `engine_name`,
                // `model_id`, …) reach the webview intact. The former
                // `format!("server error [{}]: {}", code, msg)` concat
                // destroyed them — that flat string now lives only in
                // the variant's log-facing `Display` (the `log::warn!`
                // line above preserves it for tray / heartbeat
                // consumers). See `error.rs` + the error-envelope
                // contract doc.
                let data = response
                    .get_mut("data")
                    .map(Value::take)
                    .unwrap_or(json!({}));
                return Err(VoiceTyperError::server_from_data(data));
            }
            // Move the `data` field out of `response` instead of
            // deep-cloning it. `Value::take` swaps the field with
            // `Value::Null` and returns the moved value — O(1) on the
            // hot dispatch path instead of O(n) in the size of the
            // payload (history exports / model catalogs can be hundreds
            // of KB). `response` is dropped immediately after this
            // branch, so mutating it via `get_mut` is safe.
            let data = response
                .get_mut("data")
                .map(Value::take)
                .unwrap_or(json!({}));
            Ok(data)
        }
        Ok(Err(_)) => {
            // The oneshot sender was dropped without sending — the WS
            // reader exited (sidecar crashed / WS closed mid-response).
            log::warn!(
                "[dispatch] id={} cmd={} response channel closed (WS reader dropped)",
                id,
                cmd
            );
            Err(VoiceTyperError::ChannelClosed)
        }
        Err(_) => {
            // Timeout — remove the pending entry.
            let mut pending = state.pending.lock().await;
            pending.remove(&id);
            log::error!(
                "[dispatch] id={} cmd={} timed out after {}s (pending entry removed)",
                id,
                cmd,
                timeout_secs
            );
            Err(VoiceTyperError::Timeout { secs: timeout_secs })
        }
    }
}

#[tauri::command]
pub async fn dispatch(
    cmd: String,
    data: Option<Value>,
    state: tauri::State<'_, Arc<SidecarState>>,
    window: tauri::Window,
) -> Result<Value, VoiceTyperError> {
    // FLAT-ARGS CONTRACT (do not re-wrap into a struct param): the
    // renderer invokes `invoke('dispatch', { cmd, data })` — see
    // `python-namespace.ts` and the allowlist.rs doc comment. Tauri v2
    // maps each invoke key to a parameter NAME, so the previous single
    // `args: DispatchArgs` param made the host expect the top-level key
    // `args`; every renderer call failed with "invalid args `args` for
    // command `dispatch`: missing required key args" and the UI showed
    // "Lost connection to Python backend" even though the WS link was
    // healthy (first Windows host run, 2026-08-21). Renderer unit tests
    // stub `invoke()` and cannot catch Rust-side arg-name drift.
    // Binding rule: AGENTS.md constraint C-TAURI-3.
    let args = DispatchArgs { cmd, data };

    // Bound the command-name length so a buggy or compromised
    // renderer can't DoS the WS writer (or the allowlist lookup, or the
    // JSON serializer) with a multi-MB `cmd` string. The longest
    // legitimate command name in `ALLOWED_COMMANDS` is well under 32
    // chars; 64 leaves generous headroom for future additions while
    // still rejecting anything obviously pathological. Enforced BEFORE
    // the window-label guard and allowlist check so the cap applies
    // uniformly regardless of caller.
    if args.cmd.len() > 64 {
        // Log only the length (not the cmd itself) — logging the cmd
        // would itself be a DoS vector if it's multi-MB, and slicing
        // it for a preview could panic on a UTF-8 char boundary.
        log::warn!(
            "rejected dispatch command with length {} (>64 char cap)",
            args.cmd.len()
        );
        return Err(VoiceTyperError::Host("command name too long".into()));
    }

    // Window-label guard. The bubble renderer is a sandboxed
    // window with NO `dispatch` access (ADR-0020 §7 + §9 + SEC-026).
    // The capability file `bubble-runtime.json` deliberately omits
    // `dispatch`-related permissions, but Tauri v2's capability system
    // only gates plugin commands — user-defined `#[tauri::command]`
    // functions are NOT capability-gated. Without this runtime guard,
    // a compromised bubble renderer (e.g. XSS in the waveform pill)
    // could `invoke('dispatch', {cmd:'quit_app'})` to drive the full
    // server-side command surface. Reject any call where the source
    // window's label is not "main".
    //
    // the canonical `require_main_window` helper now lives in
    // `commands/mod.rs`. We delegate to it for the envelope shape +
    // log tag. The previous inline duplicate (with the
    // "dispatch only callable from main window" message) is removed —
    // the renderer's reject path JSON-parses the envelope + keys off
    // the `code` field (`disallowed_window`), so the per-command
    // message wording doesn't matter.
    require_main_window(&window)?;

    // Enforce the ALLOWED_COMMANDS allowlist BEFORE forwarding the
    // command to the Python sidecar over WS. This mirrors the Electron
    // renderer-side gate (SEC-019 / ADR-0015) and is the
    // defense-in-depth backstop for a compromised-renderer attack
    // (XSS in the WebView → `invoke('dispatch', {cmd:'<arbitrary>'})`).
    if !is_command_allowed(&args.cmd) {
        log::warn!(
            "[DISPATCH-ALLOWLIST] rejected disallowed dispatch command: {:?} (not in ALLOWED_COMMANDS)",
            args.cmd
        );
        // The `DisallowedCommand` variant's Display AND wire string are
        // the same envelope JSON this branch used to inline —
        // single-sourced in `error.rs` (codes from `allowlist.rs`).
        return Err(VoiceTyperError::DisallowedCommand);
    }

    // Delegate to the internal dispatch path (no allowlist check —
    // already done above). Clone the Arc<SidecarState> out of the
    // Tauri State wrapper so `dispatch_inner` is callable from non-
    // command contexts too (e.g. the tray menu click handler).
    dispatch_inner(args, state.inner().clone()).await
}
