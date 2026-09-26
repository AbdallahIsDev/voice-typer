//! Unit tests for the sidecar child-output log tee
//! ([`crate::sidecar::child_log`]): the canonical line shape, the
//! per-line cap, the stream→label mapping, the tee eligibility gate,
//! and a real end-to-end write through a temp-dir
//! [`RotatingFileWriter`](crate::platform::logging::RotatingFileWriter)
//! (proving the tee actually lands `<dir>/sidecar.log`, with the
//! redaction pass applied).

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::unreachable
)]

use super::{cap_line, format_tee_line, should_tee, tee_line_to, ChildStream, MAX_TEE_LINE_BYTES};
use crate::platform::logging::RotatingFileWriter;

/// The rendered line carries the canonical timestamp, a stream label
/// (NOT a log level), and the message.
#[test]
fn test_format_tee_line_shape() {
    let line = format_tee_line(
        "2026-09-16  12:34:56",
        ChildStream::Stderr,
        "Traceback (most recent call last):",
    );
    assert_eq!(
        line,
        "2026-09-16  12:34:56  STDERR Traceback (most recent call last):"
    );
    let stdout = format_tee_line(
        "2026-09-16  12:34:56",
        ChildStream::Stdout,
        "server_started",
    );
    assert_eq!(stdout, "2026-09-16  12:34:56  STDOUT server_started");
    // Both labels are the same width, so the message column aligns
    // internally (the file is a raw capture, not the canonical level
    // column).
    assert_eq!(
        ChildStream::Stdout.label().len(),
        ChildStream::Stderr.label().len()
    );
}

/// Trailing CR/LF from a raw pipe chunk never leaks into the line (the
/// file is line-terminated by the writer itself).
#[test]
fn test_format_tee_line_strips_trailing_newline() {
    assert!(format_tee_line("T", ChildStream::Stderr, "boom\r\n").ends_with("STDERR boom"));
}

/// Redaction runs before the line is written (ADR-0020 §403).
#[test]
fn test_format_tee_line_redacts_secrets() {
    let line = format_tee_line(
        "T",
        ChildStream::Stderr,
        "authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz012345",
    );
    assert!(
        !line.contains("sk-abcdefghijklmnopqrstuvwxyz012345"),
        "line: {line}"
    );
    // The shared redactor (`platform::logging::redact::redact_pii`) masks
    // the secret in place with `***`: assert the MASK is present so the
    // line is visibly redacted rather than silently truncated.
    assert!(line.contains("***"), "line: {line}");
}

/// Over-cap lines are truncated (boundary-safe) with a visible marker;
/// at/below the cap they pass through byte-exact.
#[test]
fn test_cap_line() {
    let small = "a".repeat(MAX_TEE_LINE_BYTES);
    assert_eq!(cap_line(&small), small);
    let huge = "a".repeat(MAX_TEE_LINE_BYTES * 3);
    let capped = cap_line(&huge);
    assert!(capped.ends_with("...[truncated]"));
    assert!(capped.len() <= MAX_TEE_LINE_BYTES);
    // Multi-byte char straddling the cut never panics and stays valid.
    let multibyte = format!("{}éé", "a".repeat(MAX_TEE_LINE_BYTES));
    let capped_mb = cap_line(&multibyte);
    assert!(capped_mb.ends_with("...[truncated]"));
    assert!(capped_mb.len() <= MAX_TEE_LINE_BYTES);
}

/// Only the release sidecar's tag is teed: the worker keeps its own log
/// and the dev child inherits stderr to the terminal.
#[test]
fn test_should_tee_only_release_sidecar_tag() {
    assert!(should_tee("[SIDECAR]"));
    assert!(!should_tee("[WORKER]"));
    assert!(!should_tee("[SIDECAR-DEV]"));
    assert!(!should_tee("[WORKER-DEV]"));
    assert!(!should_tee(""));
}

/// End-to-end: a tee'd line lands in `<dir>/sidecar.log` in the canonical
/// shape (temp-dir writer, so the process-global config dir is untouched).
#[test]
fn test_tee_line_to_writes_sidecar_log() {
    let tmp = std::env::temp_dir().join(format!(
        "lausu-child-log-test-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0)
    ));
    std::fs::create_dir_all(&tmp).unwrap();
    let writer = RotatingFileWriter::new(tmp.clone(), "sidecar");
    tee_line_to(
        &writer,
        ChildStream::Stderr,
        "ImportError: libtorch missing",
    );
    writer.flush().unwrap();
    let content = std::fs::read_to_string(tmp.join("sidecar.log")).unwrap();
    let line = content.lines().next().unwrap();
    assert!(
        line.contains("  STDERR ImportError: libtorch missing"),
        "unexpected tee line: {line}"
    );
    // Canonical timestamp column: `YYYY-MM-DD  HH:MM:SS` (20 chars) then
    // two spaces.
    assert_eq!(&line[10..12], "  ", "two-space date/time separator: {line}");
    assert!(line.len() >= 20, "line too short: {line}");
    std::fs::remove_dir_all(&tmp).ok();
}
