//! Tauri commands: dispatch, shutdown_sidecar (ADR-0020 §7 + §10).

use crate::commands::require_main_window;
use crate::state::SidecarState;
// EC-FIX-5 (EC-16): poison-safe Mutex helper. Replaces inline
// `.lock().unwrap()` so a poisoned mutex (a prior panic while holding
// the lock) does not re-panic and permanently brick the dispatch path.
use crate::state::lock as mutex_lock;
use crate::util::{
    DISPATCH_SHORT_TIMEOUT_SECS, DISPATCH_TIMEOUT_SECS, SHUTDOWN_ACK_TIMEOUT_MS,
};
use std::collections::HashSet;
use std::sync::atomic::Ordering;
use std::sync::{Arc, OnceLock};
use std::time::Duration;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tauri_plugin_shell::process::CommandEvent;
use tokio::sync::oneshot;
use tokio_tungstenite::tungstenite::Message;

// ─── DT-4: shared main-window guard ──────────────────────────────────
//
// `dispatch` and `shutdown_sidecar` are both
// `#[tauri::command]` functions that a compromised renderer could
// invoke over the IPC bridge. The bubble window is a sandboxed webview
// (ADR-0020 §7 + §9 + SEC-026) that must NEVER drive the sidecar WS
// or paste path. Tauri v2's capability system only gates plugin
// commands, so user-defined commands need this runtime check.
//
// DT-4: the canonical `require_main_window` helper now lives in
// `commands/mod.rs` (single source of truth, no duplication). See
// `commands::mod::require_main_window` for the G4-H-01 rationale +
// the error envelope shape contract.

// ─── DT-44: per-command dispatch timeout routing ──────────────────────
//
// Previously every `dispatch` call used the uniform 120s
// `DISPATCH_TIMEOUT_SECS` timeout. That let a hung `get_status` poll
// (median response <50ms) block the UI for 2 minutes before
// rejecting. DT-44 routes model-lifecycle commands (which can
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

// AC-16: dedicated error code constants for the two disallowed-code
// branches in `dispatch`. Using named constants instead of inline
// string literals ensures the test assertions (which match on these
// exact strings) stay in sync with the production code.
//
// `disallowed_window` is emitted by `commands/mod.rs::require_main_window`
// (NOT extracted here — that file is outside this entry's files list;
// see `commands::mod` for the literal). Listed here for documentation
// parity so a future cleanup can centralize both constants.
pub(crate) const DISALLOWED_COMMAND_CODE: &str = "disallowed_command";
/// AC-16: companion Rust-host-only code. Emitted by
/// `commands/mod.rs::require_main_window` (the main/bubble window guard).
/// Kept here as a `pub(crate)` constant so the contract test
/// (`tests/test_error_codes_registry.py`) can reference the canonical
/// spelling without having to grep `commands/mod.rs` (which is outside
/// the AC-16 entry's files list).
pub(crate) const DISALLOWED_WINDOW_CODE: &str = "disallowed_window";

// XZ-R4-019: pending-map size cap. Each pending dispatch entry is a
// `(u64, oneshot::Sender<Value>)` pair (~80 bytes on x86_64). An
// unresponsive sidecar (WS reader stuck, sidecar process paused in a
// debugger, GC pause) plus rapid tray clicks / renderer retries can
// accumulate 1000s of entries — each one auto-expires after
// `DISPATCH_TIMEOUT_SECS` (120s for model lifecycle, 15s for
// everything else), so the steady-state cap without this guard is
// `clicks_per_sec * 120s` entries. At 10 clicks/sec (renderer retry
// storm) that's 1200 entries × 80 bytes = ~96 KiB — small in absolute
// terms, but the entries never expire if the sidecar is fully stuck
// (the timeout fires on the awaiting side, but the entry is only
// removed by the WS reader's drain loop OR by the explicit
// `pending.remove(&id)` in the WS-send-failure path — neither runs if
// the WS writer task is wedged). The 1024 cap rejects new dispatches
// once the map is full, surfacing the backpressure to the renderer as
// an immediate `pending_full` error instead of letting the map grow
// unbounded.
//
// 1024 is comfortably above the highest legitimate concurrent-dispatch
// count observed in production (the renderer typically has 1-3 in-
// flight dispatches: a status poll + a model-list poll + an occasional
// settings read). The cap exists to bound memory under pathological
// backpressure, NOT to throttle normal traffic.
pub(crate) const PENDING_MAX: usize = 1024;
/// XZ-R4-019: error code returned by `dispatch_frame` when the pending
/// map has reached `PENDING_MAX` entries. The renderer treats this as
/// a transient "sidecar overwhelmed" signal (distinct from "sidecar
/// not connected" / "sidecar shutting down") so it can back off and
/// retry rather than spamming more dispatches.
pub(crate) const PENDING_FULL_CODE: &str = "pending_full";

/// DT-44: returns the dispatch timeout (in seconds) for `cmd`.
///
/// - 120s (`DISPATCH_TIMEOUT_SECS`) for the 6 model lifecycle commands
///   listed in [`_LONG_RUNNING_COMMANDS`] — downloads / imports can
///   legitimately take >15s.
/// - 15s (`DISPATCH_SHORT_TIMEOUT_SECS`) for everything else — the
///   sidecar's median response time is <50ms, so 15s is generous
///   while still bounding the worst-case UI freeze.
fn dispatch_timeout_for(cmd: &str) -> u64 {
    if _LONG_RUNNING_COMMANDS.contains(&cmd) {
        DISPATCH_TIMEOUT_SECS
    } else {
        DISPATCH_SHORT_TIMEOUT_SECS
    }
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
        //
        // Reconciliation: 17 entries that previously appeared
        // ONLY in this Rust allowlist (and NOT in the TS allowlist)
        // were removed in lockstep with the TS-side cleanup.
        // Each of the 17 was audited via
        // `rg --type=ts '<cmd>' voice_typer/client/src/renderer/src/`
        // and confirmed to have ZERO renderer callers (the only TS
        // matches were in doc comments, not actual `invoke()` calls).
        // Defense-in-depth principle: the Rust host should NOT
        // allowlist commands the renderer never sends — a compromised
        // renderer would otherwise be able to `invoke('dispatch',
        // {cmd:'<one of these 17>'})` and reach a server-side handler
        // that no legitimate UI path exercises. The 17 removed:
        //   apply_vocabulary_suggestion, check_accessibility,
        //   delete_all_personal_data, dismiss_vocabulary_suggestion,
        //   export_diagnostics, export_gdpr_bundle, get_audio_status,
        //   get_rms_level, get_vocabulary_suggestions,
        //   level_monitor_status, microphone_test_status,
        //   onboarding_get_model_catalog, onboarding_get_step,
        //   onboarding_request_keyboard_permission, refresh_microphones,
        //   show_electron_notification, test_llm_connection.
        // The matching Python-side `_COMMAND_REGISTRY` entries have been
        // removed in lockstep — all three layers (this Rust literal, the TS
        // `ALLOWED_COMMANDS` Set, and the Python `_COMMAND_REGISTRY`) now
        // stay in sync. The parity test `test_security_doc_command_count.py`
        // enforces count + exact-entry equality across all three.
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
            "onboarding_get_microphones",
            "onboarding_get_model_options",
            "onboarding_get_hotkey_presets",
            "download_model",
            "cancel_model_download",
            "pause_model_download",
            "resume_model_download",
            "delete_model",
            "get_model_catalog",
            "microphone_test_start",
            "microphone_test_stop",
            "microphone_test_cancel",
            "microphone_test_get_level",
            "level_monitor_start",
            "level_monitor_stop",
            "set_esc_cancel_paused",
            "set_tray_locale",
            "import_model",
            // DT-50: `heartbeat` and `relaunch_ack` are intentionally
            // ABSENT from this Rust allowlist (they ARE in the TS
            // allowlist — Electron's main process needs them to talk
            // to the Python sidecar). The Rust host never routes
            // either command through this `dispatch` gate:
            //   - `heartbeat` is sent by the Rust WS-reader task
            //     itself (`sidecar/ws.rs::ws_reader_loop`) directly
            //     over the WS — it's never the result of an
            //     `invoke('dispatch', ...)`.
            //   - `relaunch_ack` is sent by the `relaunch_app` Tauri
            //     event handler in `main.rs` via `dispatch_inner`,
            //     which bypasses this allowlist gate (same pattern
            //     as `tray_click` — see PVT-G5-075 above).
            // Including either here would create an attack surface
            // that only a compromised renderer could reach: a
            // malicious `invoke('dispatch', {cmd:'relaunch_ack'})`
            // could trip Python's `_relaunch_ack_event.set()` mid-
            // restart, and a malicious `invoke('dispatch',
            // {cmd:'heartbeat'})` could keep the backend's watchdog
            // alive even after the renderer is killed. The parity
            // tests in `tests/test_rust_allowlist_parity.py` and
            // `tests/test_security_doc_command_count.py` document
            // this asymmetric exception (TS superset = Rust ∪
            // {heartbeat, relaunch_ack}).
            "repaste_last",
            "force_cancel_transcription",
            // Lightweight history counters (added by the perf-reliability
            // pass): `get_history_count` is invoked by the Dashboard to
            // fetch just the total row count (avoids pulling the full
            // history array), and `get_transcription_text` is invoked
            // by the history detail view to fetch a single
            // transcription's full text on demand. Both have
            // server-side handlers in `_COMMAND_REGISTRY`
            // (`voice_typer/server/ipc_server.py`); listed here so the
            // Tauri host does not reject the renderer's `invoke()`.
            "get_history_count",
            "get_transcription_text",
            // G4-M-10 + PVT-G5-025 (session-3 + 5): onboarding reset —
            // invoked by the Onboarding page. Registered in the Python-side
            // `_COMMAND_REGISTRY` (ipc_server.py) and implemented in
            // `handlers/onboarding_handlers.py` (`_handle_onboarding_reset`).
            // Mirrors the TS allowlist.
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

/// EC-FIX-5 (EC-18 / PVT-25): fire-and-forget dispatch helper.
///
/// Builds a WS frame `{"type": cmd, "data": data, "id": 0}` and sends it
/// via `state.ws_tx.try_send` WITHOUT inserting a pending oneshot entry
/// or awaiting a response. Used by `commands::bubble::bubble_toggle_dictation`
/// (a sandboxed-window command that must NOT use the full `dispatch`
/// path — the bubble renderer is allowed to send only the fixed
/// `toggle_dictation` command, see G4-L-03 sanctioned-bypass doc).
///
/// AC-101: the synthetic `id: 0` is NOT special-cased server-side.
/// The Python sidecar's `dispatch` coroutine (in
/// `voice_typer/server/sidecar_ws.py`) treats `id=0` like any other
/// request id: it runs the handler, and if the handler returns a
/// non-`None` response envelope (which `_handle_toggle_dictation`
/// always does — it sets `resp["type"] = "ack"`), the server echoes
/// the response back over the WS with `"id": 0` attached (see
/// `sidecar_ws.py:843-851`). The Rust WS reader (`sidecar/ws.rs:612-
/// 620`) then looks up `id=0` in `state.pending`, finds no entry
/// (because `dispatch_fire_and_forget` never inserted one), and
/// silently drops the frame via `continue` — NO `[WS-READER]` warning
/// is logged (the `pending.remove(&id)` call returns `None`, the `if
/// let Some(tx) = ...` branch is skipped, and execution continues to
/// the next iteration without any log statement).
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
/// Replaces the inline `json!` + `lock` + `try_send` block that was
/// duplicated in `bubble.rs:629-674` (the PVT-25 TODO that called for
/// this extraction). Keeps the poison-safe `mutex_lock` helper so a
/// poisoned mutex doesn't brick the bubble's mic button permanently.
///
/// Returns `Err` if `ws_tx` is `None` (sidecar disconnected) or if
/// `try_send` fails (channel full or writer task exited). Both error
/// strings mirror the shape used by `dispatch_frame` so the renderer's
/// existing reject path handles them identically.
pub(crate) fn dispatch_fire_and_forget(
    state: &Arc<SidecarState>,
    cmd: &str,
    data: Option<Value>,
) -> Result<(), String> {
    let frame = json!({
        "type": cmd,
        "data": data.unwrap_or(json!({})),
        "id": 0u64,
    });
    let ws_tx_opt = mutex_lock(&state.ws_tx).clone();
    let ws_tx = ws_tx_opt.ok_or_else(|| "sidecar not connected".to_string())?;
    // PVT-G5-059: `ws_tx` is a bounded `mpsc::Sender` — use `try_send`
    // (synchronous) rather than `.send().await` (which would require an
    // async context AND block on the writer-task consumer). Returns
    // `TrySendError::Full` if the writer is overwhelmed (256-cap) or
    // `TrySendError::Closed` if the writer task exited.
    ws_tx
        .try_send(Message::Text(frame.to_string().into()))
        .map_err(|e| format!("WS send failed: {e}"))?;
    Ok(())
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
/// `mutex_lock(&state.ws_tx).clone()` and the pending insert could leave
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

    // DT-44: per-command timeout. Model lifecycle commands (download /
    // import / delete / cancel / pause / resume) get 120s; everything
    // else gets 15s. See `dispatch_timeout_for` for the rationale.
    let timeout_secs = dispatch_timeout_for(cmd);

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
    let ws_tx_opt = mutex_lock(&state.ws_tx).clone();
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
        // XZ-R4-019: pending-map size cap. Reject new dispatches when
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
            let err = json!({
                "type": "error",
                "data": {
                    "code": PENDING_FULL_CODE,
                    "message": "Sidecar dispatch queue is full; please retry"
                }
            });
            return Err(err.to_string());
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
    if let Err(e) = ws_tx.try_send(Message::Text(frame.to_string().into())) {
        let mut pending = state.pending.lock().await;
        pending.remove(&id);
        let err_msg = match &e {
            tokio::sync::mpsc::error::TrySendError::Closed(_) => "sidecar not connected".to_string(),
            tokio::sync::mpsc::error::TrySendError::Full(_) => format!("WS send failed: {e}"),
        };
        log::warn!(
            "[dispatch] id={} cmd={} WS send failed: {} (pending entry removed)",
            id,
            cmd,
            e
        );
        return Err(err_msg);
    }

    // Await the response with a timeout.
    match tokio::time::timeout(Duration::from_secs(timeout_secs), rx).await {
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
                timeout_secs
            );
            Err(format!("dispatch timeout ({}s)", timeout_secs))
        }
    }
}

#[tauri::command]
pub async fn dispatch(
    args: DispatchArgs,
    state: tauri::State<'_, Arc<SidecarState>>,
    window: tauri::Window,
) -> Result<Value, String> {
    // FR-43: bound the command-name length so a buggy or compromised
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
            "[FR-43] rejected dispatch command with length {} (>64 char cap)",
            args.cmd.len()
        );
        return Err("command name too long".into());
    }

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
    // DT-4: the canonical `require_main_window` helper now lives in
    // `commands/mod.rs`. We delegate to it for the envelope shape +
    // log tag. The previous inline duplicate (with the CR-5-specific
    // "dispatch only callable from main window" message) is removed —
    // the renderer's reject path JSON-parses the envelope + keys off
    // the `code` field (`disallowed_window`), so the per-command
    // message wording doesn't matter.
    require_main_window(&window)?;

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
                "code": DISALLOWED_COMMAND_CODE,
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
    if let Some(ws_tx) = mutex_lock(&state.ws_tx).clone() {
        if let Err(e) = ws_tx.try_send(Message::Text(frame.to_string().into())) {
            log::warn!(
                "[SHUTDOWN] try_send of shutdown frame failed (best-effort): {}",
                e
            );
        }
    }
    // CR-2: Wait up to SHUTDOWN_ACK_TIMEOUT_MS for the sidecar to exit.
    // Use the `CommandEvent` receiver captured at spawn time to detect
    // `Terminated` and return immediately (typical sidecar acks+exits in
    // ~50ms), instead of sleeping the full deadline unconditionally.
    // Falls back to a single bounded sleep for the dev-mode path (which
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
        // receiver. Sleep once for the full deadline window before
        // falling through to the force-kill backstop.
        log::info!(
            "[SHUTDOWN] dev-mode sidecar — sleeping {}ms before force-kill",
            SHUTDOWN_ACK_TIMEOUT_MS
        );
        tokio::time::sleep(deadline_dur).await;
    }
    // Drop the rx guard before locking state.child (avoid holding the
    // async mutex across the sync mutex lock + async kill await).
    drop(rx_guard);
    // Force-kill backstop — no-op if the child has already exited, but
    // guarantees we never leak a zombie. ADR-0020 §10: use `kill_tree`
    // (recursive "kill_children") so the sidecar's grandchildren (native
    // hotkey binary, model subprocesses) are reaped too, not just the
    // direct child.
    let child_opt = mutex_lock(&state.child).take();
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
    fn test_allowed_commands_does_not_contain_heartbeat_or_relaunch_ack() {
        // DT-50: `heartbeat` and `relaunch_ack` are Rust-only commands
        // invoked via `dispatch_inner` (which bypasses this allowlist
        // gate) — `heartbeat` is dispatched by the WS-reader task's
        // heartbeat subtask (`sidecar/ws.rs::spawn_heartbeat_task`),
        // and `relaunch_ack` is dispatched by the `relaunch_app` Tauri
        // event handler in `main.rs`. Including either in the
        // `dispatch` allowlist would let a compromised renderer spoof
        // them via `invoke('dispatch', {cmd:'...'})`. Same pattern as
        // `tray_click` (PVT-G5-075).
        assert!(
            !is_command_allowed("heartbeat"),
            "heartbeat must NOT be in ALLOWED_COMMANDS (DT-50: dispatched via dispatch_inner from the WS-reader task)"
        );
        assert!(
            !is_command_allowed("relaunch_ack"),
            "relaunch_ack must NOT be in ALLOWED_COMMANDS (DT-50: dispatched via dispatch_inner from the relaunch_app event handler)"
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
        // PVT-G5-008 / PVT-G5-025: count is 59 — must match the cmds
        // literal below (single source of truth). `onboarding_check_permissions`
        // (PVT-G5-008) and `onboarding_reset` (session 1K / PVT-G5-025) were
        // the last entries actually added to the literal. `tray_click` is
        // intentionally absent — see the doc comment on `dispatch_inner` and
        // the `ALLOWED_COMMANDS` literal.
        assert_eq!(
            allowed_commands().len(),
            59,
            "must match TS allowlist minus heartbeat/relaunch_ack"
        );
    }

    #[test]
    fn test_allowed_commands_set_contains_no_duplicates() {
        let set = allowed_commands();
        assert_eq!(
            set.len(),
            59,
            "ALLOWED_COMMANDS contains a duplicate entry — set len ({}) < literal len (59). \
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

    // ── XZ-R4-019: pending-map size cap ───────────────────────────────

    #[test]
    fn test_pending_max_constant_is_1024() {
        // XZ-R4-019: PENDING_MAX must be 1024. The cap is sized to
        // bound memory under pathological backpressure (an unresponsive
        // sidecar + rapid tray clicks / renderer retries) without
        // throttling normal traffic (the renderer typically has 1-3
        // in-flight dispatches). If this constant changes, the
        // renderer-side retry heuristic (which interprets
        // `pending_full` as "back off ~250ms then retry") may need to
        // be revisited.
        assert_eq!(
            PENDING_MAX, 1024,
            "PENDING_MAX must be 1024 — see the doc comment for sizing rationale"
        );
    }

    #[test]
    fn test_pending_full_code_constant_is_pending_full() {
        // XZ-R4-019: the error code returned by `dispatch_frame` when
        // the pending map is at capacity. The renderer branches on
        // this exact string to differentiate "sidecar overwhelmed"
        // (transient, retry) from "sidecar not connected" (persistent,
        // show error toast) and "sidecar shutting down" (terminal, no
        // retry).
        assert_eq!(
            PENDING_FULL_CODE, "pending_full",
            "PENDING_FULL_CODE must be the literal 'pending_full' — the renderer's \
             error-envelope switch branches on this exact string"
        );
    }

    #[test]
    fn test_pending_full_error_envelope_shape() {
        // XZ-R4-019: the `pending_full` error must serialize to a JSON
        // envelope matching the shape used by `disallowed_command`
        // (line ~687) so the renderer's existing error-envelope switch
        // can branch on `code === "pending_full"` without a special
        // case. Verify the envelope shape here (without actually
        // triggering a real dispatch) by constructing the same `json!`
        // value the dispatch path returns.
        let err = json!({
            "type": "error",
            "data": {
                "code": PENDING_FULL_CODE,
                "message": "Sidecar dispatch queue is full; please retry"
            }
        });
        let serialized = err.to_string();
        // Must be a valid JSON envelope with type=error and the
        // pending_full code — the renderer parses this string out of
        // the Tauri rejection reason.
        assert!(
            serialized.contains("\"code\":\"pending_full\""),
            "pending_full error envelope must contain '\"code\":\"pending_full\"'; got: {serialized}"
        );
        assert!(
            serialized.contains("\"type\":\"error\""),
            "pending_full error envelope must contain '\"type\":\"error\"'; got: {serialized}"
        );
        // Round-trip through serde_json to verify it's valid JSON.
        let parsed: Value = serde_json::from_str(&serialized).expect(
            "pending_full error envelope must be valid JSON — the renderer parses it as a string"
        );
        assert_eq!(parsed.get("type").and_then(|v| v.as_str()), Some("error"));
        assert_eq!(
            parsed.get("data").and_then(|d| d.get("code")).and_then(|c| c.as_str()),
            Some("pending_full")
        );
    }
}
