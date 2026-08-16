//! `ALLOWED_COMMANDS` defense-in-depth allowlist + shared error-code /
//! pending-map constants — extracted from the former single-file
//! `commands/sidecar_cmds.rs` (EO-35 split).

use std::collections::HashSet;
use std::sync::OnceLock;

// dedicated error code constants for the two disallowed-code
// branches in `dispatch`. Using named constants instead of inline
// string literals ensures the test assertions (which match on these
// exact strings) stay in sync with the production code.
//
// `disallowed_window` is emitted by `commands/mod.rs::require_main_window`
// (NOT extracted here — that file is outside this entry's files list;
// see `commands::mod` for the literal). Listed here for documentation
// parity so a future cleanup can centralize both constants.
pub(crate) const DISALLOWED_COMMAND_CODE: &str = "disallowed_command";
// companion Rust-host-only code. Emitted by
/// `commands/mod.rs::require_main_window` (the main/bubble window guard).
/// Kept here as a `pub(crate)` constant so the contract test
/// (`tests/test_error_codes_registry.py`) can reference the canonical
/// spelling without having to grep `commands/mod.rs` (which is outside
/// the  entry's files list).
pub(crate) const DISALLOWED_WINDOW_CODE: &str = "disallowed_window";

// pending-map size cap. Each pending dispatch entry is a
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
// error code returned by `dispatch_frame` when the pending
/// map has reached `PENDING_MAX` entries. The renderer treats this as
/// a transient "sidecar overwhelmed" signal (distinct from "sidecar
/// not connected" / "sidecar shutting down") so it can back off and
/// retry rather than spamming more dispatches.
pub(crate) const PENDING_FULL_CODE: &str = "pending_full";

// ALLOWED_COMMANDS allowlist (ADR-0015 defense-in-depth) ──────
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
// `tray_click` is intentionally ABSENT from this literal
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
        // this list MUST mirror the Electron renderer's
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
            // (RESTORED 2026-08-14) `get_prewarm_status` +
            // `open_prewarm_log` came back in lockstep with the TS
            // `ALLOWED_COMMANDS` Set and the Python `_COMMAND_REGISTRY`
            // (see the inline history block in
            // `voice_typer/server/ipc/registry.py` and the §6.4 IPC
            // parity contract): the About-page Cache Status card is a
            // user-facing product feature, not prewarm machinery.
            // `run_prewarm` was ALSO restored the same day (§6.3
            // addendum second half) but re-implemented server-side —
            // the Python handler no longer spawns the deleted
            // standalone-prewarm subprocess; it re-runs the worker's
            // warm phase in-process (warm_imports_for_worker on a
            // daemon thread, see prewarm/status.run_prewarm_now).
            "get_prewarm_status",
            "open_prewarm_log",
            "run_prewarm",
            "get_vocabulary",
            "save_vocabulary",
            "test_vocabulary_correction",
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
            // previously missing from the Rust literal —
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
            // `heartbeat` and `relaunch_ack` are intentionally
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
            //     as `tray_click` — see  above).
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
            // +  (session-3 + 5): onboarding reset —
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
            // Master plan §7.4 — new IPC request `transcribe_offline`
            // (slim core → worker). Mirrors the TS allowlist and the
            // Python `_COMMAND_REGISTRY`. The renderer invokes this to
            // run an offline transcription through the runtime-pack
            // worker (the slim core forwards the request to the worker
            // over the worker's dedicated WS hop). The push counterpart
            // `transcribe_offline_result` is published via
            // `event_bus.publish(...)` (NOT a command — see
            // `ALLOWED_EVENT_TYPES` in
            // `src-tauri/src/sidecar/ws/event_protocol.rs`). Pinned by
            // `tests/test_event_types_parity.py`.
            "transcribe_offline",
            // Auto-update feature (docs/auto-update-feature.md):
            // runtime-pack update check. The renderer's `useNetworkOnline`
            // hook fires it on the `online` transition; Python handler
            // `_handle_check_offline_pack_update` delegates to
            // `update_check.handle_check_offline_pack_update_ipc` (GitHub-API
            // manifest check + consent-gated background download).
            // Mirrors the TS allowlist + Python `_COMMAND_REGISTRY`.
            "check_offline_pack_update",
        ];
        // Build the set in one pass. Duplicate detection is enforced
        // by the `test_allowed_commands_set_contains_no_duplicates`
        // unit test (which asserts `set.len() == 63`), so we don't
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
