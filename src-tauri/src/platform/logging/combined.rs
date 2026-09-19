//! Combined stderr + rotating-file logger + shared truthy-env helpers.

use super::redact::redact_pii;
use super::rotating::RotatingFileWriter;
use crate::util::now_timestamps;
use std::sync::atomic::{AtomicBool, Ordering};

/// Shared truthy matcher for boolean env vars: `"1"` / `"true"` / `"yes"`
/// (case-insensitive, trimmed). Mirrors Python `env_validation.py`.
pub(crate) fn is_truthy_value(value: Option<&str>) -> bool {
    match value {
        Some(v) => matches!(v.trim().to_ascii_lowercase().as_str(), "1" | "true" | "yes"),
        None => false,
    }
}

/// Env-var form of the truthy predicate. Unset vars are falsy.
pub(crate) fn is_truthy_env_var(name: &str) -> bool {
    is_truthy_value(std::env::var(name).ok().as_deref())
}

/// `VOICE_TYPER_DEBUG` value form (kept for unit-testability).
pub(crate) fn is_debug_env_truthy(value: Option<&str>) -> bool {
    is_truthy_value(value)
}

/// Combined stderr + rotating-file logger (replaces `env_logger` so the
/// file sink needs no multiplexer crate).
pub(crate) struct CombinedLogger {
    pub(crate) file_writer: Option<RotatingFileWriter>,
    /// Most-verbose of the two sinks; checked in `enabled()` before format.
    pub(crate) level_filter: log::LevelFilter,
    /// FILE-sink level gate (default WARN/ERROR so support logs stay
    /// small; INFO opt-in via `VOICE_TYPER_RUST_INFO_LOG=1` / `RUST_LOG`).
    /// Terminal keeps the more verbose `level_filter`.
    pub(crate) file_level: log::LevelFilter,
    /// Cached stderr-logging predicate (debug builds or `RUST_LOG_STDERR=1`).
    /// Atomic so a future command can toggle without restart; Relaxed load
    /// is a plain MOV.
    pub(crate) stderr_verbose: AtomicBool,
}

impl log::Log for CombinedLogger {
    fn enabled(&self, metadata: &log::Metadata) -> bool {
        metadata.level() <= self.level_filter
    }

    fn log(&self, record: &log::Record) {
        if !self.enabled(record.metadata()) {
            return;
        }
        // Redact PII before any sink write (C-LOG-1 / SEC redaction pass).
        let raw_msg = record.args().to_string();
        let msg = redact_pii(&raw_msg);
        // C-LOG-1: FILE `YYYY-MM-DD  HH:MM:SS  LEVEL  msg`, TERMINAL
        // `HH:MM:SS  LEVEL  msg`. Single clock read so both lines share
        // one timestamp. Session id appears only on the startup banner.
        // see docs/code-notes/tauri-host.md#logging-format-c-log-1
        let (file_ts, term_ts) = now_timestamps();
        let file_line = format!("{}  {:5} {}", file_ts, record.level(), msg);
        // Terminal line only when stderr logging is enabled (avoids a
        // wasted String allocation per record in release).
        if self.stderr_verbose.load(Ordering::Relaxed) {
            let term_line = format!("{} {:5} {}", term_ts, record.level(), msg);
            eprintln!("{}", term_line);
        }
        // ADR-0020 §11: exclude Info+ `bubble_level` from the file log
        // (60 Hz would fill disk); Error/Warn are preserved.
        if let Some(writer) = &self.file_writer {
            if record.level() > self.file_level {
                return;
            }
            let is_filtered_bubble = record.level() >= log::Level::Info
                && msg.starts_with("[WS-READER] bubble_level event");
            if !is_filtered_bubble {
                // ERROR bypasses the bounded-queue drop gate (crash-path
                // evidence is never sacrificed when the writer is wedged).
                let _ = writer.write_line_level(&file_line, record.level());
                // Flush Warn+ immediately so an impending crash does not
                // strand the diagnostic line in the 8 KB buffer.
                if record.level() <= log::Level::Warn {
                    let _ = writer.flush();
                }
            }
        }
    }

    fn flush(&self) {
        if let Some(writer) = &self.file_writer {
            let _ = writer.flush();
        }
    }
}
