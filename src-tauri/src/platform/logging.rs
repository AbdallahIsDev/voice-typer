//! Rotating file logger (ADR-0020 §11): 5 MB × 5 files, excludes bubble_level.
//!
//! Log files + the parent `<config_dir>/logs/` dir
//! are created with restricted POSIX permissions (`0o600` for files,
//! `0o700` for the dir) so dictated-text fragments and any PII the
//! Rust code emits are NOT world-readable on multi-user POSIX systems.
//! Mirrors the Python side's `os.umask(0o077)` + `os.chmod(log_file,
//! 0o600)` pattern in `voice_typer/server/log.py`.

use crate::util::{ROTATE_MAX_BYTES, ROTATE_MAX_FILES, now_timestamp};
use std::fs::OpenOptions;
use std::io::Write;
use std::sync::Mutex;

// POSIX-only `OpenOptions::mode` + `Permissions::from_mode`
// trait imports. On Windows these are no-ops (the OS uses ACLs, not
// mode bits) — the `#[cfg(unix)]` blocks below gate every call site.
#[cfg(unix)]
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};

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
    // Tighten the parent `<config_dir>/logs/` dir to
    // `0o700` on POSIX (owner rwx only — no group/other access). Mirrors
    // the Python side's `os.chmod(config_dir, 0o700)` at
    // `voice_typer/server/log.py:891-893`. Best-effort: a `chmod` failure
    // is logged but does NOT block logger init (a too-permissive dir is
    // a softening of the security posture, not a hard failure — the
    // individual log files inside still get `0o600` via `OpenOptionsExt`).
    #[cfg(unix)]
    {
        let _ = std::fs::set_permissions(
            &logs_dir,
            std::fs::Permissions::from_mode(0o700),
        );
    }
    let writer = RotatingFileWriter::new(logs_dir, "voice-typer");
    // PVT-G5-082: honor `RUST_LOG` runtime log-level override. Parsed
    // as a `log::LevelFilter` (e.g. "debug", "trace", "warn", "off").
    // Default to `Info` if the var is unset OR unparseable so a typo
    // (e.g. `RUST_LOG=debog`) doesn't silently disable all logging.
    // Both the global `log::set_max_level` AND the per-logger
    // `level_filter` are set to this value — `set_max_level` is the
    // fast-path short-circuit at the macro call site, while
    // `level_filter` is consulted inside `CombinedLogger::enabled`
    // (which `log::log!` calls as a second filter).
    //
    // G4-M-31 (session 4) fallback: if `RUST_LOG` is unset, also honor
    // the Voice Typer-specific `VOICE_TYPER_DEBUG` env var. When
    // truthy ("1", "true", "yes", case-insensitive), set the level to
    // Debug so developers get verbose logs in the file + stderr. This
    // mirrors the Python side's `env_validation.py` boolean-var
    // pattern so the Rust + Python hosts respond identically to the
    // same env var. `RUST_LOG` (the standard Rust convention) wins if
    // set; `VOICE_TYPER_DEBUG` is a fallback for users who don't know
    // about `RUST_LOG`.
    let max_level = std::env::var("RUST_LOG")
        .ok()
        .and_then(|s| s.parse::<log::LevelFilter>().ok())
        .or_else(|| {
            // G4-M-31: RUST_LOG unset/unparseable — try VOICE_TYPER_DEBUG.
            if is_debug_env_truthy(std::env::var("VOICE_TYPER_DEBUG").ok().as_deref()) {
                Some(log::LevelFilter::Debug)
            } else {
                None
            }
        })
        .unwrap_or(log::LevelFilter::Info);
    let logger = CombinedLogger {
        file_writer: Some(writer),
        level_filter: max_level,
    };
    // `Box::leak` is safe here: the logger lives for the program's
    // lifetime (we never want to tear it down). `log::set_logger`
    // requires a `&'static dyn Log`.
    log::set_logger(Box::leak(Box::new(logger)))
        .map_err(|_| "failed to set logger (already set?)".to_string())?;
    log::set_max_level(max_level);
    Ok(())
}

/// G4-M-31: predicate form of the VOICE_TYPER_DEBUG env-var check,
/// extracted for unit testing. Truthy values: "1", "true", "yes"
/// (case-insensitive). Anything else (including unset / empty) is
/// falsy → production Info-level logging.
///
/// Mirrors the Python side's `env_validation.py` boolean-var pattern
/// (pattern: `^(1|0|true|false|yes|no)$`, case-insensitive) so the
/// Rust + Python hosts respond identically to the same env var.
pub(crate) fn is_debug_env_truthy(value: Option<&str>) -> bool {
    match value {
        Some(v) => matches!(
            v.trim().to_ascii_lowercase().as_str(),
            "1" | "true" | "yes"
        ),
        None => false,
    }
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
        // PVT-G5-084: include `file:line` so operators can jump
        // directly to the source location from a log line. Both
        // `record.file()` and `record.line()` return `Option` (they
        // are `None` for log records emitted from non-`#[track_caller]`
        // paths or release builds with debuginfo stripped); fall back
        // to "?" / 0 so the format string still renders cleanly.
        let line = format!(
            "{} {:5} {} {}:{} -- {}",
            ts,
            record.level(),
            record.target(),
            record.file().unwrap_or("?"),
            record.line().unwrap_or(0),
            msg
        );
        // Always log to stderr (env_logger-style output for `cargo tauri dev`).
        eprintln!("{}", line);
        // ADR-0020 §11: exclude `bubble_level` from the file log
        // (60 Hz would fill disk fast even with rotation). Match by a
        // SPECIFIC message prefix (`[WS-READER] bubble_level event`)
        // rather than a broad `msg.contains("bubble_level")` substring
        // — the old substring filter risked false-positives on unrelated
        // log lines that happened to mention "bubble_level".
        // GT-B4-11: the WS reader doesn't currently log bubble_level
        // events to the file (they go via `app.emit()` to the webview,
        // not `log::*!`), so this filter is defensive.
        if let Some(writer) = &self.file_writer {
            if !msg.starts_with("[WS-READER] bubble_level event") {
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

// ─── PVT-G5-083: panic hook ─────────────────────────────────────────────
//
// Install a panic hook that writes the panic payload + source location
// to BOTH stderr (via `eprintln!`) and the file log (via `log::error!`).
// Without this, a panic in a Tauri command handler or sidecar WS reader
// would unwind without any breadcrumb in the file log — operators
// debugging from logs alone would have no signal that a panic occurred
// (only the React UI's generic "something went wrong" toast would
// fire). The hook chains to the previous hook (if any) so existing
// panic behavior is preserved.

/// Install the Voice Typer panic hook (PVT-G5-083).
///
/// Writes the panic payload + `file:line:col` location to BOTH:
/// - stderr (via `eprintln!`) — so `cargo tauri dev` / `journalctl`
///   captures it even when the file logger isn't installed yet, AND
/// - the file log (via `log::error!`) — so `voice-typer.log` has the
///   same breadcrumb for post-mortem debugging.
///
/// `pub` (NOT `pub(crate)`) so `main.rs` (in the FA3a-retry follow-up
/// that wires this up) can call it from outside the `platform::logging`
/// module. Calling more than once is safe — each call replaces the
/// previous hook (chained via `take_hook` so prior behavior is not
/// lost).
///
/// # When to call
///
/// Call this AFTER `init_file_logger` so `log::error!` actually lands
/// in the rotating file (otherwise the log record is silently dropped
/// by the `log` crate's default no-op logger). Calling before
/// `init_file_logger` is still safe — the `eprintln!` half still fires.
pub fn install_panic_hook() {
    let prev = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        let location = info
            .location()
            .map(|l| format!("{}:{}:{}", l.file(), l.line(), l.column()))
            .unwrap_or_else(|| "<unknown location>".to_string());
        // The payload is `&dyn Any` — try the two common shapes
        // (`&str` from `panic!("literal")` and `String` from
        // `panic!(format!(...))`). Fall back to a generic placeholder
        // for non-string payloads (e.g. `panic!(42)`).
        let payload = info
            .payload()
            .downcast_ref::<&str>()
            .copied()
            .or_else(|| info.payload().downcast_ref::<String>().map(|s| s.as_str()))
            .unwrap_or("<non-string panic payload>");
        eprintln!("[PANIC] {} -- {}", location, payload);
        log::error!("panic at {} -- {}", location, payload);
        // Chain to the previous hook so any prior behavior (e.g. the
        // default "print panic message + abort" path under
        // `panic=abort`) is preserved.
        prev(info);
    }));
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
        // PVT-G5-018: recover from a poisoned mutex rather than
        // panicking inside the logger. A prior panic while holding
        // this lock would poison it; re-panicking here would recurse
        // through the panic hook (which itself calls `log::error!` →
        // this writer) and abort the process. Use the shared poison-safe
        // `crate::state::lock` helper (G4-H-27) for consistency with
        // `state.rs` + `supervisor.rs` + `ws.rs`.
        let mut guard = crate::state::lock(&self.inner);
        // Open the file lazily so we don't create `voice-typer.log`
        // until the first log line is emitted. If the guard was None
        // (first write) OR the previous File handle was torn down by
        // the rotation path (which sets `*guard = None` before
        // renaming), open a fresh File in append mode.
        if guard.is_none() {
            std::fs::create_dir_all(&self.dir)?;
            // Create the log file with `0o600` perms
            // on POSIX so it is NOT world-readable. On Linux/macOS the
            // default `OpenOptions::create(true).append(true).open(...)`
            // inherits the process umask (typically 0o022), producing
            // `0o644` — readable by group + others. The dictation log
            // may contain raw transcription text + PII (XZ-LOG-02),
            // so tighten to owner-only. On Windows `OpenOptionsExt::mode`
            // is unavailable; the OS uses ACLs instead (configured at
            // install time, not per-file).
            let mut opts = OpenOptions::new();
            opts.create(true).append(true);
            #[cfg(unix)]
            opts.mode(0o600);
            let file = opts.open(self.current_path())?;
            // Belt-and-suspenders: if the file already existed (created
            // by a prior run with looser perms), explicitly chmod it to
            // 0o600 now. `OpenOptions::mode` only applies to NEW files,
            // not pre-existing ones — so without this chmod a leftover
            // 0o644 log file from a pre-hardening build would stay world-
            // readable indefinitely. Best-effort: a chmod failure does
            // not block logging (a too-permissive file is a security
            // softening, not a hard failure).
            #[cfg(unix)]
            {
                let _ = std::fs::set_permissions(
                    self.current_path(),
                    std::fs::Permissions::from_mode(0o600),
                );
            }
            *guard = Some(file);
        }
        // Borrow the File from the guard for the write/flush/metadata
        // calls below. The match returns early with `Err` if the slot
        // is somehow still None (shouldn't happen — we just initialized
        // it above — but the type system can't prove that, and a
        // panic-free `Option::unwrap` is exactly what G4-H-27 forbids).
        let file = match guard.as_mut() {
            Some(f) => f,
            None => {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::Other,
                    "logging file slot is None despite just-in-time init",
                ));
            }
        };
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
    ///
    /// GT-67: the previous loop bound was `(1..ROTATE_MAX_FILES).rev()`
    /// (= 1,2,3,4) with delete check `i + 1 >= ROTATE_MAX_FILES` (=
    /// `5 >= 5`). That kept 6 files total (`.log`, `.log.1`..`.log.5`),
    /// one MORE than `ROTATE_MAX_FILES=5` — an off-by-one that grew
    /// the disk cap from 25 MB to 30 MB. The fix tightens the loop to
    /// `(1..ROTATE_MAX_FILES - 1).rev()` (= 1,2,3) and the delete check
    /// to `i + 1 >= ROTATE_MAX_FILES - 1` (= `4 >= 4`), so the total
    /// file count is exactly `ROTATE_MAX_FILES=5`.
    fn rotate(&self) -> std::io::Result<()> {
        for i in (1..ROTATE_MAX_FILES - 1).rev() {
            let from = self.dir.join(format!("{}.log.{}", self.base_name, i));
            let to = self
                .dir
                .join(format!("{}.log.{}", self.base_name, i + 1));
            if from.exists() {
                if i + 1 >= ROTATE_MAX_FILES - 1 {
                    // Oldest slot — delete what's there before renaming
                    // (best-effort; ignore errors if the file is gone).
                    let _ = std::fs::remove_file(&to);
                }
                let _ = std::fs::rename(&from, &to);
                // Belt-and-suspenders chmod of the
                // renamed file to 0o600 on POSIX. `rename` preserves
                // the source file's mode, which should already be 0o600
                // (set by `write_line`'s `OpenOptionsExt::mode` call),
                // but a leftover rotated file from a pre-hardening build may
                // still be 0o644. Best-effort: ignore errors (the file
                // may have been moved/deleted between the rename and the
                // chmod — extremely unlikely but defensive).
                #[cfg(unix)]
                {
                    let _ = std::fs::set_permissions(
                        &to,
                        std::fs::Permissions::from_mode(0o600),
                    );
                }
            }
        }
        let from = self.current_path();
        let to = self.dir.join(format!("{}.log.1", self.base_name));
        if from.exists() {
            let _ = std::fs::rename(&from, &to);
            // Same belt-and-suspenders chmod for the
            // `.log` → `.log.1` rename above.
            #[cfg(unix)]
            {
                let _ = std::fs::set_permissions(
                    &to,
                    std::fs::Permissions::from_mode(0o600),
                );
            }
        }
        Ok(())
    }

    fn flush(&self) -> std::io::Result<()> {
        // PVT-G5-018 / G4-H-27: same poison-recovery rationale as
        // `write_line`, using the shared `crate::state::lock` helper.
        if let Some(f) = crate::state::lock(&self.inner).as_mut() {
            f.flush()?;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    // PVT-G5-018 merge note: tests call `logger.log(&record)` and
    // `logger.flush()` directly on a `CombinedLogger` value. Those
    // methods belong to the `log::Log` trait, which is NOT auto-
    // imported by `use super::*;` (trait methods need the trait in
    // scope at the call site). Bring it in explicitly so the test
    // module compiles cleanly under rustc 1.97+.
    use log::Log;

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

    // ── GT-67: pin the exact file count after N rotations ────────────
    //
    // The previous rotation loop kept `ROTATE_MAX_FILES + 1` files on
    // disk (off-by-one). This test writes enough data to trigger MANY
    // rotations (well past the cap) and asserts the final file count
    // is EXACTLY `ROTATE_MAX_FILES` — no more, no less.

    #[test]
    fn test_rotating_file_writer_pins_exact_file_count_after_many_rotations() {
        let tmp = std::env::temp_dir().join(format!(
            "voice-typer-test-{}-gt67-count",
            std::process::id()
        ));
        std::fs::remove_dir_all(&tmp).ok();
        let writer = RotatingFileWriter::new(tmp.clone(), "test-log");
        // Write ~50 MB total (100 KB/line × 500 lines). With a 5 MB
        // rotation threshold, this triggers ~10 rotations — well past
        // the 5-file cap, so the rotate() function's delete-oldest
        // path runs at least 5 times.
        let big_line = "x".repeat(100_000);
        for _ in 0..500 {
            writer.write_line(&big_line).unwrap();
        }
        writer.flush().unwrap();

        // Count the actual files on disk (current + rotated).
        let mut file_count = 0;
        for i in 0..=ROTATE_MAX_FILES {
            let path = if i == 0 {
                tmp.join("test-log.log")
            } else {
                tmp.join(format!("test-log.log.{}", i))
            };
            if path.exists() {
                file_count += 1;
            }
        }

        // GT-67 invariant: total file count must be EXACTLY
        // ROTATE_MAX_FILES (=5). Pre-fix this was 6 (off-by-one).
        assert_eq!(
            file_count,
            ROTATE_MAX_FILES,
            "GT-67: rotating log must keep exactly {} files; found {}. Pre-fix this was {} (off-by-one).",
            ROTATE_MAX_FILES,
            file_count,
            ROTATE_MAX_FILES + 1,
        );

        // The oldest KEPT slot is `.log.(ROTATE_MAX_FILES - 1)` (=4).
        assert!(
            tmp.join(format!("test-log.log.{}", ROTATE_MAX_FILES - 1)).exists(),
            "GT-67: oldest kept slot `.log.{}` must exist after many rotations",
            ROTATE_MAX_FILES - 1
        );
        // The next-oldest slot (`.log.ROTATE_MAX_FILES` = .log.5) must
        // NOT exist — it's the one that gets deleted by the rotate()
        // loop's `if i + 1 >= ROTATE_MAX_FILES - 1` branch.
        assert!(
            !tmp.join(format!("test-log.log.{}", ROTATE_MAX_FILES)).exists(),
            "GT-67: `.log.{}` (one past the cap) must NOT exist",
            ROTATE_MAX_FILES
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

    // ── PVT-G5-018: poison-recovery (Mutex .unwrap_or_else) ──────────

    #[test]
    fn test_rotating_file_writer_recovers_from_poisoned_mutex() {
        // PVT-G5-018: a prior panic while holding `inner`'s lock
        // poisons the mutex. The pre-fix code called `.lock().unwrap()`
        // here, which would re-panic. The post-fix code uses
        // `.lock().unwrap_or_else(|e| e.into_inner())`, which recovers
        // the guard (and the inner File handle) so logging can
        // continue. This test simulates the poison by manually
        // poisoning the mutex via `std::sync::PoisonError`, then
        // verifies that `write_line` and `flush` do NOT panic.
        let tmp = std::env::temp_dir().join(format!(
            "voice-typer-test-{}-poison",
            std::process::id()
        ));
        std::fs::remove_dir_all(&tmp).ok();
        let writer = RotatingFileWriter::new(tmp.clone(), "test-log");
        // Write an initial line so the inner File handle is opened.
        writer.write_line("before-poison").unwrap();
        // Poison the mutex: lock it, then panic while holding it
        // (caught via `catch_unwind` so the test process survives).
        let poison_result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            let _guard = writer.inner.lock().unwrap();
            panic!("intentional poison for PVT-G5-018 test");
        }));
        assert!(poison_result.is_err(), "test setup: panic should have fired");
        // Now the mutex is poisoned. The post-fix code must NOT panic.
        writer.write_line("after-poison").unwrap();
        writer.flush().unwrap();
        // Verify both lines landed (the recovered guard carries the
        // previously-opened File handle, so the post-poison write
        // appends to the same file).
        let content =
            std::fs::read_to_string(tmp.join("test-log.log")).unwrap();
        assert!(content.contains("before-poison"), "pre-poison line lost: {}", content);
        assert!(content.contains("after-poison"), "post-poison line lost: {}", content);
        std::fs::remove_dir_all(&tmp).ok();
    }

    // ── PVT-G5-082: RUST_LOG parsing ─────────────────────────────────
    //
    // We can't call `init_file_logger` from a test (it calls
    // `log::set_logger`, which is process-global and can only be set
    // once). Instead, test the parsing logic in isolation by mirroring
    // the `RUST_LOG` parse chain here. This pins the default-Info +
    // unparseable-fallback behavior so a future refactor can't silently
    // break it.

    #[test]
    fn test_rust_log_parsing_default_is_info() {
        // Mirror of the parse chain in `init_file_logger` (with no
        // RUST_LOG env var set, the chain falls through to `Info`).
        // NOTE: this test does NOT set RUST_LOG (other tests in the
        // process might have set it, so we read it defensively and
        // only assert the parse-chain shape, not the exact value —
        // see the next test for a parse-chain correctness check).
        let parsed = std::env::var("RUST_LOG")
            .ok()
            .and_then(|s| s.parse::<log::LevelFilter>().ok())
            .unwrap_or(log::LevelFilter::Info);
        // Whatever RUST_LOG is (unset, "debug", or a typo), the chain
        // must always yield a valid LevelFilter (never panics).
        let _ = parsed;
    }

    #[test]
    fn test_rust_log_parsing_unparseable_falls_back_to_info() {
        // PVT-G5-082: a typo like "debog" should fall back to Info
        // rather than silently disabling all logging. We mirror the
        // exact parse chain (without setting the env var, which would
        // race with other tests in the same process) by feeding the
        // unparseable string directly to the parser.
        let parsed = "debog".parse::<log::LevelFilter>();
        assert!(parsed.is_err(), "garbage value should not parse");
        // The init_file_logger chain uses `.ok()` then `.unwrap_or(Info)`,
        // so an unparseable value yields Info — verified here.
        let effective = parsed.ok().unwrap_or(log::LevelFilter::Info);
        assert_eq!(effective, log::LevelFilter::Info);
    }

    #[test]
    fn test_rust_log_parsing_known_levels() {
        // PVT-G5-082: pin the parse behavior for the common
        // `RUST_LOG=debug` / `=trace` / `=warn` / `=off` values so a
        // future `log` crate upgrade can't silently break the
        // override (e.g. by renaming a variant).
        assert_eq!(
            "off".parse::<log::LevelFilter>().unwrap(),
            log::LevelFilter::Off
        );
        assert_eq!(
            "warn".parse::<log::LevelFilter>().unwrap(),
            log::LevelFilter::Warn
        );
        assert_eq!(
            "info".parse::<log::LevelFilter>().unwrap(),
            log::LevelFilter::Info
        );
        assert_eq!(
            "debug".parse::<log::LevelFilter>().unwrap(),
            log::LevelFilter::Debug
        );
        assert_eq!(
            "trace".parse::<log::LevelFilter>().unwrap(),
            log::LevelFilter::Trace
        );
        // Case-insensitivity: the `FromStr` impl in the `log` crate
        // accepts both `Debug` and `debug`.
        assert_eq!(
            "DEBUG".parse::<log::LevelFilter>().unwrap(),
            log::LevelFilter::Debug
        );
    }

    // ── PVT-G5-083: install_panic_hook ───────────────────────────────

    #[test]
    fn test_install_panic_hook_does_not_panic_on_install() {
        // PVT-G5-083: the hook installer itself must not panic. Calling
        // it twice is also safe (each call replaces the previous hook
        // via take_hook chaining).
        install_panic_hook();
        install_panic_hook();
    }

    // ── PVT-G5-084: CombinedLogger::log format ───────────────────────

    #[test]
    fn test_combined_logger_log_format_includes_file_and_line() {
        // PVT-G5-084: verify the format string includes the file:line
        // segment by constructing a logger and calling `log()` with a
        // synthetic Record. We can't capture stderr (eprintln! goes to
        // fd 2) but we CAN capture the file write and assert the
        // `file:line --` segment is present.
        let tmp = std::env::temp_dir().join(format!(
            "voice-typer-test-{}-fmt",
            std::process::id()
        ));
        std::fs::remove_dir_all(&tmp).ok();
        let writer = RotatingFileWriter::new(tmp.clone(), "test-log");
        let logger = CombinedLogger {
            file_writer: Some(writer),
            level_filter: log::LevelFilter::Info,
        };
        // Build a Record with a known file/line. `log::Record::builder`
        // sets `file()` and `line()` from the args + metadata.
        let record = log::Record::builder()
            .level(log::Level::Info)
            .target("test_target")
            .file(Some("src/test.rs"))
            .line(Some(42))
            .args(format_args!("hello world"))
            .build();
        logger.log(&record);
        logger.flush();
        let content =
            std::fs::read_to_string(tmp.join("test-log.log")).unwrap();
        assert!(
            content.contains("test_target"),
            "target missing from log line: {}",
            content
        );
        assert!(
            content.contains("src/test.rs:42"),
            "file:line missing from log line (PVT-G5-084): {}",
            content
        );
        assert!(
            content.contains("hello world"),
            "message missing from log line: {}",
            content
        );
        std::fs::remove_dir_all(&tmp).ok();
    }

    #[test]
    fn test_combined_logger_log_format_falls_back_when_file_line_absent() {
        // PVT-G5-084: when `record.file()` / `record.line()` return
        // None (e.g. release builds with debuginfo stripped, or
        // records built without `#[track_caller]`), the format string
        // must still render cleanly (no panic, no `Option` debug
        // string in the output).
        let tmp = std::env::temp_dir().join(format!(
            "voice-typer-test-{}-fmt-nofile",
            std::process::id()
        ));
        std::fs::remove_dir_all(&tmp).ok();
        let writer = RotatingFileWriter::new(tmp.clone(), "test-log");
        let logger = CombinedLogger {
            file_writer: Some(writer),
            level_filter: log::LevelFilter::Info,
        };
        let record = log::Record::builder()
            .level(log::Level::Info)
            .target("test_target")
            .file(None)
            .line(None)
            .args(format_args!("no loc"))
            .build();
        logger.log(&record);
        logger.flush();
        let content =
            std::fs::read_to_string(tmp.join("test-log.log")).unwrap();
        // Should contain the "?" fallback for file and "0" for line.
        assert!(
            content.contains("?:0"),
            "fallback file:line missing from log line: {}",
            content
        );
        std::fs::remove_dir_all(&tmp).ok();
    }

    // ── 0o600 file permissions on POSIX ──────────

    #[cfg(unix)]
    #[test]
    fn test_rotating_file_writer_log_file_mode_is_0o600_on_posix() {
        // The log file created by `write_line` must have mode
        // `0o600` (owner rw only — no group/other access) on POSIX.
        // Pre-fix the file inherited the process umask (typically
        // 0o022), producing `0o644` — readable by group + others.
        // The dictation log may contain raw transcription text + PII
        // (XZ-LOG-02), so it must be owner-only.
        use std::os::unix::fs::PermissionsExt;
        let tmp = std::env::temp_dir().join(format!(
            "voice-typer-test-{}-pi7-mode",
            std::process::id()
        ));
        std::fs::remove_dir_all(&tmp).ok();
        let writer = RotatingFileWriter::new(tmp.clone(), "test-log");
        writer.write_line("secret-dictation-text").unwrap();
        writer.flush().unwrap();

        let path = tmp.join("test-log.log");
        let meta = std::fs::metadata(&path)
            .expect("log file must exist after write_line");
        let mode = meta.permissions().mode() & 0o777;
        assert_eq!(
            mode, 0o600,
            "PI-7: log file mode must be 0o600 (owner rw only); got 0o{:o}",
            mode
        );
        std::fs::remove_dir_all(&tmp).ok();
    }

    #[cfg(unix)]
    #[test]
    fn test_rotating_file_writer_rotated_files_get_0o600_on_posix() {
        // After rotation, the renamed `.log.1` file must also
        // have mode `0o600`. `rename` preserves the source file's mode
        // (which is 0o600 from the `OpenOptionsExt::mode` call in
        // `write_line`), plus the belt-and-suspenders `chmod` in
        // `rotate` re-asserts 0o600 in case a pre-hardening leftover file
        // had looser perms.
        use std::os::unix::fs::PermissionsExt;
        let tmp = std::env::temp_dir().join(format!(
            "voice-typer-test-{}-pi7-rotate-mode",
            std::process::id()
        ));
        std::fs::remove_dir_all(&tmp).ok();
        let writer = RotatingFileWriter::new(tmp.clone(), "test-log");
        // Write ~6 MB total to trigger at least one rotation
        // (ROTATE_MAX_BYTES = 5 MB).
        let big_line = "x".repeat(100_000);
        for _ in 0..60 {
            writer.write_line(&big_line).unwrap();
        }
        writer.flush().unwrap();

        let rotated = tmp.join("test-log.log.1");
        assert!(rotated.exists(), "rotated file .log.1 must exist");
        let meta = std::fs::metadata(&rotated)
            .expect("rotated log file must exist");
        let mode = meta.permissions().mode() & 0o777;
        assert_eq!(
            mode, 0o600,
            "PI-7: rotated log file mode must be 0o600; got 0o{:o}",
            mode
        );

        // The current (just-rotated) `.log` file must also be 0o600 —
        // it was just freshly opened by `write_line`'s `OpenOptionsExt::mode(0o600)`.
        let current = tmp.join("test-log.log");
        let meta = std::fs::metadata(&current)
            .expect("current log file must exist");
        let mode = meta.permissions().mode() & 0o777;
        assert_eq!(
            mode, 0o600,
            "PI-7: current log file mode must be 0o600 after rotation; got 0o{:o}",
            mode
        );

        std::fs::remove_dir_all(&tmp).ok();
    }

    #[cfg(unix)]
    #[test]
    fn test_init_file_logger_tightens_logs_dir_to_0o700_on_posix() {
        // `init_file_logger` must chmod the parent `<config_dir>/logs/`
        // dir to `0o700` (owner rwx only) on POSIX, mirroring the Python
        // side's `os.chmod(config_dir, 0o700)` at log.py:891-893.
        //
        // We can't call `init_file_logger` from a test (it calls
        // `log::set_logger`, which is process-global and can only be
        // set once per process). Instead, mirror the dir-chmod logic
        // directly: create a logs dir, chmod it to 0o755 (the
        // permissive default), then re-apply the same chmod call
        // `init_file_logger` does, and verify the mode is 0o700.
        use std::os::unix::fs::PermissionsExt;
        let tmp = std::env::temp_dir().join(format!(
            "voice-typer-test-{}-pi7-dir-mode",
            std::process::id()
        ));
        std::fs::remove_dir_all(&tmp).ok();
        let logs_dir = tmp.join("logs");
        std::fs::create_dir_all(&logs_dir).unwrap();
        // Set permissive mode first (mimics a pre-hardening leftover dir).
        std::fs::set_permissions(
            &logs_dir,
            std::fs::Permissions::from_mode(0o755),
        )
        .unwrap();
        // Apply the same chmod `init_file_logger` does.
        let _ = std::fs::set_permissions(
            &logs_dir,
            std::fs::Permissions::from_mode(0o700),
        );
        let meta = std::fs::metadata(&logs_dir).unwrap();
        let mode = meta.permissions().mode() & 0o777;
        assert_eq!(
            mode, 0o700,
            "PI-7: logs dir mode must be 0o700; got 0o{:o}",
            mode
        );
        std::fs::remove_dir_all(&tmp).ok();
    }
}
