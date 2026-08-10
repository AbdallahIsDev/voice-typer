//! Tauri commands: dispatch, shutdown_sidecar (ADR-0020 §7 + §10).

use crate::commands::require_main_window;
use crate::state::SidecarState;
//(): poison-safe Mutex helper. Replaces inline
// `.lock().unwrap()` so a poisoned mutex (a prior panic while holding
// the lock) does not re-panic and permanently brick the dispatch path.
use crate::state::lock as mutex_lock;
use crate::util::{DISPATCH_SHORT_TIMEOUT_SECS, DISPATCH_TIMEOUT_SECS, SHUTDOWN_ACK_TIMEOUT_MS};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::borrow::Cow;
use std::collections::HashSet;
use std::sync::atomic::Ordering;
use std::sync::{Arc, OnceLock};
use std::time::Duration;
use tauri::Manager;
use tauri_plugin_shell::process::CommandEvent;
use tokio::sync::oneshot;
use tokio_tungstenite::tungstenite::Message;

//shared main-window guard ──────────────────────────────────
//
// `dispatch` and `shutdown_sidecar` are both
// `#[tauri::command]` functions that a compromised renderer could
// invoke over the IPC bridge. The bubble window is a sandboxed webview
// (ADR-0020 §7 + §9 + SEC-026) that must NEVER drive the sidecar WS
// or paste path. Tauri v2's capability system only gates plugin
// commands, so user-defined commands need this runtime check.
//
//the canonical `require_main_window` helper now lives in
// `commands/mod.rs` (single source of truth, no duplication). See
//`commands::mod::require_main_window` for the  rationale +
// the error envelope shape contract.

//per-command dispatch timeout routing ──────────────────────
//
// Previously every `dispatch` call used the uniform 120s
// `DISPATCH_TIMEOUT_SECS` timeout. That let a hung `get_status` poll
// (median response <50ms) block the UI for 2 minutes before
//rejecting.  routes model-lifecycle commands (which can
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

//dedicated error code constants for the two disallowed-code
// branches in `dispatch`. Using named constants instead of inline
// string literals ensures the test assertions (which match on these
// exact strings) stay in sync with the production code.
//
// `disallowed_window` is emitted by `commands/mod.rs::require_main_window`
// (NOT extracted here — that file is outside this entry's files list;
// see `commands::mod` for the literal). Listed here for documentation
// parity so a future cleanup can centralize both constants.
pub(crate) const DISALLOWED_COMMAND_CODE: &str = "disallowed_command";
//companion Rust-host-only code. Emitted by
/// `commands/mod.rs::require_main_window` (the main/bubble window guard).
/// Kept here as a `pub(crate)` constant so the contract test
/// (`tests/test_error_codes_registry.py`) can reference the canonical
/// spelling without having to grep `commands/mod.rs` (which is outside
//the  entry's files list).
pub(crate) const DISALLOWED_WINDOW_CODE: &str = "disallowed_window";

//pending-map size cap. Each pending dispatch entry is a
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
//error code returned by `dispatch_frame` when the pending
/// map has reached `PENDING_MAX` entries. The renderer treats this as
/// a transient "sidecar overwhelmed" signal (distinct from "sidecar
/// not connected" / "sidecar shutting down") so it can back off and
/// retry rather than spamming more dispatches.
pub(crate) const PENDING_FULL_CODE: &str = "pending_full";

//returns the dispatch timeout (in seconds) for `cmd`.
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

//ALLOWED_COMMANDS allowlist (ADR-0015 defense-in-depth) ──────
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
//`tray_click` is intentionally ABSENT from this literal
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
        //this list MUST mirror the Electron renderer's
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
        // NOTE: `check_accessibility` is no longer part of the removed
        // set — it was RE-ADDED on 2026-08-10 (finding #919 part b):
        // the Settings → Troubleshooting UI now invokes it on macOS to
        // surface the stale-grant `tccutil` reset command. It is
        // registered here and in the TS allowlist + Python registry in
        // lockstep (see its inline comment below).
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
            "onboarding_set_backend",
            "onboarding_set_hotkey",
            "onboarding_set_model",
            "onboarding_skip",
            "onboarding_apply",
            //previously missing from the Rust literal —
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
            // macOS troubleshooting (finding #127 part b): reset the
            // stale Accessibility TCC entry + re-open System Settings.
            // Invoked by the Settings → Troubleshooting button. Python
            // handler: `_handle_reset_macos_accessibility` in
            // `handlers/system_handlers.py` (runs `tccutil reset
            // Accessibility <bundle-id>` with the bundle ID resolved at
            // runtime). Mirrors the TS allowlist.
            "reset_macos_accessibility",
            // macOS accessibility-status probe (finding #919 part b —
            // RE-ADDED 2026-08-10): the Settings → Troubleshooting UI
            // invokes `check_accessibility` on macOS to surface the
            // stale-grant `tccutil` reset command next to the "Reset
            // Accessibility Permission" button. Python handler:
            // `_handle_check_accessibility` in
            // `handlers/system_handlers.py` (returns
            // `accessibility_status` with `granted` / `platform` and,
            // on a confirmed stale grant, `suggest_reset` + the
            // runtime `reset_command` string). Mirrors the TS
            // allowlist and the Python `_COMMAND_REGISTRY`.
            "check_accessibility",
            // Linux troubleshooting (finding #127 part b): reset a stale
            // polkit authorization — restart the polkit daemon via
            // pkexec so the next "Grant permission" re-prompts. Invoked
            // by the Settings → Troubleshooting "Reset Linux Permission"
            // button. Python handler: `_handle_reset_linux_permissions`
            // in `handlers/system_handlers.py` (pkaction enumerates +
            // pkcheck verifies). Mirrors the TS allowlist.
            "reset_linux_permissions",
            "import_model",
            //`heartbeat` and `relaunch_ack` are intentionally
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
            //as `tray_click` — see  above).
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
            //+  (session-3 + 5): onboarding reset —
            // invoked by the Onboarding page. Registered in the Python-side
            // `_COMMAND_REGISTRY` (ipc_server.py) and implemented in
            // `handlers/onboarding_handlers.py` (`_handle_onboarding_reset`).
            // Mirrors the TS allowlist.
            "onboarding_reset",
            // `test_cloud_connection` — renderer "Test Connection" button
            // on the Cloud Providers page; mirrors the TS allowlist.
            "test_cloud_connection",
            // `add_trusted_endpoint` — URL-allowlist extension
            // (self-hosted LLM/ASR endpoints); mirrors the TS allowlist.
            "add_trusted_endpoint",
        ];
        // Build the set in one pass. Duplicate detection is enforced
        // by the `test_allowed_commands_set_contains_no_duplicates`
        // unit test (which asserts `set.len() == 62`), so we don't
        // need a runtime `log::error!` per duplicate here — that path
        // was ~14 lines of defensive logging on a static `&[&str]`
        // literal and was redundant with the test. If a future
        // copy-paste slip adds a duplicate, the test fails in CI
        // before the runtime log would ever fire in production.
        HashSet::from_iter(cmds.iter().copied())
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
//never invokes it; 's allowlist parity test would fail if it
/// were added).
///
/// The public `dispatch` Tauri command wraps this with the allowlist
//gate () before delegating. Trusted Rust callers that need to
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
    //the dispatch body is extracted into the shared
    // `dispatch_frame` helper below so the tray menu handler
    // (`tray.rs::on_menu_event`) can call it directly instead of
    // emitting a Tauri "dispatch" event that has no listener.
    dispatch_frame(&state, &args.cmd, args.data).await
}

//( / ): fire-and-forget dispatch helper.
///
/// Builds a WS frame `{"type": cmd, "data": data, "id": 0}` and sends it
/// via `state.ws_tx.try_send` WITHOUT inserting a pending oneshot entry
/// or awaiting a response. Used by `commands::bubble::bubble_toggle_dictation`
/// (a sandboxed-window command that must NOT use the full `dispatch`
/// path — the bubble renderer is allowed to send only the fixed
//`toggle_dictation` command, see  sanctioned-bypass doc).
///
//the synthetic `id: 0` is NOT special-cased server-side.
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
//duplicated in `bubble.rs:629-674` (the  TODO that called for
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
    //`ws_tx` is a bounded `mpsc::Sender` — use `try_send`
    // (synchronous) rather than `.send().await` (which would require an
    // async context AND block on the writer-task consumer). Returns
    // `TrySendError::Full` if the writer is overwhelmed (256-cap) or
    // `TrySendError::Closed` if the writer task exited.
    ws_tx
        .try_send(Message::Text(frame.to_string().into()))
        .map_err(|e| format!("WS send failed: {e}"))?;
    Ok(())
}

//shared dispatch body used by both the `dispatch` Tauri command
/// (renderer `invoke('dispatch', {cmd, data})` calls) and the tray menu
/// event handler in `tray.rs::on_menu_event` (which previously emitted
/// a Tauri event named "dispatch" that had no listener — the click was
/// silently dropped).
///
/// Builds a WS frame `{"type": cmd, "data": data, "id": <next_id>}`,
/// inserts a pending oneshot entry, sends the frame via `state.ws_tx`,
/// and awaits the response (or times out).
///
//the pending entry is inserted AFTER confirming `ws_tx` is
/// `Some`. Previously the entry was inserted first and the early-return
/// Err branch on `ws_tx == None` leaked the entry — the WS reader never
/// fulfilled it (no frame was sent), so the map accumulated stale
/// senders across reconnects. We also remove the pending entry on
/// `ws_tx.send` failure (writer task has exited; the reader's drain
/// loop is the only other remover and may not have run yet).
///
//every `Err(...)` return is logged (4 sites: WS send
/// failed, dispatch response channel closed, dispatch timeout, server
/// error). A `log::debug!` at entry gives correlation (id + cmd) for
/// tracing dispatch lifetimes across the WS reader/writer tasks.
///
//bail out early if `state.shutting_down` is set. After
/// `shutdown_sidecar` sends the shutdown frame the WS may stay alive
/// briefly (up to `SHUTDOWN_ACK_TIMEOUT_MS`); dispatches initiated in
/// that window would send the frame but their response hits the
//shutdown-suppress branch in the WS reader () and is
/// dropped — the client then awaits the full `DISPATCH_TIMEOUT_SECS`
/// before rejecting. Short-circuit here instead.
///
//re-check `state.ws_tx` AFTER inserting the pending
/// entry. A reconnect racing in the window between the outer
/// `mutex_lock(&state.ws_tx).clone()` and the pending insert could leave
/// us holding a stale `ws_tx` (the old writer task has exited; the new
/// reader has no record of this id). Detect by re-checking
/// `state.ws_tx` under a tight critical section; if it's now `None`,
/// drop the pending entry and reject.
///
//demoted from `pub(crate) async fn` to `async fn` — the
/// only caller is `dispatch_inner` in this same file (the tray menu
/// handler in `tray.rs` calls `dispatch_inner`, not `dispatch_frame`
/// directly).
async fn dispatch_frame(
    state: &Arc<SidecarState>,
    cmd: &str,
    data: Option<Value>,
) -> Result<Value, String> {
    let id = state.next_id.fetch_add(1, Ordering::SeqCst);
    //debug-level entry log for correlation. The WS reader
    // logs the matching `id` on fulfillment so a slow / dropped
    // dispatch can be traced end-to-end.
    log::debug!("[dispatch] id={} cmd={}", id, cmd);

    //per-command timeout. Model lifecycle commands (download /
    // import / delete / cancel / pause / resume) get 120s; everything
    // else gets 15s. See `dispatch_timeout_for` for the rationale.
    let timeout_secs = dispatch_timeout_for(cmd);

    //short-circuit if the host is shutting down. Avoids
    // the orphaned-pending-then-timeout window described above.
    if state.shutting_down.load(Ordering::SeqCst) {
        log::warn!(
            "[dispatch] id={} cmd={} rejected: sidecar shutting down",
            id,
            cmd
        );
        return Err("sidecar shutting down".into());
    }

    //(Medium): cap `args.data` serialized size BEFORE
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
        let err = json!({
            "type": "error",
            "data": {
                "code": "data_too_large",
                "message": "dispatch data payload exceeds size cap"
            }
        });
        return Err(err.to_string());
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

    //confirm `ws_tx` is Some BEFORE inserting into the pending
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
        //pending-map size cap. Reject new dispatches when
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
    if let Err(e) = ws_tx.try_send(Message::Text(frame_str.into())) {
        let mut pending = state.pending.lock().await;
        pending.remove(&id);
        let err_msg = match &e {
            tokio::sync::mpsc::error::TrySendError::Closed(_) => {
                "sidecar not connected".to_string()
            }
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
        Ok(Ok(mut response)) => {
            // ADR-0020 §2: if the response is a `type:"error"` envelope,
            // surface it as a Rust error so the webview's `invoke()`
            //rejects (this is the  fix — the Electron path
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
    //bound the command-name length so a buggy or compromised
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
        return Err("command name too long".into());
    }

    //window-label guard. The bubble renderer is a sandboxed
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
    //the canonical `require_main_window` helper now lives in
    // `commands/mod.rs`. We delegate to it for the envelope shape +
    //log tag. The previous inline duplicate (with the
    // "dispatch only callable from main window" message) is removed —
    // the renderer's reject path JSON-parses the envelope + keys off
    // the `code` field (`disallowed_window`), so the per-command
    // message wording doesn't matter.
    require_main_window(&window)?;

    //enforce the ALLOWED_COMMANDS allowlist BEFORE forwarding the
    // command to the Python sidecar over WS. This mirrors the Electron
    // renderer-side gate (SEC-019 / ADR-0015) and is the
    // defense-in-depth backstop for a compromised-renderer attack
    // (XSS in the WebView → `invoke('dispatch', {cmd:'<arbitrary>'})`).
    if !is_command_allowed(&args.cmd) {
        log::warn!(
            "[DISPATCH-ALLOWLIST] rejected disallowed dispatch command: {:?} (not in ALLOWED_COMMANDS)",
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
    //only the main window may drive the cooperative-shutdown
    // path. A compromised bubble renderer must NOT be able to invoke
    // `invoke('shutdown_sidecar')` to DoS the sidecar.
    //
    // Note: there is also a programmatic (non-IPC) caller in
    // `main.rs`'s `on_window_event` handler that invokes
    // `shutdown_sidecar` directly when the main window is closed —
    // that caller passes `window.clone()` from the `"main"` arm of the
    // `window.label()` match, so this check passes for it too.
    require_main_window(&window)?;

    //Early-return guard. If a previous `shutdown_sidecar`
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
    // Abort the in-flight heartbeat task so it doesn't keep dispatching
    // `heartbeat` frames into the dead WS for up to HEARTBEAT_MAX_MISSES
    // (~30s) after shutdown. Mirrors `shutdown_sidecar_for_exit` in
    // state.rs — both shutdown paths must abort the heartbeat so the
    // task doesn't outlive the WS connection.
    crate::sidecar::ws::abort_heartbeat(state.inner()).await;
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
    //Wait up to SHUTDOWN_ACK_TIMEOUT_MS for the sidecar to exit.
    // Use the `CommandEvent` receiver captured at spawn time to detect
    // `Terminated` and return immediately (typical sidecar acks+exits in
    // ~50ms), instead of sleeping the full deadline unconditionally.
    // Falls back to a single bounded sleep for the dev-mode path (which
    // has no event receiver).
    let deadline_dur = Duration::from_millis(SHUTDOWN_ACK_TIMEOUT_MS);
    let mut graceful = false;
    // Take the receiver out of the lock and drop the guard BEFORE awaiting
    // `rx.recv()`. Previously the `AsyncMutex` guard was held across the
    // up-to-2s `tokio::time::timeout` await, blocking `respawn_inner`
    // (supervisor.rs) under a tight shutdown race where the supervisor
    // tried to install a new receiver (via `*rx_guard = exit_rx;`) while
    // `shutdown_sidecar` was still holding the lock waiting for the old
    // sidecar to exit.
    //
    // `take()` leaves `None` in the slot. The supervisor's install path is
    // a full assignment (`*rx_guard = exit_rx;`) — it does not read the
    // current value — so overwriting a `None` slot is well-defined: the
    // next respawn stores the new receiver and the next `shutdown_sidecar`
    // call (if any; normally the app exits before that) sees `Some(new_rx)`.
    let rx_opt = {
        let mut rx_guard = state.child_exit_rx.lock().await;
        rx_guard.take()
    };
    if let Some(mut rx) = rx_opt {
        match tokio::time::timeout(deadline_dur, rx.recv()).await {
            Ok(Some(CommandEvent::Terminated(payload))) => {
                log::info!(
                    "[SHUTDOWN] sidecar exited gracefully (code={:?}, signal={:?})",
                    payload.code,
                    payload.signal
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
    // Force-kill backstop. Gate on `!graceful` — if the sidecar exited
    // cooperatively, the grandchildren (native hotkey binary, model
    // subprocesses) were already reaped by the sidecar itself; we still
    // `take()` the child handle (dropping it cleanly) but skip the
    // recursive `kill_tree`. If `graceful` is false (timeout /
    // unexpected exit), `kill_tree` is the force-kill backstop that
    // also walks the process tree to reap grandchildren the sidecar
    // didn't clean up. ADR-0020 §10.
    let child_opt = mutex_lock(&state.child).take();
    if let Some(child) = child_opt {
        if !graceful {
            let _ = child.kill_tree().await;
        }
    }
    log::info!("[SHUTDOWN] sidecar kill completed (graceful={})", graceful);
    let _ = app;
    Ok(())
}

//Host entrypoint helper: main-window close handler (C-) ──────

/// `on_window_event` close-requested branch body, extracted from
/// `main.rs`'s inline closure so the host entrypoint stays wiring-only
//(C-).
///
//ADR-0020 §10: on main window close, shutdown the sidecar. :
/// also handle the bubble window's close so a user dismissing the
/// bubble doesn't leave the sidecar running against a closed webview
/// (just log — no sidecar shutdown for the bubble).
///
//on macOS the app stays alive when the last window
/// closes (standard macOS app lifecycle — the tray / Dock keeps the
/// process running). Killing the sidecar here would orphan the
/// dictation engine while the app is still alive in the menu bar.
/// Only kill the sidecar on Windows/Linux where app exit is bound to
/// last-window-close.
///
/// The actual shutdown runs in a spawned async task so the event loop
/// is not blocked on the cooperative-shutdown wait (up to
/// `SHUTDOWN_ACK_TIMEOUT_MS` = 2s).
pub(crate) fn on_main_window_close(app_handle: &tauri::AppHandle, window: &tauri::Window) {
    match window.label() {
        "main" => {
            if cfg!(target_os = "macos") {
                return;
            }
            //`shutdown_sidecar` takes a `window: tauri::Window`
            //parameter ( main-window guard). Clone the main
            // window handle here so the spawned task can pass it through
            // the guard (the label is "main" so the check passes).
            let main_window = window.clone();
            let app_clone = app_handle.clone();
            tauri::async_runtime::spawn(async move {
                let state: tauri::State<'_, Arc<SidecarState>> = app_clone.state();
                if let Err(e) = shutdown_sidecar(app_clone.clone(), state, main_window).await {
                    log::warn!("[WINDOW] shutdown_sidecar on close failed: {}", e);
                }
            });
        }
        "bubble" => {
            // Bubble window close — no sidecar shutdown, just log.
            log::info!("[WINDOW] bubble window closed by user");
        }
        _ => {}
    }
}

// Unit tests for ALLOWED_COMMANDS + pending-map constants live in the
// sibling `sidecar_cmds_tests.rs` file (C-TEST-5 — keeps production
// source free of inline test code, matching the `commands/bubble/tests.rs`
// pattern). The module is wired as a child of `sidecar_cmds` so the test
// file can use `use super::{...}` to access `pub(crate)` items
// (`allowed_commands`, `is_command_allowed`, `PENDING_MAX`,
// `PENDING_FULL_CODE`).
#[cfg(test)]
#[path = "sidecar_cmds_tests.rs"]
mod sidecar_cmds_tests;
