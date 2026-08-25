//! Minimal rotating-file writer: appends to a single log file and
//! truncates in place once it crosses the size ceiling.

use crate::util::LOG_MAX_BYTES;
use std::fs::OpenOptions;
use std::io::Seek;
use std::io::Write;
use std::sync::Mutex;

// POSIX-only `OpenOptions::mode` + `Permissions::from_mode`
// trait imports. On Windows these are no-ops (the OS uses ACLs, not
// mode bits) — the `#[cfg(unix)]` blocks below gate every call site.
#[cfg(unix)]
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};

/// Minimal rotating-file writer: appends to
/// `<dir>/<base_name>.log` until the file exceeds `LOG_MAX_BYTES`
/// (the Tier-3 mid-session hard ceiling, 40 MB), then truncates IN
/// PLACE (empties it) and keeps writing — single-file policy, numbered
/// backups (`.log.1`, ...) are NEVER created. Thread-safe via a single
/// `Mutex<Option<BufWriter<File>>>`.
///
/// The `File` is wrapped in a `std::io::BufWriter` (8 KB buffer) so
/// the per-line `write_all` lands in an in-memory buffer instead of
/// issuing one `write(2)` syscall per log line. The buffer is flushed
/// on (a) explicit `flush()` — called by the panic hook + by
/// `CombinedLogger::log` after Warn+ records, (b) buffer fill (BufWriter
/// auto-flushes at 8 KB), and (c) drop — the `*guard = None` path on
/// rotation triggers `BufWriter::drop` which flushes the buffer to the
/// underlying `File` before the fd is closed, so rotation never loses
/// buffered data.
pub(crate) struct RotatingFileWriter {
    dir: std::path::PathBuf,
    base_name: String,
    pub(crate) inner: Mutex<Option<std::io::BufWriter<std::fs::File>>>,
    /// in-memory byte counter — replaces the per-line
    /// `file.metadata()?.len()` stat() syscall. Incremented by
    /// `line.len() + 1` (for the newline) on each successful
    /// `write_all`. Reset to 0 on rotation (the file is renamed and
    /// a fresh empty file is opened on the next `write_line` call).
    /// `Relaxed` ordering is correct: we hold the `inner` Mutex
    /// during both the increment and the load (the only concurrent
    /// access is from `flush()`, which doesn't read this field), so
    /// there's no cross-thread ordering requirement.
    current_size: std::sync::atomic::AtomicU64,
    // Single-file policy: no rotation lock is needed — the
    // truncate-in-place path runs while holding `inner`, so writers
    // are already serialized and truncation is a single `set_len(0)`
    // syscall (no slow rename chain to coordinate).
}

impl RotatingFileWriter {
    pub(crate) fn new(dir: std::path::PathBuf, base_name: &str) -> Self {
        Self {
            dir,
            base_name: base_name.to_string(),
            inner: Mutex::new(None),
            current_size: std::sync::atomic::AtomicU64::new(0),
        }
    }

    fn current_path(&self) -> std::path::PathBuf {
        self.dir.join(format!("{}.log", self.base_name))
    }

    pub(crate) fn write_line(&self, line: &str) -> std::io::Result<()> {
        //recover from a poisoned mutex rather than
        // panicking inside the logger. A prior panic while holding
        // this lock would poison it; re-panicking here would recurse
        // through the panic hook (which itself calls `log::error!` →
        // this writer) and abort the process. Use the shared poison-safe
        //`crate::state::lock` helper () for consistency with
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
            // default `OpenOptions::create(true).write(true).open(...)`
            // inherits the process umask (typically 0o022), producing
            // `0o644` — readable by group + others. The dictation log
            //may contain raw transcription text + PII (),
            // so tighten to owner-only. On Windows `OpenOptionsExt::mode`
            // is unavailable; the OS uses ACLs instead (configured at
            // install time, not per-file).
            //
            // NOTE: we open with `write(true)` (NOT `append(true)`). On
            // Windows an append-mode handle lacks `FILE_WRITE_DATA`, so
            // the rotation path's `set_len(0)` fails with
            // `ERROR_ACCESS_DENIED` (code 5) and the file silently stops
            // being written once it crosses `LOG_MAX_BYTES`. With write
            // mode we position at EOF explicitly below (append
            // semantics), and both `set_len` and `seek` work on every
            // platform. Writes are already serialized by the `inner`
            // Mutex, so the explicit position management is race-free.
            let mut opts = OpenOptions::new();
            opts.create(true).write(true);
            #[cfg(unix)]
            opts.mode(0o600);
            let mut file = opts.open(self.current_path())?;
            // Position at EOF so new writes append to any pre-existing
            // content (write mode does not carry the O_APPEND / append-
            // only semantics). If a prior run left a stale
            // `voice-typer.log`, this continues appending to it instead
            // of overwriting from the start.
            file.seek(std::io::SeekFrom::End(0))?;
            // Wrap the raw `File` in a `std::io::BufWriter` (8 KB
            // default capacity) so per-line `write_all` calls land in
            // an in-memory buffer instead of issuing one `write(2)`
            // syscall per log line. The buffer is flushed on (a)
            // explicit `flush()` (called by the panic hook + by
            // `CombinedLogger::log` after Warn+ records), (b) buffer
            // fill (auto-flush at 8 KB), and (c) drop (the `*guard =
            // None` path on rotation triggers `BufWriter::drop` which
            // flushes the buffer to the underlying `File` before the
            // fd is closed — so rotation never loses buffered data).
            let buf_writer = std::io::BufWriter::new(file);
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
            *guard = Some(buf_writer);
            //seed the in-memory byte counter from the on-disk
            // file size on first open. The file is opened in
            // `create(true).append(true)` mode — if a prior run left a
            // stale `voice-typer.log`, its bytes are still on disk
            // and writes append to them. Without this seed, the
            // counter would start at 0 and rotation would not trigger
            // until the file grows past `LOG_MAX_BYTES + <pre-
            // existing size>`. This is one `metadata()` syscall per
            // file OPEN (not per line) — a ~99% reduction vs the
            // prior per-line `metadata()` call.
            //
            // `BufWriter::get_ref` returns `&File` (the underlying
            // handle) so we can stat it without unwrapping the
            // BufWriter.
            let existing_len = guard
                .as_ref()
                .and_then(|f| f.get_ref().metadata().ok())
                .map(|m| m.len())
                .unwrap_or(0);
            self.current_size
                .store(existing_len, std::sync::atomic::Ordering::Relaxed);
        }
        // Borrow the BufWriter<File> from the guard for the write
        // calls below. The match returns early with `Err` if the slot
        // is somehow still None (shouldn't happen — we just initialized
        // it above — but the type system can't prove that, and a
        //panic-free `Option::unwrap` is exactly what  forbids).
        let file = match guard.as_mut() {
            Some(f) => f,
            None => {
                return Err(std::io::Error::other(
                    "logging file slot is None despite just-in-time init",
                ));
            }
        };
        //combine the line payload + the trailing newline into a
        // single `write_all` call. The prior version did two separate
        // `write_all` calls (`line.as_bytes()` then `b"\n"`), which is
        // two `write(2)` syscalls per log line. Coalescing into one
        // buffer halves the syscall count for the file-write path.
        // The `Vec` allocation here is small (typical log line ≈ 200 B)
        // and is dominated by the syscall savings — `write(2)` is
        // ~1–2 µs on Linux, `Vec::push` is ~5 ns.
        let mut buf: Vec<u8> = Vec::with_capacity(line.len() + 1);
        buf.extend_from_slice(line.as_bytes());
        buf.push(b'\n');
        let written = buf.len() as u64;
        file.write_all(&buf)?;
        //in-memory byte counter — increment by the bytes we
        // just wrote. Replaces the per-line `file.metadata()?.len()`
        // stat() syscall. The counter is reset to 0 below when the
        // file is truncated in place.
        self.current_size
            .fetch_add(written, std::sync::atomic::Ordering::Relaxed);
        // The BufWriter<File> accumulates the write in its 8 KB
        // in-memory buffer (no `write(2)` syscall unless the buffer
        // fills). The buffer is flushed by (a) the explicit
        // `RotatingFileWriter::flush` path (called by the panic hook +
        // by `CombinedLogger::log` after Warn+ records), (b) BufWriter
        // auto-flush at 8 KB, or (c) drop (the `*guard = None` path on
        // rotation triggers BufWriter::drop which flushes before the
        // fd is closed).
        // Check size; truncate in place if we've crossed the threshold.
        let len = self.current_size.load(std::sync::atomic::Ordering::Relaxed);
        if len > LOG_MAX_BYTES {
            // Single-file policy: truncate the log file IN PLACE. A
            // numbered backup (`.log.1`, `.log.2`, ...) is NEVER
            // created — the file on disk is always exactly one file.
            // We hold the `inner` Mutex for the whole `write_line`, so
            // no other writer can interleave during the truncate.
            use std::io::Seek;
            // Flush the BufWriter's in-memory buffer first so the
            // file's byte count is accurate, then truncate to zero and
            // seek back to the start. The line that crossed the
            // threshold is dropped (bounded-size trade-off); subsequent
            // lines start the file fresh.
            file.flush()?;
            file.get_mut().set_len(0)?;
            file.seek(std::io::SeekFrom::Start(0))?;
            self.current_size
                .store(0, std::sync::atomic::Ordering::Relaxed);
        }
        Ok(())
    }

    pub(crate) fn flush(&self) -> std::io::Result<()> {
        //same poison-recovery rationale as
        // `write_line`, using the shared `crate::state::lock` helper.
        if let Some(f) = crate::state::lock(&self.inner).as_mut() {
            f.flush()?;
        }
        Ok(())
    }
}
