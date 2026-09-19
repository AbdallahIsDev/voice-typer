
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

    fn level(self) -> log::Level {
        match self {
            Self::Stdout => log::Level::Info,
            Self::Stderr => log::Level::Warn,
        }
    }
}

const MAX_TEE_LINE_BYTES: usize = 8 * 1024;

/// Marker appended to a line that hit [`MAX_TEE_LINE_BYTES`].
const TRUNCATION_MARKER: &str = " ...[truncated]";

/// Basename of the tee file (`<config_dir>/logs/sidecar.log`, the path
/// the validation runbooks reference).
const TEE_BASE_NAME: &str = "sidecar";

/// Process-wide tee writer, created on first use.
static TEE_WRITER: OnceLock<RotatingFileWriter> = OnceLock::new();

pub(crate) fn should_tee(log_tag: &str) -> bool {
    log_tag == "[SIDECAR]"
}

fn tee_writer() -> &'static RotatingFileWriter {
    TEE_WRITER.get_or_init(|| {
        let logs_dir = crate::platform::paths::config_dir().join("logs");
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

pub(crate) fn tee_line_to(writer: &RotatingFileWriter, stream: ChildStream, raw_line: &str) {
    let (file_ts, _) = now_timestamps();
    let line = format_tee_line(&file_ts, stream, raw_line);
    let _ = writer.write_line_level(&line, stream.level());
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
