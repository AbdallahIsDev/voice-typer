#![allow(clippy::unwrap_used, clippy::expect_used, clippy::panic, clippy::unreachable, clippy::todo, clippy::unimplemented, clippy::cast_possible_truncation)]

//! Unit tests for the Electron → Tauri migration module.
//!
//! Moved verbatim from the original `migrate.rs` monolith as part of
//! the Phase 4.5 split. No test logic changed — only the
//! `use super::*;` parent path now points at `migrate/mod.rs`
//! instead of `migrate.rs` (the file).

use super::*;
use std::sync::atomic::{AtomicU64, Ordering};

/// Best-effort scratch directory — unique per test invocation so
/// parallel `cargo test` threads don't race on the same path. The
/// directory is created fresh and removed on drop (best-effort —
/// if removal fails we leak a temp dir, which is harmless in tests).
struct ScratchDir(PathBuf);
impl ScratchDir {
    fn new(label: &str) -> Self {
        static COUNTER: AtomicU64 = AtomicU64::new(0);
        let n = COUNTER.fetch_add(1, Ordering::SeqCst);
        let mut p = std::env::temp_dir();
        p.push(format!(
            "vt-migrate-test-{}-{}-{}-{}",
            label,
            std::process::id(),
            n,
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos())
                .unwrap_or(0)
        ));
        std::fs::create_dir_all(&p).expect("create scratch dir");
        ScratchDir(p)
    }
    fn path(&self) -> &Path {
        &self.0
    }
}
impl Drop for ScratchDir {
    fn drop(&mut self) {
        // Best-effort recursive remove — ignore errors (test runner
        // may have left files open on Windows).
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

//a symlink in the source `models/` dir must NOT be
/// followed — neither the link itself nor its target is copied.
/// Pre-fix, `path.is_file()` followed the symlink and `std::fs::copy`
/// happily copied whatever the link pointed at (potentially
/// `~/.ssh/id_rsa`).
#[test]
fn copy_missing_recursive_skips_symlinks() {
    let _scratch = ScratchDir::new("symlink-skip");
    let root = _scratch.path().to_path_buf();
    let src = root.join("src");
    let dst = root.join("dst");
    std::fs::create_dir_all(&src).unwrap();
    std::fs::create_dir_all(&dst).unwrap();

    // Regular file — should be copied.
    std::fs::write(src.join("real.bin"), b"model-weights").unwrap();
    // Symlink pointing OUTSIDE the source dir (the exfil scenario).
    // Create the target file first so the symlink resolves.
    let outside = root.join("secret.txt");
    std::fs::write(&outside, b"PRIVATE").unwrap();
    #[cfg(unix)]
    std::os::unix::fs::symlink(&outside, src.join("evil.bin")).unwrap();

    let count = copy_missing_files(&src, &dst);
    // Only `real.bin` should have been copied — the symlink is skipped.
    assert_eq!(count, 1, "only the regular file should be copied");
    assert!(dst.join("real.bin").is_file());
    // The symlink target's contents must NOT appear under the dst.
    assert!(
        !dst.join("evil.bin").exists(),
        "symlink must NOT be copied or followed"
    );
    // Sanity: the original secret file is untouched.
    assert_eq!(std::fs::read_to_string(&outside).unwrap(), "PRIVATE");
}

//`atomic_copy_file` (now in `crate::util` per )
/// must produce a destination whose bytes match the source exactly,
/// and must NOT leave a temp file behind on success.
#[test]
fn atomic_copy_file_produces_identical_destination() {
    let _scratch = ScratchDir::new("atomic-copy");
    let root = _scratch.path().to_path_buf();
    let src = root.join("src.bin");
    let dst = root.join("dst.bin");
    // 8 KiB of pseudo-random bytes (deterministic seed so the test
    // is reproducible).
    let mut data = Vec::with_capacity(8 * 1024);
    let mut state: u32 = 0x1234_5678;
    for _ in 0..(8 * 1024) {
        // xorshift32 — fast, deterministic, no extra deps.
        state ^= state << 13;
        state ^= state >> 17;
        state ^= state << 5;
        data.push((state & 0xFF) as u8);
    }
    std::fs::write(&src, &data).unwrap();
    util::atomic_copy_file(&src, &dst).expect("atomic copy should succeed");
    let got = std::fs::read(&dst).unwrap();
    assert_eq!(got, data, "destination bytes must match source");
    // No `.tmp.copy.*` files left behind on success.
    let leftover: Vec<_> = std::fs::read_dir(&root)
        .unwrap()
        .flatten()
        .filter(|e| e.file_name().to_string_lossy().contains(".tmp.copy."))
        .collect();
    assert!(
        leftover.is_empty(),
        "no temp files should remain: {:?}",
        leftover
    );
}

//`atomic_copy_file` must NOT leave a partial destination
/// if the source doesn't exist (the copy fails before the rename).
#[test]
fn atomic_copy_file_no_partial_on_missing_source() {
    let _scratch = ScratchDir::new("atomic-copy-missing");
    let root = _scratch.path().to_path_buf();
    let src = root.join("nonexistent.bin");
    let dst = root.join("dst.bin");
    let err = util::atomic_copy_file(&src, &dst).expect_err("missing source must produce an error");
    assert!(err.contains("copy"), "error should mention copy: {}", err);
    assert!(!dst.exists(), "destination must NOT exist after failure");
}

//when `merge_config` encounters a corrupt source
/// config.json (invalid JSON), it must back up the corrupt file
/// to `<name>.corrupt-pre-migration.<ts>.bak` BEFORE treating it
/// as Null. Without the backup, the user's old Electron settings
/// would be silently dropped on the next migration pass.
#[test]
fn merge_config_backs_up_corrupt_source() {
    let _scratch = ScratchDir::new("corrupt-backup");
    let root = _scratch.path().to_path_buf();
    let old = root.join("config.json");
    let new = root.join("config.new.json");
    // Source is corrupt JSON.
    std::fs::write(&old, b"{not valid json").unwrap();
    // Target must exist for the merge (parse-failure) path to run.
    std::fs::write(&new, b"{\"existing\": true}").unwrap();

    let outcome = merge_config(&old, &new).expect("merge should not error");
    // The merge proceeds fail-open: corrupt source treated as Null,
    // so 0 keys are written from old.
    match outcome {
        MergeOutcome::Merged(0) => {}
        MergeOutcome::Merged(n) => {
            panic!("expected Merged(0) for corrupt source, got Merged({})", n)
        }
        MergeOutcome::Copied => panic!("expected Merged for existing target, got Copied"),
    }
    // A backup file matching the corrupt-pre-migration pattern
    // must now exist next to the source.
    let backups: Vec<_> = std::fs::read_dir(&root)
        .unwrap()
        .flatten()
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .filter(|n| n.starts_with("config.json.corrupt-pre-migration."))
        .filter(|n| n.ends_with(".bak"))
        .collect();
    assert_eq!(
        backups.len(),
        1,
        "exactly one backup should exist: {:?}",
        backups
    );
    // The backup must contain the original corrupt bytes.
    let backup_contents = std::fs::read(root.join(&backups[0])).unwrap();
    assert_eq!(backup_contents, b"{not valid json");
}

//merge_config uses whole-file mtime (not per-key) to
/// decide which file's values win for overlapping keys. When `old`
/// is newer, ALL of old's keys overwrite new's; when `new` is
/// newer, NONE of old's overlapping keys are taken (only keys
/// present solely in old are merged).
#[test]
fn merge_config_whole_file_mtime_wins() {
    let _scratch = ScratchDir::new("mtime-wins");
    let root = _scratch.path().to_path_buf();
    let old = root.join("old.json");
    let new = root.join("new.json");

    // Write old first, then new (so new is newer by mtime).
    std::fs::write(&old, b"{\"a\": 1, \"b\": 2}").unwrap();
    // Sleep briefly so mtimes are distinguishable on filesystems
    // with coarse mtime granularity (HFS+ has 1s resolution).
    std::thread::sleep(std::time::Duration::from_millis(1100));
    std::fs::write(&new, b"{\"b\": 99, \"c\": 3}").unwrap();

    // new is newer -> b stays 99 (new's value), a is taken from old
    // (only in old), c stays 3 (only in new).
    let outcome = merge_config(&old, &new).expect("merge should succeed");
    // Only `a` is solely in old -> 1 key written.
    match outcome {
        MergeOutcome::Merged(1) => {}
        other => panic!(
            "expected Merged(1) (only `a` is solely in old), got {:?}",
            other
        ),
    }
    let merged: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(&new).unwrap()).unwrap();
    let obj = merged.as_object().expect("merged should be an object");
    assert_eq!(
        obj.get("a").and_then(|v| v.as_i64()),
        Some(1),
        "a taken from old"
    );
    assert_eq!(
        obj.get("b").and_then(|v| v.as_i64()),
        Some(99),
        "b stays new's value (new is newer)"
    );
    assert_eq!(
        obj.get("c").and_then(|v| v.as_i64()),
        Some(3),
        "c stays new's value"
    );

    // Now flip: make old newer than new. ALL of old's overlapping
    // keys win.
    // Sleep BEFORE the rewrite: the first `merge_config` call above
    // rewrote `new` (merged output), so `new`'s mtime is now as fresh
    // as the merge. Without a settling delay, the second `old` write
    // can land in the same coarse-mtime tick (NTFS / FAT have 1-2s
    // granularity) and `file_newer_than(old, new)` would return false
    // → flaky failure. 1100ms matches the delay used at the top of
    // the test so both "newer" relationships are equally robust.
    std::thread::sleep(std::time::Duration::from_millis(1100));
    std::fs::write(&old, b"{\"b\": 7, \"d\": 4}").unwrap();
    // old is now newer (just rewritten, a full second after the
    // merge rewrote new). new's mtime is unchanged since the merge.
    let outcome2 = merge_config(&old, &new).expect("merge should succeed");
    // `b` overlaps and old is newer -> taken. `d` is solely in old -> taken.
    // `a` and `c` are absent from old (this run's old only has b, d),
    // so they stay in new.
    match outcome2 {
        MergeOutcome::Merged(2) => {}
        other => panic!("expected Merged(2) (b + d from old), got {:?}", other),
    }
    let merged2: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(&new).unwrap()).unwrap();
    let obj2 = merged2.as_object().unwrap();
    assert_eq!(
        obj2.get("b").and_then(|v| v.as_i64()),
        Some(7),
        "b taken from old (old is newer)"
    );
    assert_eq!(
        obj2.get("d").and_then(|v| v.as_i64()),
        Some(4),
        "d taken from old"
    );
}

//`sidecar_path` appends the suffix to the file_name
/// (NOT to the extension) so `history.db` -> `history.db-wal`.
/// Critical for SQLite WAL mode — the sidecar files live next to
/// the main db with the literal `-wal` / `-shm` suffix.
#[test]
fn sidecar_path_appends_suffix_to_filename() {
    let db = PathBuf::from("/tmp/config/history.db");
    assert_eq!(
        sidecar_path(&db, "-wal"),
        PathBuf::from("/tmp/config/history.db-wal")
    );
    assert_eq!(
        sidecar_path(&db, "-shm"),
        PathBuf::from("/tmp/config/history.db-shm")
    );
}

//(companion): `sidecar_path` for a db whose name
/// already contains dots — the suffix is appended to the WHOLE
/// name, not after the first dot.
#[test]
fn sidecar_path_handles_dotfiles() {
    let db = PathBuf::from("/tmp/config/my.history.db");
    assert_eq!(
        sidecar_path(&db, "-wal"),
        PathBuf::from("/tmp/config/my.history.db-wal")
    );
}

//(companion): `copy_missing_files` must NOT clobber
/// an existing target file. Even with the symlink-skip fix, a
/// pre-existing target file (e.g. a newer model the user just
/// downloaded) must be preserved.
#[test]
fn copy_missing_files_does_not_clobber_existing() {
    let _scratch = ScratchDir::new("no-clobber");
    let root = _scratch.path().to_path_buf();
    let src = root.join("src");
    let dst = root.join("dst");
    std::fs::create_dir_all(&src).unwrap();
    std::fs::create_dir_all(&dst).unwrap();

    std::fs::write(src.join("model.bin"), b"OLD").unwrap();
    std::fs::write(dst.join("model.bin"), b"NEW (preserved)").unwrap();

    let count = copy_missing_files(&src, &dst);
    assert_eq!(count, 0, "no files should be copied (target exists)");
    // The target's content must be unchanged.
    assert_eq!(
        std::fs::read_to_string(dst.join("model.bin")).unwrap(),
        "NEW (preserved)"
    );
}

//sentinel gating on partial failures ───────────────

//when `migration_failed == 0`, the sentinel marker
/// MUST be written so the next launch skips re-migration.
/// Pre-fix, the sentinel was written UNCONDITIONALLY — even after
/// a partial failure — which silently dropped the user's data on
/// the next launch (migration skipped because sentinel present).
#[test]
fn write_sentinel_if_clean_writes_on_success() {
    let _scratch = ScratchDir::new("sentinel-success");
    let new_dir = _scratch.path().to_path_buf();
    let sentinel = new_dir.join(".migrated-from-electron");
    assert!(!sentinel.exists(), "sentinel must not exist before call");

    let written = write_sentinel_if_clean(&new_dir, 0);
    assert!(
        written,
        "sentinel should be written when migration_failed == 0"
    );
    assert!(sentinel.exists(), "sentinel file must exist on disk");
    // The sentinel is an empty marker file — its presence (not its
    // contents) is what the early-return guard checks.
    assert_eq!(
        std::fs::read(&sentinel).unwrap(),
        b"",
        "sentinel marker must be empty"
    );
}

//when `migration_failed > 0`, the sentinel marker
/// MUST NOT be written. Next launch will re-attempt the migration
/// (the operations are idempotent — atomic_copy uses temp+rename,
/// merge_config is key-by-key). This is the core fix: a partial
/// failure must not silently skip re-migration on next launch.
#[test]
fn write_sentinel_if_clean_skips_on_failure() {
    let _scratch = ScratchDir::new("sentinel-skip");
    let new_dir = _scratch.path().to_path_buf();
    let sentinel = new_dir.join(".migrated-from-electron");
    assert!(!sentinel.exists(), "sentinel must not exist before call");

    // Simulate 1 critical-step failure (e.g. history.db copy failed).
    let written = write_sentinel_if_clean(&new_dir, 1);
    assert!(
        !written,
        "sentinel must NOT be written when migration_failed > 0"
    );
    assert!(
        !sentinel.exists(),
        "sentinel file must NOT exist on disk after a failed migration"
    );
}

//the gate must hold for any non-zero failure count
/// (not just 1). A migration that fails 2 critical steps (e.g.
/// config.json merge AND history.db copy both fail) must still
/// skip the sentinel.
#[test]
fn write_sentinel_if_clean_skips_on_multiple_failures() {
    let _scratch = ScratchDir::new("sentinel-multi-fail");
    let new_dir = _scratch.path().to_path_buf();
    let sentinel = new_dir.join(".migrated-from-electron");

    let written = write_sentinel_if_clean(&new_dir, 3);
    assert!(
        !written,
        "sentinel must NOT be written when migration_failed > 1"
    );
    assert!(!sentinel.exists(), "sentinel file must NOT exist on disk");
}

//(companion): when `migration_failed == 0` AND the
/// target directory does not exist, `write_sentinel_if_clean`
/// must return `false` (the `std::fs::write` will fail because
/// the parent dir doesn't exist). The function must not panic -
/// the migration caller treats a `false`
/// return as "try again next launch", which is the safe
/// behavior.
#[test]
fn write_sentinel_if_clean_handles_write_error() {
    // Use a path whose parent doesn't exist — std::fs::write will
    // fail with NotFound.
    let bogus_dir = std::env::temp_dir().join(format!(
        "vt-migrate-sentinel-noexist-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0)
    ));
    // Note: we deliberately do NOT create bogus_dir.
    let written = write_sentinel_if_clean(&bogus_dir, 0);
    assert!(!written, "write to non-existent dir must return false");
    assert!(
        !bogus_dir.join(".migrated-from-electron").exists(),
        "no sentinel must be left behind"
    );
}

/// `atomic_copy` was moved from this module to `crate::util`
/// (alongside `atomic_write_bytes`). This test now exercises the
/// qualified `util::atomic_copy` call path end-to-end, guarding
/// against an accidental future regression where someone removes
/// the qualification at the call site or forgets to re-export the
/// helper from `util`. (Pre-this test guarded the local
/// `use crate::util::atomic_write_bytes;` bridge import; that
/// bridge is gone now that `atomic_copy` itself lives in `util.rs`
/// and calls `atomic_write_bytes` as a same-module sibling.)
#[test]
fn atomic_copy_uses_local_atomic_write_bytes_import() {
    let _scratch = ScratchDir::new("atomic-copy-import");
    let root = _scratch.path().to_path_buf();
    let src = root.join("src.bin");
    let dst = root.join("dst.bin");
    std::fs::write(&src, b"hello-migrate").unwrap();

    util::atomic_copy(&src, &dst).expect("atomic_copy must succeed");

    let written = std::fs::read(&dst).expect("dst must exist after copy");
    assert_eq!(written, b"hello-migrate");
    assert_eq!(
        std::fs::read(&src).expect("src must be unchanged"),
        b"hello-migrate",
        "atomic_copy must NOT mutate the source file"
    );
}

// migrate_inner + migrate_electron_userdata_async ────────────────
//
// The migration logic lives in `migrate_inner(new_dir: &Path)` so it
// is callable from the async wrapper `migrate_electron_userdata_async`
// (the production entry point - called from `main.rs`'s setup closure;
// the former sync wrapper `migrate_electron_userdata` was removed as
// dead code once main.rs switched to the async variant).
//
// The two tests below pin the new behavior:
//   1. `migrate_inner` short-circuits when the sentinel marker is
//      already present (no env-var manipulation needed — the
//      sentinel check runs BEFORE `electron_userdata_candidates()`
//      reads any env vars).
//   2. `migrate_inner` is callable from a `spawn_blocking` closure
//      (the exact pattern `migrate_electron_userdata_async` uses
//      internally) without panic and returns cleanly.

/// `migrate_inner` must early-return without doing any fs work when
/// the `.migrated-from-electron` sentinel marker is already present
/// in `new_dir`. This is the idempotency short-circuit that makes
/// the migration safe to call on every launch.
///
/// We pre-create the sentinel before calling `migrate_inner` so the
/// function returns at the FIRST guard (sentinel.exists() check) —
/// BEFORE `electron_userdata_candidates()` is called. This means
/// the test does NOT need to manipulate any env vars (HOME,
/// APPDATA, XDG_CONFIG_HOME) and is safe to run in parallel with
/// other tests.
#[test]
fn migrate_inner_returns_early_when_sentinel_present() {
    let _scratch = ScratchDir::new("sentinel-shortcircuit");
    let new_dir = _scratch.path().to_path_buf();
    // Pre-create the sentinel marker so migrate_inner short-circuits.
    std::fs::write(new_dir.join(".migrated-from-electron"), b"").unwrap();
    // Drop a "decoy" file that the migration WOULD copy if it ran —
    // proves the short-circuit didn't proceed past the sentinel guard.
    // (If the migration proceeded, it would have created config.json
    // from a candidate old Electron userData dir. By asserting no
    // config.json appears, we verify the short-circuit held.)
    assert!(
        !new_dir.join("config.json").exists(),
        "config.json must not exist before migrate_inner call"
    );

    // Call the refactored body. Should return immediately without
    // touching env vars or probing candidate paths.
    migrate_inner(&new_dir);

    // The sentinel must still be present (migrate_inner must not
    // delete it on the early-return path).
    assert!(
        new_dir.join(".migrated-from-electron").exists(),
        "sentinel marker must still exist after early-return"
    );
    // No config.json should have been created (the migration did
    // not proceed past the sentinel check).
    assert!(
        !new_dir.join("config.json").exists(),
        "config.json must NOT be created when sentinel short-circuited the migration"
    );
}

/// `migrate_inner` runs unchanged when called from inside a
/// `spawn_blocking` closure — the exact pattern
/// `migrate_electron_userdata_async` uses to move the fs-heavy
/// migration off the async runtime's worker threads.
///
/// This test exercises the same `spawn_blocking(move || migrate_inner(...))`
/// plumbing that the async wrapper uses, but calls `tokio::task::spawn_blocking`
/// directly (rather than `tauri::async_runtime::spawn_blocking`) so the
/// test does not require initializing Tauri's global async runtime.
/// `tauri::async_runtime::spawn_blocking` delegates to the same
/// Tokio blocking-pool mechanism, so the pattern is functionally
/// identical.
///
/// We pre-create the sentinel marker so `migrate_inner` short-circuits
/// at its first guard (no env-var manipulation needed — see the
/// companion sync test above).
#[tokio::test]
async fn migrate_inner_runs_under_spawn_blocking_without_panic() {
    let _scratch = ScratchDir::new("spawn-blocking-plumbing");
    let new_dir = _scratch.path().to_path_buf();
    // Pre-create the sentinel so migrate_inner short-circuits
    // without needing env vars (which would be racy under parallel
    // test execution).
    std::fs::write(new_dir.join(".migrated-from-electron"), b"").unwrap();
    // Clone `new_dir` into the closure (the same move pattern used
    // by `migrate_electron_userdata_async`).
    let new_dir_for_closure = new_dir.clone();

    // Spawn migrate_inner on the blocking pool and await its
    // completion. This mirrors the exact shape of
    // `migrate_electron_userdata_async`'s body:
    //   tauri::async_runtime::spawn_blocking(move || migrate_inner(&new_dir)).await
    let join_result = tokio::task::spawn_blocking(move || {
        migrate_inner(&new_dir_for_closure);
    })
    .await;

    // The JoinHandle must resolve to Ok (no panic in the closure).
    // `migrate_inner` is designed to never panic (all fs ops are
    // wrapped in `match`/`if let Err(e)` with log-and-continue),
    // so a panic here would indicate a regression.
    assert!(
        join_result.is_ok(),
        "spawn_blocking(migrate_inner) must not panic: {:?}",
        join_result.err()
    );
    // Sentinel must still be present (migrate_inner short-circuited).
    assert!(
        new_dir.join(".migrated-from-electron").exists(),
        "sentinel marker must still exist after spawn_blocking migration"
    );
}
