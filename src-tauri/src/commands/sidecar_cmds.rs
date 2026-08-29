//! Tauri commands: dispatch, shutdown_sidecar (ADR-0020 §7 + §10).
//!
//! Module layout (split out of the former single-file module):
//!
//! - `self` — orchestrator: submodule declarations + the crate-visible
//!   re-exports (`dispatch`, `dispatch_inner`, `DispatchArgs`,
//!   `dispatch_fire_and_forget`, `shutdown_sidecar`,
//!   `on_main_window_close`, `DISALLOWED_COMMAND_CODE`,
//!   `DISALLOWED_WINDOW_CODE`, `PENDING_FULL_CODE`).
//! - [`allowlist`] — the `ALLOWED_COMMANDS` defense-in-depth allowlist
//!   (`allowed_commands` / `is_command_allowed`), the shared error-code
//!   and the pending-map constants (`DISALLOWED_COMMAND_CODE`,
//!   `DISALLOWED_WINDOW_CODE`, `PENDING_MAX`, `PENDING_FULL_CODE`).
//! - [`dispatch`] — the generic `dispatch` Tauri command + the
//!   dispatch helpers (`dispatch_inner`, `dispatch_frame`,
//!   `dispatch_fire_and_forget`, `DispatchArgs`, per-command timeout
//!   routing).
//! - [`shutdown`] — the `shutdown_sidecar` cooperative-shutdown Tauri
//!   command (ADR-0020 §10).
//! - [`window_close`] — the main-window close-requested branch body
//!   (`on_main_window_close`).
//!
//! The `dispatch` and `shutdown_sidecar` `#[tauri::command]` functions
//! are both guarded by `commands::mod::require_main_window` (the
//! canonical main/bubble window guard — SEC-026) so a compromised
//! bubble renderer can never drive the sidecar WS or paste path.

mod allowlist;
mod dispatch;
mod shutdown;
mod window_close;

// Crate-visible re-exports (external callers import these through the
// `sidecar_cmds` namespace — `main.rs`, `commands/mod.rs`,
// `sidecar/ws/heartbeat.rs`, `commands/bubble/commands.rs`, `error.rs`).
// The three error-code constants are re-exported unconditionally so
// `error.rs` (the single home of the envelope strings) can single-source
// the codes without inline literals.
pub(crate) use allowlist::{
    DISALLOWED_COMMAND_CODE, DISALLOWED_WINDOW_CODE, PENDING_FULL_CODE,
};
pub(crate) use dispatch::{dispatch, dispatch_fire_and_forget, dispatch_inner, DispatchArgs};
pub(crate) use shutdown::shutdown_sidecar;
pub(crate) use window_close::on_main_window_close;

// Test-only re-exports: the sibling `sidecar_cmds_tests.rs` file (and
// the Rust unit tests) access these via `use super::{...}`. Production
// callers reach them through the submodules directly. `PENDING_FULL_CODE`
// is NOT here — it moved to the unconditional re-export block above
// (`error.rs` imports it in production code).
#[cfg(test)]
pub(crate) use allowlist::{allowed_commands, is_command_allowed, PENDING_MAX};

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
