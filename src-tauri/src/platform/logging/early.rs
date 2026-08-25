//! EarlyLogger — the Python `logging.lastResort`-equivalent stderr-only
//! fallback sink installed as the first line of `main()`.

use super::combined::{is_truthy_env_var, CombinedLogger};
use super::redact::redact_pii;
use crate::util::now_time_only;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::OnceLock;

//EarlyLogger (lastResort-equivalent for the Rust host) ──────
//
// Python's `logging` module ships with `logging.lastResort` — a
// stderr-only handler of level WARNING that fires when no other
// handlers are configured, so `log.warning(...)`/`log.error(...)`
// calls during early startup (before `logging.basicConfig` runs) are
// NOT silently lost. The TS side has `console.*` which always fires.
// The Rust `log` crate has NO equivalent: before `log::set_logger`
// returns, every `log::*!` call is silently dropped by the default
// no-op logger.
//
//Pre-, `main.rs` called `config_dir_from_env(...)` BEFORE
// `init_file_logger`, and `paths.rs` had to work around the silent
// drop with manual `eprintln!("{}", warn_msg); log::warn!("{}", warn_msg);`
// pairs (paths.rs:165-166). Any NEW pre-init `log::*!` call would be
// silently lost with no workaround.
//
//fix: install an `EarlyLogger` as the FIRST line of `main()`.
// The EarlyLogger is a minimal stderr-only `log::Log` impl that runs
// until `init_file_logger` upgrades it to the combined file+stderr
// sink via a swap pattern (the global `log::set_logger` can only be
// called ONCE per process, so we can't replace the EarlyLogger — we
// swap a `CombinedLogger` INTO it via a `OnceLock`).

/// Process-global handle to the leaked `&'static EarlyLogger` instance,
/// set by `install_early_logger`. Read by `init_file_logger` so it can
/// swap the file sink in without calling `log::set_logger` a second
/// time (which would fail — `set_logger` is process-global one-shot).
pub(crate) static EARLY_LOGGER_HANDLE: OnceLock<&'static EarlyLogger> = OnceLock::new();

//minimal stderr-only `log::Log` impl installed as the FIRST
/// line of `main()` (before `install_panic_hook`, before
/// `config_dir_from_env`, before `init_file_logger`). Until
/// `init_file_logger` runs, all `log::*!` records go to stderr only
/// (subject to the `stderr_verbose` flag + `level_filter`). Once
/// `init_file_logger` runs, a `CombinedLogger` is swapped into
/// `inner` and all subsequent records delegate to it (file + stderr).
///
/// The hot path (`log()`) is a single `OnceLock::get` (one atomic
/// load) — same cost as a `bool` load. The pre-init fallback path
/// (rare — only runs between `install_early_logger` and
/// `init_file_logger`, a window of microseconds in `main()`) does
/// the format + `eprintln!` inline.
pub(crate) struct EarlyLogger {
    /// The `CombinedLogger` installed by `init_file_logger`. `None`
    /// (via `OnceLock::get()` returning `None`) until that call.
    /// `OnceLock::get` is a single atomic load — no mutex on the hot
    /// path. `OnceLock::set` is called exactly once (init_file_logger
    /// returns Err if called twice).
    pub(crate) inner: OnceLock<CombinedLogger>,
    //Pre-init fallback state: stderr verbosity flag. : AtomicBool
    /// so future code can toggle at runtime. After `init_file_logger`
    /// upgrades the EarlyLogger, this field is no longer consulted —
    /// the CombinedLogger's own `stderr_verbose` takes over.
    pub(crate) stderr_verbose: AtomicBool,
    /// Pre-init fallback state: level filter. Plain `log::LevelFilter`
    /// (no atomic) because it's only set once at construction and read
    /// in the pre-init fallback path. After upgrade, the CombinedLogger's
    /// own `level_filter` is used.
    pub(crate) level_filter: log::LevelFilter,
}

impl EarlyLogger {
    /// Return the process-global `&'static EarlyLogger`, if
    /// `install_early_logger` has been called. Returns `None` in
    /// tests / host entrypoints that skip the early-logger install
    /// (in which case `init_file_logger` falls back to the
    //pre- path of calling `log::set_logger` directly).
    pub(super) fn instance() -> Option<&'static EarlyLogger> {
        EARLY_LOGGER_HANDLE.get().copied()
    }
}

impl log::Log for EarlyLogger {
    fn enabled(&self, metadata: &log::Metadata) -> bool {
        // If the CombinedLogger has been swapped in, delegate to its
        // `enabled` check (which consults the upgraded level filter).
        if let Some(combined) = self.inner.get() {
            return combined.enabled(metadata);
        }
        // Pre-init fallback: use the EarlyLogger's own level filter.
        metadata.level() <= self.level_filter
    }

    fn log(&self, record: &log::Record) {
        // Hot path: delegate to the CombinedLogger if installed.
        if let Some(combined) = self.inner.get() {
            combined.log(record);
            return;
        }
        // Pre-init fallback (only runs between `install_early_logger`
        // and `init_file_logger` — a window of microseconds in
        // `main()`). Format the line and emit to stderr only.
        if !self.enabled(record.metadata()) {
            return;
        }
        let raw_msg = record.args().to_string();
        // Apply PII redaction in the pre-init fallback too — the
        // CombinedLogger path calls redact_pii at line 325, but this
        // pre-init path (between install_early_logger and
        // init_file_logger) previously emitted the raw message to
        // stderr without redaction. If init_file_logger fails (config
        // dir not writable, disk full), the EarlyLogger stays in
        // pre-init mode for the entire process lifetime, and every
        // log::*! call would land on stderr unredacted. Mirror the
        // CombinedLogger's redaction here.
        let msg = redact_pii(&raw_msg);
        // stderr-only pre-init sink — clean time-only line
        // (`HH:MM:SS LEVEL msg`), matching the CombinedLogger terminal
        // line + Python `_ColorFormatter`. The module path / file:line
        // are deliberately NOT rendered (they add noise to every line;
        // the message carries its own `[TOPIC]` prefix).
        let ts = now_time_only();
        let line = format!("{} {:5} {}", ts, record.level(), msg);
        //AtomicBool::load(Relaxed) — runtime-toggleable.
        if self.stderr_verbose.load(Ordering::Relaxed) {
            eprintln!("{}", line);
        }
        // No file sink in the pre-init fallback — `init_file_logger`
        // hasn't run yet, so there's no `RotatingFileWriter` to write
        // to. The record is preserved on stderr, which is the
        // Python `lastResort` equivalent.
    }

    fn flush(&self) {
        if let Some(combined) = self.inner.get() {
            combined.flush();
        }
        // Pre-init fallback: no buffered state to flush (eprintln! is
        // unbuffered on POSIX — writes go straight to the fd via
        // `write(2)`).
    }
}

//install the `EarlyLogger` as the process-global `log` sink.
/// MUST be the FIRST line of `main()` — before `install_panic_hook`,
/// before `config_dir_from_env`, before any other code that might
/// call `log::*!`. Mirrors Python's `logging.lastResort` pattern.
///
/// After this call returns, ALL `log::*!` records (subject to the
/// level filter) land on stderr (if `stderr_verbose` is true) until
/// `init_file_logger` upgrades the EarlyLogger to the combined
/// file+stderr sink.
///
/// `pub` (NOT `pub(crate)`) so `main.rs` can call it from outside
/// the `platform::logging` module. Calling more than once is safe —
/// the second call is a no-op (the EarlyLogger is already installed
/// in `EARLY_LOGGER_HANDLE` and `log::set_logger` was already called).
///
/// if `log::set_logger` returns `Err` (another logger is
/// already installed as the process-global sink — e.g. a test that
/// called `log::set_logger` before `install_early_logger`), this
/// function does NOT set `EARLY_LOGGER_HANDLE`. Pre-fix it set the
/// handle unconditionally, which orphaned the EarlyLogger:
/// `init_file_logger` would later swap a `CombinedLogger` into the
/// handle's `inner`, but the global `log` dispatch still routed to
/// the OTHER logger → silent log loss. Now `init_file_logger` takes
/// its fallback path (direct `log::set_logger`) which propagates the
/// failure to the caller.
pub fn install_early_logger() {
    if EARLY_LOGGER_HANDLE.get().is_some() {
        // Already installed — no-op. Allows `main()` to call this
        // defensively (e.g. in tests that exercise `main`'s startup
        // path) without panicking on the second `log::set_logger`.
        return;
    }
    //same stderr_verbose computation as
    // `init_file_logger` — debug builds OR `RUST_LOG_STDERR=1`. Now
    // delegates to the shared `is_truthy_env_var` helper so the
    // truthy contract is defined in exactly one place (the prior
    // 4-line `matches!` block was duplicated at lines 143-146 of
    // `init_file_logger`; both sites now go through the same helper).
    let stderr_verbose = cfg!(debug_assertions) || is_truthy_env_var("RUST_LOG_STDERR");
    let logger = Box::leak(Box::new(EarlyLogger {
        inner: OnceLock::new(),
        stderr_verbose: AtomicBool::new(stderr_verbose),
        // Pre-init default: Info level so `log::info!`/`log::warn!`/
        // `log::error!` from `config_dir_from_env` etc. all land on
        // stderr. `init_file_logger` will bump this via
        // `log::set_max_level` once it parses `RUST_LOG`.
        level_filter: log::LevelFilter::Info,
    }));
    // `log::set_logger` is a one-shot — returns Err if a
    // logger is already installed. Pre-fix this code did `let _ =` and
    // unconditionally set `EARLY_LOGGER_HANDLE` below, which ORPHANED
    // the EarlyLogger: `init_file_logger` would later find the handle,
    // swap a `CombinedLogger` into the EarlyLogger's `inner`, but the
    // global `log` dispatch still routed to the OTHER (pre-installed)
    // logger → silent log loss (the swapped-in `CombinedLogger` never
    // received records).
    //
    // Fix: if `set_logger` failed, do NOT set `EARLY_LOGGER_HANDLE`.
    // `init_file_logger` will then take its fallback path (call
    // `log::set_logger` directly with the `CombinedLogger`), which
    // also fails — but that failure is propagated to the caller as an
    // `Err`, which is the correct outcome (the caller can fall back
    // to `env_logger` for stderr-only output). Emit a stderr warning
    // so operators see the orphan in `journalctl` output.
    if log::set_logger(logger).is_err() {
        eprintln!(
            "[EarlyLogger] install_early_logger: log::set_logger failed \
             (another logger is already installed as the process-global \
             log sink). EARLY_LOGGER_HANDLE NOT set — init_file_logger \
             will fall back to direct log::set_logger. Subsequent \
             log::*! records route to the pre-installed logger until \
             init_file_logger runs."
        );
        // Note: the leaked `EarlyLogger` is now orphaned (no handle,
        // no global registration). This is a one-time ~200-byte leak
        // on an error path that should never fire in production
        // (only in tests that install their own logger before calling
        // install_early_logger). Acceptable.
        return;
    }
    log::set_max_level(log::LevelFilter::Info);
    let _ = EARLY_LOGGER_HANDLE.set(logger);
}
