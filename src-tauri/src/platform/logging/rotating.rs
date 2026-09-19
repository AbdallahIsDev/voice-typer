
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

const FLUSH_ACK_TIMEOUT_MS: u64 = 2_000;

const WRITER_IDLE_FLUSH_MS: u64 = if cfg!(test) { 60 } else { 250 };

const QUEUE_BYTE_CEILING: usize = 4 * 1024 * 1024;

/// Once-per-process guard for the missed-flush-ack warn line.
static MISSED_FLUSH_ACK_LOGGED: AtomicBool = AtomicBool::new(false);

/// Once-per-process guard for the queue-saturation stderr notice.
static QUEUE_SATURATION_NOTICED: AtomicBool = AtomicBool::new(false);

#[cfg(unix)]
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};

pub(crate) struct WriterState {
    /// Lazily-opened `BufWriter<File>`. `None` until the first write.
    file: Option<std::io::BufWriter<std::fs::File>>,
    current_size: u64,
}

enum WriterCommand {
    /// A complete log line (payload + trailing `\n`).
    Write(Vec<u8>),
    /// Synchronous flush barrier: the sender's `recv()` resolves only
    /// after the writer thread has flushed the underlying file.
    Flush(mpsc::Sender<std::io::Result<()>>),
}

pub(crate) struct RotatingFileWriter {
    dir: PathBuf,
    base_name: String,
    pub(crate) inner: Arc<Mutex<WriterState>>,
    /// Command channel to the background writer thread. `None` if the
    /// thread could not be spawned (fall back to inline best-effort I/O).
    tx: Option<mpsc::Sender<WriterCommand>>,
    pub(crate) queued_bytes: Arc<AtomicUsize>,
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

    pub(crate) fn write_line_level(&self, line: &str, level: log::Level) -> std::io::Result<()> {
        let critical = level == log::Level::Error;
        let mut buf: Vec<u8> = Vec::with_capacity(line.len() + 1);
        buf.extend_from_slice(line.as_bytes());
        buf.push(b'\n');
        match &self.tx {
            // Normal path: enqueue on the writer thread. The caller
            // never blocks on file I/O.
            Some(tx) => {
                if !critical && self.queued_bytes.load(Ordering::Relaxed) >= QUEUE_BYTE_CEILING {
                    notify_queue_saturation_once();
                    return Ok(());
                }
                let queued_len = buf.len();
                self.queued_bytes.fetch_add(queued_len, Ordering::Relaxed);
                if tx.send(WriterCommand::Write(buf)).is_err() {
                    self.queued_bytes.fetch_sub(queued_len, Ordering::Relaxed);
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

    pub(crate) fn flush_with_timeout(&self, timeout: Duration) -> std::io::Result<()> {
        let Some(tx) = &self.tx else {
            return self.flush_inline();
        };
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
            Err(RecvTimeoutError::Disconnected) => {
                self.end_flush_barrier();
                self.flush_inline()
            }
            Err(RecvTimeoutError::Timeout) => {
                notify_missed_flush_ack_once(timeout);
                self.end_flush_barrier();
                Ok(())
            }
        }
    }

    pub(crate) fn try_begin_flush_barrier(&self) -> bool {
        !self.flush_barrier_pending.swap(true, Ordering::SeqCst)
    }

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

fn notify_queue_saturation_once() {
    if QUEUE_SATURATION_NOTICED.swap(true, Ordering::SeqCst) {
        return;
    }
    let ts = crate::util::now_time_only();
    eprintln!(
        "{} {:5} [LOG] writer queue saturated ({} bytes in flight), non-error log lines \
         dropped until it drains",
        ts, "WARN", QUEUE_BYTE_CEILING
    );
}

fn maybe_rearm_queue_saturation_notice(queued: usize) {
    if queued < QUEUE_BYTE_CEILING / 2 {
        QUEUE_SATURATION_NOTICED.store(false, Ordering::SeqCst);
    }
}

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
            Err(RecvTimeoutError::Timeout) => {
                let _ = flush_file(&inner);
                continue;
            }
            // All senders dropped: exit; the final flush below lands
            // the last buffered bytes.
            Err(RecvTimeoutError::Disconnected) => break,
        };
        let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| match cmd {
            WriterCommand::Write(buf) => {
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
