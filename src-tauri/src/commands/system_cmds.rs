//! Main-window system-surface commands, split by concern (one file per
//! concern under `system_cmds/` — mirrors the `commands/bubble/` and
//! `commands/sidecar_cmds/` decompositions):
//!
//! - [`dialogs`] — native OS-surface commands: `open_logs` (OS file
//!   manager) + `open_model_import_dialog` (native folder picker).
//! - [`renderer_log`] — the `renderer_log_error` sink + its
//!   bounded-payload (8 KiB cap) serialization core.
//! - [`export`] — `export_templates` + `export_config` (thin wrappers
//!   over `crate::commands::export::export_data`).
//! - [`redaction`] — the defense-in-depth config-secret scrubbing
//!   library (`REDACTED_MARKER`, `is_sensitive_key`,
//!   `redact_config_secrets`), consumed by `export_config`.
//! - [`locale`] — `set_host_locale` + its pure decision core.
//!
//! This file is the orchestrator only: submodule declarations + the
//! crate-visible re-exports that keep every historical public name
//! resolving (`main.rs` imports the 6 commands from
//! `commands::system_cmds`). The sibling test file reaches the
//! redaction + locale helpers through their owning submodules
//! directly (`super::redaction::...` / `super::locale::...` — the
//! same submodule-direct import shape as `commands/bubble/tests.rs`).
//!
//! Every `#[tauri::command]` in this tree is guarded by
//! `commands::mod::require_main_window` (the canonical main/bubble
//! window guard — SEC-026) so a compromised bubble renderer can never
//! open OS surfaces, write host state, or trigger exports.

mod dialogs;
mod export;
mod locale;
mod redaction;
mod renderer_log;

// Crate-visible re-exports — `main.rs` imports these six commands from
// `commands::system_cmds` (see the `use commands::system_cmds::{...}`
// block + `generate_handler!` registration there). The redaction,
// locale-core, and bounded-serialization helpers keep their owning
// submodule as the single import path — no extra re-export surface for
// items only the sibling test file consumes.
pub(crate) use dialogs::{open_logs, open_model_import_dialog};
pub(crate) use export::{export_config, export_templates};
pub(crate) use locale::set_host_locale;
pub(crate) use renderer_log::renderer_log_error;

// Unit tests for the redaction library + locale core live in the
// sibling `system_cmds_tests.rs` file (C-TEST-5 — keeps production
// source free of inline test code, matching the
// `commands/bubble/tests.rs` pattern). Bounded-serialization tests for
// the renderer log sink live in `system_cmds/renderer_log_tests.rs`,
// wired from `renderer_log.rs`.
#[cfg(test)]
#[path = "system_cmds_tests.rs"]
mod system_cmds_tests;
