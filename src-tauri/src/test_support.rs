//! Shared state for tests that must not run concurrently.
//!
//! `cargo test` runs every test in the same process on parallel
//! threads. Two cross-module serialization families live here:
//! `PANIC_HOOK_TEST_LOCK` (below) for panic-hook tests, and
//! `CHILD_PROCESS_TEST_LOCK` (further below) for tests that spawn
//! REAL OS child processes vs. tests that snapshot the test
//! process's own children.
//!
//! Most tests are hermetic, but a few fire REAL panics
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
//!
//! Current lock holders (KEEP THIS LIST IN SYNC — any NEW test that
//! fires a real panic through the global hook, or that reads/writes
//! `PANIC_HOOK_REENTRY`, MUST acquire `PANIC_HOOK_TEST_LOCK` and be
//! added here, otherwise it can reintroduce the "guard must be reset"
//! flake under parallel test execution):
//!
//! - `platform::logging_tests::test_rotating_file_writer_recovers_from_poisoned_mutex`
//! - `platform::logging_tests::test_si11_panic_hook_reentry_swap_semantics`
//! - `platform::logging_tests::test_si11_panic_hook_does_not_abort_and_resets_guard`
//! - `sidecar::supervisor_tests::test_gt9_catch_unwind_clears_respawn_in_progress_on_panic`

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

// ─── Child-process serialization ───────────────────────────────────

/// Serializes tests that spawn REAL OS child processes (children of
/// the shared test binary) against tests that enumerate / signal the
/// test process's OWN pid's children.
///
/// `cargo test` runs every test in the same process on parallel
/// threads. A test that spawns a subprocess (e.g. `sleep 30` for a
/// dev-mode sidecar, or a POSIX reaper) makes that subprocess a child
/// of the TEST BINARY. Tests that snapshot the test binary's own
/// children — `enumerate_children(own_pid)` /
/// `kill_process_tree(own_pid)` — then see the sibling's live child:
/// - `test_enumerate_children_*_own_pid_*` asserts the list is EMPTY
///   → fails with the sibling's child in the list.
/// - `test_kill_process_tree_own_pid_does_not_self_kill`'s DFS would
///   descend into the sibling's child and SIGTERM/SIGKILL it → the
///   sibling test's liveness assertions fail.
///
/// The lock below is held by EVERY test that spawns a real child
/// process (for the whole test body) AND by every test that
/// enumerates/signals the test process's own children, so the two
/// families never overlap. (The tokio spawns in the locked tests use
/// `std::sync::Mutex` guards held across `.await` — this only compiles
/// because `#[tokio::test]` defaults to the `current_thread` flavor;
/// do not flip those to `multi_thread` without switching this to a
/// `tokio::sync::Mutex`.)
///
/// Current lock holders (KEEP THIS LIST IN SYNC — any NEW test that
/// spawns a real OS child process, or that enumerates/signals the
/// test process's OWN pid's children, MUST acquire
/// `CHILD_PROCESS_TEST_LOCK` and be added here, otherwise it can
/// reintroduce the own-pid enumeration flake under parallel test
/// execution):
///
/// - `platform::process_tests::test_kill_process_tree_own_pid_does_not_self_kill`
/// - `platform::process_tests::test_enumerate_children_procfs_own_pid_no_children`
/// - `platform::process_tests::test_enumerate_children_own_pid_returns_empty`
/// - `platform::process_tests::test_register_kill_on_parent_exit_returns_result_not_panic`
///   (POSIX branch — spawns a reaper subprocess)
/// - `platform::open_path_tests::test_open_path_reaper_thread_does_not_panic`
///   (spawns `true` / `cmd /c ver`)
/// - `state_tests::test_devmode_drop_kills_child_when_kill_on_drop_set`
///   (spawns `sleep 30`)
/// - `state_tests::test_devmode_drop_does_not_kill_when_kill_on_drop_unset`
///   (spawns `sleep 30`)
/// - `sidecar::supervisor_tests::test_cr14_kill_tree_kills_dev_mode_child`
///   (spawns `sleep 30`)
/// - `sidecar::supervisor_tests::test_cr14_retry_loop_kills_old_child_before_storing_new`
///   (spawns `sleep 30`)
/// - `sidecar::supervisor_tests::test_cr14_retry_loop_first_iteration_kills_crashed_sidecar`
///   (spawns `sleep 30`)
///
/// Poison-recovery (`.unwrap_or_else(|e| e.into_inner())`) mirrors
/// `PANIC_HOOK_TEST_LOCK` — a panicking test would poison the mutex,
/// and the next lock() must recover rather than re-panic.
pub(crate) static CHILD_PROCESS_TEST_LOCK: Mutex<()> = Mutex::new(());
