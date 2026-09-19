//! EarlyLogger: stderr-only fallback sink installed as the first line of
//! `main()` (Python `logging.lastResort` equivalent). `log::set_logger`
//! is process-once, so `init_file_logger` swaps a CombinedLogger into
//! `inner` rather than replacing the logger.

use super::combined::{is_truthy_env_var, CombinedLogger};
use super::redact::redact_pii;
use crate::util::now_time_only;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::OnceLock;

/// Process-global handle to the leaked `&'static EarlyLogger`. Read by
/// `init_file_logger` to swap the file sink in without a second
/// `log::set_logger`.
pub(crate) static EARLY_LOGGER_HANDLE: OnceLock<&'static EarlyLogger> = OnceLock::new();

/// Minimal stderr-only `log::Log`. Until `init_file_logger` runs, records
/// go to stderr only; afterwards they delegate to the CombinedLogger.
/// Hot path is one `OnceLock::get` (atomic load).
pub(crate) struct EarlyLogger {
    /// CombinedLogger installed by `init_file_logger`; `None` until then.
    pub(crate) inner: OnceLock<CombinedLogger>,
    /// Pre-init stderr verbosity (debug builds or `RUST_LOG_STDERR=1`).
    /// After upgrade, the CombinedLogger's flag takes over.
    pub(crate) stderr_verbose: AtomicBool,
    /// Pre-init level filter (set once; CombinedLogger's filter after upgrade).
    pub(crate) level_filter: log::LevelFilter,
}

impl EarlyLogger {
    /// Process-global instance if `install_early_logger` has run.
    pub(super) fn instance() -> Option<&'static EarlyLogger> {
        EARLY_LOGGER_HANDLE.get().copied()
    }
}

impl log::Log for EarlyLogger {
    fn enabled(&self, metadata: &log::Metadata) -> bool {
        if let Some(combined) = self.inner.get() {
            return combined.enabled(metadata);
        }
        metadata.level() <= self.level_filter
    }

    fn log(&self, record: &log::Record) {
        // Hot path: delegate once CombinedLogger is swapped in.
        if let Some(combined) = self.inner.get() {
            combined.log(record);
            return;
        }
        // Pre-init fallback (microseconds in `main`, or the whole process
        // if init_file_logger fails). Redact PII — if init fails, this
        // path is the permanent sink and must not emit raw secrets.
        if !self.enabled(record.metadata()) {
            return;
        }
        let raw_msg = record.args().to_string();
        let msg = redact_pii(&raw_msg);
        // C-LOG-1 terminal form: `HH:MM:SS LEVEL msg`.
        let ts = now_time_only();
        let line = format!("{} {:5} {}", ts, record.level(), msg);
        if self.stderr_verbose.load(Ordering::Relaxed) {
            eprintln!("{}", line);
        }
        // No file sink yet (no RotatingFileWriter). stderr is the lastResort.
    }

    fn flush(&self) {
        if let Some(combined) = self.inner.get() {
            combined.flush();
        }
        // Pre-init: eprintln! is unbuffered; nothing to flush.
    }
}

/// Install the EarlyLogger as the process-global `log` sink. MUST be the
/// FIRST line of `main()`. Idempotent (second call is a no-op). If
/// `log::set_logger` fails (another logger already installed), do NOT set
/// `EARLY_LOGGER_HANDLE` — otherwise init_file_logger would swap into an
/// orphaned logger while dispatch still routes to the other one (silent
/// log loss). The leaked EarlyLogger on that error path is ~200 bytes.
pub fn install_early_logger() {
    if EARLY_LOGGER_HANDLE.get().is_some() {
        return;
    }
    // Same truthy contract as `init_file_logger` (single helper).
    let stderr_verbose = cfg!(debug_assertions) || is_truthy_env_var("RUST_LOG_STDERR");
    let logger = Box::leak(Box::new(EarlyLogger {
        inner: OnceLock::new(),
        stderr_verbose: AtomicBool::new(stderr_verbose),
        // Pre-init default Info so startup `log::info!`/`warn!`/`error!` land.
        level_filter: log::LevelFilter::Info,
    }));
    if log::set_logger(logger).is_err() {
        eprintln!(
            "[EarlyLogger] install_early_logger: log::set_logger failed \
             (another logger is already installed as the process-global \
             log sink). EARLY_LOGGER_HANDLE NOT set: init_file_logger \
             will fall back to direct log::set_logger. Subsequent \
             log::*! records route to the pre-installed logger until \
             init_file_logger runs."
        );
        return;
    }
    log::set_max_level(log::LevelFilter::Info);
    let _ = EARLY_LOGGER_HANDLE.set(logger);
}
