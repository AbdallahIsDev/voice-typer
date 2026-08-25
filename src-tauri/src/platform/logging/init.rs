//! Init orchestration: startup sweep, rotating-file logger init, and the
//! host-entrypoint stderr-fallback wrapper.

use super::combined::{is_debug_env_truthy, is_truthy_env_var, CombinedLogger};
use super::early::EarlyLogger;
use super::rotating::RotatingFileWriter;
use crate::util::{LOG_AGE_RETENTION_SECS, LOG_SIZE_FALLBACK_BYTES};
use std::sync::atomic::AtomicBool;

// POSIX-only `Permissions::from_mode` trait import. On Windows this is
// a no-op (the OS uses ACLs, not mode bits) — the `#[cfg(unix)]` blocks
// below gate every call site.
#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;

/// Startup sweep — Tiers 1 (age) + 2 (size fallback) of the three-tier
/// log-cleanup design. Deletes any regular file in `logs_dir` that is
/// EITHER older than [`crate::util::LOG_AGE_RETENTION_SECS`] (7 days)
/// OR larger than [`crate::util::LOG_SIZE_FALLBACK_BYTES`] (25 MB).
///
/// Mirrors the Python `_sweep_stale_logs`
/// (`voice_typer/server/log/__init__.py`) and the Electron
/// `sweepStaleLogs` (`client/src/main/logging/rotation.ts`).
///
/// Scope: every regular file in the directory EXCEPT `*.lock` files —
/// the inter-process truncation locks must persist across sessions.
/// Files locked by another live process (e.g. `voice-typer.log` held
/// open by an already-running Python backend in host-first launch
/// order) fail the remove and are skipped silently — their owner
/// sweeps them at its own startup.
///
/// Best-effort: every error is swallowed — a sweep failure must never
/// block logger init or app startup.
pub(crate) fn sweep_stale_logs(logs_dir: &std::path::Path) {
    let entries = match std::fs::read_dir(logs_dir) {
        Ok(entries) => entries,
        Err(_) => return, // missing dir (fresh install) — nothing to sweep
    };
    let now_secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    for entry in entries.flatten() {
        let path = entry.path();
        if !path.is_file() {
            continue;
        }
        // NEVER delete the inter-process truncation lock files —
        // they must persist across sessions.
        if entry
            .file_name()
            .to_str()
            .is_some_and(|name| name.ends_with(".lock"))
        {
            continue;
        }
        let Ok(meta) = std::fs::metadata(&path) else {
            continue;
        };
        let mtime_secs = meta
            .modified()
            .ok()
            .and_then(|m| m.duration_since(std::time::UNIX_EPOCH).ok())
            .map(|d| d.as_secs())
            .unwrap_or(now_secs);
        let age = now_secs.saturating_sub(mtime_secs);
        if age <= LOG_AGE_RETENTION_SECS && meta.len() <= LOG_SIZE_FALLBACK_BYTES {
            continue;
        }
        // Best-effort remove — a locked file (another live process)
        // fails here and is skipped; its owner sweeps it.
        let _ = std::fs::remove_file(&path);
    }
}

/// ADR-0020 §11: initialize a rotating file logger writing to
/// `<config_dir>/logs/voice-typer-rust.log`.
///
/// **Excludes `bubble_level` events** from the file log: at ~60 Hz
/// they would fill disk fast even with rotation. The Rust WS-reader
/// already coalesces them to ≤30 Hz for the UI (§9); the file path
/// drops them entirely so file logs capture events/errors, not the
/// level stream.
///
/// Replaces the prior `env_logger::Builder::init()` call — this
/// logger writes to BOTH stderr (matching the prior env_logger
/// output) AND the rotating file. If file init fails, the caller
/// should fall back to `env_logger` for stderr-only output.
///
/// # Implementation choice: hand-rolled, not `log4rs`
///
/// `log4rs` is a heavy dep (~30 transitive crates) for a feature
/// that just needs "rotate at N bytes, keep N files". This
/// hand-rolled `RotatingFileWriter` is ~80 lines and has no deps
/// beyond `log` (already required) + `std::fs`. The rotation is
/// triggered lazily on the write that crosses the size threshold
/// (not on a timer), which is fine for our write volume.
pub(crate) fn init_file_logger(config_dir: &std::path::Path) -> Result<(), String> {
    let logs_dir = config_dir.join("logs");
    std::fs::create_dir_all(&logs_dir).map_err(|e| format!("create logs dir failed: {e}"))?;
    // Startup sweep — Tiers 1 (age, 7 days) + 2 (size fallback, 25 MB)
    // of the three-tier cleanup design. Runs BEFORE the writer opens
    // `voice-typer-rust.log` so a stale/oversized active file is removed
    // and a fresh one created for this session. Mirrors the Python
    // `_sweep_stale_logs` and the Electron `sweepStaleLogs`. Best-effort:
    // every error is swallowed — a sweep failure must never block logger
    // init.
    sweep_stale_logs(&logs_dir);
    // Tighten the parent `<config_dir>/logs/` dir to
    // `0o700` on POSIX (owner rwx only — no group/other access). Mirrors
    // the Python side's `os.chmod(config_dir, 0o700)` at
    // `voice_typer/server/log.py:891-893`. Best-effort: a `chmod` failure
    // is logged but does NOT block logger init (a too-permissive dir is
    // a softening of the security posture, not a hard failure — the
    // individual log files inside still get `0o600` via `OpenOptionsExt`).
    #[cfg(unix)]
    {
        let _ = std::fs::set_permissions(&logs_dir, std::fs::Permissions::from_mode(0o700));
    }
    // rename Rust's log basename to `voice-typer-rust` so the
    // final path is `<config_dir>/logs/voice-typer-rust.log`. Pre-fix
    // the basename was `voice-typer`, producing
    // `<config_dir>/logs/voice-typer.log` — the SAME basename as the
    // Python sidecar's `<config_dir>/voice-typer.log`. The two paths
    // were different (Python wrote to the config_dir root, Rust to
    // `logs/`) so they didn't actually collide, BUT the basename
    // parity was a fragile contract: a future Python change moving
    // its log into `logs/` (a reasonable cleanup) would silently
    // cause both layers to append to the same file → rotation races
    // + interleaved lines with different timestamp formats. Renaming
    // Rust's file makes the contract explicit and survives a Python
    // layout change. Mirrors the Python side's
    // `RotatingFileHandler(filename=...)` at log.py:891-893.
    let writer = RotatingFileWriter::new(logs_dir, "voice-typer-rust");
    // honor `RUST_LOG` runtime log-level override. Parsed
    // as a `log::LevelFilter` (e.g. "debug", "trace", "warn", "off").
    // Default to `Info` if the var is unset OR unparseable so a typo
    // (e.g. `RUST_LOG=debog`) doesn't silently disable all logging.
    // Both the global `log::set_max_level` AND the per-logger
    // `level_filter` are set to this value — `set_max_level` is the
    // fast-path short-circuit at the macro call site, while
    // `level_filter` is consulted inside `CombinedLogger::enabled`
    // (which `log::log!` calls as a second filter).
    //
    // fallback: if `RUST_LOG` is unset, also honor
    // the Voice Typer-specific `VOICE_TYPER_DEBUG` env var. When
    // truthy ("1", "true", "yes", case-insensitive), set the level to
    // Debug so developers get verbose logs in the file + stderr. This
    // mirrors the Python side's `env_validation.py` boolean-var
    // pattern so the Rust + Python hosts respond identically to the
    // same env var. `RUST_LOG` (the standard Rust convention) wins if
    // set; `VOICE_TYPER_DEBUG` is a fallback for users who don't know
    // about `RUST_LOG`.
    let max_level = std::env::var("RUST_LOG")
        .ok()
        .and_then(|s| s.parse::<log::LevelFilter>().ok())
        .or_else(|| {
            // RUST_LOG unset/unparseable — try VOICE_TYPER_DEBUG.
            if is_debug_env_truthy(std::env::var("VOICE_TYPER_DEBUG").ok().as_deref()) {
                Some(log::LevelFilter::Debug)
            } else {
                None
            }
        })
        .unwrap_or(log::LevelFilter::Info);
    // gate stderr output on debug builds OR `RUST_LOG_STDERR=1`.
    // Release builds with no env var skip the per-line `eprintln!`
    // syscall (saves 1 `write(2)` per log line). The env var is the
    // release-build escape hatch for operators who want stderr tailing
    // (`journalctl -u voice-typer` etc.).
    //
    // use the shared `is_truthy_env_var` helper so the truthy
    // contract ("1" / "true" / "yes", case-insensitive, trimmed) is
    // defined in exactly one place. The same helper is used by
    // `install_early_logger` and `is_debug_env_truthy`.
    let stderr_verbose_init = cfg!(debug_assertions) || is_truthy_env_var("RUST_LOG_STDERR");
    let combined = CombinedLogger {
        file_writer: Some(writer),
        level_filter: max_level,
        // `AtomicBool` so future code (e.g. a Tauri command)
        // can toggle stderr verbosity at runtime. The per-line cost
        // is a single `AtomicBool::load(Relaxed)` — same as a `bool`
        // load on x86/ARM (Relaxed loads compile to a plain MOV).
        stderr_verbose: AtomicBool::new(stderr_verbose_init),
    };

    // prefer the swap pattern when an `EarlyLogger` is already
    // installed as the process-global `log` sink (the standard path —
    // `install_early_logger` runs as the first line of `main()`).
    // `log::set_logger` can only be called ONCE per process, so we
    // can't replace the global logger; instead, we swap the
    // `CombinedLogger` into the `EarlyLogger`'s `OnceLock` so all
    // subsequent `log::*!` records delegate to the combined file+stderr
    // sink. `OnceLock::get` is a single atomic load on the hot path —
    // no mutex acquisition per log call.
    if let Some(early) = EarlyLogger::instance() {
        if early.inner.set(combined).is_err() {
            return Err(
                "init_file_logger called twice (EarlyLogger already upgraded to file sink)"
                    .to_string(),
            );
        }
        // Bump the global max-level to the resolved value (the
        // EarlyLogger was installed with `Info` as a safe default; the
        // file-logger init may have parsed `RUST_LOG=debug` etc.).
        // `set_max_level` can be called multiple times safely.
        log::set_max_level(max_level);
        return Ok(());
    }

    // Fallback: EarlyLogger was NOT installed (e.g. tests, or a host
    // entrypoint that skipped `install_early_logger`). Install the
    // `CombinedLogger` directly via `log::set_logger`. This path
    // preserves the behavior so existing tests that depend
    // on `init_file_logger` calling `set_logger` continue to compile
    // and run.
    log::set_logger(Box::leak(Box::new(combined)))
        .map_err(|_| "failed to set logger (already set?)".to_string())?;
    log::set_max_level(max_level);
    Ok(())
}

/// Host entrypoint convenience wrapper: try the rotating file logger
/// first, and if that fails (e.g. config-dir not writable), fall back
/// to a stderr-only `env_logger` sink so early startup diagnostics
/// still land somewhere visible. Both failures are surfaced to stderr
/// via `eprintln!` (the global `log` sink may not be installed yet).
///
/// Extracted from `main.rs` so the host entrypoint stays wiring-only
//(C-) — no `env_logger::Builder` plumbing inline.
///
/// # Error handling
///
/// This function NEVER panics:
/// - `init_file_logger` failure -> log to stderr, try env_logger.
/// - env_logger `try_init` failure (e.g. another logger already
///   installed) -> log to stderr, return. The host continues with NO
///   logger; all `log::*!` calls become no-ops (the `log` crate's
///   default sink is a no-op until `set_logger` is called).
pub(crate) fn init_file_logger_or_stderr_fallback(config_dir: &std::path::Path) {
    if let Err(e) = init_file_logger(config_dir) {
        eprintln!(
            "[MAIN] file logger init failed (falling back to stderr-only env_logger): {}",
            e
        );
        // Best-effort: env_logger for stderr only (no file sink).
        // `try_init` avoids panic if `log::set_logger` was already
        // called (e.g. by the EarlyLogger swap path above).
        if let Err(e2) =
            env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info"))
                .format_timestamp_millis()
                .try_init()
        {
            eprintln!(
                "[MAIN] env_logger fallback ALSO failed: {} — running with NO logger; all log::*! calls will be dropped",
                e2
            );
        }
    }
}
