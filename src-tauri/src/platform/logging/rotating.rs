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
//!
//! # Bounded-degradation contract
//!
//! The flush barrier and the command queue are BOUNDED so a stalled
//! disk (roaming profile, sync-watched config dir, full disk, AV scan)
//! can never wedge the threads that log warnings/errors:
//!
//! - **Flush ack timeout**: `flush()` waits for the writer thread's
//!   ack with [`FLUSH_ACK_TIMEOUT_MS`] (2 s), not forever. On timeout
//!   the flush is best-effort: a missed-flush warn line is logged
//!   (once per process) and `flush()` returns instead of hanging the
//!   warning-logging thread (which may be the panic hook, firing while
//!   panic-point locks are still held).
//! - **Barrier coalescing**: at most ONE flush barrier may be
//!   outstanding at a time. Concurrent warn-level callers during a
//!   pending barrier skip issuing their own (their lines are covered
//!   by the in-flight barrier's FIFO position if enqueued before it,
//!   and by the idle auto-flush below otherwise). This collapses the
//!   cross-thread barrier pile-up where every warning-logging thread
//!   parked on its own ack while the writer drained the queue.
//! - **Idle auto-flush**: the writer thread's receive loop uses
//!   `recv_timeout(WRITER_IDLE_FLUSH_MS)`; on idle timeout it flushes
//!   the BufWriter, so lines whose barrier was coalesced (and ordinary
//!   buffered info lines) land on disk within the idle window instead
//!   of sitting in the 8 KB buffer until the next barrier or exit.
//! - **Bounded queue**: the in-flight command queue's byte total is
//!   tracked in an atomic counter; once it exceeds
//!   [`QUEUE_BYTE_CEILING`] (4 MB) NON-error records are dropped (with
//!   a once-per-process stderr notice) so a wedged writer means
//!   bounded memory, not grow-only. ERROR-level records bypass the
//!   gate (the crash-path evidence is the one thing never dropped).
//!   This is drop-newest on a saturated queue rather than the
//!   drop-oldest of a ring buffer: `std::sync::mpsc` cannot remove
//!   from the middle of the queue, and a custom ring-buffer channel
//!   would fork the writer's design for a pathological-only path —
//!   the essential property (bounded memory, error records never
//!   dropped) is what the gate enforces.

use crate::util::LOG_MAX_BYTES;
use std::fs::OpenOptions;
use std::io::Seek;
use std::io::Write;
use std::path::Path;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::mpsc;
use std::sync::mpsc::RecvTimeoutError;
use std::sync::{Arc, Mutex};
use std::time::Duration;

/// How long `flush()` waits for the writer thread's flush ack before
/// giving up (best-effort) and returning. 2 s is generous for a
/// legitimate slow flush (cold disk + AV scan can exceed 1 s) while
/// still bounding the panic-hook / warning-logging threads that fire
/// the barrier.
const FLUSH_ACK_TIMEOUT_MS: u64 = 2_000;

/// Idle interval for the writer thread's receive loop: after this long
/// with no commands, the BufWriter is flushed (bounded buffering for
/// coalesced-barrier lines and ordinary info records). 60 ms under
/// `cfg(test)` so the idle-flush contract is testable in a few hundred
/// milliseconds; 250 ms in production.
const WRITER_IDLE_FLUSH_MS: u64 = if cfg!(test) { 60 } else { 250 };

/// In-flight command-queue byte ceiling. Enqueued-but-unwritten bytes
/// above this total cause non-error records to be dropped (once-per-
/// process stderr notice) instead of grow-only memory. 4 MB is ~10% of
/// the on-disk single-file ceiling (`LOG_MAX_BYTES`, 40 MB), generous
/// for any healthy session (the queue is normally near-empty because
/// the writer thread drains it continuously).
const QUEUE_BYTE_CEILING: usize = 4 * 1024 * 1024;

/// Once-per-process guard for the missed-flush-ack warn line.
static MISSED_FLUSH_ACK_LOGGED: AtomicBool = AtomicBool::new(false);

/// Once-per-process guard for the queue-saturation stderr notice.
static QUEUE_SATURATION_NOTICED: AtomicBool = AtomicBool::new(false);

// POSIX-only `OpenOptions::mode` + `Permissions::from_mode`
// trait imports. On Windows these are no-ops (the OS uses ACLs, not
// mode bits): the `#[cfg(unix)]` blocks below gate every call site.
#[cfg(unix)]
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};

/// Writer-thread-owned file state. `current_size` is a plain `u64`
/// (only accessed under the mutex by the writer thread and the degraded
/// inline-fallback path).
pub(crate) struct WriterState {
    /// Lazily-opened `BufWriter<File>`. `None` until the first write.
    file: Option<std::io::BufWriter<std::fs::File>>,
    /// In-memory byte counter: replaces the per-line
    /// `file.metadata()?.len()` stat() syscall. Incremented by
    /// `buf.len()` on each successful `write_all`. Reset to 0 on
    /// rotation (the file is truncated in place).
    current_size: u64,
}

/// Commands enqueued by callers and drained by the background writer
/// thread (FIFO order: a `Flush` barrier covers every `Write` sent
/// before it).
enum WriterCommand {
    /// A complete log line (payload + trailing `\n`).
    Write(Vec<u8>),
    /// Synchronous flush barrier: the sender's `recv()` resolves only
    /// after the writer thread has flushed the underlying file.
    Flush(mpsc::Sender<std::io::Result<()>>),
}

/// Minimal rotating-file writer: appends to
/// `<dir>/<base_name>.log` until the file exceeds `LOG_MAX_BYTES`
/// (the Tier-3 mid-session hard ceiling, 40 MB), then truncates IN
/// PLACE (empties it) and keeps writing, single-file policy, numbered
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
    /// In-flight command-queue byte total (enqueued-but-unwritten
    /// bytes). Incremented by `write_line*` after a successful send,
    /// decremented by the writer thread when it takes a `Write` command
    /// out of the queue. Gates the bounded-queue drop for non-error
    /// records (see [`QUEUE_BYTE_CEILING`]). `pub(crate)` so tests can
    /// pin the bounded-memory contract directly.
    pub(crate) queued_bytes: Arc<AtomicUsize>,
    /// In-flight flush-barrier gate: `true` while some caller is inside
    /// the barrier protocol (command sent, ack pending). Concurrent
    /// warn-level flushes during a pending barrier are coalesced
    /// instead of piling up. `pub(crate)` so tests can pin the
    /// at-most-one-in-flight contract directly.
    pub(crate) flush_barrier_pending: AtomicBool,
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
        let queued_bytes = Arc::new(AtomicUsize::new(0));
        let thread_queued_bytes = queued_bytes.clone();
        // Best-effort spawn: if the OS refuses (resource exhaustion),
        // the writer degrades to inline best-effort I/O on the calling
        // thread rather than crashing logger init.
        let tx = std::thread::Builder::new()
            .name(format!("voice-typer-log-{base_name}"))
            .spawn(move || {
                writer_thread(
                    thread_inner,
                    thread_queued_bytes,
                    thread_dir,
                    thread_base,
                    rx,
                )
            })
            .map(|_| tx)
            .ok();
        Self {
            dir,
            base_name: base_name.to_string(),
            inner,
            tx,
            queued_bytes,
            flush_barrier_pending: AtomicBool::new(false),
        }
    }

    /// Enqueue a complete log line, severity-aware.
    ///
    /// ERROR-level records bypass the bounded-queue drop gate (the
    /// crash-path evidence is the one record class never sacrificed
    /// when the writer is wedged); every other level participates in
    /// the gate (see [`QUEUE_BYTE_CEILING`]).
    pub(crate) fn write_line_level(&self, line: &str, level: log::Level) -> std::io::Result<()> {
        let critical = level == log::Level::Error;
        let mut buf: Vec<u8> = Vec::with_capacity(line.len() + 1);
        buf.extend_from_slice(line.as_bytes());
        buf.push(b'\n');
        match &self.tx {
            // Normal path: enqueue on the writer thread. The caller
            // never blocks on file I/O.
            Some(tx) => {
                // Bounded queue: drop non-error records once the
                // in-flight queue is saturated. The check-then-send
                // pair is racy by design, a few concurrent senders
                // may each pass the check before any increment lands,
                // bounding the overshoot to a handful of records
                // (never to grow-only memory).
                if !critical
                    && self.queued_bytes.load(Ordering::Relaxed) >= QUEUE_BYTE_CEILING
                {
                    notify_queue_saturation_once();
                    return Ok(());
                }
                let queued_len = buf.len();
                // Increment BEFORE the send: the writer thread's
                // fetch_sub fires the moment it RECEIVES the command,
                // so an increment placed after `send` can land after
                // the matching decrement: the counter transiently
                // underflows (usize wrap) and nets to 0, defeating the
                // gate whenever the writer is fast. Ordering the add
                // first keeps the invariant: every sub matches an
                // already-counted add. A failed send rolls the add
                // back before the inline fallback.
                self.queued_bytes.fetch_add(queued_len, Ordering::Relaxed);
                if tx.send(WriterCommand::Write(buf)).is_err() {
                    self.queued_bytes.fetch_sub(queued_len, Ordering::Relaxed);
                    // Writer thread gone: best-effort inline write.
                    // Rebuild the buffer (the original was moved into
                    // the failed `send`).
                    let mut fallback: Vec<u8> = Vec::with_capacity(line.len() + 1);
                    fallback.extend_from_slice(line.as_bytes());
                    fallback.push(b'\n');
                    return self.write_inline(&fallback);
                }
                Ok(())
            }
            // Writer thread never spawned: inline best-effort.
            None => self.write_inline(&buf),
        }
    }

    pub(crate) fn flush(&self) -> std::io::Result<()> {
        self.flush_with_timeout(Duration::from_millis(FLUSH_ACK_TIMEOUT_MS))
    }

    /// Deadline-parameterized core of [`Self::flush`], the timeout is
    /// the only thing the tests vary (the production timeout is 2 s;
    /// tests pass tens of milliseconds and stall the writer thread on
    /// the `inner` mutex so the timeout leg is exercised without
    /// slowing the suite).
    pub(crate) fn flush_with_timeout(&self, timeout: Duration) -> std::io::Result<()> {
        let Some(tx) = &self.tx else {
            return self.flush_inline();
        };
        // Barrier coalescing: at most ONE flush barrier outstanding at
        // a time. A caller arriving while another barrier's ack is
        // pending skips issuing its own, its lines are covered either
        // by the in-flight barrier's FIFO position (if enqueued before
        // the barrier command) or by the writer thread's idle
        // auto-flush. This collapses the cross-thread pile-up where
        // every warning-logging thread hung on its own ack while the
        // writer drained the whole queue.
        if !self.try_begin_flush_barrier() {
            return Ok(());
        }
        let (ack_tx, ack_rx) = mpsc::channel();
        if tx.send(WriterCommand::Flush(ack_tx)).is_err() {
            self.end_flush_barrier();
            return self.flush_inline();
        }
        match ack_rx.recv_timeout(timeout) {
            Ok(result) => {
                self.end_flush_barrier();
                result
            }
            // The writer thread dropped the ack sender without replying
            // (thread died mid-queue): same fallback the old
            // `recv()`-Err path took.
            Err(RecvTimeoutError::Disconnected) => {
                self.end_flush_barrier();
                self.flush_inline()
            }
            // The writer thread is stalled (slow disk / AV scan), the
            // flush becomes best-effort: log the miss (once per
            // process) and return instead of hanging the calling
            // thread. The notice is emitted while the barrier gate is
            // still held, so the notice's own warn-level record's
            // flush (the CombinedLogger flushes Warn+ immediately) is
            // coalesced rather than nesting another full timeout wait.
            Err(RecvTimeoutError::Timeout) => {
                notify_missed_flush_ack_once(timeout);
                self.end_flush_barrier();
                Ok(())
            }
        }
    }

    /// Attempt to claim the in-flight flush-barrier gate. Returns
    /// `false` if a barrier is already outstanding (the caller's flush
    /// is coalesced into it). `pub(crate)` so the sibling tests pin
    /// the at-most-one-in-flight contract without timing tricks.
    pub(crate) fn try_begin_flush_barrier(&self) -> bool {
        !self.flush_barrier_pending.swap(true, Ordering::SeqCst)
    }

    /// Release the in-flight flush-barrier gate (every exit path of
    /// [`Self::flush_with_timeout`] calls this: including the timeout
    /// leg, so a stalled writer never latches the gate shut forever).
    pub(crate) fn end_flush_barrier(&self) {
        self.flush_barrier_pending.store(false, Ordering::SeqCst);
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

/// Log the missed flush ack, once per process. A stalled writer would
/// otherwise re-notify on every warn-level record, adding log traffic
/// to an already-degraded sink; one breadcrumb is enough for triage.
fn notify_missed_flush_ack_once(timeout: Duration) {
    if MISSED_FLUSH_ACK_LOGGED.swap(true, Ordering::SeqCst) {
        return;
    }
    log::warn!(
        "[LOG] flush barrier ack not received within {}ms: continuing best-effort \
         (writer thread stalled or disk saturated)",
        timeout.as_millis()
    );
}

/// Notify (once per process, on stderr, in the canonical terminal line
/// format) that the in-flight queue is saturated and non-error records
/// are being dropped. stderr rather than `log::warn!` because the file
/// sink is exactly what's degraded here, the notice must reach an
/// independent channel. The guard re-arms once the writer thread
/// drains the queue back under half the ceiling (see
/// [`maybe_rearm_queue_saturation_notice`]), so a recovery followed by
/// a later relapse notifies again.
fn notify_queue_saturation_once() {
    if QUEUE_SATURATION_NOTICED.swap(true, Ordering::SeqCst) {
        return;
    }
    let ts = crate::util::now_time_only();
    eprintln!(
        "{} {:5} [LOG] writer queue saturated ({} bytes in flight), non-error log lines \
         dropped until it drains",
        ts,
        "WARN",
        QUEUE_BYTE_CEILING
    );
}

/// Called by the writer thread after draining a `Write` command: once
/// the in-flight total drops below half the ceiling, re-arm the
/// saturation notice so a later relapse notifies again.
fn maybe_rearm_queue_saturation_notice(queued: usize) {
    if queued < QUEUE_BYTE_CEILING / 2 {
        QUEUE_SATURATION_NOTICED.store(false, Ordering::SeqCst);
    }
}

/// Background writer thread loop. Drains the command channel, performs
/// all file I/O (write, flush, rotation), and acks flush barriers.
///
/// The receive loop uses `recv_timeout(WRITER_IDLE_FLUSH_MS)`: on idle
/// timeout the BufWriter is flushed, bounding how long a line whose
/// flush barrier was coalesced (or any ordinary buffered info line)
/// can sit in the 8 KB in-memory buffer.
fn writer_thread(
    inner: Arc<Mutex<WriterState>>,
    queued_bytes: Arc<AtomicUsize>,
    dir: PathBuf,
    base_name: String,
    rx: mpsc::Receiver<WriterCommand>,
) {
    loop {
        let cmd = match rx.recv_timeout(Duration::from_millis(WRITER_IDLE_FLUSH_MS)) {
            Ok(cmd) => cmd,
            // Idle: flush any buffered bytes so coalesced-barrier
            // lines and ordinary info records land within the idle
            // window (a no-op syscall-wise when the buffer is empty).
            Err(RecvTimeoutError::Timeout) => {
                let _ = flush_file(&inner);
                continue;
            }
            // All senders dropped: exit; the final flush below lands
            // the last buffered bytes.
            Err(RecvTimeoutError::Disconnected) => break,
        };
        // catch_unwind: a panic in file I/O must not silently kill the
        // writer thread (which would degrade every caller to inline
        // blocking writes). Poisoned-lock recovery is handled by
        // `crate::state::lock` inside the helpers.
        let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| match cmd {
            WriterCommand::Write(buf) => {
                // Decrement BEFORE the write: the counter tracks
                // queue residency (the command left the queue the
                // moment we received it), not write success, a
                // failed write must not keep saturation accounting
                // inflated for bytes that are no longer queued.
                // `fetch_sub` returns the PREVIOUS value, so the
                // post-decrement total is `prev - buf.len()`.
                let prev = queued_bytes.fetch_sub(buf.len(), Ordering::Relaxed);
                maybe_rearm_queue_saturation_notice(prev.wrapping_sub(buf.len()));
                let _ = write_to_file(&inner, &dir, &base_name, &buf);
            }
            WriterCommand::Flush(ack) => {
                let result = flush_file(&inner);
                let _ = ack.send(result);
            }
        }));
    }
    // All senders dropped: final flush so buffered data is not lost
    // when the logger is torn down.
    let _ = flush_file(&inner);
}
