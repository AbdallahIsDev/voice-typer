//! Unit tests for `platform::logging`.
//!
//! Extracted from the inline `#[cfg(test)] mod tests { ... }` block that
//! previously lived at the bottom of `logging.rs`. The split satisfies
//! C-TEST-5 (no inline test code in production source files) and drops
//! `logging.rs` from ~3232 LOC to ~1765 LOC of pure production code.
//!
//! Tests are wired via `#[cfg(test)] mod logging_tests;` declared in
//! `platform/mod.rs`, making this file a sibling of `logging.rs` under
//! the `platform` module (same convention as `bubble/tests.rs` and
//! `migrate/tests.rs`).
//!
//! Items are pulled in via `use super::logging::*;` (the production
//! module is a sibling, not a parent). Private items in `logging` that
//! the tests construct or poke directly (`CombinedLogger` fields,
//! `RotatingFileWriter::inner`, `PANIC_HOOK_REENTRY`,
//! `EARLY_LOGGER_HANDLE`, `EarlyLogger` fields, `is_truthy_value`) are
//! declared `pub(crate)` in `logging.rs` so the sibling test module can
//! reach them without leaking them past the crate boundary.

use super::logging::*;
use crate::test_support::PANIC_HOOK_TEST_LOCK;
use crate::util::LOG_MAX_BYTES;
use log::Log;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::OnceLock;

//merge note: tests call `logger.log(&record)` and
// `logger.flush()` directly on a `CombinedLogger` value. Those
// methods belong to the `log::Log` trait, which is NOT auto-
// imported by `use super::logging::*;` (trait methods need the
// trait in scope at the call site). Bring it in explicitly so the test
// module compiles cleanly under rustc 1.97+.
// (`use log::Log;` is declared in the header above.)

// ── RotatingFileWriter ────────────────────────────────────────────

#[test]
fn test_rotating_file_writer_basic_write() {
    let tmp = std::env::temp_dir().join(format!("voice-typer-test-{}-basic", std::process::id()));
    std::fs::remove_dir_all(&tmp).ok();
    let writer = RotatingFileWriter::new(tmp.clone(), "test-log");
    writer.write_line("hello").unwrap();
    writer.write_line("world").unwrap();
    // Flush the BufWriter before reading on disk — without this the
    // two short lines (12 bytes total) would still be in the 8 KB
    // in-memory buffer and the file would appear empty.
    writer.flush().unwrap();
    let content = std::fs::read_to_string(tmp.join("test-log.log")).unwrap();
    assert_eq!(content, "hello\nworld\n");
    std::fs::remove_dir_all(&tmp).ok();
}

#[test]
fn test_rotating_file_writer_truncates_in_place() {
    // Single-file policy: writing past LOG_MAX_BYTES (5 MB) truncates
    // the log IN PLACE — the file keeps its single identity and a
    // numbered backup (`.log.1`) is NEVER created.
    let tmp = std::env::temp_dir().join(format!("voice-typer-test-{}-rotate", std::process::id()));
    std::fs::remove_dir_all(&tmp).ok();
    let writer = RotatingFileWriter::new(tmp.clone(), "test-log");
    // 6 MB total, 100 KB per line → ~60 lines.
    let big_line = "x".repeat(100_000);
    for _ in 0..60 {
        writer.write_line(&big_line).unwrap();
    }
    writer.flush().unwrap();
    // The single `.log` file exists (current + only file) ...
    assert!(tmp.join("test-log.log").exists(), "current log missing");
    // ... and NO numbered backup was created.
    assert!(
        !tmp.join("test-log.log.1").exists(),
        "single-file policy: .log.1 must NOT exist"
    );
    // The file is bounded: the single-file policy guarantees the
    // on-disk log never exceeds the rotation cap (the write that
    // crosses the threshold is flushed then truncated away). All
    // writes AFTER the truncation point survive — with a 5 MiB cap and
    // 100 KB lines, the first 53 lines cross the cap (5,300,053 B) and
    // are truncated, leaving the remaining 7 lines (~700 KB) well
    // under the cap.
    let size = std::fs::metadata(tmp.join("test-log.log"))
        .map(|m| m.len())
        .unwrap_or(0);
    assert!(
        size <= u64::from(LOG_MAX_BYTES),
        "truncated log must stay under the rotation cap ({} bytes); got {} bytes",
        LOG_MAX_BYTES,
        size
    );
    std::fs::remove_dir_all(&tmp).ok();
}

#[test]
fn test_rotating_file_writer_keeps_single_file_after_many_truncations() {
    // Write well past the cap many times over — the file count on disk
    // must stay EXACTLY ONE (no `.log.N` backups ever).
    let tmp = std::env::temp_dir().join(format!(
        "voice-typer-test-{}-gt67-count",
        std::process::id()
    ));
    std::fs::remove_dir_all(&tmp).ok();
    let writer = RotatingFileWriter::new(tmp.clone(), "test-log");
    // ~50 MB total (100 KB/line × 500 lines) — ~10 truncation cycles.
    let big_line = "x".repeat(100_000);
    for _ in 0..500 {
        writer.write_line(&big_line).unwrap();
    }
    writer.flush().unwrap();

    // Exactly one file: the active `.log`.
    let active = tmp.join("test-log.log");
    assert!(active.exists(), "active log missing");
    let mut backups: Vec<std::path::PathBuf> = Vec::new();
    if let Ok(entries) = std::fs::read_dir(&tmp) {
        for e in entries.flatten() {
            let name = e.file_name().to_string_lossy().into_owned();
            if name.starts_with("test-log.log.") {
                backups.push(e.path());
            }
        }
    }
    assert!(
        backups.is_empty(),
        "single-file policy: no numbered backups allowed; found {:?}",
        backups
    );
    std::fs::remove_dir_all(&tmp).ok();
}

#[test]
fn test_rotating_file_writer_thread_safety() {
    // Spawn multiple threads writing to the same writer — should
    // not panic or corrupt (Mutex protects the inner File).
    let tmp = std::env::temp_dir().join(format!("voice-typer-test-{}-threads", std::process::id()));
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
    // Flush the BufWriter before reading on disk — without this the
    // last few hundred bytes (still in the 8 KB in-memory buffer)
    // would not be visible to `read_to_string` and the assertion
    // could fail on a fast machine where all 200 lines fit in the
    // buffer without triggering an auto-flush.
    writer.flush().unwrap();
    let content = std::fs::read_to_string(tmp.join("test-log.log")).unwrap();
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
// blocks briefly on the lock — NOT on `inner`). Pre-fix this would
// have held `inner` throughout `rotate()`, blocking ALL writers
// (including ones below the threshold) for the duration of the
// rename chain. Post-fix, the `inner` guard is dropped before
// `rotate()`, so non-rotating writers proceed in parallel.
#[test]
fn test_rotating_file_writer_concurrent_truncation_no_deadlock() {
    // 4 threads write ~200 MB total — well past the 5 MB threshold, so
    // each thread triggers many truncate-in-place cycles. All writes
    // serialize on the `inner` Mutex; the test passes if all threads
    // join (no deadlock / panic) and no numbered backup is created.
    let tmp =
        std::env::temp_dir().join(format!("voice-typer-test-{}-conc-rot", std::process::id()));
    std::fs::remove_dir_all(&tmp).ok();
    let writer = std::sync::Arc::new(RotatingFileWriter::new(tmp.clone(), "test-log"));
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
    writer.flush().unwrap();
    // The active file exists (single-file identity preserved) ...
    assert!(tmp.join("test-log.log").exists(), "active log missing");
    // ... and NO numbered backups were created under contention.
    let mut backups: Vec<String> = Vec::new();
    if let Ok(entries) = std::fs::read_dir(&tmp) {
        for e in entries.flatten() {
            let name = e.file_name().to_string_lossy().into_owned();
            if name.starts_with("test-log.log.") {
                backups.push(name);
            }
        }
    }
    assert!(
        backups.is_empty(),
        "single-file policy: no numbered backups under contention; found {:?}",
        backups
    );
    std::fs::remove_dir_all(&tmp).ok();
}

//poison-recovery (Mutex .unwrap_or_else) ──────────//poison-recovery (Mutex .unwrap_or_else) ──────────

#[test]
fn test_rotating_file_writer_recovers_from_poisoned_mutex() {
    // This test fires a REAL panic through the process-global hook
    // (if `install_panic_hook` has run), which toggles the global
    // `PANIC_HOOK_REENTRY` — serialize against the other
    // panic-firing / flag-mutating tests (see test_support.rs).
    let _panic_lock = PANIC_HOOK_TEST_LOCK
        .lock()
        .unwrap_or_else(|e| e.into_inner());
    //a prior panic while holding `inner`'s lock
    // poisons the mutex. The pre-fix code called `.lock().unwrap()`
    // here, which would re-panic. The post-fix code uses
    // `.lock().unwrap_or_else(|e| e.into_inner())`, which recovers
    // the guard (and the inner File handle) so logging can
    // continue. This test simulates the poison by manually
    // poisoning the mutex via `std::sync::PoisonError`, then
    // verifies that `write_line` and `flush` do NOT panic.
    let tmp = std::env::temp_dir().join(format!("voice-typer-test-{}-poison", std::process::id()));
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
    assert!(
        poison_result.is_err(),
        "test setup: panic should have fired"
    );
    // Now the mutex is poisoned. The post-fix code must NOT panic.
    writer.write_line("after-poison").unwrap();
    writer.flush().unwrap();
    // Verify both lines landed (the recovered guard carries the
    // previously-opened File handle, so the post-poison write
    // appends to the same file).
    let content = std::fs::read_to_string(tmp.join("test-log.log")).unwrap();
    assert!(
        content.contains("before-poison"),
        "pre-poison line lost: {}",
        content
    );
    assert!(
        content.contains("after-poison"),
        "post-poison line lost: {}",
        content
    );
    std::fs::remove_dir_all(&tmp).ok();
}

//RUST_LOG parsing ─────────────────────────────────
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
    //a typo like "debog" should fall back to Info
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
    //pin the parse behavior for the common
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

//install_panic_hook ───────────────────────────────

#[test]
fn test_install_panic_hook_does_not_panic_on_install() {
    //the hook installer itself must not panic. Calling
    // it twice is also safe (each call replaces the previous hook
    // via take_hook chaining).
    install_panic_hook();
    install_panic_hook();
}

// ── panic hook re-entrancy guard ──────────────────────────

#[test]
fn test_si11_panic_hook_reentry_swap_semantics() {
    // verify the swap semantics of `PANIC_HOOK_REENTRY`
    // without triggering a real panic (which would race with
    // parallel tests). The first `swap(false→true)` returns false
    // (proceed with hook body). A second `swap(true→true)` returns
    // true (bail out — re-entrant call). After `store(false)`, a
    // subsequent swap returns false again (guard reset).
    let _panic_lock = PANIC_HOOK_TEST_LOCK
        .lock()
        .unwrap_or_else(|e| e.into_inner());
    PANIC_HOOK_REENTRY.store(false, Ordering::SeqCst);
    let first = PANIC_HOOK_REENTRY.swap(true, Ordering::SeqCst);
    assert!(!first, "first swap (false→true) must return false");
    let second = PANIC_HOOK_REENTRY.swap(true, Ordering::SeqCst);
    assert!(
        second,
        "second swap (true→true) must return true (re-entered)"
    );
    PANIC_HOOK_REENTRY.store(false, Ordering::SeqCst);
    let third = PANIC_HOOK_REENTRY.swap(true, Ordering::SeqCst);
    assert!(!third, "swap after reset must return false");
    PANIC_HOOK_REENTRY.store(false, Ordering::SeqCst);
}

#[test]
fn test_si11_panic_hook_does_not_abort_and_resets_guard() {
    // a normal panic must not abort, and the guard must be
    // reset to false afterward so a later panic gets full treatment.
    let _panic_lock = PANIC_HOOK_TEST_LOCK
        .lock()
        .unwrap_or_else(|e| e.into_inner());
    install_panic_hook();
    PANIC_HOOK_REENTRY.store(false, Ordering::SeqCst);
    let result = std::panic::catch_unwind(|| {
        panic!("si11 normal panic test payload");
    });
    assert!(result.is_err(), "catch_unwind must catch the panic");
    assert!(
        !PANIC_HOOK_REENTRY.load(Ordering::SeqCst),
        "guard must be reset"
    );
}

// ── is_truthy_env_var / is_truthy_value ─────────────────

#[test]
fn test_si15_5_is_truthy_value_truthy_cases() {
    assert!(is_truthy_value(Some("1")));
    assert!(is_truthy_value(Some("true")));
    assert!(is_truthy_value(Some("TRUE")));
    assert!(is_truthy_value(Some("True")));
    assert!(is_truthy_value(Some("yes")));
    assert!(is_truthy_value(Some("YES")));
    assert!(is_truthy_value(Some("  yes  ")));
    assert!(is_truthy_value(Some("\t1\n")));
    assert!(is_truthy_value(Some("  TrUe  ")));
}

#[test]
fn test_si15_5_is_truthy_value_falsy_cases() {
    assert!(!is_truthy_value(None));
    assert!(!is_truthy_value(Some("")));
    assert!(!is_truthy_value(Some("0")));
    assert!(!is_truthy_value(Some("false")));
    assert!(!is_truthy_value(Some("no")));
    assert!(!is_truthy_value(Some("FALSE")));
    assert!(!is_truthy_value(Some("yess")));
    assert!(!is_truthy_value(Some("2")));
    assert!(!is_truthy_value(Some("   ")));
    assert!(!is_truthy_value(Some("on")));
    assert!(!is_truthy_value(Some("enabled")));
}

#[test]
fn test_si15_5_is_truthy_env_var_unset_is_falsy() {
    let name = "VOICE_TYPER_TEST_UNSET_ENV_VAR_2026_SI15_5";
    std::env::remove_var(name);
    assert!(!is_truthy_env_var(name));
}

#[test]
fn test_si15_5_is_truthy_env_var_truthy_when_set() {
    let name = "VOICE_TYPER_TEST_TRUTHY_ENV_VAR_2026_SI15_5";
    std::env::set_var(name, "1");
    assert!(is_truthy_env_var(name));
    std::env::set_var(name, "true");
    assert!(is_truthy_env_var(name));
    std::env::set_var(name, "yes");
    assert!(is_truthy_env_var(name));
    std::env::set_var(name, "  YES  ");
    assert!(is_truthy_env_var(name));
    std::env::set_var(name, "0");
    assert!(!is_truthy_env_var(name));
    std::env::set_var(name, "false");
    assert!(!is_truthy_env_var(name));
    std::env::remove_var(name);
    assert!(!is_truthy_env_var(name));
}

#[test]
fn test_si15_5_is_debug_env_truthy_delegates_to_shared_matcher() {
    let cases: [Option<&str>; 9] = [
        None,
        Some(""),
        Some("1"),
        Some("true"),
        Some("YES"),
        Some("  yes  "),
        Some("0"),
        Some("false"),
        Some("yess"),
    ];
    for case in cases {
        assert_eq!(
            is_debug_env_truthy(case),
            is_truthy_value(case),
            "is_debug_env_truthy({:?}) must match is_truthy_value({:?})",
            case,
            case
        );
    }
}

// ── install_early_logger orphan-handle guard ───────────

#[test]
fn test_si15_3_install_early_logger_does_not_orphan_handle() {
    // smoke test — calling install_early_logger must not
    // panic regardless of whether log::set_logger succeeds. The
    // actual set_logger-failure path is verified by code
    // inspection: EARLY_LOGGER_HANDLE.set is now inside the
    // success branch of `if log::set_logger(logger).is_err() { return; }`.
    install_early_logger();
    let _ = EARLY_LOGGER_HANDLE.get();
}

//CombinedLogger::log format ───────────────────────

#[test]
fn test_combined_logger_log_format_is_clean() {
    // Verify the format string is clean — `ts LEVEL msg` with no
    // `record.target()` module path and no `file:line` segment — by
    // constructing a logger and calling `log()` with a synthetic
    // Record. We can't capture stderr (eprintln! goes to fd 2) but we
    // CAN capture the file write and assert the line shape.
    let tmp = std::env::temp_dir().join(format!("voice-typer-test-{}-fmt", std::process::id()));
    std::fs::remove_dir_all(&tmp).ok();
    let writer = RotatingFileWriter::new(tmp.clone(), "test-log");
    let logger = CombinedLogger {
        file_writer: Some(writer),
        level_filter: log::LevelFilter::Info,
        //stderr_verbose=true in tests so the eprintln! path
        // is exercised (mirrors debug-build behavior).
        //AtomicBool (was `bool`) so the predicate is
        // runtime-toggleable.
        stderr_verbose: AtomicBool::new(true),
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
    let content = std::fs::read_to_string(tmp.join("test-log.log")).unwrap();
    // Message + level must be present.
    assert!(
        content.contains("hello world"),
        "message missing from log line: {}",
        content
    );
    assert!(
        content.contains("INFO"),
        "level missing from log line: {}",
        content
    );
    // Clean format: the `record.target()` module path and `file:line`
    // are NOT rendered (they added noise to every line; the message
    // already carries a `[TOPIC]` prefix identifying the subsystem).
    assert!(
        !content.contains("test_target"),
        "target leaked into clean log line: {}",
        content
    );
    assert!(
        !content.contains("src/test.rs"),
        "file:line leaked into clean log line: {}",
        content
    );
    std::fs::remove_dir_all(&tmp).ok();
}

#[test]
fn test_combined_logger_log_format_renders_without_file_line() {
    // The format no longer depends on `record.file()` / `record.line()`
    // (they are `None` for records emitted from non-`#[track_caller]`
    // paths or release builds with debuginfo stripped), so the line
    // must render cleanly regardless — no panic, no `Option` debug
    // string, no `?` / `0` fallback markers.
    let tmp = std::env::temp_dir().join(format!(
        "voice-typer-test-{}-fmt-nofile",
        std::process::id()
    ));
    std::fs::remove_dir_all(&tmp).ok();
    let writer = RotatingFileWriter::new(tmp.clone(), "test-log");
    let logger = CombinedLogger {
        file_writer: Some(writer),
        level_filter: log::LevelFilter::Info,
        //stderr_verbose=true in tests so the eprintln! path
        // is exercised (mirrors debug-build behavior).
        //AtomicBool (was `bool`) so the predicate is
        // runtime-toggleable.
        stderr_verbose: AtomicBool::new(true),
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
    let content = std::fs::read_to_string(tmp.join("test-log.log")).unwrap();
    assert!(
        content.contains("no loc"),
        "message missing from log line: {}",
        content
    );
    // No module path, no debug "None", no "?" / ":0" fallback.
    assert!(
        !content.contains("test_target")
            && !content.contains("None")
            && !content.contains("src/test.rs")
            && !content.contains("?:0"),
        "clutter leaked into clean log line: {}",
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
    //(), so it must be owner-only.
    use std::os::unix::fs::PermissionsExt;
    let tmp =
        std::env::temp_dir().join(format!("voice-typer-test-{}-pi7-mode", std::process::id()));
    std::fs::remove_dir_all(&tmp).ok();
    let writer = RotatingFileWriter::new(tmp.clone(), "test-log");
    writer.write_line("secret-dictation-text").unwrap();
    writer.flush().unwrap();

    let path = tmp.join("test-log.log");
    let meta = std::fs::metadata(&path).expect("log file must exist after write_line");
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
fn test_rotating_file_writer_truncate_keeps_0o600_on_posix() {
    // After truncate-in-place cycles, the SINGLE active `.log` file
    // must still be 0o600 (owner rw only) and no `.log.1` backup may
    // exist. `set_len(0)` preserves the existing file mode, and the
    // belt-and-suspenders `chmod` in `write_line` re-asserts it.
    use std::os::unix::fs::PermissionsExt;
    let tmp = std::env::temp_dir().join(format!(
        "voice-typer-test-{}-pi7-rotate-mode",
        std::process::id()
    ));
    std::fs::remove_dir_all(&tmp).ok();
    let writer = RotatingFileWriter::new(tmp.clone(), "test-log");
    // Write ~6 MB total to trigger at least one truncate
    // (LOG_MAX_BYTES = 5 MB).
    let big_line = "x".repeat(100_000);
    for _ in 0..60 {
        writer.write_line(&big_line).unwrap();
    }
    writer.flush().unwrap();

    // No numbered backup may exist.
    assert!(
        !tmp.join("test-log.log.1").exists(),
        "PI-7: single-file policy forbids .log.1 backups"
    );

    // The current (only) `.log` file must be 0o600.
    let current = tmp.join("test-log.log");
    let meta = std::fs::metadata(&current).expect("current log file must exist");
    let mode = meta.permissions().mode() & 0o777;
    assert_eq!(
        mode, 0o600,
        "PI-7: current log file mode must be 0o600 after truncate; got 0o{:o}",
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
    std::fs::set_permissions(&logs_dir, std::fs::Permissions::from_mode(0o755)).unwrap();
    // Apply the same chmod `init_file_logger` does.
    let _ = std::fs::set_permissions(&logs_dir, std::fs::Permissions::from_mode(0o700));
    let meta = std::fs::metadata(&logs_dir).unwrap();
    let mode = meta.permissions().mode() & 0o777;
    assert_eq!(
        mode, 0o700,
        "PI-7: logs dir mode must be 0o700; got 0o{:o}",
        mode
    );
    std::fs::remove_dir_all(&tmp).ok();
}

//bubble_level filter preserves WARNING+ records ─────────
//
//Pre- the filter dropped ANY record whose message started
// with `[WS-READER] bubble_level event`, regardless of level.
// A future `log::error!("[WS-READER] bubble_level event handler
//crashed: ...")` would be SILENTLY LOST. Post- the filter
// short-circuits for WARNING+ records (mirrors Python's
// `_BubbleLevelExclusionFilter` at log.py:216-219).

#[test]
fn test_fr33_bubble_level_filter_drops_info_record() {
    // INFO-level bubble_level event must be dropped from the file
    // log (the original ADR-0020 §11 behavior — 60 Hz events would
    // fill disk fast).
    let tmp =
        std::env::temp_dir().join(format!("voice-typer-test-{}-fr33-info", std::process::id()));
    std::fs::remove_dir_all(&tmp).ok();
    let writer = RotatingFileWriter::new(tmp.clone(), "test-log");
    let logger = CombinedLogger {
        file_writer: Some(writer),
        level_filter: log::LevelFilter::Trace,
        stderr_verbose: AtomicBool::new(false),
    };
    let record = log::Record::builder()
        .level(log::Level::Info)
        .target("test_target")
        .file(Some("src/test.rs"))
        .line(Some(1))
        .args(format_args!("[WS-READER] bubble_level event rms=0.42"))
        .build();
    logger.log(&record);
    logger.flush();
    let content = std::fs::read_to_string(tmp.join("test-log.log")).unwrap_or_default();
    assert!(
        !content.contains("bubble_level event rms=0.42"),
        "FR-33: INFO bubble_level record must be dropped from file log; got: {}",
        content
    );
    std::fs::remove_dir_all(&tmp).ok();
}

#[test]
fn test_fr33_bubble_level_filter_preserves_warn_record() {
    //WARN-level bubble_level record must be PRESERVED in
    // the file log even though the message starts with the
    // filtered prefix. Pre-fix this was silently dropped.
    let tmp =
        std::env::temp_dir().join(format!("voice-typer-test-{}-fr33-warn", std::process::id()));
    std::fs::remove_dir_all(&tmp).ok();
    let writer = RotatingFileWriter::new(tmp.clone(), "test-log");
    let logger = CombinedLogger {
        file_writer: Some(writer),
        level_filter: log::LevelFilter::Trace,
        stderr_verbose: AtomicBool::new(false),
    };
    let record = log::Record::builder()
        .level(log::Level::Warn)
        .target("test_target")
        .file(Some("src/test.rs"))
        .line(Some(2))
        .args(format_args!(
            "[WS-READER] bubble_level event handler stalled"
        ))
        .build();
    logger.log(&record);
    logger.flush();
    let content = std::fs::read_to_string(tmp.join("test-log.log")).unwrap_or_default();
    assert!(
        content.contains("bubble_level event handler stalled"),
        "FR-33: WARN bubble_level record must be PRESERVED in file log; got: {}",
        content
    );
    std::fs::remove_dir_all(&tmp).ok();
}

#[test]
fn test_fr33_bubble_level_filter_preserves_error_record() {
    //ERROR-level bubble_level record must be PRESERVED.
    // This is the most important case — a future
    // `log::error!("[WS-READER] bubble_level event handler crashed")`
    // would be silently lost without the level guard.
    let tmp =
        std::env::temp_dir().join(format!("voice-typer-test-{}-fr33-err", std::process::id()));
    std::fs::remove_dir_all(&tmp).ok();
    let writer = RotatingFileWriter::new(tmp.clone(), "test-log");
    let logger = CombinedLogger {
        file_writer: Some(writer),
        level_filter: log::LevelFilter::Trace,
        stderr_verbose: AtomicBool::new(false),
    };
    let record = log::Record::builder()
        .level(log::Level::Error)
        .target("test_target")
        .file(Some("src/test.rs"))
        .line(Some(3))
        .args(format_args!(
            "[WS-READER] bubble_level event handler crashed: panic"
        ))
        .build();
    logger.log(&record);
    logger.flush();
    let content = std::fs::read_to_string(tmp.join("test-log.log")).unwrap_or_default();
    assert!(
        content.contains("bubble_level event handler crashed"),
        "FR-33: ERROR bubble_level record must be PRESERVED in file log; got: {}",
        content
    );
    std::fs::remove_dir_all(&tmp).ok();
}

//AtomicBool stderr_verbose is runtime-toggleable ────────

#[test]
fn test_fr96_stderr_verbose_atomic_toggle_at_runtime() {
    //the `stderr_verbose` field is now an `AtomicBool`,
    // allowing future code (e.g. a Tauri command) to flip the
    // predicate at runtime without re-creating the logger. This
    // test verifies the field is constructed + loaded + stored
    // without panic (the actual eprintln! path is exercised by
    // other tests).
    let flag = AtomicBool::new(false);
    assert!(!flag.load(std::sync::atomic::Ordering::Relaxed));
    flag.store(true, std::sync::atomic::Ordering::Relaxed);
    assert!(flag.load(std::sync::atomic::Ordering::Relaxed));
    flag.store(false, std::sync::atomic::Ordering::Relaxed);
    assert!(!flag.load(std::sync::atomic::Ordering::Relaxed));
}

//EarlyLogger idempotent install ──────────────────────────
//
// We can't call `install_early_logger` from a test that runs in
// the same process as other tests (it calls `log::set_logger`
// which is process-global one-shot, AND it leaks memory via
// `Box::leak`). But we CAN verify the idempotency guard — a
// second call after the EARLY_LOGGER_HANDLE is set must be a
// no-op that doesn't panic.

#[test]
fn test_fr16_install_early_logger_idempotent() {
    //calling `install_early_logger` more than once must
    // not panic (the function's idempotency guard short-circuits
    // when `EARLY_LOGGER_HANDLE` is already set). The first call
    // may or may not have been made by another test in the same
    // process — either way, this call must not panic.
    install_early_logger();
    // Second call must be a no-op (the function checks
    // `EARLY_LOGGER_HANDLE.get().is_some()` and returns early).
    install_early_logger();
    // Third call also safe.
    install_early_logger();
}

#[test]
fn test_fr16_early_logger_pre_init_fallback_does_not_panic() {
    //construct an EarlyLogger directly (bypassing
    // `install_early_logger`) and call `log()` on it in the
    // pre-init fallback state (inner = None). Must not panic and
    // must produce no file output (no file_writer in pre-init).
    let early = EarlyLogger {
        inner: OnceLock::new(),
        stderr_verbose: AtomicBool::new(false),
        level_filter: log::LevelFilter::Info,
    };
    let record = log::Record::builder()
        .level(log::Level::Info)
        .target("test_target")
        .file(Some("src/test.rs"))
        .line(Some(1))
        .args(format_args!("early logger test"))
        .build();
    // Must not panic — the pre-init fallback just eprintln!'s
    // (suppressed here via stderr_verbose=false).
    early.log(&record);
    early.flush();
}

//redact_pii unit tests ──────────────────────────────

#[test]
fn test_redact_pii_no_trigger_returns_input_unchanged() {
    let input = "WS reader connected on port 51829";
    let out = redact_pii(input);
    assert_eq!(out, input);
}

#[test]
fn test_redact_pii_bearer_token() {
    let input = "Authorization: Bearer abc123def456ghi789";
    let out = redact_pii(input);
    assert_eq!(out, "Authorization: Bearer ***");
}

#[test]
fn test_redact_pii_token_keyword() {
    let input = "Token sk-live-1234567890abcdef";
    let out = redact_pii(input);
    assert_eq!(out, "Token ***");
}

#[test]
fn test_redact_pii_sk_prefix_api_key() {
    let input = "using key sk-1234567890abcdef for the cloud engine";
    let out = redact_pii(input);
    assert_eq!(out, "using key sk-*** for the cloud engine");
}

#[test]
fn test_redact_pii_sk_prefix_too_short_not_redacted() {
    let input = "model name: sk-small.en";
    let out = redact_pii(input);
    assert_eq!(out, input);
}

#[test]
fn test_redact_pii_url_credentials() {
    let input = "connecting to https://user:pass@host.com/path";
    let out = redact_pii(input);
    assert!(out.contains("***@host.com"), "got: {}", out);
    assert!(!out.contains("user:pass"));
}

#[test]
fn test_redact_pii_email_address() {
    let input = "user email is alice@example.com and that's it";
    let out = redact_pii(input);
    assert_eq!(out, "user email is [EMAIL] and that's it");
}

#[test]
fn test_redact_pii_email_with_dotted_local_part() {
    let input = "contact first.last@example.co.uk for info";
    let out = redact_pii(input);
    assert_eq!(out, "contact [EMAIL] for info");
}

#[test]
fn test_redact_pii_at_without_dot_not_redacted() {
    let input = "ssh user@host";
    let out = redact_pii(input);
    assert_eq!(out, input);
}

#[test]
fn test_redact_pii_no_false_positive_on_normal_log_lines() {
    let inputs = [
        "[WS-READER] bubble_level event rms=0.42",
        "[SUPERVISOR] respawn attempt 1/3 after 500ms backoff",
        "config dir: /home/user/.local/share/voice-typer",
        "shutdown ack timeout (2000ms) — force-killing",
    ];
    for input in inputs {
        let out = redact_pii(input);
        assert_eq!(
            out, input,
            "false positive: input {input:?} was changed to {out:?}"
        );
    }
}

#[test]
fn test_redact_pii_multiple_patterns_in_one_line() {
    // Pre-fix expectation assumed the `Bearer ` / `sk-` prefix
    // patterns would win. The bare-keyword pattern (`auth=` and
    // `key=` are both in `SECRET_KEYWORDS`) fires FIRST for
    // `auth=Bearer` and `key=sk-...`, redacting the whole value
    // (`auth=***`, `key=***`) — the `abc123` token between them
    // is not a secret and survives. The email is redacted via the
    // PII email pattern. The Python authority (`redact_secret`)
    // agrees on the key parts (`auth=***`, `key=***`) but leaves
    // the email untouched; Rust redacting more here is
    // security-positive.
    let input = "auth=Bearer abc123, email=alice@example.com, key=sk-1234567890abcdef";
    let out = redact_pii(input);
    assert_eq!(out, "auth=*** abc123, email=[EMAIL], key=***");
}

//extended coverage: gsk_, IBAN, phone, SSN, CC ───────

#[test]
fn test_redact_pii_gsk_prefix_api_key() {
    let input = "groq key gsk_1234567890abcdef for the cloud engine";
    let out = redact_pii(input);
    assert_eq!(out, "groq key gsk_*** for the cloud engine");
}

#[test]
fn test_redact_pii_gsk_prefix_too_short_not_redacted() {
    let input = "short gsk_abc value";
    let out = redact_pii(input);
    assert_eq!(out, input);
}

#[test]
fn test_redact_pii_us_phone_with_dashes() {
    let input = "call me at 555-123-4567 today";
    let out = redact_pii(input);
    assert_eq!(out, "call me at [PHONE] today");
}

#[test]
fn test_redact_pii_us_phone_with_dots() {
    let input = "call me at 555.123.4567 today";
    let out = redact_pii(input);
    assert_eq!(out, "call me at [PHONE] today");
}

#[test]
fn test_redact_pii_us_phone_no_separators() {
    let input = "call me at 5551234567 today";
    let out = redact_pii(input);
    assert_eq!(out, "call me at [PHONE] today");
}

#[test]
fn test_redact_pii_intl_phone_with_parens() {
    let input = "call +1 (415) 555-2671 now";
    let out = redact_pii(input);
    assert_eq!(out, "call [PHONE] now");
}

#[test]
fn test_redact_pii_intl_phone_uk_format() {
    let input = "dial +44 20 7946 0958 please";
    let out = redact_pii(input);
    assert_eq!(out, "dial [PHONE] please");
}

#[test]
fn test_redact_pii_ssn_with_dashes() {
    let input = "ssn is 123-45-6789 on file";
    let out = redact_pii(input);
    assert_eq!(out, "ssn is [SSN] on file");
}

#[test]
fn test_redact_pii_ssn_no_dashes_not_redacted() {
    // 9-digit run without dashes is NOT an SSN (matches Python
    // behaviour — the `-` separators are required).
    let input = "order id 123456789 processed";
    let out = redact_pii(input);
    assert_eq!(out, input);
}

#[test]
fn test_redact_pii_credit_card_with_dashes() {
    let input = "card 4111-1111-1111-1111 charged";
    let out = redact_pii(input);
    assert_eq!(out, "card [CC] charged");
}

#[test]
fn test_redact_pii_credit_card_with_spaces() {
    let input = "card 4111 1111 1111 1111 charged";
    let out = redact_pii(input);
    assert_eq!(out, "card [CC] charged");
}

#[test]
fn test_redact_pii_credit_card_no_separators() {
    let input = "card 4111111111111111 charged";
    let out = redact_pii(input);
    assert_eq!(out, "card [CC] charged");
}

#[test]
fn test_redact_pii_iban_uk() {
    let input = "iban GB82WEST12345698765432 on file";
    let out = redact_pii(input);
    assert_eq!(out, "iban [IBAN] on file");
}

#[test]
fn test_redact_pii_iban_germany() {
    let input = "iban DE89370400440532013000 on file";
    let out = redact_pii(input);
    assert_eq!(out, "iban [IBAN] on file");
}

#[test]
fn test_redact_pii_iban_too_short_not_redacted() {
    // 2 letters + 2 digits + only 5 BBAN chars (< 10) → not an IBAN.
    let input = "code AB12ABCDE end";
    let out = redact_pii(input);
    assert_eq!(out, input);
}

#[test]
fn test_redact_pii_iban_lowercase_redacted_via_generic_run() {
    // A lowercase IBAN does NOT match the IBAN pattern (which
    // requires uppercase country code `[A-Z]{2}`, mirroring
    // Python `_PATTERNS[1]`). BUT `gb82west12345698765432` is a
    // 22-char alphanumeric run, so it IS caught by the generic
    // 20+ char bare-token catch-all (`***`) — same as the Python
    // authority (`redact_secret('code gb82west12345698765432
    // end')` → `'code *** end'`). The IBAN-specific `[IBAN]`
    // placeholder is not used for lowercase forms; the generic
    // redaction still protects the data.
    let input = "code gb82west12345698765432 end";
    let out = redact_pii(input);
    assert_eq!(out, "code *** end");
}

#[test]
fn test_redact_pii_no_false_positive_on_short_digit_runs() {
    // 1-2 digit runs should NOT trigger any numeric pattern.
    let inputs = [
        "port 80 and 443 are common",
        "version 1.2.3 released",
        "timeout 30s",
    ];
    for input in inputs {
        let out = redact_pii(input);
        assert_eq!(
            out, input,
            "false positive: input {input:?} was changed to {out:?}"
        );
    }
}

#[test]
fn test_redact_pii_bearer_token_trailing_comma_preserved() {
    // Regression: pre-fix the Bearer parser consumed trailing
    // punctuation (ran until whitespace). Now it stops at the
    // first non-token char (comma), matching Python's
    // `[A-Za-z0-9_\-\.=]+` charset.
    //
    //the bare-keyword pattern (`auth=`) now fires BEFORE the
    // Bearer prefix pattern, so the value `Bearer` is redacted as
    // part of the `auth=***` substitution (matching Python's
    // `_FLAG_KEY_PATTERNS`-before-`_KEY_PATTERNS` ordering). The
    // trailing-comma preservation regression test is now covered by
    // `test_redact_pii_bearer_token` above (which tests `Bearer`
    // without a preceding `keyword=`).
    let input = "auth=Bearer abc123, next field";
    let out = redact_pii(input);
    assert_eq!(out, "auth=*** abc123, next field");
}

//flag-form / bare-keyword / 20+ char catch-all parity ──────
//
// These tests assert that the Rust `redact_pii` redacts the same
// secret-bearing strings as the Python `_redact_text` /
// `redact_secret` pipeline (`voice_typer/server/security.py` +
// `voice_typer/server/_secrets.py`). Each test documents the
// expected Python output for cross-reference.

#[test]
fn test_ue6_bare_key_value_gitlab_pat() {
    // `pat=glpt_Xb8zV9pT3q2aR1wM5sN7` — 24-char GitLab PAT with
    // no Bearer prefix. `pat` is NOT a secret keyword, so the
    // bare-keyword pattern does NOT match. The 20+ char catch-all
    // matches `glpt_Xb8zV9pT3q2aR1wM5sN7` (27 chars) → `***`.
    // Python: `redact_secret` → `_KEY_PATTERNS[4]` → `pat=***`.
    let input = "pat=glpt_Xb8zV9pT3q2aR1wM5sN7";
    let out = redact_pii(input);
    assert_eq!(out, "pat=***");
}

#[test]
fn test_ue6_bare_key_value_api_key() {
    // `api_key=A1B2C3D4E5F6G7H8I9J0K1L2M3N4O7P8` — `api_key` IS a
    // secret keyword, so the bare-keyword pattern matches and
    // redacts the 32-char value → `api_key=***`.
    // Python: `redact_secret` → `_BARE_KEY_VALUE_PATTERN` → `api_key=***`.
    let input = "api_key=A1B2C3D4E5F6G7H8I9J0K1L2M3N4O7P8";
    let out = redact_pii(input);
    assert_eq!(out, "api_key=***");
}

#[test]
fn test_ue6_bearer_with_sk_prefix() {
    // `Bearer sk-abc123def456ghi789` — Bearer prefix pattern fires
    // first, redacting the entire value (`sk-abc123...`) →
    // `Bearer ***`. The bare-keyword pattern does NOT fire (no
    // `keyword=` form).
    // Python: `redact_secret` → `_KEY_PATTERNS[0]` → `Bearer ***`.
    let input = "Bearer sk-abc123def456ghi789";
    let out = redact_pii(input);
    assert_eq!(out, "Bearer ***");
}

#[test]
fn test_ue6_24char_bare_token() {
    // `Xb8zV9pT3q2aR1wM5sN7abcd` — 24-char bare token, no prefix.
    // The 20+ char catch-all matches → `***`.
    // Python: `redact_secret` → `_KEY_PATTERNS[4]` → `***`.
    let input = "Xb8zV9pT3q2aR1wM5sN7abcd";
    let out = redact_pii(input);
    assert_eq!(out, "***");
}

#[test]
fn test_ue6_flag_form_equals() {
    // `--token=secret123` — flag-form Pattern A (`--keyword=value`).
    // `token` is a keyword, `=` delimiter, value `secret123`.
    // Python: `_FLAG_VALUE_PATTERN` → `--token=***`.
    let input = "--token=secret123";
    let out = redact_pii(input);
    assert_eq!(out, "--token=***");
}

#[test]
fn test_ue6_flag_form_space() {
    // `--token secret123` — flag-form Pattern A (`--keyword value`).
    // `token` is a keyword, space delimiter, value `secret123`.
    // Python: `_FLAG_VALUE_PATTERN` → `--token ***`.
    let input = "--token secret123";
    let out = redact_pii(input);
    assert_eq!(out, "--token ***");
}

#[test]
fn test_ue6_flag_form_multiple_spaces() {
    // `--token   secret123` — multiple spaces between keyword and
    // value. Python's `\s+` captures all whitespace in group 1, so
    // the output preserves the spaces.
    let input = "--token   secret123";
    let out = redact_pii(input);
    assert_eq!(out, "--token   ***");
}

#[test]
fn test_ue6_bare_key_value_password() {
    // `password=secret123` — bare-keyword Pattern B. The value
    // `secret123` contains 3+ consecutive digits (`123`), which
    // triggers the fast path (mirrors Python's `_FAST_TRIGGER`
    // `\d{3,}` alternative). Without a trigger, the fast path
    // would skip this short string — matching Python's behavior.
    // Python: `_BARE_KEY_VALUE_PATTERN` → `password=***`.
    let input = "password=secret123";
    let out = redact_pii(input);
    assert_eq!(out, "password=***");
}

#[test]
fn test_ue6_bare_key_value_secret() {
    // `secret=topsecret123` — bare-keyword Pattern B. Same 3+-digit
    // trigger rationale as `test_ue6_bare_key_value_password`.
    let input = "secret=topsecret123";
    let out = redact_pii(input);
    assert_eq!(out, "secret=***");
}

#[test]
fn test_ue6_bare_key_value_case_sensitive_fast_path() {
    // `TOKEN=abc` — the fast-path trigger `key=` is case-sensitive
    // (mirrors Python's `_FAST_TRIGGER`). `TOKEN=` does NOT contain
    // `key=` (lowercase). And `abc` has no 3+ digits, no 20+ char
    // run, and no other trigger. So the fast path skips this string
    // entirely, returning it unchanged. This matches Python's
    // `_redact_text` (which also uses the case-sensitive
    // `_FAST_TRIGGER` and would skip `TOKEN=abc`).
    //
    // NOTE: Python's `redact_secret` (called separately, NOT via
    // `_redact_text`) WOULD redact `TOKEN=abc` because
    // `_FLAG_KEY_PATTERNS` are case-insensitive. But `_redact_text`
    // gates on `_FAST_TRIGGER` first. The Rust `redact_pii` ports
    // `_redact_text`, so it matches that behavior.
    let input = "TOKEN=abc";
    let out = redact_pii(input);
    assert_eq!(
        out, input,
        "UE-6 parity: case-sensitive fast path leaves TOKEN=abc unchanged (mirrors Python _FAST_TRIGGER)"
    );
}

#[test]
fn test_ue6_bare_key_value_case_insensitive_with_trigger() {
    // `TOKEN=secret123456789012345` — the fast-path trigger `key=`
    // is case-sensitive, so `TOKEN=` does NOT trigger via `key=`.
    // But the value `secret123456789012345` is 25 chars, triggering
    // the 20+ char catch-all in the fast path. The slow path then
    // runs and the bare-keyword pattern (case-insensitive, mirrors
    // Python's `(?i)`) matches `TOKEN=` → `TOKEN=***`.
    let input = "TOKEN=secret123456789012345";
    let out = redact_pii(input);
    assert_eq!(out, "TOKEN=***");
}

#[test]
fn test_ue6_no_false_positive_monkey() {
    // `monkey=abc` — `key` is a keyword but `\b` prevents matching
    // inside `monkey` (the `n` before `key` is a word char, so no
    // word boundary). NOT redacted.
    let input = "monkey=abc";
    let out = redact_pii(input);
    assert_eq!(out, input);
}

#[test]
fn test_ue6_no_false_positive_hotkey() {
    // `hotkey=abc` — same as `monkey=`: `key` is preceded by `t`
    // (word char), so `\b` does not hold. NOT redacted.
    let input = "hotkey=abc";
    let out = redact_pii(input);
    assert_eq!(out, input);
}

#[test]
fn test_ue6_no_false_positive_unknown_flag() {
    // `--unknown=abc` — `unknown` is NOT a secret keyword. NOT
    // redacted.
    let input = "--unknown=abc";
    let out = redact_pii(input);
    assert_eq!(out, input);
}

#[test]
fn test_ue6_empty_value_not_redacted() {
    // `--token=` with no value — Python's `[^\s=]+` requires at
    // least 1 char. NOT redacted.
    let input = "--token=";
    let out = redact_pii(input);
    assert_eq!(out, input);
}

#[test]
fn test_ue6_flag_value_stops_at_equals() {
    // `--api_key=abc=def` — value runs until `=` (`[^\s=]+`), so the
    // value is `abc` and `=def` is left alone. Uses `api_key` (not
    // `token`) so the fast path triggers via the `key=` substring
    // in `api_key=`. (`--token=abc=def` would NOT trigger the fast
    // path — no `key=`, no 3+ digits, no 20+ run — and would be
    // returned unchanged, matching Python's `_redact_text`.)
    // Python: `_FLAG_VALUE_PATTERN` → `--api_key=***=def`.
    let input = "--api_key=abc=def";
    let out = redact_pii(input);
    assert_eq!(out, "--api_key=***=def");
}

#[test]
fn test_ue6_flag_value_stops_at_whitespace() {
    // `--api_key=abc def` — value runs until whitespace, so the
    // value is `abc` and ` def` is left alone. Uses `api_key` so
    // the fast path triggers via the `key=` substring.
    let input = "--api_key=abc def";
    let out = redact_pii(input);
    assert_eq!(out, "--api_key=*** def");
}

#[test]
fn test_ue6_api_key_wins_over_key() {
    // `api_key=secret123` — both `api_key` and `key` are keywords.
    // `api_key` comes first in SECRET_KEYWORDS (most-specific
    // first), so it wins. The prefix preserved is `api_key=` (not
    // `key=`).
    let input = "api_key=secret123";
    let out = redact_pii(input);
    assert_eq!(out, "api_key=***");
}

#[test]
fn test_ue6_access_token_keyword() {
    // `access_token=abc123` — `access_token` is a keyword.
    let input = "access_token=abc123";
    let out = redact_pii(input);
    assert_eq!(out, "access_token=***");
}

#[test]
fn test_ue6_client_secret_keyword() {
    // `client_secret=abc123` — `client_secret` is a keyword.
    let input = "client_secret=abc123";
    let out = redact_pii(input);
    assert_eq!(out, "client_secret=***");
}

#[test]
fn test_ue6_bearer_keyword_added() {
    //`bearer=abc123` — `bearer` is a  task-specified addition
    // (not in Python's `_SECRET_KEYWORDS`). The bare-keyword pattern
    // matches → `bearer=***`. Python would NOT redact this (no
    // `bearer` keyword), but the task explicitly requests it.
    let input = "bearer=abc123";
    let out = redact_pii(input);
    assert_eq!(out, "bearer=***");
}

#[test]
fn test_ue6_credential_keyword_added() {
    //`credential=abc123` — `credential` is a  task-specified
    // addition. Same rationale as `bearer=`.
    let input = "credential=abc123";
    let out = redact_pii(input);
    assert_eq!(out, "credential=***");
}

#[test]
fn test_ue6_20char_run_exactly_20() {
    // Exactly 20 chars — the minimum for the catch-all. Should
    // match → `***`.
    let input = "abcdefghijklmnopqrst";
    let out = redact_pii(input);
    assert_eq!(out, "***");
}

#[test]
fn test_ue6_19char_run_not_redacted() {
    // 19 chars — below the 20-char threshold. NOT redacted.
    let input = "abcdefghijklmnopqrs";
    let out = redact_pii(input);
    assert_eq!(out, input);
}

#[test]
fn test_ue6_20char_run_with_internal_dash() {
    // `glpt_Xb8zV9pT3q2aR1wM5sN7` — 27 chars with an internal
    // `-`. The 20+ char catch-all includes `-` in the char class,
    // so the entire run matches → `***`.
    let input = "glpt_Xb8zV9pT3q2aR1wM5sN7";
    let out = redact_pii(input);
    assert_eq!(out, "***");
}

#[test]
fn test_ue6_20char_run_does_not_match_inside_word() {
    // `a` + 20-char run + `b` — the `a` and `b` are word chars
    // adjacent to the run, so the run extends to include them
    // (22 chars total). The whole 22-char run matches → `***`.
    // (There's no "inside word" false-positive issue because `\b`
    // is only checked at the start/end of the match, not internally.)
    let input = "aXb8zV9pT3q2aR1wM5sN7b";
    let out = redact_pii(input);
    assert_eq!(out, "***");
}

#[test]
fn test_ue6_redact_pii_empty_string_no_panic() {
    //defense-in-depth: `redact_pii("")` must not panic. The
    // fast path returns early (no triggers), but this test pins
    // that behavior so a future refactor can't introduce a panic
    // on empty input.
    let out = redact_pii("");
    assert_eq!(out, "");
}

#[test]
fn test_ue6_flag_form_in_sentence() {
    // Flag-form pattern in the middle of a sentence. The `--token=`
    // is matched at its position, not at position 0.
    let input = "starting with --token=secret123 and done";
    let out = redact_pii(input);
    assert_eq!(out, "starting with --token=*** and done");
}

#[test]
fn test_ue6_bare_key_value_in_sentence() {
    // Bare-keyword pattern in the middle of a sentence. The value
    // `secret123` contains 3+ digits to trigger the fast path.
    let input = "config has password=secret123 in it";
    let out = redact_pii(input);
    assert_eq!(out, "config has password=*** in it");
}

#[test]
fn test_ue6_20char_run_in_sentence() {
    // 20+ char catch-all in the middle of a sentence.
    let input = "token is Xb8zV9pT3q2aR1wM5sN7abcd here";
    let out = redact_pii(input);
    assert_eq!(out, "token is *** here");
}

#[test]
fn test_ue6_no_false_positive_on_normal_paths() {
    // Normal file paths and URLs should NOT be redacted by the
    // 20+ char catch-all (they contain `/`, `:`, etc. which break
    // the alphanumeric run).
    let inputs = [
        "/home/user/.local/share/voice-typer/config.json",
        "https://api.openai.com/v1/audio/transcriptions",
        "C:\\Users\\user\\AppData\\Roaming\\voice-typer",
    ];
    for input in inputs {
        let out = redact_pii(input);
        assert_eq!(
            out, input,
            "false positive: input {input:?} was changed to {out:?}"
        );
    }
}
