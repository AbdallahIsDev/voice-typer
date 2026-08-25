//! Tauri command handler modules (ADR-0020 §6 + §7 + §10 +  +  + ).

pub(crate) mod bubble;
pub(crate) mod export;
pub(crate) mod sidecar_cmds;
pub(crate) mod system_cmds;
//the `paste` module + the `paste_text` Tauri command
// were deleted as dead production code. The Python sidecar owns the
// paste path end-to-end via
// `voice_typer/server/dictation_pipeline.py::_dispatch_paste`
// (clipboard write + Ctrl/Cmd+V keystroke), and no Python or TS code
//ever invoked `invoke('paste_text', ...)`. See review.md  +
//for the full deletion rationale.

//`dispatch_inner` + `DispatchArgs` are `pub(crate)` (NOT Tauri
// commands — they are the allowlist-bypass inner function the tray
// menu click handler uses). Re-exported with crate visibility because
// `crate::tray` imports them via `use crate::commands::{...}`.
//
//the 5 `pub use` re-export blocks for the `#[tauri::command]`
// functions that used to live here were DEAD — `main.rs` imports each
// command directly from its submodule, so the `pub use` re-exports had
// no caller. Both the re-exports and the `#[allow(unused_imports)]`
// annotations are deleted here; `cargo check` confirms `generate_handler!`
// still resolves every command via the direct submodule imports.
pub(crate) use sidecar_cmds::{dispatch_inner, DispatchArgs};

//the unified command error type. Every `#[tauri::command]` in this
// tree returns `Result<T, VoiceTyperError>`; see `error.rs` for the
// Display-vs-Serialize split (log strings vs renderer wire strings).
use crate::error::VoiceTyperError;

//canonical main-window guard (ADR-0020 §7 + §9 + SEC-026) ────
//
// `dispatch`, `shutdown_sidecar`, `export_*`, `bubble_signal_ready`
// are all `#[tauri::command]` functions that a compromised renderer could
// invoke over the IPC bridge. The bubble window is a sandboxed webview
// (ADR-0020 §7 + §9 + SEC-026) that must NEVER drive the sidecar WS,
// export path, or sidecar-level bubble readiness handshake.
// Tauri v2's capability system only gates plugin commands, so user-defined
// commands need this runtime check.
//
// Previously (`sidecar_cmds.rs:32`, `bubble.rs:67`, `export.rs:26`) this
// helper was duplicated 3× with subtly different log tags, error messages,
//and even different visibility (`fn`-private vs `pub(crate)`).
// consolidates on a single canonical copy here. The error envelope shape
// mirrors the sidecar's WS error envelope
// ({"type":"error","data":{"code":...,"message":...}}) so the renderer's
// existing reject path treats this identically to a server-side rejection.
//
// `main_window_label_check` is the pure-helper that does NOT require a
// `tauri::Window` — extracted so unit tests can verify the gate logic
// without constructing a Tauri runtime. Returns `true` iff `label == "main"`.

//pure main-window label predicate. Returns `true` iff `label` is
/// the canonical main-window label (`"main"`, registered in
/// `main.rs::setup` via `WindowBuilder::new("main")`). Used by
/// [`require_main_window`] as the testable surface.
pub(crate) fn main_window_label_check(label: &str) -> bool {
    label == "main"
}

//gate a `#[tauri::command]` on the calling window being the main
/// window. Logs a `[window-guard]` warning on rejection so the security
/// audit trail shows the rejected call attempt + the offending window
/// label. Returns `Err(VoiceTyperError::disallowed_main_window())` for
/// non-main windows — the variant serializes to the same
/// `{"type":"error","data":{"code":"disallowed_window",...}}`
/// envelope string the renderer's reject path has always parsed, so it
/// is handled identically to a server-side rejection.
pub(crate) fn require_main_window(window: &tauri::Window) -> Result<(), VoiceTyperError> {
    if !main_window_label_check(window.label()) {
        log::warn!(
            "[window-guard] command rejected from non-main window: {}",
            window.label()
        );
        return Err(VoiceTyperError::disallowed_main_window());
    }
    Ok(())
}

/// Gate a `#[tauri::command]` on the calling window being the bubble
/// window. Mirrors `require_main_window` but for bubble-only commands
/// (e.g. `bubble_signal_ready` — the bubble renderer's readiness signal).
/// Returns `Err(VoiceTyperError::disallowed_bubble_window())` — the same
/// canonical JSON error envelope on the wire, so the renderer's reject
/// path handles it identically to a server-side rejection.
pub(crate) fn require_bubble_window(window: &tauri::Window) -> Result<(), VoiceTyperError> {
    if window.label() != "bubble" {
        log::warn!(
            "[window-guard] command rejected from non-bubble window: {}",
            window.label()
        );
        return Err(VoiceTyperError::disallowed_bubble_window());
    }
    Ok(())
}
