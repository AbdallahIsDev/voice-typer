//! Rotating file-writer extracted from `platform/logging.rs`.
//!
//! Owns the **file-handle management** — lazy open, append-write,
//! flush, and the in-memory byte counter — while
//! `platform::log_rotation` owns the **rotation policy** (when to
//! rotate, how to shuffle the rename chain). The split mirrors the
//! Python side's separation between `RotatingFileHandler.emit` (write
//! a record + maybe trigger rollover) and `RotatingFileHandler.
//! doRollover` (the rename chain).
//!
//! # Security
//!
//! Log files + the parent `<config_dir>/logs/` dir are created with
//! restricted POSIX permissions (`0o600` for files) so dictated-text
//! fragments and any PII the Rust code emits are NOT world-readable
//! on multi-user POSIX systems. Mirrors the Python side's
//! `os.umask(0o077)` + `os.chmod(log_file, 0o600)` pattern in
//! `voice_typer/server/log.py`.
//!
//! # Concurrency
//!
//! Two `Mutex`es, with different scopes:
//!
//! - `inner: Mutex<Option<File>>` — guards the file handle. Held
//!   during the per-line `write_all` + byte-counter increment.
//!   Recovered via `crate::state::lock` (poison-safe) so a prior
//!   panic in another thread doesn't permanently break logging.
//! - `rotation_lock: Mutex<()>` — serializes rotations WITHOUT
//!   blocking normal writers. The `inner` Mutex is dropped BEFORE
//!   `rotate()` runs so concurrent writers can continue appending to
//!   a fresh `.log` while the rename/remove fs ops execute (those can
//!   take 100ms+ on slow disks / AV-scanned Windows / network
//!   filesystems). Multiple writers may independently detect
//!   `size > ROTATE_MAX_BYTES` and both reach the rotation path;
//!   this lock ensures only one `rotate()` call executes at a time.
//!   The losers' `rotate()` calls are no-ops (rename of nonexistent
//!   files fails silently via `let _ =`).

use crate::platform::log_rotation;
use std::fs::OpenOptions;
use std::io::Write;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;

// POSIX-only `OpenOptions::mode` + `Permissions::from_mode`
// trait imports. On Windows these are no-ops (the OS uses ACLs, not
// mode bits) — the `#[cfg(unix)]` blocks below gate every call site.
#[cfg(unix)]
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};

/// Minimal rotating-file writer: appends to
/// `<dir>/<base_name>.log` until the file exceeds `ROTATE_MAX_BYTES`,
/// then rotates (`.log` → `.log.1` → `.log.2` → … → `.log.4` → delete).
/// Thread-safe via a single `Mutex<Option<File>>`.
pub(crate) struct RotatingFileWriter {
    dir: std::path::PathBuf,
    base_name: String,
    inner: Mutex<Option<std::fs::File>>,
    /// in-memory byte counter — replaces the per-line
    /// `file.metadata()?.len()` stat() syscall. Incremented by
    /// `line.len() + 1` (for the newline) on each successful
    /// `write_all`. Reset to 0 on rotation (the file is renamed and
    /// a fresh empty file is opened on the next `write_line` call).
    /// `Relaxed` ordering is correct: we hold the `inner` Mutex
    /// during both the increment and the load (the only concurrent
    /// access is from `flush()`, which doesn't read this field), so
    /// there's no cross-thread ordering requirement.
    current_size: AtomicU64,
    /// Serializes rotations WITHOUT blocking normal writers. The
    /// `inner` Mutex is dropped BEFORE `rotate()` runs (see the
    /// module-level doc comment for the full rationale).
    rotation_lock: Mutex<()>,
}

impl RotatingFileWriter {
    pub(crate) fn new(dir: std::path::PathBuf, base_name: &str) -> Self {
        Self {
            dir,
            base_name: base_name.to_string(),
            inner: Mutex::new(None),
            current_size: AtomicU64::new(0),
            rotation_lock: Mutex::new(()),
        }
    }

    fn current_path(&self) -> std::path::PathBuf {
        self.dir.join(format!("{}.log", self.base_name))
    }

    pub(crate) fn write_line(&self, line: &str) -> std::io::Result<()> {
        // Recover from a poisoned mutex rather than panicking inside
        // the logger. A prior panic while holding this lock would
        // poison it; re-panicking here would recurse through the
        // panic hook (which itself calls `log::error!` → this writer)
        // and abort the process. Use the shared `crate::state::lock`
        // helper for consistency with `state.rs` + `supervisor.rs` +
        // `ws.rs`.
        let mut guard = crate::state::lock(&self.inner);
        // Open the file lazily so we don't create `voice-typer.log`
        // until the first log line is emitted. If the guard was None
        // (first write) OR the previous File handle was torn down by
        // the rotation path (which sets `*guard = None` before
        // renaming), open a fresh File in append mode.
        if guard.is_none() {
            std::fs::create_dir_all(&self.dir)?;
            // Create the log file with `0o600` perms on POSIX so it
            // is NOT world-readable. On Linux/macOS the default
            // `OpenOptions::create(true).append(true).open(...)`
            // inherits the process umask (typically 0o022), producing
            // `0o644` — readable by group + others. The dictation log
            // may contain raw transcription text + PII, so tighten to
            // owner-only. On Windows `OpenOptionsExt::mode` is
            // unavailable; the OS uses ACLs instead (configured at
            // install time, not per-file).
            let mut opts = OpenOptions::new();
            opts.create(true).append(true);
            #[cfg(unix)]
            opts.mode(0o600);
            let file = opts.open(self.current_path())?;
            // Belt-and-suspenders: if the file already existed
            // (created by a prior run with looser perms), explicitly
            // chmod it to 0o600 now. `OpenOptions::mode` only applies
            // to NEW files, not pre-existing ones — so without this
            // chmod a leftover 0o644 log file from a pre-hardening
            // build would stay world-readable indefinitely.
            // Best-effort: a chmod failure does not block logging.
            #[cfg(unix)]
            {
                let _ = std::fs::set_permissions(
                    self.current_path(),
                    std::fs::Permissions::from_mode(0o600),
                );
            }
            *guard = Some(file);
            // Seed the in-memory byte counter from the on-disk file
            // size on first open. The file is opened in
            // `create(true).append(true)` mode — if a prior run left
            // a stale `voice-typer.log`, its bytes are still on disk
            // and writes append to them. Without this seed, the
            // counter would start at 0 and rotation would not trigger
            // until the file grows past `ROTATE_MAX_BYTES + <pre-
            // existing size>`. This is one `metadata()` syscall per
            // file OPEN (not per line) — a ~99% reduction vs the
            // prior per-line `metadata()` call.
            let existing_len = guard
                .as_ref()
                .and_then(|f| f.metadata().ok())
                .map(|m| m.len())
                .unwrap_or(0);
            self.current_size.store(existing_len, Ordering::Relaxed);
        }
        // Borrow the File from the guard for the write/flush/metadata
        // calls below. The match returns early with `Err` if the slot
        // is somehow still None (shouldn't happen — we just
        // initialized it above — but the type system can't prove
        // that, and a panic-free `Option::unwrap` is exactly what
        // forbids).
        let file = match guard.as_mut() {
            Some(f) => f,
            None => {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::Other,
                    "logging file slot is None despite just-in-time init",
                ));
            }
        };
        // Combine the line payload + the trailing newline into a
        // single `write_all` call. The prior version did two separate
        // `write_all` calls (`line.as_bytes()` then `b"\n"`), which
        // is two `write(2)` syscalls per log line. Coalescing into
        // one buffer halves the syscall count for the file-write
        // path. The `Vec` allocation here is small (typical log line
        // ≈ 200 B) and is dominated by the syscall savings —
        // `write(2)` is ~1–2 µs on Linux, `Vec::push` is ~5 ns.
        let mut buf: Vec<u8> = Vec::with_capacity(line.len() + 1);
        buf.extend_from_slice(line.as_bytes());
        buf.push(b'\n');
        let written = buf.len() as u64;
        file.write_all(&buf)?;
        // In-memory byte counter — increment by the bytes we just
        // wrote. Replaces the per-line `file.metadata()?.len()`
        // stat() syscall. The counter is reset to 0 below when the
        // file rotates.
        self.current_size.fetch_add(written, Ordering::Relaxed);
        // `std::fs::File::flush` is a documented no-op ("File doesn't
        // have a buffer"), so the prior `file.flush()?` call was a
        // wasted method dispatch with no syscall savings. Drop it.
        // The OS write buffer is flushed by the kernel on its own
        // schedule (or by the explicit `RotatingFileWriter::flush`
        // path that the panic hook calls).
        // Check size; rotate if we've crossed the threshold.
        let len = self.current_size.load(Ordering::Relaxed);
        if log_rotation::should_rotate(len) {
            // Drop the file handle BEFORE renaming (Windows refuses
            // to rename a file that's open by another handle).
            *guard = None;
            // Reset the in-memory counter — the file is about to be
            // renamed to `.log.1`, and the next `write_line` call
            // opens a fresh empty `.log` whose size starts at 0.
            self.current_size.store(0, Ordering::Relaxed);
            // Drop the `inner` Mutex guard BEFORE calling `rotate()`
            // so other loggers aren't blocked during the (potentially
            // slow — 100ms+ on AV-scanned Windows / network
            // filesystems) rename/remove `fs` operations. The
            // rotation path does NOT need the `File` handle: we just
            // set `*guard = None` above (closing the fd), and
            // `rotate()` works purely on filesystem paths. Concurrent
            // writers that arrive during rotation will see
            // `guard.is_none()` and lazily open a fresh `.log`
            // (which `rotate()` may rename out from under them — on
            // POSIX the open fd follows the inode, so their writes
            // land in `.log.1`; an acceptable edge case for a
            // logging path, far better than blocking the entire
            // logger pool during rotation).
            drop(guard);
            // Serialize rotations WITHOUT blocking writers: a separate
            // `Mutex<()>` ensures only one `rotate()` runs at a time
            // (two writers that both crossed the threshold would
            // otherwise race on the rename chain). Losers' `rotate()`
            // calls are no-ops — `rename` of a nonexistent source
            // fails silently via `let _ =`.
            let _rotation_guard = crate::state::lock(&self.rotation_lock);
            log_rotation::rotate(&self.dir, &self.base_name)?;
        }
        Ok(())
    }

    pub(crate) fn flush(&self) -> std::io::Result<()> {
        // Same poison-recovery rationale as `write_line`, using the
        // shared `crate::state::lock` helper.
        if let Some(f) = crate::state::lock(&self.inner).as_mut() {
            f.flush()?;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── Basic write + lazy-open ───────────────────────────────────────

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
    fn test_rotating_file_writer_lazy_open_skips_empty_dir() {
        // The writer must NOT create the `.log` file until the first
        // `write_line` call — `new()` is pure construction.
        let tmp = std::env::temp_dir().join(format!(
            "voice-typer-test-{}-lazy",
            std::process::id()
        ));
        std::fs::remove_dir_all(&tmp).ok();
        let _writer = RotatingFileWriter::new(tmp.clone(), "test-log");
        // No file should exist yet (lazy open).
        assert!(!tmp.join("test-log.log").exists());
        // Even the dir shouldn't be created until first write.
        // (Note: we DID create the dir in the test setup via
        // `remove_dir_all` which leaves no dir; `new()` doesn't
        // create one either.)
        std::fs::remove_dir_all(&tmp).ok();
    }

    // ── Rotation triggers on size threshold ───────────────────────────
    //
    // ROTATE_MAX_BYTES is 5 MB; writing 6 MB should trigger at least
    // one rotation. The rotation policy itself is unit-tested in
    // `log_rotation.rs`; this test pins the integration — the writer
    // must call `should_rotate` after each line and trigger
    // `rotate()` when the threshold is crossed.

    #[test]
    fn test_rotating_file_writer_rotation() {
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

    // ── GT-67: pin the exact file count after many rotations ──────────
    //
    // The previous rotation loop kept `ROTATE_MAX_FILES + 1` files on
    // disk (off-by-one). This test writes enough data to trigger MANY
    // rotations (well past the cap) and asserts the final file count
    // is EXACTLY `ROTATE_MAX_FILES` — no more, no less. (The loop
    // bound itself is unit-tested in `log_rotation.rs::rotate`; this
    // is the end-to-end integration pin.)

    #[test]
    fn test_rotating_file_writer_pins_exact_file_count_after_many_rotations() {
        use crate::util::ROTATE_MAX_FILES;
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

        // Invariant: total file count must be EXACTLY
        // ROTATE_MAX_FILES (=5). Pre-fix this was 6 (off-by-one).
        assert_eq!(
            file_count,
            ROTATE_MAX_FILES,
            "GT-67: rotating log must keep exactly {} files; found {}. Pre-fix this was {} (off-by-one).",
            ROTATE_MAX_FILES,
            file_count,
            ROTATE_MAX_FILES + 1,
        );

        std::fs::remove_dir_all(&tmp).ok();
    }

    // ── Thread safety ────────────────────────────────────────────────

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

    // Concurrent rotation must not deadlock — the rotation_lock
    // is separate from `inner`, so two writers that both cross the
    // threshold serialize on `rotation_lock` (one rotates, the other
    // blocks briefly on the lock — NOT on `inner`). Pre-fix this
    // would have held `inner` throughout `rotate()`, blocking ALL
    // writers (including ones below the threshold) for the duration
    // of the rename chain. Post-fix, the `inner` guard is dropped
    // before `rotate()`, so non-rotating writers proceed in parallel.
    #[test]
    fn test_rotating_file_writer_concurrent_rotation_no_deadlock() {
        let tmp = std::env::temp_dir().join(format!(
            "voice-typer-test-{}-conc-rot",
            std::process::id()
        ));
        std::fs::remove_dir_all(&tmp).ok();
        let writer = std::sync::Arc::new(RotatingFileWriter::new(tmp.clone(), "test-log"));
        // 4 threads × 500 lines × ~100KB/line = ~200MB total — well
        // past the 5MB threshold, so each thread triggers many
        // rotations. The `rotation_lock` serializes the rotations;
        // without it, two concurrent `rotate()` calls would race on
        // the rename chain and could clobber each other's `.log.N`
        // files.
        let big_line = "x".repeat(100_000);
        let mut handles = Vec::new();
        for _ in 0..4 {
            let w = writer.clone();
            let line = big_line.clone();
            handles.push(std::thread::spawn(move || {
                for _ in 0..500 {
                    w.write_line(&line).unwrap();
                }
            }));
        }
        for h in handles {
            h.join().expect("writer thread panicked — likely deadlock");
        }
        // The test passes if all threads joined (no deadlock / panic).
        // Verify at least one rotated file exists (proof that rotation
        // actually fired under contention).
        assert!(
            tmp.join("test-log.log.1").exists(),
            "expected .log.1 to exist after concurrent rotations"
        );
        std::fs::remove_dir_all(&tmp).ok();
    }

    // ── poison-recovery (Mutex .unwrap_or_else) ──────────────────────

    #[test]
    fn test_rotating_file_writer_recovers_from_poisoned_mutex() {
        // A prior panic while holding `inner`'s lock poisons the
        // mutex. The pre-fix code called `.lock().unwrap()` here,
        // which would re-panic. The post-fix code uses
        // `.lock().unwrap_or_else(|e| e.into_inner())` (via
        // `crate::state::lock`), which recovers the guard (and the
        // inner File handle) so logging can continue. This test
        // simulates the poison by manually poisoning the mutex via
        // `std::sync::PoisonError`, then verifies that `write_line`
        // and `flush` do NOT panic.
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

    // ── 0o600 file permissions on POSIX ───────────────────────────────

    #[cfg(unix)]
    #[test]
    fn test_rotating_file_writer_log_file_mode_is_0o600_on_posix() {
        // The log file created by `write_line` must have mode
        // `0o600` (owner rw only — no group/other access) on POSIX.
        // Pre-fix the file inherited the process umask (typically
        // 0o022), producing `0o644` — readable by group + others.
        // The dictation log may contain raw transcription text + PII,
        // so it must be owner-only.
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
        // After rotation, the renamed `.log.1` file must also have
        // mode `0o600`. `rename` preserves the source file's mode
        // (which is 0o600 from the `OpenOptionsExt::mode` call in
        // `write_line`), plus the belt-and-suspenders `chmod` in
        // `log_rotation::rotate` re-asserts 0o600 in case a
        // pre-hardening leftover file had looser perms.
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
}
