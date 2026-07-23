//! Tauri commands: dispatch, paste_text, shutdown_sidecar (ADR-0020 §6.2 + §7 + §10).

use crate::state::SidecarState;
use crate::util::{DISPATCH_TIMEOUT_SECS, SHUTDOWN_ACK_TIMEOUT_MS, SHUTDOWN_POLL_INTERVAL_MS};
use std::collections::HashSet;
use std::sync::atomic::Ordering;
use std::sync::{Arc, OnceLock};
use std::time::{Duration, Instant};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tauri_plugin_shell::process::CommandEvent;
use tokio::sync::oneshot;
use tokio_tungstenite::tungstenite::Message;

// ─── G4-H-01: shared main-window guard ──────────────────────────────────
//
// `dispatch`, `paste_text`, and `shutdown_sidecar` are all
// `#[tauri::command]` functions that a compromised renderer could
// invoke over the IPC bridge. The bubble window is a sandboxed webview
// (ADR-0020 §7 + §9 + SEC-026) that must NEVER drive the sidecar WS
// or paste path. Tauri v2's capability system only gates plugin
// commands, so user-defined commands need this runtime check.
//
// The error envelope shape mirrors the sidecar's WS error envelope
// ({"type":"error","data":{"code":...,"message":...}}) so the
// renderer's existing reject path treats this identically to a
// server-side rejection.
fn require_main_window(window: &tauri::Window) -> Result<(), String> {
    if window.label() != "main" {
        log::warn!(
            "[G4-H-01] command rejected from non-main window: {}",
            window.label()
        );
        let err = json!({
            "type": "error",
            "data": {
                "code": "disallowed_window",
                "message": "command only allowed from main window"
            }
        });
        return Err(err.to_string());
    }
    Ok(())
}

// ─── CR-4: ALLOWED_COMMANDS allowlist (ADR-0015 defense-in-depth) ──────
//
// Mirrors the Electron renderer-side allowlist in
// `voice_typer/client/src/main/allowed-commands.ts` (the canonical
// declaration since R6-F10 — previously inline in `index.ts:79-191`,
// SEC-019). The Tauri `dispatch` command is the only path from the
// webview to the Python sidecar over WS — without this gate, a
// compromised renderer (XSS in the WebView, malicious extension) could
// invoke arbitrary server-side commands by
// `invoke('dispatch', {cmd: 'quit_app'})` or
// `invoke('dispatch', {cmd: 'set_config', data: {...}})`.
//
// The Electron path enforces `ALLOWED_COMMANDS` in
// `voice_typer/client/src/main/python/send-to-python.ts:48-51` — this
// Rust gate is the **defense-in-depth** equivalent (an attacker who
// escapes the renderer sandbox cannot bypass it by talking to Rust
// directly).
//
// PVT-G5-025: `delete_all_personal_data` and `export_gdpr_bundle` are
// now renderer-callable (the Settings → Privacy page exposes both —
// the renderer invokes `dispatch({cmd:'delete_all_personal_data'})`
// and `dispatch({cmd:'export_gdpr_bundle'})`). They are present in
// BOTH the TS allowlist (`allowed-commands.ts`) and this Rust literal
// so the defense-in-depth gate does not reject them.
//
// PVT-G5-075: `tray_click` is intentionally ABSENT from this literal
// (and from the TS allowlist) — it is a Rust-only command invoked by
// the tray menu handler in `tray.rs::on_menu_event` via
// `dispatch_inner`, which bypasses the allowlist gate. The renderer
// never sends `tray_click`; including it here would create an attack
// surface that only a compromised renderer could reach.
//
// KEEP IN SYNC with the TS allowlist:
//   - The Python test `tests/test_security_doc_command_count.py` (this
//     fix adds a parity assertion that the Rust `ALLOWED_COMMANDS`
//     set matches the TS set's command count + exact entries).
//   - The TS test in `tests/test_electron_ipc_and_build.py` cross-checks
//     the renderer allowlist against the server command registry.
//   - When adding/removing a command in TS, do the same here in the
//     same PR.
//
// The set is constructed once at process startup and stored in a
// `OnceLock` so all subsequent `dispatch` calls share the same
// `HashSet<&'static str>` allocation. `HashSet::contains` is O(1).
static ALLOWED_COMMANDS: OnceLock<HashSet<&'static str>> = OnceLock::new();

/// Returns the process-global `ALLOWED_COMMANDS` set, initializing it
/// on first call. Separated from the static so unit tests can call it
/// directly without going through `dispatch`.
pub(crate) fn allowed_commands() -> &'static HashSet<&'static str> {
    ALLOWED_COMMANDS.get_or_init(|| {
        // CR-4: this list MUST mirror the Electron renderer's
        // ALLOWED_COMMANDS in `voice_typer/client/src/main/allowed-commands.ts`
        // (canonical declaration since R6-F10 — was previously inline
        // in `index.ts`). The Python test
        // `tests/test_security_doc_command_count.py` cross-checks
        // parity (count + exact entries).
        let cmds: &[&str] = &[
            "get_status",
            "toggle_dictation",
            "undo_last",
            "get_config",
            "get_defaults",
            "set_config",
            "get_history",
            "search_history",
            "get_today_stats",
            "delete_history",
            "restore_history",
            "clear_history",
            "toggle_favorite",
            "get_favorites",
            "get_microphones",
            "restart_app",
            "quit_app",
            "get_templates",
            "save_templates",
            "get_volume_backend_status",
            "get_model_status",
            "get_prewarm_status",
            "run_prewarm",
            "open_prewarm_log",
            "get_vocabulary",
            "save_vocabulary",
            "onboarding_is_first_run",
            "onboarding_start",
            "onboarding_get_step",
            "onboarding_next_step",
            "onboarding_prev_step",
            "onboarding_set_microphone",
            "onboarding_set_hotkey",
            "onboarding_set_model",
            "onboarding_skip",
            "onboarding_apply",
            // PVT-G5-008: previously missing from the Rust literal —
            // present in the TS allowlist and in the server
            // `_COMMAND_REGISTRY`. Without these, the Onboarding
            // page's "Check Permissions" button (macOS/Linux mic +
            // accessibility prompts) and "Model Catalog" call would
            // reject with `disallowed_command` under Tauri.
            "onboarding_check_permissions",
            "onboarding_get_model_catalog",
            "onboarding_get_microphones",
            "onboarding_get_model_options",
            "onboarding_get_hotkey_presets",
            "download_model",
            "cancel_model_download",
            "pause_model_download",
            "resume_model_download",
            "test_llm_connection",
            "delete_model",
            "get_model_catalog",
            "microphone_test_start",
            "microphone_test_stop",
            "microphone_test_cancel",
            "microphone_test_status",
            "microphone_test_get_level",
            "level_monitor_start",
            "level_monitor_stop",
            "level_monitor_status",
            "set_esc_cancel_paused",
            "set_tray_locale",
            "import_model",
            "heartbeat",
            "relaunch_ack",
            "repaste_last",
            "refresh_microphones",
            "get_rms_level",
            "get_audio_status",
            "export_diagnostics",
            "check_accessibility",
            "show_electron_notification",
            "get_vocabulary_suggestions",
            "apply_vocabulary_suggestion",
            "dismiss_vocabulary_suggestion",
            "force_cancel_transcription",
            // PVT-G5-025: GDPR Art. 17 (right to erasure) + Art. 20
            // (right to data portability) — now renderer-callable from
            // the Settings → Privacy page. Mirrors the TS allowlist.
            "delete_all_personal_data",
            "export_gdpr_bundle",
            // Onboarding keyboard-permission request + reset — invoked
            // by the renderer's Onboarding page. Both are registered in
            // the Python-side `_COMMAND_REGISTRY` and implemented in
            // `handlers/onboarding_handlers.py`. Without these entries
            // in the Rust allowlist, the renderer's Onboarding page
            // calls would be rejected by the defense-in-depth gate
            // (`disallowed_command`) under Tauri even though they
            // succeed under Electron. Mirrors the TS allowlist (CR-4
            // parity — keep both files in sync).
            "onboarding_request_keyboard_permission",
            "onboarding_reset",
        ];
        let mut set = HashSet::with_capacity(cmds.len());
        for c in cmds {
            // Defensive — should never happen (no dupes in the literal
            // above), but a duplicate entry would silently drop one
            // command from the set. Log so a future copy-paste slip is
            // visible during dev.
            if !set.insert(*c) {
                log::error!(
                    "[CR-4] duplicate command in ALLOWED_COMMANDS literal: {} — \
                     update src-tauri/src/commands/sidecar_cmds.rs",
                    c
                );
            }
        }
        set
    })
}

/// Returns true iff `cmd` is in the `ALLOWED_COMMANDS` allowlist.
/// Pure + unit-testable (no state mutation).
pub(crate) fn is_command_allowed(cmd: &str) -> bool {
    allowed_commands().contains(cmd)
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
/// never invokes it; CR-4's allowlist parity test would fail if it
/// were added).
///
/// The public `dispatch` Tauri command wraps this with the allowlist
/// gate (CR-4) before delegating. Trusted Rust callers that need to
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
) -> Result<Value, String> {
    // CR-14: the dispatch body is extracted into the shared
    // `dispatch_frame` helper below so the tray menu handler
    // (`tray.rs::on_menu_event`) can call it directly instead of
    // emitting a Tauri "dispatch" event that has no listener.
    dispatch_frame(&state, &args.cmd, args.data).await
}

/// CR-14: shared dispatch body used by both the `dispatch` Tauri command
/// (renderer `invoke('dispatch', {cmd, data})` calls) and the tray menu
/// event handler in `tray.rs::on_menu_event` (which previously emitted
/// a Tauri event named "dispatch" that had no listener — the click was
/// silently dropped).
///
/// Builds a WS frame `{"type": cmd, "data": data, "id": <next_id>}`,
/// inserts a pending oneshot entry, sends the frame via `state.ws_tx`,
/// and awaits the response (or times out).
///
/// CR-50: the pending entry is inserted AFTER confirming `ws_tx` is
/// `Some`. Previously the entry was inserted first and the early-return
/// Err branch on `ws_tx == None` leaked the entry — the WS reader never
/// fulfilled it (no frame was sent), so the map accumulated stale
/// senders across reconnects. We also remove the pending entry on
/// `ws_tx.send` failure (writer task has exited; the reader's drain
/// loop is the only other remover and may not have run yet).
///
/// PVT-G5-017: every `Err(...)` return is logged (4 sites: WS send
/// failed, dispatch response channel closed, dispatch timeout, server
/// error). A `log::debug!` at entry gives correlation (id + cmd) for
/// tracing dispatch lifetimes across the WS reader/writer tasks.
///
/// PVT-G5-035: bail out early if `state.shutting_down` is set. After
/// `shutdown_sidecar` sends the shutdown frame the WS may stay alive
/// briefly (up to `SHUTDOWN_ACK_TIMEOUT_MS`); dispatches initiated in
/// that window would send the frame but their response hits the
/// shutdown-suppress branch in the WS reader (PVT-G5-013) and is
/// dropped — the client then awaits the full `DISPATCH_TIMEOUT_SECS`
/// before rejecting. Short-circuit here instead.
///
/// PVT-G5-036: re-check `state.ws_tx` AFTER inserting the pending
/// entry. A reconnect racing in the window between the outer
/// `ws_tx.lock().unwrap().clone()` and the pending insert could leave
/// us holding a stale `ws_tx` (the old writer task has exited; the new
/// reader has no record of this id). Detect by re-checking
/// `state.ws_tx` under a tight critical section; if it's now `None`,
/// drop the pending entry and reject.
///
/// PVT-G5-087: demoted from `pub(crate) async fn` to `async fn` — the
/// only caller is `dispatch_inner` in this same file (the tray menu
/// handler in `tray.rs` calls `dispatch_inner`, not `dispatch_frame`
/// directly).
async fn dispatch_frame(
    state: &Arc<SidecarState>,
    cmd: &str,
    data: Option<Value>,
) -> Result<Value, String> {
    let id = state.next_id.fetch_add(1, Ordering::SeqCst);
    // PVT-G5-017: debug-level entry log for correlation. The WS reader
    // logs the matching `id` on fulfillment so a slow / dropped
    // dispatch can be traced end-to-end.
    log::debug!("[dispatch] id={} cmd={}", id, cmd);

    // PVT-G5-035: short-circuit if the host is shutting down. Avoids
    // the orphaned-pending-then-timeout window described above.
    if state.shutting_down.load(Ordering::SeqCst) {
        log::warn!(
            "[dispatch] id={} cmd={} rejected: sidecar shutting down",
            id,
            cmd
        );
        return Err("sidecar shutting down".into());
    }

    let frame = json!({
        "type": cmd,
        "data": data.unwrap_or(json!({})),
        "id": id,
    });

    // CR-50: confirm `ws_tx` is Some BEFORE inserting into the pending
    // map so the early-return Err path doesn't leak a stale entry.
    let ws_tx_opt = state.ws_tx.lock().unwrap().clone();
    let ws_tx = match ws_tx_opt {
        Some(tx) => tx,
        None => {
            log::warn!(
                "[dispatch] id={} cmd={} rejected: sidecar not connected (ws_tx is None)",
                id,
                cmd
            );
            return Err("sidecar not connected".to_string());
        }
    };

    let (tx, rx) = oneshot::channel::<Value>();
    {
        let mut pending = state.pending.lock().await;
        pending.insert(id, tx);
    }

    // PVT-G5-036: re-check `state.ws_tx` is still `Some` AFTER inserting
    // the pending entry — a reconnect between the outer clone above and
    // the insert could have left us holding a stale `ws_tx`. If the
    // current value is `None`, the reader has exited (or is about to)
    // and the pending entry would never be fulfilled; remove it and
    // reject. Tight critical section: lock, check, drop the guard
    // before awaiting the pending mutex.
    //
    // Note: the MutexGuard is held in its OWN block scope so the
    // compiler can prove it is dropped BEFORE the `.await` on
    // `state.pending.lock()` — `std::sync::MutexGuard` is `!Send`
    // (the inner `Option<Sender<Message>>` is `Send` but not `Sync`),
    // so holding it across an `.await` would make the surrounding
    // future `!Send`, which Tauri's `#[tauri::command]` requires to
    // be `Send`. The boolean `needs_cleanup` carries the result out
    // of the lock scope so the await happens AFTER the guard is gone.
    let needs_cleanup = {
        let ws_tx_now = state.ws_tx.lock().unwrap();
        ws_tx_now.is_none()
    };
    if needs_cleanup {
        let mut pending = state.pending.lock().await;
        pending.remove(&id);
        log::warn!(
            "[dispatch] id={} cmd={} rejected: WS disconnected mid-dispatch \
             (orphaned pending entry removed)",
            id,
            cmd
        );
        return Err("sidecar not connected".into());
    }

    // Send the frame via the WS writer channel. On send failure, remove
    // the pending entry too — the writer task has exited so the WS
    // reader's drain loop is the only other remover and it may not have
    // run yet (race window).
    if let Err(e) = ws_tx.try_send(Message::Text(frame.to_string())) {
        let mut pending = state.pending.lock().await;
        pending.remove(&id);
        log::warn!(
            "[dispatch] id={} cmd={} WS send failed: {} (pending entry removed)",
            id,
            cmd,
            e
        );
        return Err(format!("WS send failed: {e}"));
    }

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
                log::warn!(
                    "[dispatch] id={} cmd={} server error [{}]: {}",
                    id,
                    cmd,
                    code,
                    msg
                );
                return Err(format!("server error [{}]: {}", code, msg));
            }
            Ok(response.get("data").cloned().unwrap_or(json!({})))
        }
        Ok(Err(_)) => {
            // The oneshot sender was dropped without sending — the WS
            // reader exited (sidecar crashed / WS closed mid-response).
            log::warn!(
                "[dispatch] id={} cmd={} response channel closed (WS reader dropped)",
                id,
                cmd
            );
            Err("dispatch response channel closed".into())
        }
        Err(_) => {
            // Timeout — remove the pending entry.
            let mut pending = state.pending.lock().await;
            pending.remove(&id);
            log::error!(
                "[dispatch] id={} cmd={} timed out after {}s (pending entry removed)",
                id,
                cmd,
                DISPATCH_TIMEOUT_SECS
            );
            Err(format!("dispatch timeout ({}s)", DISPATCH_TIMEOUT_SECS))
        }
    }
}

#[tauri::command]
pub async fn dispatch(
    args: DispatchArgs,
    state: tauri::State<'_, Arc<SidecarState>>,
    window: tauri::Window,
) -> Result<Value, String> {
    // CR-5: window-label guard. The bubble renderer is a sandboxed
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
    // The error envelope shape mirrors the sidecar's WS error envelope
    // ({"type":"error","data":{"code":...,"message":...}}) so the
    // renderer's existing `dispatch` reject path treats this identically
    // to a server-side rejection.
    if window.label() != "main" {
        log::warn!(
            "[CR-5] dispatch rejected from non-main window: {}",
            window.label()
        );
        let err = json!({
            "type": "error",
            "data": {
                "code": "disallowed_window",
                "message": "dispatch only callable from main window"
            }
        });
        return Err(err.to_string());
    }

    // CR-4: enforce the ALLOWED_COMMANDS allowlist BEFORE forwarding the
    // command to the Python sidecar over WS. This mirrors the Electron
    // renderer-side gate (SEC-019 / ADR-0015) and is the
    // defense-in-depth backstop for a compromised-renderer attack
    // (XSS in the WebView → `invoke('dispatch', {cmd:'<arbitrary>'})`).
    if !is_command_allowed(&args.cmd) {
        log::warn!(
            "[CR-4] rejected disallowed dispatch command: {:?} (not in ALLOWED_COMMANDS)",
            args.cmd
        );
        let err = json!({
            "type": "error",
            "data": {
                "code": "disallowed_command",
                "message": "Command not in allowlist"
            }
        });
        return Err(err.to_string());
    }

    // Delegate to the internal dispatch path (no allowlist check —
    // already done above). Clone the Arc<SidecarState> out of the
    // Tauri State wrapper so `dispatch_inner` is callable from non-
    // command contexts too (e.g. the tray menu click handler).
    dispatch_inner(args, state.inner().clone()).await
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
/// # CR-75 — DevTools-only / manual testing status
///
/// The original ADR-0020 §6.2 design called for the React UI to invoke
/// this Rust command on every transcription completion. In practice,
/// the Python sidecar does its OWN paste internally in
/// `voice_typer/server/dictation_pipeline.py:990-1010` via
/// `self._app.clipboard.paste(...)` (which uses the same clipboard +
/// Ctrl+V mechanism but runs in the sidecar process). Grep confirms:
/// no Python code publishes a `paste_text` event, and no TS code
/// invokes `invoke('paste_text', ...)`.
///
/// The command is retained (still registered in `main.rs`'s
/// `generate_handler!` list, still has `#[tauri::command]`) so that:
///   1. The behavioral contract pinned by `tests/tauri/mig15/`,
///      `tests/tauri/mig16/`, `tests/tauri/mig17/`, and
///      `tests/tauri/mig19/test_final_glue.py` keeps passing.
///   2. Developers debugging paste issues can drive the Rust paste
///      path directly from the WebView DevTools console via
///      `await window.__TAURI__.core.invoke('paste_text', {text:'...'})`
///      (requires the `dev` Cargo feature for `tauri/devtools`).
///
/// Production traffic never reaches this command. If the Python-side
/// paste path is removed in a future refactor, this command becomes
/// the live paste entry point again — keep the logic in sync with
/// `dictation_pipeline.py::_dispatch_paste`.
#[tauri::command]
pub async fn paste_text(
    args: PasteTextArgs,
    app: tauri::AppHandle,
    window: tauri::Window,
) -> Result<(), String> {
    // G4-H-01: only the main window may drive the paste path. A
    // compromised bubble renderer (XSS in the waveform pill) must NOT
    // be able to invoke `invoke('paste_text', {text:'...'})` to inject
    // arbitrary text into the foreground window.
    require_main_window(&window)?;

    // CR-066: the paste-text implementation is extracted to
    // `commands::paste::execute_paste` (a focused module split out per
    // ADR-0020 §6.2 + §6.3 + §6.6 — see `paste.rs` for the per-platform
    // paste strategy, Windows focus-restore dance, and Wayland
    // fallback). This thin wrapper preserves the `#[tauri::command]`
    // Tauri registration so `invoke('paste_text', ...)` still resolves
    // and the migration glue tests (`tests/tauri/mig15-19/`) that
    // source-grep the `paste_text` signature + `#[tauri::command]`
    // attribute keep passing. The 165-LOC inline platform branches +
    // the duplicate `paste_via_clipboard_and_ctrl_v` helper that used
    // to live here were deleted — `paste.rs` is now the single source
    // of truth for the paste path.
    Ok(crate::commands::paste::execute_paste(app, args.text).await?)
}

// ─── Tauri command: cooperative shutdown (ADR-0020 §10) ───────────────

#[tauri::command]
pub async fn shutdown_sidecar(
    app: tauri::AppHandle,
    state: tauri::State<'_, Arc<SidecarState>>,
    window: tauri::Window,
) -> Result<(), String> {
    // G4-H-01: only the main window may drive the cooperative-shutdown
    // path. A compromised bubble renderer must NOT be able to invoke
    // `invoke('shutdown_sidecar')` to DoS the sidecar.
    //
    // Note: there is also a programmatic (non-IPC) caller in
    // `main.rs`'s `on_window_event` handler that invokes
    // `shutdown_sidecar` directly when the main window is closed —
    // that caller passes `window.clone()` from the `"main"` arm of the
    // `window.label()` match, so this check passes for it too.
    require_main_window(&window)?;

    // PVT-17: Early-return guard. If a previous `shutdown_sidecar`
    // invocation already flipped `shutting_down` to true, the sidecar
    // is already being torn down (or has been). Re-entering here would
    // re-send the (idempotent) shutdown frame AND block on
    // `state.child_exit_rx` for the full `SHUTDOWN_ACK_TIMEOUT_MS`
    // (2s) — a duplicate `invoke('shutdown_sidecar')` (renderer-
    // invocable via `generate_handler!`) thus freezes the UI for 2s.
    // `swap` returns the previous value: if it was already `true`,
    // short-circuit immediately.
    if state
        .shutting_down
        .swap(true, std::sync::atomic::Ordering::SeqCst)
    {
        log::info!("[SHUTDOWN] already in progress — duplicate call short-circuited");
        return Ok(());
    }
    // Send the shutdown frame.
    let frame = json!({"type": "shutdown"});
    if let Some(ws_tx) = state.ws_tx.lock().unwrap().clone() {
        let _ = ws_tx.try_send(Message::Text(frame.to_string()));
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

// ─── CR-4: unit tests for ALLOWED_COMMANDS ─────────────────────────────
//
// These tests pin the dispatch allowlist (SEC-019 / ADR-0015 defense-in-
// depth). The Python parity test in
// `tests/test_security_doc_command_count.py::test_rust_allowlist_matches_ts_allowlist`
// cross-checks the Rust set against the TS `ALLOWED_COMMANDS` literal —
// these Rust tests are the unit-level sanity checks (must contain key
// commands, must NOT contain dangerous commands).

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_allowed_commands_contains_get_status() {
        assert!(
            is_command_allowed("get_status"),
            "get_status must be in ALLOWED_COMMANDS (used by Home.tsx on mount)"
        );
    }

    #[test]
    fn test_allowed_commands_contains_set_config() {
        assert!(
            is_command_allowed("set_config"),
            "set_config must be in ALLOWED_COMMANDS (Settings page saves)"
        );
    }

    #[test]
    fn test_allowed_commands_contains_quit_app() {
        assert!(
            is_command_allowed("quit_app"),
            "quit_app must be in ALLOWED_COMMANDS (tray Quit menu item)"
        );
    }

    #[test]
    fn test_allowed_commands_contains_toggle_dictation() {
        assert!(
            is_command_allowed("toggle_dictation"),
            "toggle_dictation must be in ALLOWED_COMMANDS (main hotkey action)"
        );
    }

    #[test]
    fn test_allowed_commands_contains_download_model() {
        assert!(
            is_command_allowed("download_model"),
            "download_model must be in ALLOWED_COMMANDS (Models page download button)"
        );
    }

    #[test]
    fn test_allowed_commands_contains_heartbeat() {
        assert!(
            is_command_allowed("heartbeat"),
            "heartbeat must be in ALLOWED_COMMANDS (RW-10 watchdog)"
        );
    }

    #[test]
    fn test_allowed_commands_does_not_contain_delete_everything() {
        assert!(
            !is_command_allowed("delete_everything"),
            "delete_everything must NOT be in ALLOWED_COMMANDS (no such server command; \
             a positive result here means a typo added a dangerous sentinel)"
        );
    }

    #[test]
    fn test_allowed_commands_does_not_contain_eval() {
        assert!(
            !is_command_allowed("eval"),
            "eval must NOT be in ALLOWED_COMMANDS (would let a compromised renderer run \
             arbitrary Python)"
        );
    }

    #[test]
    fn test_allowed_commands_does_not_contain_exec() {
        assert!(
            !is_command_allowed("exec"),
            "exec must NOT be in ALLOWED_COMMANDS (would let a compromised renderer run \
             arbitrary shell commands)"
        );
    }

    #[test]
    fn test_allowed_commands_does_not_contain_shutdown() {
        assert!(
            !is_command_allowed("shutdown"),
            "shutdown must NOT be in ALLOWED_COMMANDS (sent via shutdown_sidecar, \
             not via the generic dispatch path)"
        );
    }

    #[test]
    fn test_allowed_commands_does_not_contain_empty_string() {
        assert!(
            !is_command_allowed(""),
            "empty string must NOT be in ALLOWED_COMMANDS"
        );
    }

    #[test]
    fn test_allowed_commands_does_not_contain_arbitrary_string() {
        assert!(
            !is_command_allowed("not_a_real_command_xyz"),
            "arbitrary string must NOT pass the allowlist"
        );
    }

    #[test]
    fn test_allowed_commands_set_is_nonempty() {
        assert!(
            !allowed_commands().is_empty(),
            "ALLOWED_COMMANDS must not be empty"
        );
    }

    #[test]
    fn test_allowed_commands_count_matches_ts_parity() {
        // CR-4: the Rust allowlist must contain EXACTLY the same number
        // of commands as the TS allowlist in
        // `voice_typer/client/src/main/allowed-commands.ts` (canonical
        // declaration since R6-F10 — was previously inline in
        // `index.ts:79-191`). The Python test
        // `tests/test_security_doc_command_count.py::test_rust_allowlist_matches_ts_allowlist`
        // asserts the entries match exactly — this Rust-side test pins
        // the COUNT so a local `cargo test` catches a drift before the
        // Python test even runs.
        //
        // PVT-G5-008 / PVT-G5-025 / PVT-G5-075: count is 76 (70 prior
        // + `onboarding_check_permissions` + `onboarding_get_model_catalog`
        // + `delete_all_personal_data` + `export_gdpr_bundle`
        // + `onboarding_request_keyboard_permission` + `onboarding_reset`
        // (added session 1K); `tray_click`
        // is intentionally absent — see the doc comment on
        // `dispatch_inner` and the `ALLOWED_COMMANDS` literal).
        assert_eq!(
            allowed_commands().len(),
            76,
            "ALLOWED_COMMANDS must contain exactly 76 entries (parity with TS allowlist). \
             Got {} — update both src-tauri/src/commands/sidecar_cmds.rs and \
             voice_typer/client/src/main/allowed-commands.ts together.",
            allowed_commands().len()
        );
    }

    #[test]
    fn test_allowed_commands_set_contains_no_duplicates() {
        let set = allowed_commands();
        assert_eq!(
            set.len(),
            76,
            "ALLOWED_COMMANDS contains a duplicate entry — set len ({}) < literal len (76). \
             Check the constructor log for the duplicate name.",
            set.len()
        );
    }

    #[test]
    fn test_is_command_allowed_is_case_sensitive() {
        assert!(
            !is_command_allowed("Get_Status"),
            "is_command_allowed must be case-sensitive (Get_Status should not match get_status)"
        );
        assert!(
            !is_command_allowed("GET_STATUS"),
            "is_command_allowed must be case-sensitive (GET_STATUS should not match get_status)"
        );
        assert!(
            !is_command_allowed("get_Status"),
            "is_command_allowed must be case-sensitive (get_Status should not match get_status)"
        );
    }
}
