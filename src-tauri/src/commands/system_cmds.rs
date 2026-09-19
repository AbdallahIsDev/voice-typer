//! Main-window system-surface commands, split by concern under
//! `system_cmds/`. Orchestrator: submodule decls + re-exports.
//! Every `#[tauri::command]` is guarded by `require_main_window`
//! (SEC-026). Layout: docs/code-notes/tauri-host.md#module-layout

mod dialog_titles;
mod dialogs;
mod export;
// `heartbeat`: renderer-liveness timestamp for the host watchdog.
mod heartbeat;
mod locale;
mod redaction;
mod renderer_log;
mod stats_image;

// Crate-visible re-exports (`main.rs` imports the commands from here).
// Dialog titles also re-exported because `commands::export` resolves
// save-dialog titles through them.
pub(crate) use dialog_titles::{localized_title_for, DialogTitle};
pub(crate) use dialogs::{
    open_external_url_command, open_logs, open_model_import_dialog, reveal_path_command,
};
pub(crate) use export::{export_config, export_templates};
pub(crate) use heartbeat::renderer_heartbeat;
pub(crate) use locale::set_host_locale;
pub(crate) use renderer_log::renderer_log_error;
pub(crate) use stats_image::save_stats_image;

// C-TEST-5: sibling test file (redaction + locale core).
#[cfg(test)]
#[path = "system_cmds_tests.rs"]
mod system_cmds_tests;
