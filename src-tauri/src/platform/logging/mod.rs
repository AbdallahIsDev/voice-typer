//! Rotating file logger (ADR-0020 §11). POSIX perms: files `0o600`,
//! logs dir `0o700` (dictated-text fragments must not be world-readable).
//! Format: C-LOG-1 (docs/code-notes/tauri-host.md#logging-format-c-log-1).
//! Layout: docs/code-notes/tauri-host.md#module-layout-orchestrator-files

mod combined;
mod early;
mod init;
mod panic_hook;
mod redact;
mod rotating;

// Production API (bootstrap order in `main.rs`): install_early_logger →
// install_panic_hook → init_file_logger_or_stderr_fallback.
pub(crate) use early::install_early_logger;
pub(crate) use init::init_file_logger_or_stderr_fallback;
pub(crate) use panic_hook::install_panic_hook;
// Production API for the sidecar child-output tee (`sidecar/child_log.rs`):
// reuses the canonical redaction pass + rotating writer.
pub(crate) use redact::redact_pii;
pub(crate) use rotating::RotatingFileWriter;

// Test-only re-exports for the sibling `platform/logging_tests.rs`.
// `redact_pii` / `RotatingFileWriter` are production re-exports above.
#[cfg(test)]
pub(crate) use combined::{
    is_debug_env_truthy, is_truthy_env_var, is_truthy_value, CombinedLogger,
};
#[cfg(test)]
pub(crate) use early::{EarlyLogger, EARLY_LOGGER_HANDLE};
#[cfg(test)]
pub(crate) use init::{init_file_logger, sweep_stale_logs};
#[cfg(test)]
pub(crate) use panic_hook::{
    breaker_should_exit, PANIC_BREAKER_MAX, PANIC_BREAKER_WINDOW_SECS, PANIC_HOOK_REENTRY,
};
#[cfg(test)]
pub(crate) use redact::has_any_fast_trigger;
