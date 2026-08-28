//! Minimal rotating-file writer: appends to a single log file and
//! truncates in place once it crosses the size ceiling.
//!
//! All file I/O (write syscalls, rotation truncation, explicit flushes)
//! happens on a dedicated background writer thread, so a `log::error!`
//! from a tokio worker (or any other thread) never blocks the caller on
//! a blocking syscall. Callers enqueue bytes over an mpsc channel;
//! `flush()` is a synchronous barrier that blocks until the writer
//! thread has actually flushed (required by the panic-hook and shutdown
//! flush paths).

use crate::util::LOG_MAX_BYTES;
use std::fs::OpenOptions;
use std::io::Seek;
use std::io::Write;
use std::path::Path;
use std::path::PathBuf;
use std::sync::mpsc;
use std::sync::Arc;
use std::sync::Mutex;

// POSIX-only `OpenOptions::mode` + `Permissions::from_mode`
// trait imports. On Windows these are no-ops (the OS uses ACLs, not
// mode bits) — the `#[cfg(unix)]` blocks below gate every call site.
#[cfg(unix)]
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};

/// Writer-thread-owned file state. `current_size` is a plain `u64`
/// (only accessed under the mutex by the writer thread and the degraded
/// inline-fallback path).
pub(crate) struct WriterState {
    /// Lazily-opened `BufWriter<File>`. `None` until the first write.
    file: Option<std::io::BufWriter<std::fs::File>>,
    /// In-memory byte counter — replaces the per-line
    /// `file.metadata()?.len()` stat() syscall. Incremented by
    /// `buf.len()` on each successful `write_all`. Reset to 0 on
    /// rotation (the file is truncated in place).
    current_size: u64,
}

/// Commands enqueued by callers and drained by the background writer
/// thread (FIFO order — a `Flush` barrier covers every `Write` sent
/// before it).
enum WriterCommand {
    /// A complete log line (payload + trailing `\n`).
    Write(Vec<u8>),
    /// Synchronous flush barrier — the sender's `recv()` resolves only
    /// after the writer thread has flushed the underlying file.
    Flush(mpsc::Sender<std::io::Result<()>>),
}

/// Minimal rotating-file writer: appends to
/// `<dir>/<base_name>.log` until the file exceeds `LOG_MAX_BYTES`
/// (the Tier-3 mid-session hard ceiling, 40 MB), then truncates IN
/// PLACE (empties it) and keeps writing — single-file policy, numbered
/// backups (`.log.1`, ...) are NEVER created. Thread-safe via a
/// background writer thread that owns the file handle.
pub(crate) struct RotatingFileWriter {
    dir: PathBuf,
    base_name: String,
    /// Shared writer state. Locked ONLY by the background writer thread
    /// (and the degraded inline-fallback path when that thread is gone).
    /// `pub(crate)` so tests can exercise the poisoned-lock recovery
    /// contract directly.
    pub(crate) inner: Arc<Mutex<WriterState>>,
    /// Command channel to the background writer thread. `None` if the
    /// thread could not be spawned (fall back to inline best-effort I/O).
    tx: Option<mpsc::Sender<WriterCommand>>,
}

fn current_path(dir: &Path, base_name: &str) -> PathBuf {
    dir.join(format!("{base_name}.log"))
}

impl RotatingFileWriter {
    pub(crate) fn new(dir: PathBuf, base_name: &str) -> Self {
        let inner = Arc::new(Mutex::new(WriterState {
            file: None,
            current_size: 0,
        }));
        let (tx, rx) = mpsc::channel();
        let thread_inner = inner.clone();
        let thread_dir = dir.clone();
        let thread_base = base_name.to_string();
        // Best-effort spawn: if the OS refuses (resource exhaustion),
        // the writer degrades to inline best-effort I/O on the calling
        // thread rather than crashing logger init.
        let tx = std::thread::Builder::new()
            .name(format!("voice-typer-log-{base_name}"))
            .spawn(move || writer_thread(thread_inner, thread_dir, thread_base, rx))
            .map(|_| tx)
            .ok();
        Self {
            dir,
            base_name: base_name.to_string(),
            inner,
            tx,
        }
    }

    pub(crate) fn write_line(&self, line: &str) -> std::io::Result<()> {
        let mut buf: Vec<u8> = Vec::with_capacity(line.len() + 1);
        buf.extend_from_slice(line.as_bytes());
        buf.push(b'\n');
        match &self.tx {
            // Normal path: enqueue on the writer thread. The caller
            // never blocks on file I/O.
            Some(tx) => {
                if tx.send(WriterCommand::Write(buf)).is_err() {
                    // Writer thread gone — best-effort inline write.
                    // Rebuild the buffer (the original was moved into
                    // the failed `send`).
                    let mut fallback: Vec<u8> = Vec::with_capacity(line.len() + 1);
                    fallback.extend_from_slice(line.as_bytes());
                    fallback.push(b'\n');
                    return self.write_inline(&fallback);
                }
                Ok(())
            }
            // Writer thread never spawned — inline best-effort.
            None => self.write_inline(&buf),
        }
    }

    pub(crate) fn flush(&self) -> std::io::Result<()> {
        let Some(tx) = &self.tx else {
            return self.flush_inline();
        };
        let (ack_tx, ack_rx) = mpsc::channel();
        if tx.send(WriterCommand::Flush(ack_tx)).is_err() {
            return self.flush_inline();
        }
        match ack_rx.recv() {
            Ok(result) => result,
            Err(_) => self.flush_inline(),
        }
    }

    fn write_inline(&self, buf: &[u8]) -> std::io::Result<()> {
        write_to_file(&self.inner, &self.dir, &self.base_name, buf)
    }

    fn flush_inline(&self) -> std::io::Result<()> {
        flush_file(&self.inner)
    }
}

/// Write `buf` to the file managed by `inner`, opening it lazily if
/// needed. Handles rotation (truncate-in-place) when the accumulated
/// size exceeds `LOG_MAX_BYTES`.
fn write_to_file(
    inner: &Mutex<WriterState>,
    dir: &Path,
    base_name: &str,
    buf: &[u8],
) -> std::io::Result<()> {
    let mut guard = crate::state::lock(inner);
    if guard.file.is_none() {
        std::fs::create_dir_all(dir)?;
        let mut opts = OpenOptions::new();
        opts.create(true).write(true);
        #[cfg(unix)]
        opts.mode(0o600);
        let path = current_path(dir, base_name);
        let mut file = opts.open(&path)?;
        file.seek(std::io::SeekFrom::End(0))?;
        let buf_writer = std::io::BufWriter::new(file);
        #[cfg(unix)]
        {
            let _ = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600));
        }
        let existing_len = buf_writer
            .get_ref()
            .metadata()
            .map(|m| m.len())
            .unwrap_or(0);
        guard.file = Some(buf_writer);
        guard.current_size = existing_len;
    }
    let written = buf.len() as u64;
    {
        let file = match guard.file.as_mut() {
            Some(f) => f,
            None => return Err(std::io::Error::other("logging file slot is None")),
        };
        file.write_all(buf)?;
    }
    guard.current_size += written;
    if guard.current_size > LOG_MAX_BYTES {
        let file = guard
            .file
            .as_mut()
            .ok_or_else(|| std::io::Error::other("logging file slot is None during rotation"))?;
        file.flush()?;
        file.get_mut().set_len(0)?;
        file.seek(std::io::SeekFrom::Start(0))?;
        guard.current_size = 0;
    }
    Ok(())
}

/// Flush the BufWriter inside `inner`, if present.
fn flush_file(inner: &Mutex<WriterState>) -> std::io::Result<()> {
    if let Some(f) = crate::state::lock(inner).file.as_mut() {
        f.flush()?;
    }
    Ok(())
}

/// Background writer thread loop. Drains the command channel, performs
/// all file I/O (write, flush, rotation), and acks flush barriers.
fn writer_thread(
    inner: Arc<Mutex<WriterState>>,
    dir: PathBuf,
    base_name: String,
    rx: mpsc::Receiver<WriterCommand>,
) {
    while let Ok(cmd) = rx.recv() {
        // catch_unwind: a panic in file I/O must not silently kill the
        // writer thread (which would degrade every caller to inline
        // blocking writes). Poisoned-lock recovery is handled by
        // `crate::state::lock` inside the helpers.
        let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| match cmd {
            WriterCommand::Write(buf) => {
                let _ = write_to_file(&inner, &dir, &base_name, &buf);
            }
            WriterCommand::Flush(ack) => {
                let result = flush_file(&inner);
                let _ = ack.send(result);
            }
        }));
    }
    // All senders dropped — final flush so buffered data is not lost
    // when the logger is torn down.
    let _ = flush_file(&inner);
}
