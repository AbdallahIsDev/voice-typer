//! Shared state for tests that must not run concurrently.
//!
//! `cargo test` runs every test in the same process on parallel
//! threads. Most tests are hermetic, but a few fire REAL panics
//! through the process-global panic hook installed by
//! `platform::logging::install_panic_hook`. That hook toggles the
//! process-global `PANIC_HOOK_REENTRY` AtomicBool (`swap(true)` on
//! entry, `store(false)` at the end of the body). If two such tests
//! run at the same time, one test's `swap(true)` can land between the
//! other test's `store(false)` and its terminal `load()` — a spurious
//! "guard must be reset" failure that is purely a test-isolation bug,
//! not a production defect.
//!
//! The serialization lock below is held by EVERY test that fires a
//! real panic through the global hook (or that directly pokes
//! `PANIC_HOOK_REENTRY`), regardless of which module the test lives
//! in, so the global flag is only ever touched by one test at a time.

use std::sync::Mutex;

/// Serializes tests that fire a real panic through the process-global
/// panic hook or directly mutate `PANIC_HOOK_REENTRY`.
///
/// Poison-recovery (`.unwrap_or_else(|e| e.into_inner())`) mirrors the
/// pattern in `platform::logging`'s `RotatingFileWriter` — a panic
/// while the guard is held marks the mutex poisoned, but the panicking
/// test always catches its own panic (`catch_unwind`), so the next
/// lock() must recover the guard rather than re-panic.
pub(crate) static PANIC_HOOK_TEST_LOCK: Mutex<()> = Mutex::new(());
