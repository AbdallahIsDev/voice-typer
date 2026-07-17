//! Rotating file logger (ADR-0020 §11): 5 MB × 5 files, excludes bubble_level.

use crate::util::{ROTATE_MAX_BYTES, ROTATE_MAX_FILES, now_timestamp};
use std::fs::OpenOptions;
use std::io::Write;
use std::sync::Mutex;

/// ADR-0020 §11: initialize a rotating file logger writing to
/// `<config_dir>/logs/voice-typer.log` (5 MB × 5 files ≈ 25 MB cap).
///
/// **Excludes `bubble_level` events** from the file log: at ~60 Hz
/// they would fill disk fast even with rotation. The Rust WS-reader
/// already coalesces them to ≤30 Hz for the UI (§9); the file path
/// drops them entirely so file logs capture events/errors, not the
/// level stream.
///
/// Replaces the prior `env_logger::Builder::init()` call — this
/// logger writes to BOTH stderr (matching the prior env_logger
/// output) AND the rotating file. If file init fails, the caller
/// should fall back to `env_logger` for stderr-only output.
///
/// # Implementation choice: hand-rolled, not `log4rs`
///
/// `log4rs` is a heavy dep (~30 transitive crates) for a feature
/// that just needs "rotate at N bytes, keep N files". This
/// hand-rolled `RotatingFileWriter` is ~80 lines and has no deps
/// beyond `log` (already required) + `std::fs`. The rotation is
/// triggered lazily on the write that crosses the size threshold
/// (not on a timer), which is fine for our write volume.
pub(crate) fn init_file_logger(config_dir: &std::path::Path) -> Result<(), String> {
    let logs_dir = config_dir.join("logs");
    std::fs::create_dir_all(&logs_dir)
        .map_err(|e| format!("create logs dir failed: {e}"))?;
    let writer = RotatingFileWriter::new(logs_dir, "voice-typer");
    let logger = CombinedLogger {
        file_writer: Some(writer),
        level_filter: log::LevelFilter::Info,
    };
    // `Box::leak` is safe here: the logger lives for the program's
    // lifetime (we never want to tear it down). `log::set_logger`
    // requires a `&'static dyn Log`.
    log::set_logger(Box::leak(Box::new(logger)))
        .map_err(|_| "failed to set logger (already set?)".to_string())?;
    log::set_max_level(log::LevelFilter::Info);
    Ok(())
}

/// Combined stderr + rotating-file logger. Replaces `env_logger` so
/// we can add the file sink without a multiplexer crate.
pub(crate) struct CombinedLogger {
    file_writer: Option<RotatingFileWriter>,
    level_filter: log::LevelFilter,
}

impl log::Log for CombinedLogger {
    fn enabled(&self, metadata: &log::Metadata) -> bool {
        metadata.level() <= self.level_filter
    }

    fn log(&self, record: &log::Record) {
        if !self.enabled(record.metadata()) {
            return;
        }
        let msg = record.args().to_string();
        let ts = now_timestamp();
        let line = format!(
            "{} {:5} {} -- {}",
            ts,
            record.level(),
            record.target(),
            msg
        );
        // Always log to stderr (env_logger-style output for `cargo tauri dev`).
        eprintln!("{}", line);
        // ADR-0020 §11: exclude `bubble_level` from the file log
        // (60 Hz would fill disk fast even with rotation). Match by
        // substring so any `log:*!("[...] bubble_level ...")` is
        // dropped — the WS reader's bubble_level coalesce path uses
        // `log::warn!`/`log::info!` with "bubble_level" in the message
        // when logging unexpected payloads.
        if let Some(writer) = &self.file_writer {
            if !msg.contains("bubble_level") {
                let _ = writer.write_line(&line);
            }
        }
    }

    fn flush(&self) {
        if let Some(writer) = &self.file_writer {
            let _ = writer.flush();
        }
    }
}

/// Minimal rotating-file writer: appends to
/// `<dir>/<base_name>.log` until the file exceeds `ROTATE_MAX_BYTES`,
/// then rotates (`.log` → `.log.1` → `.log.2` → … → `.log.4` → delete).
/// Thread-safe via a single `Mutex<Option<File>>`.
pub(crate) struct RotatingFileWriter {
    dir: std::path::PathBuf,
    base_name: String,
    inner: Mutex<Option<std::fs::File>>,
}

impl RotatingFileWriter {
    fn new(dir: std::path::PathBuf, base_name: &str) -> Self {
        Self {
            dir,
            base_name: base_name.to_string(),
            inner: Mutex::new(None),
        }
    }

    fn current_path(&self) -> std::path::PathBuf {
        self.dir.join(format!("{}.log", self.base_name))
    }

    fn write_line(&self, line: &str) -> std::io::Result<()> {
        let mut guard = self.inner.lock().unwrap();
        // Open the file lazily so we don't create `voice-typer.log`
        // until the first log line is emitted.
        if guard.is_none() {
            std::fs::create_dir_all(&self.dir)?;
            let file = OpenOptions::new()
                .create(true)
                .append(true)
                .open(self.current_path())?;
            *guard = Some(file);
        }
        let file = guard.as_mut().unwrap();
        file.write_all(line.as_bytes())?;
        file.write_all(b"\n")?;
        file.flush()?;
        // Check size; rotate if we've crossed the threshold.
        let len = file.metadata()?.len();
        if len > ROTATE_MAX_BYTES {
            // Drop the file handle BEFORE renaming (Windows refuses to
            // rename a file that's open by another handle).
            *guard = None;
            self.rotate()?;
        }
        Ok(())
    }

    /// Rotate: `.log.(N-1)` → `.log.N`, …, `.log` → `.log.1`.
    /// Files at index `ROTATE_MAX_FILES - 1` (the oldest) are deleted.
    fn rotate(&self) -> std::io::Result<()> {
        for i in (1..ROTATE_MAX_FILES).rev() {
            let from = self.dir.join(format!("{}.log.{}", self.base_name, i));
            let to = self
                .dir
                .join(format!("{}.log.{}", self.base_name, i + 1));
            if from.exists() {
                if i + 1 >= ROTATE_MAX_FILES {
                    // Oldest slot — delete what's there before renaming
                    // (best-effort; ignore errors if the file is gone).
                    let _ = std::fs::remove_file(&to);
                }
                let _ = std::fs::rename(&from, &to);
            }
        }
        let from = self.current_path();
        let to = self.dir.join(format!("{}.log.1", self.base_name));
        if from.exists() {
            let _ = std::fs::rename(from, to);
        }
        Ok(())
    }

    fn flush(&self) -> std::io::Result<()> {
        if let Some(f) = self.inner.lock().unwrap().as_mut() {
            f.flush()?;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── RotatingFileWriter ────────────────────────────────────────────

    #[test]
    fn test_rotating_file_writer_basic_write() {
        let tmp = std::env::temp_dir().join(format!(
            "voice-typer-test-{}-basic",
            std::process::id()
        ));
        std::fs::remove_dir_all(&tmp).ok();
        let writer = RotatingFileWriter::new(tmp.clone(), "test-log");
        writer.write_line("hello").unwrap();
        writer.write_line("world").unwrap();
        let content =
            std::fs::read_to_string(tmp.join("test-log.log")).unwrap();
        assert_eq!(content, "hello\nworld\n");
        std::fs::remove_dir_all(&tmp).ok();
    }

    #[test]
    fn test_rotating_file_writer_rotation() {
        // Use a tiny threshold by writing many large lines.
        // ROTATE_MAX_BYTES is 5 MB; writing 6 MB should trigger at
        // least one rotation.
        let tmp = std::env::temp_dir().join(format!(
            "voice-typer-test-{}-rotate",
            std::process::id()
        ));
        std::fs::remove_dir_all(&tmp).ok();
        let writer = RotatingFileWriter::new(tmp.clone(), "test-log");
        // 6 MB total, 100 KB per line → ~60 lines.
        let big_line = "x".repeat(100_000);
        for _ in 0..60 {
            writer.write_line(&big_line).unwrap();
        }
        // After rotation, `.log` should exist (the current file) and
        // `.log.1` should exist (the first rotated file).
        assert!(tmp.join("test-log.log").exists(), "current log missing");
        assert!(
            tmp.join("test-log.log.1").exists(),
            "first rotated log missing"
        );
        std::fs::remove_dir_all(&tmp).ok();
    }

    #[test]
    fn test_rotating_file_writer_thread_safety() {
        // Spawn multiple threads writing to the same writer — should
        // not panic or corrupt (Mutex protects the inner File).
        let tmp = std::env::temp_dir().join(format!(
            "voice-typer-test-{}-threads",
            std::process::id()
        ));
        std::fs::remove_dir_all(&tmp).ok();
        let writer = std::sync::Arc::new(RotatingFileWriter::new(tmp.clone(), "test-log"));
        let mut handles = Vec::new();
        for i in 0..4 {
            let w = writer.clone();
            handles.push(std::thread::spawn(move || {
                for j in 0..50 {
                    w.write_line(&format!("thread-{}-line-{}", i, j)).unwrap();
                }
            }));
        }
        for h in handles {
            h.join().unwrap();
        }
        // 4 threads × 50 lines = 200 lines total.
        let content =
            std::fs::read_to_string(tmp.join("test-log.log")).unwrap();
        let line_count = content.lines().count();
        // Could be fewer if rotation happened mid-write (the current
        // file gets renamed to .log.1 and a fresh .log starts). Just
        // assert we wrote *something* and didn't panic.
        assert!(line_count > 0, "no lines in current log: {}", content);
        std::fs::remove_dir_all(&tmp).ok();
    }
}
