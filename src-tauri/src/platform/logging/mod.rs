//! Rotating file logger (ADR-0020 §11): 5 MB × 5 files, excludes bubble_level.
//!
//! Log files + the parent `<config_dir>/logs/` dir
//! are created with restricted POSIX permissions (`0o600` for files,
//! `0o700` for the dir) so dictated-text fragments and any PII the
//! Rust code emits are NOT world-readable on multi-user POSIX systems.
//! Mirrors the Python side's `os.umask(0o077)` + `os.chmod(log_file,
//! 0o600)` pattern in `voice_typer/server/log.py`.
//!
//! # Module layout
//!
//! Decomposed into per-concern submodules (the split this file's old
//! header used to propose; executed with zero behavior change — bodies
//! moved verbatim, only visibility qualifiers + imports adjusted):
//!
//! ```text
//! src/platform/logging/
//!   mod.rs          // module doc + re-exports of the public API
//!   init.rs         // sweep_stale_logs + init_file_logger
//!                   //   + init_file_logger_or_stderr_fallback
//!   combined.rs     // CombinedLogger + truthy-env helpers
//!   redact.rs       // redact_pii + try_match_* + SECRET_KEYWORDS
//!   panic_hook.rs   // install_panic_hook + PANIC_HOOK_REENTRY
//!   early.rs        // EarlyLogger + EARLY_LOGGER_HANDLE
//!                   //   + install_early_logger
//!   rotating.rs     // RotatingFileWriter
//! ```
//!
//! Tests live in the SIBLING file `platform/logging_tests.rs`
//! (wired via `#[cfg(test)] mod logging_tests;` in `platform/mod.rs`) —
//! no per-submodule test files, per the repo-wide no-inline-tests
//! convention. The sibling test module reaches this module's items via
//! `use super::logging::*;`, so everything the tests poke is
//! re-exported below.
//!
//! Submodules stay private; all external access goes through the
//! re-exports. `init_file_logger` is intentionally NOT re-exported —
//! its only caller is `init_file_logger_or_stderr_fallback` inside
//! `init.rs` (re-exporting it would be a dead re-export).

mod combined;
mod early;
mod init;
mod panic_hook;
mod redact;
mod rotating;

// Production API — consumed by `main.rs` (logger bootstrap order:
// install_early_logger → install_panic_hook →
// init_file_logger_or_stderr_fallback).
pub(crate) use early::install_early_logger;
pub(crate) use init::init_file_logger_or_stderr_fallback;
pub(crate) use panic_hook::install_panic_hook;

// Test-only re-exports — the sibling `logging_tests.rs` resolves these
// via `use super::logging::*;` / `super::logging::<name>`; production
// callers reach the submodules directly (same pattern as
// `sidecar/spawn.rs`). Gated so non-test builds carry no dead
// re-exports.
#[cfg(test)]
pub(crate) use combined::{
    is_debug_env_truthy, is_truthy_env_var, is_truthy_value, CombinedLogger,
};
#[cfg(test)]
pub(crate) use early::{EarlyLogger, EARLY_LOGGER_HANDLE};
#[cfg(test)]
pub(crate) use init::sweep_stale_logs;
#[cfg(test)]
pub(crate) use panic_hook::PANIC_HOOK_REENTRY;
#[cfg(test)]
pub(crate) use redact::{has_any_fast_trigger, redact_pii};
#[cfg(test)]
pub(crate) use rotating::RotatingFileWriter;
