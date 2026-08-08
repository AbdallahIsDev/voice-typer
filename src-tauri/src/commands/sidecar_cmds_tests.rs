//! Unit tests for `sidecar_cmds` (extracted per C-TEST-5).
//!
//! Originally inline in `sidecar_cmds.rs` as `#[cfg(test)] mod tests { ... }`;
//! moved to this sibling file to keep production source files free of test
//! code (C-TEST-5 — matches the pattern established by
//! `commands/bubble/tests.rs`).
//!
//! These tests pin the dispatch allowlist (SEC-019 / ADR-0015 defense-in-
//! depth). The Python parity test in
//! `tests/test_security_doc_command_count.py::test_rust_allowlist_matches_ts_allowlist`
//! cross-checks the Rust set against the TS `ALLOWED_COMMANDS` literal —
//! these Rust tests are the unit-level sanity checks (must contain key
//! commands, must NOT contain dangerous commands).

use super::{allowed_commands, is_command_allowed, PENDING_FULL_CODE, PENDING_MAX};
use serde_json::{json, Value};

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
    //`heartbeat` and `relaunch_ack` are Rust-only commands
    // invoked via `dispatch_inner` (which bypasses this allowlist
    // gate) — `heartbeat` is dispatched by the WS-reader task's
    // heartbeat subtask (`sidecar/ws.rs::spawn_heartbeat_task`),
    // and `relaunch_ack` is dispatched by the `relaunch_app` Tauri
    // event handler in `main.rs`. Including either in the
    // `dispatch` allowlist would let a compromised renderer spoof
    // them via `invoke('dispatch', {cmd:'...'})`. Same pattern as
    //`tray_click` ().
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
    //the Rust allowlist must contain EXACTLY the same number
    // of commands as the TS allowlist in
    // `voice_typer/client/src/main/allowed-commands.ts` (canonical
    // declaration since R6-F10 — was previously inline in
    // `index.ts:79-191`). The Python test
    // `tests/test_security_doc_command_count.py::test_rust_allowlist_matches_ts_allowlist`
    // asserts the entries match exactly — this Rust-side test pins
    // the COUNT so a local `cargo test` catches a drift before the
    // Python test even runs.
    //
    // 62 shared commands (TS has 64 = 62 shared + heartbeat +
    // relaunch_ack). `heartbeat` and `relaunch_ack` are
    // intentionally ABSENT from this Rust literal — see the
    // doc comment on the cmds literal below. `add_trusted_endpoint`
    // was the most recent entry added; `tray_click` is also
    // intentionally absent — see `dispatch_inner`.
    assert_eq!(
        allowed_commands().len(),
        62,
        "must match TS allowlist (64 entries) minus heartbeat/relaunch_ack (62 entries)"
    );
}

#[test]
fn test_allowed_commands_set_contains_no_duplicates() {
    let set = allowed_commands();
    // 62 entries — must match the cmds literal below (single
    // source of truth). A duplicate in the literal would make
    // set.len() < 62.
    assert_eq!(
        set.len(),
        62,
        "ALLOWED_COMMANDS contains a duplicate entry — set len ({}) < literal len (62). \
         Check the constructor log for the duplicate name.",
        set.len()
    );
}

#[test]
fn test_allowed_commands_exact_snapshot() {
    //Stricter parity test: pin the EXACT 62-entry set (sorted)
    // so any drift between the Rust literal and the TS allowlist is
    // caught at `cargo test` time, BEFORE the cross-layer Python
    // parity test in
    // `tests/test_security_doc_command_count.py::test_rust_allowlist_matches_ts_allowlist`
    // runs. The count-only test above catches add/remove drift but
    // MISSES a rename (e.g. `onboarding_reset` → `reset_onboarding`)
    // that keeps the count at 62. This snapshot test catches both
    // renames and any silent reordering that would mask a missing
    // entry.
    //
    // The expected list is the alphabetically-sorted union of:
    //   - the TS `ALLOWED_COMMANDS` literal in
    //     `voice_typer/client/src/main/allowed-commands.ts` (64 entries)
    //   - minus the two Rust-only-excluded commands:
    //     `heartbeat` (sent by the Rust WS-reader task) and
    //     `relaunch_ack` (sent by the Rust `relaunch_app` event
    //     handler). Both bypass the `dispatch` allowlist via
    //     `dispatch_inner` — see the doc comment on the `cmds`
    //     literal above for the security rationale.
    //
    // MAINTENANCE: when adding/removing a command from the Rust
    // literal, ALSO update this snapshot and the TS allowlist in
    // the same PR. The Python parity test will catch a missed TS
    // update, but this test catches a missed Rust snapshot update
    // faster (no Python venv required).
    let mut actual: Vec<&str> = allowed_commands().iter().copied().collect();
    actual.sort();
    let expected: &[&str] = &[
        "add_trusted_endpoint",
        "cancel_model_download",
        "clear_history",
        "delete_history",
        "delete_model",
        "download_model",
        "force_cancel_transcription",
        "get_config",
        "get_defaults",
        "get_favorites",
        "get_history",
        "get_history_count",
        "get_microphones",
        "get_model_catalog",
        "get_model_status",
        "get_prewarm_status",
        "get_status",
        "get_templates",
        "get_today_stats",
        "get_transcription_text",
        "get_vocabulary",
        "get_volume_backend_status",
        "import_model",
        "level_monitor_start",
        "level_monitor_stop",
        "microphone_test_cancel",
        "microphone_test_get_level",
        "microphone_test_start",
        "microphone_test_stop",
        "onboarding_apply",
        "onboarding_check_permissions",
        "onboarding_get_hotkey_presets",
        "onboarding_get_microphones",
        "onboarding_get_model_options",
        "onboarding_is_first_run",
        "onboarding_next_step",
        "onboarding_prev_step",
        "onboarding_reset",
        "onboarding_set_backend",
        "onboarding_set_hotkey",
        "onboarding_set_microphone",
        "onboarding_set_model",
        "onboarding_skip",
        "onboarding_start",
        "open_prewarm_log",
        "pause_model_download",
        "quit_app",
        "repaste_last",
        "restart_app",
        "restore_history",
        "resume_model_download",
        "run_prewarm",
        "save_templates",
        "save_vocabulary",
        "search_history",
        "set_config",
        "set_esc_cancel_paused",
        "set_tray_locale",
        "test_cloud_connection",
        "toggle_dictation",
        "toggle_favorite",
        "undo_last",
    ];
    assert_eq!(
        actual.len(),
        expected.len(),
        "snapshot length mismatch — actual Rust set has {} entries, snapshot expected {}. \
         If you added/removed a command, update BOTH this snapshot AND the cmds literal AND \
         the TS allowlist in voice_typer/client/src/main/allowed-commands.ts.",
        actual.len(),
        expected.len()
    );
    assert_eq!(
        actual, expected,
        "ALLOWED_COMMANDS snapshot drift — the Rust literal no longer matches the pinned \
         62-entry snapshot. Diff the actual vs expected Vec above. If the change is \
         intentional, update this snapshot in lockstep with the cmds literal AND the TS \
         allowlist (see MAINTENANCE note above)."
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

//pending-map size cap ───────────────────────────────

#[test]
fn test_pending_max_constant_is_1024() {
    //PENDING_MAX must be 1024. The cap is sized to
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
    //the error code returned by `dispatch_frame` when
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
    //the `pending_full` error must serialize to a JSON
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
