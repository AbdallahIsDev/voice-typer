//! Main-window system-surface commands, split by concern (one file per
//! concern under `system_cmds/`: mirrors the `commands/bubble/` and
//! `commands/sidecar_cmds/` decompositions):
//!
//! - [`dialog_titles`]: the Rust-side locale→title lookup that
//!   consumes the renderer-pushed `host_locale` at the native
//!   dialog title sites (mirrors the predecessor's `mainT()` dialog
//!   strings).
//! - [`dialogs`]: native OS-surface commands: `open_logs` (OS file
//!   manager), `open_external_url_command` (https-only browser launch,
//!   MO-118), `reveal_path_command` (predecessor
//!   `shell.showItemInFolder` parity, MO-120) + `open_model_import_dialog`
//!   (native folder picker).
//! - [`renderer_log`]: the `renderer_log_error` sink + its
//!   bounded-payload (8 KiB cap) serialization core.
//! - [`export`]: `export_templates` + `export_config` (thin wrappers
//!   over `crate::commands::export::export_data`).
//! - [`redaction`]: the defense-in-depth config-secret scrubbing
//!   library (`REDACTED_MARKER`, `is_sensitive_key`,
//!   `redact_config_secrets`), consumed by `export_config`.
//! - [`locale`]: `set_host_locale` + its pure decision core.
//! - [`stats_image`]: `save_stats_image`, the share-image
//!   Downloads-instant-save + localized Save-As dialog (MO-121,
//!   predecessor `stats-image:save` parity).
//!
//! This file is the orchestrator only: submodule declarations + the
//! crate-visible re-exports that keep every historical public name
//! resolving (`main.rs` imports the 6 commands from
//! `commands::system_cmds`). The sibling test file reaches the
//! redaction + locale helpers through their owning submodules
//! directly (`super::redaction::...` / `super::locale::...`, the
//! same submodule-direct import shape as `commands/bubble/tests.rs`).
//!
//! Every `#[tauri::command]` in this tree is guarded by
//! `commands::mod::require_main_window` (the canonical main/bubble
//! window guard: SEC-026) so a compromised bubble renderer can never
//! open OS surfaces, write host state, or trigger exports.

mod dialog_titles;
mod dialogs;
mod export;
// [`heartbeat`]: `renderer_heartbeat`, the renderer-liveness timestamp
// consumed by `platform::renderer_watchdog` (MO-113).
mod heartbeat;
mod locale;
mod redaction;
mod renderer_log;
mod stats_image;

// Crate-visible re-exports: `main.rs` imports these six commands from
// `commands::system_cmds` (see the `use commands::system_cmds::{...}`
// block + `generate_handler!` registration there). The dialog-title
// lookup is ALSO re-exported because `commands::export::export_data`
// (outside this module tree) resolves its save-dialog titles through
// it. The redaction, locale-core, and bounded-serialization helpers
// keep their owning submodule as the single import path, no extra
// re-export surface for items only the sibling test files consume.
pub(crate) use dialog_titles::{localized_title_for, DialogTitle};
pub(crate) use dialogs::{
    open_external_url_command, open_logs, open_model_import_dialog, reveal_path_command,
};
pub(crate) use export::{export_config, export_templates};
pub(crate) use heartbeat::renderer_heartbeat;
pub(crate) use locale::set_host_locale;
pub(crate) use renderer_log::renderer_log_error;
pub(crate) use stats_image::save_stats_image;

// Unit tests for the redaction library + locale core live in the
// sibling `system_cmds_tests.rs` file (C-TEST-5: keeps production
// source free of inline test code, matching the
// `commands/bubble/tests.rs` pattern). Bounded-serialization tests for
// the renderer log sink live in `system_cmds/renderer_log_tests.rs`,
// wired from `renderer_log.rs`; the dialog-title lookup tests live in
// `system_cmds/dialog_titles_tests.rs`, wired from `dialog_titles.rs`,
// and the `open_logs` target-path tests live in
// `system_cmds/dialogs_tests.rs`, wired from `dialogs.rs`.
#[cfg(test)]
#[path = "system_cmds_tests.rs"]
mod system_cmds_tests;
