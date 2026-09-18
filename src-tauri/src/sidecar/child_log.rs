//! Durable tee for the sidecar child's raw stdout + stderr →
//! `<config_dir>/logs/sidecar.log` (ADR-0020 §11).
//!
//! # Why this exists
//!
//! The slim-core sidecar's own rotating log
//! (`<config_dir>/logs/voice-typer.log`) only starts receiving records
//! once Python's logging is configured, i.e. several seconds into the
//! process. Everything the child writes BEFORE that point (interpreter
//! startup warnings, `import torch` failures, a traceback from a broken
//! dependency, the frozen onefile bootloader's own errors) goes to raw
//! stderr and would otherwise be visible only on a terminal. In a
//! release install there is no terminal: the host pipes the child's
//! streams and, before this module, dropped every line at DEBUG (the
//! default level), so "the sidecar died on launch" had zero durable
//! evidence.
//!
//! predecessor kept this window observable by inheriting stdio
//! (`stdio: "inherit"`). The Tauri host clears the child's env and
//! pipes the streams, so the equivalent guarantee must be an explicit
//! file tee, which is exactly what ADR-0020 §11 specifies and what the
//! Windows/Linux validation runbooks already expect to find at
//! `<config>/logs/sidecar.log`.
//!
//! # Contract
//!
//! - **File**: `<config_dir>/logs/sidecar.log`, single file, rotated
//!   IN PLACE at [`crate::util::LOG_MAX_BYTES`] (40 MB) — the same
//!   single-file policy as every other Voice Typer log, and inside the
//!   directory both startup sweeps (Python `_sweep_stale_logs`, Rust
//!   [`crate::platform::logging::sweep_stale_logs`]) already cover for
//!   age (7 days) + size-fallback (25 MB) cleanup.
//! - **Shape**: `YYYY-MM-DD  HH:MM:SS  STDERR  <line>`. The timestamp
//!   uses the shared canonical formatter
//!   ([`crate::util::now_timestamps`]: local wall clock, two-space
//!   separator, seconds precision), and the label column carries the
//!   STREAM rather than a log level: these are the child's raw writes,
//!   which have no severity, and claiming a level would misrepresent a
//!   traceback as a warning. Once the child's own logger is up its
//!   records (with real levels) land in `voice-typer.log`; this file is
//!   the raw capture that covers the gap before that.
//! - **Redaction**: every line passes through
//!   [`redact_pii`](crate::platform::logging::redact_pii) before it is
//!   written (ADR-0020 §403): a child that echoes an env var or a URL
//!   credential into a traceback must not persist it.
//! - **Bounded**: one line is capped at
//!   [`MAX_TEE_LINE_BYTES`] (8 KiB) with a visible marker, and the
//!   writer's own bounded queue drops non-error lines under
//!   saturation. A chatty child can therefore never turn this tee into
//!   an unbounded memory or disk consumer.
//! - **Non-blocking for the child**: the tee reuses
//!   [`RotatingFileWriter`], whose file I/O happens on a dedicated
//!   writer thread behind a bounded queue. The drain task that feeds it
//!   must never park — parking it would re-open the exact
//!   backpressure window the drain exists to close — so a saturated
//!   queue drops tee lines instead of blocking.
//!
//! The DEV-mode child is intentionally out of scope: it inherits stderr
//! from the terminal (`spawn_sidecar_dev_mode`), so its early output is
//! already visible without a file.

use crate::platform::logging::redact_pii;
use crate::platform::logging::RotatingFileWriter;
use crate::util::now_timestamps;
use std::sync::OnceLock;

/// Which of the child's two streams a tee'd line came from.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum ChildStream {
    Stdout,
    Stderr,
}

impl ChildStream {
    /// Column label printed in place of a log level (see the module doc:
    /// raw child writes carry no severity).
    fn label(self) -> &'static str {
        match self {
            Self::Stdout => "STDOUT",
            Self::Stderr => "STDERR",
        }
    }

    /// Severity handed to the writer's queue gate: the writer only
    /// exempts ERROR records from the saturation drop, and a raw stderr
    /// line is diagnostic rather than fatal (the child's own logger,
    /// which does carry real levels, writes `voice-typer.log`), so both
    /// streams participate in the gate.
    fn level(self) -> log::Level {
        match self {
            Self::Stdout => log::Level::Info,
            Self::Stderr => log::Level::Warn,
        }
    }
}

/// Per-line cap for a tee'd child line. A single pathological line (a
/// giant device dump, a minified JS blob echoed by a native library)
/// must not consume the whole 40 MB budget.
const MAX_TEE_LINE_BYTES: usize = 8 * 1024;

/// Marker appended to a line that hit [`MAX_TEE_LINE_BYTES`].
const TRUNCATION_MARKER: &str = " ...[truncated]";

/// Basename of the tee file (`<config_dir>/logs/sidecar.log`, the path
/// the validation runbooks reference).
const TEE_BASE_NAME: &str = "sidecar";

/// Process-wide tee writer, created on first use.
static TEE_WRITER: OnceLock<RotatingFileWriter> = OnceLock::new();

/// Whether the given spawn-path log tag identifies a child whose output
/// should be teed. Only the RELEASE sidecar qualifies: the worker keeps
/// its own rotating log, and the dev-sidecar child inherits stderr to
/// the terminal (see the module doc).
pub(crate) fn should_tee(log_tag: &str) -> bool {
    log_tag == "[SIDECAR]"
}

/// The shared tee writer. Created lazily (and only when a release
/// sidecar actually spawns), writing to
/// `<config_dir>/logs/sidecar.log`.
fn tee_writer() -> &'static RotatingFileWriter {
    TEE_WRITER.get_or_init(|| {
        let logs_dir = crate::platform::paths::config_dir().join("logs");
        // Best-effort: `RotatingFileWriter` creates the directory again
        // on first write, and the host's file logger has normally
        // created it moments earlier.
        let _ = std::fs::create_dir_all(&logs_dir);
        RotatingFileWriter::new(logs_dir, TEE_BASE_NAME)
    })
}

/// Tee one raw chunk of child output (may contain several lines).
pub(crate) fn tee_child_output(stream: ChildStream, bytes: &[u8]) {
    let text = String::from_utf8_lossy(bytes);
    let writer = tee_writer();
    for line in text.lines() {
        if line.trim().is_empty() {
            continue;
        }
        tee_line_to(writer, stream, line);
    }
}

/// Write ONE line to `writer` in the canonical tee format. Split out
/// (and `pub(crate)`) so the shape contract is unit-testable against a
/// temp-dir writer without touching the process-global config dir.
pub(crate) fn tee_line_to(writer: &RotatingFileWriter, stream: ChildStream, raw_line: &str) {
    let (file_ts, _) = now_timestamps();
    let line = format_tee_line(&file_ts, stream, raw_line);
    let _ = writer.write_line_level(&line, stream.level());
    // stderr is where early-startup crashes land: flush it immediately
    // so a traceback is on disk even if the host dies right after. The
    // flush barrier is coalescing + 2s-bounded (see `RotatingFileWriter`),
    // so this cannot park the drain task indefinitely.
    if stream == ChildStream::Stderr {
        let _ = writer.flush();
    }
}

/// Render one canonical tee line: `ts  STREAM  <redacted, capped line>`.
fn format_tee_line(ts: &str, stream: ChildStream, raw_line: &str) -> String {
    let redacted = redact_pii(raw_line.trim_end_matches(['\r', '\n']));
    format!("{}  {:6} {}", ts, stream.label(), cap_line(&redacted))
}

/// Truncate `line` to at most [`MAX_TEE_LINE_BYTES`] bytes, floored to a
/// UTF-8 char boundary, with a visible marker when it actually cut.
fn cap_line(line: &str) -> String {
    if line.len() <= MAX_TEE_LINE_BYTES {
        return line.to_string();
    }
    let mut cut = MAX_TEE_LINE_BYTES - TRUNCATION_MARKER.len();
    while cut > 0 && !line.is_char_boundary(cut) {
        cut -= 1;
    }
    format!("{}{}", &line[..cut], TRUNCATION_MARKER)
}

// Sibling test module: tests live in `child_log_tests.rs` (per C-TEST-5:
// no inline `#[cfg(test)] mod tests` blocks in production source).
#[cfg(test)]
#[path = "child_log_tests.rs"]
mod child_log_tests;
