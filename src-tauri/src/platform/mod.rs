//! Platform-specific code: per-OS paths + file logging (ADR-0020 §8 + §11).
//!
//! ``open_path`` hosts the per-OS "open path in file manager"
//! dispatch (``explorer.exe`` / ``open`` / ``xdg-open``). Previously
//! this lived in ``commands/system_cmds.rs``, mixing the Tauri command
//! facade concern with the per-OS binary-dispatch concern. Moved here
//! so ``platform/`` is the single home for per-OS code (alongside
//! ``paths.rs`` for per-OS config-dir resolution and ``logging.rs``
//! for per-OS file logging).

pub(crate) mod logging;
pub(crate) mod open_path;
pub(crate) mod paths;
pub(crate) mod power;
pub(crate) mod process;
// Renderer/webview liveness watchdog: a heartbeat-based stand-in
// for the predecessor's `child-process-gone` telemetry, which no Tauri/wry
// platform exposes (see the module docs).
pub(crate) mod renderer_watchdog;
pub(crate) mod worker_path;

#[cfg(test)]
mod logging_tests;
