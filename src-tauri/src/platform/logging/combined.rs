//! Combined stderr + rotating-file logger and the shared truthy-env
//! predicate helpers.

use super::redact::redact_pii;
use super::rotating::RotatingFileWriter;
use crate::util::now_timestamps;
use std::sync::atomic::{AtomicBool, Ordering};

/// shared truthy matcher for boolean environment variables.
/// Truthy values: `"1"`, `"true"`, `"yes"` (case-insensitive, after
/// trimming leading/trailing whitespace). Anything else — including
/// unset, empty, `"0"`, `"false"`, `"no"`, or a typo like `"yess"` —
/// is falsy.
///
/// This is the single source of truth for the truthy contract across
/// the Voice Typer Rust host. It is wrapped by two thin callers:
/// - `is_truthy_env_var(name)` — looks up an env var by name and
///   applies this matcher (used by both `stderr_verbose` sites).
/// - `is_debug_env_truthy(value)` — applies this matcher to a
///   caller-supplied value (kept for unit-testability without env
///   mutation).
///
/// Mirrors the Python side's `env_validation.py` boolean-var pattern
/// (pattern: `^(1|0|true|false|yes|no)$`, case-insensitive) so the
/// Rust + Python hosts respond identically to the same env var.
pub(crate) fn is_truthy_value(value: Option<&str>) -> bool {
    match value {
        Some(v) => matches!(v.trim().to_ascii_lowercase().as_str(), "1" | "true" | "yes"),
        None => false,
    }
}

/// env-var form of the truthy predicate. Looks up `name` in
/// the process environment and applies `is_truthy_value` to the
/// result. Unset vars are falsy (not an error). Used by both
/// `stderr_verbose` computation sites (`init_file_logger` +
/// `install_early_logger`) so the truthy contract lives in exactly
/// one place.
pub(crate) fn is_truthy_env_var(name: &str) -> bool {
    is_truthy_value(std::env::var(name).ok().as_deref())
}

//predicate form of the VOICE_TYPER_DEBUG env-var check,
/// extracted for unit testing. now delegates to the shared
/// `is_truthy_value` matcher (the same one `is_truthy_env_var` uses)
/// so the truthy contract is defined in exactly one place. Truthy
/// values: "1", "true", "yes" (case-insensitive). Anything else
/// (including unset / empty) is falsy → production Info-level logging.
///
/// Mirrors the Python side's `env_validation.py` boolean-var pattern
/// (pattern: `^(1|0|true|false|yes|no)$`, case-insensitive) so the
/// Rust + Python hosts respond identically to the same env var.
pub(crate) fn is_debug_env_truthy(value: Option<&str>) -> bool {
    is_truthy_value(value)
}

/// Combined stderr + rotating-file logger. Replaces `env_logger` so
/// we can add the file sink without a multiplexer crate.
pub(crate) struct CombinedLogger {
    pub(crate) file_writer: Option<RotatingFileWriter>,
    pub(crate) level_filter: log::LevelFilter,
    //cached predicate — `true` if log lines should ALSO be
    /// written to stderr. Computed ONCE at logger init from
    /// `cfg!(debug_assertions)` (always true in debug builds) OR the
    /// `RUST_LOG_STDERR=1` env var (opt-in for release builds via
    /// `RUST_LOG_STDERR=1 cargo tauri dev`). The prior code called
    /// `eprintln!` unconditionally — a wasted `write(2)` syscall per
    /// log line in release builds where stderr is typically
    /// `/dev/null` (the Tauri app's stdout/stderr are not connected
    /// to a terminal in `cargo tauri build` release binaries).
    /// Caching the predicate here means the per-line cost is a single
    /// bool load, not an `env::var` lookup.
    ///
    // changed from `bool` to `AtomicBool` so the predicate
    /// becomes runtime-toggleable. Future code (e.g. a Tauri command
    /// that flips stderr verbosity without restarting the host) can
    /// `store(true/false, Ordering::Relaxed)` at any time. The per-
    /// line `log()` path uses `load(Ordering::Relaxed)`, which on
    /// x86/ARM compiles to a plain MOV — same cost as a `bool` load.
    /// `Relaxed` is correct: we don't need cross-thread ordering for
    /// a boolean flag whose only consumer is the same thread that
    /// calls `eprintln!`.
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
        // redact PII from the log message before writing
        // to file or stderr. The redactor is intentionally MINIMAL —
        // it covers the highest-signal patterns (Bearer/Token/sk- prefix
        // tokens, user:pass@host URL credentials, basic email addresses)
        // using std-only substring scanning. The Python side's
        // `_FAST_TRIGGER` shortcut (skip if no trigger char present) is
        // mirrored here so the common-case cost is a single
        // `str::contains('@')` / `str::contains('+')` / etc. scan.
        let raw_msg = record.args().to_string();
        let msg = redact_pii(&raw_msg);
        // Clean line format: `ts LEVEL msg`. The `record.target()`
        // module path and `file:line` were deliberately removed — the
        // module path added noise to every line (matching the Python
        // side's removal of the `[component]` label) and the message
        // already carries a `[TOPIC]` prefix identifying the subsystem.
        //
        // The FILE sink gets the full timestamp (`YYYY-MM-DD  HH:MM:SS`,
        // matching Python's `_FileFormatter`) while the TERMINAL sink
        // shows TIME ONLY (`HH:MM:SS` — the date lives only in the log
        // file, matching Python's `_ColorFormatter`). Two lines are
        // built from a SINGLE clock read (`now_timestamps`) so the file
        // and terminal lines for one record can never straddle a second
        // boundary; the level + message body are identical.
        let (file_ts, term_ts) = now_timestamps();
        // Carry the per-process session ID (same value passed to
        // the Python sidecar via `VOICE_TYPER_SESSION_ID`) in the FILE
        // line so the Rust + Python log streams share a join key for
        // cross-process correlation (crash-report matching). The
        // terminal line stays session-free — it's a dev convenience
        // view and the extra 10 chars/line would add noise.
        let file_line = format!(
            "{} {:5} [sid {}] {}",
            file_ts,
            record.level(),
            crate::util::session_id(),
            msg
        );
        // The terminal line is built ONLY when stderr logging is
        // actually enabled — the `format!` used to run unconditionally,
        // wasting one String allocation per log line in release builds
        // where `stderr_verbose` is false (the line was formatted and
        // immediately dropped). Format is unchanged: `HH:MM:SS LEVEL msg`.
        if self.stderr_verbose.load(Ordering::Relaxed) {
            let term_line = format!("{} {:5} {}", term_ts, record.level(), msg);
            eprintln!("{}", term_line);
        }
        // ADR-0020 §11: exclude `bubble_level` from the file log
        // (60 Hz would fill disk fast even with rotation). Match by a
        // SPECIFIC message prefix (`[WS-READER] bubble_level event`)
        // rather than a broad `msg.contains("bubble_level")` substring
        // — the old substring filter risked false-positives on unrelated
        // log lines that happened to mention "bubble_level".
        // the WS reader doesn't currently log bubble_level
        // events to the file (they go via `app.emit()` to the webview,
        // not `log::*!`), so this filter is defensive.
        //
        // preserve WARNING+ records even when they start with
        // the bubble_level prefix. Pre-fix this dropped ANY record
        // matching the prefix regardless of level — a future
        // `log::error!("[WS-READER] bubble_level event handler
        // crashed: ...")` would be SILENTLY LOST from the file log.
        // Mirrors Python's `_BubbleLevelExclusionFilter` short-circuit
        // at `log.py:216-219`:
        // `if record.levelno >= logging.WARNING: return True`
        // (filter returning True = "do NOT filter out" in Python's
        // logging API). The Rust equivalent is the level-guarded
        // early-skip below.
        if let Some(writer) = &self.file_writer {
            // The `log` crate orders levels by severity: Error(1) <
            // Warn(2) < Info(3) < Debug(4) < Trace(5). So "WARNING+"
            // (preserve Error/Warn) is `record.level() <= Warn`.
            // `record.level() >= Info` therefore matches ONLY the
            // low-severity records (Info/Debug/Trace) that the ADR
            // wanted excluded from the file log. Pre-fix this used
            // `<=` which inverted the guard — it dropped ERROR and
            // WARN bubble_level records too (the exact records the
            // this level-guard was added to preserve).
            let is_filtered_bubble = record.level() >= log::Level::Info
                && msg.starts_with("[WS-READER] bubble_level event");
            if !is_filtered_bubble {
                let _ = writer.write_line(&file_line);
                // Flush the BufWriter immediately for Warn+ records so
                // an impending crash (the most likely producer of
                // `log::error!`) does NOT leave the diagnostic line
                // stranded in the 8 KB in-memory buffer when the
                // process aborts. Info/Debug/Trace records stay
                // buffered — they're high-volume and the periodic /
                // drop-flush paths are sufficient. Mirrors the
                // Python side's `logging.Handler.flush()` call inside
                // `emit()` for WARNING+ records (log.py:216-219).
                //
                // The `log` crate orders levels by severity:
                // Error(1) < Warn(2) < Info(3) < Debug(4) < Trace(5).
                // `<= Warn` matches Error + Warn.
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
