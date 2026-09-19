//! Tauri commands: dispatch, shutdown_sidecar (ADR-0020 §7 + §10).
//! Orchestrator only: submodule decls + crate-visible re-exports.
//! Layout: docs/code-notes/tauri-host.md#module-layout-orchestrator-files

mod allowlist;
mod dispatch;
mod restart;
mod shutdown;
mod window_close;

// Crate-visible re-exports (`main.rs`, `commands/mod.rs`, ws heartbeat,
// bubble commands, `error.rs`). Error codes live in `error.rs`.
pub(crate) use allowlist::{DISALLOWED_COMMAND_CODE, DISALLOWED_WINDOW_CODE, PENDING_FULL_CODE};
pub(crate) use dispatch::{dispatch, dispatch_fire_and_forget, dispatch_inner, DispatchArgs};
pub(crate) use restart::restart_sidecar;
pub(crate) use shutdown::shutdown_sidecar;
pub(crate) use window_close::on_main_window_close;

// Test-only re-exports for the sibling test file.
#[cfg(test)]
pub(crate) use allowlist::{allowed_commands, is_command_allowed, PENDING_MAX};

// C-TEST-5: sibling test file.
#[cfg(test)]
#[path = "sidecar_cmds_tests.rs"]
mod sidecar_cmds_tests;
