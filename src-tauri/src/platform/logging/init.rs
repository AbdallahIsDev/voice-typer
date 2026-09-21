//! Init orchestration: startup sweep, rotating-file logger init, and the
//! host-entrypoint stderr-fallback wrapper.
//! C-LOG-1: docs/code-notes/tauri-host.md#logging-format-c-log-1

use super::combined::{is_debug_env_truthy, is_truthy_env_var, CombinedLogger};
use super::early::EarlyLogger;
use super::redact::redact_pii;
use super::rotating::RotatingFileWriter;
use crate::util::{LOG_AGE_RETENTION_SECS, LOG_SIZE_FALLBACK_BYTES};
use std::io::Write;
use std::sync::atomic::AtomicBool;

// POSIX-only; Windows uses ACLs (cfg(unix) gates every call site).
#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;

/// Startup sweep: delete log files older than LOG_AGE_RETENTION_SECS
/// or larger than LOG_SIZE_FALLBACK_BYTES. Skips `*.lock` (truncation
/// locks persist across sessions). Best-effort; never blocks logger init.
/// NOTE: see docs/code-notes/tauri-host.md#logging-rotation-tiers-adr-0020-11
pub(crate) fn sweep_stale_logs(logs_dir: &std::path::Path) {
    let entries = match std::fs::read_dir(logs_dir) {
        Ok(entries) => entries,
        Err(_) => return, // missing dir (fresh install): nothing to sweep
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
        // NEVER delete inter-process truncation lock files.
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
        // Locked files skip silently; their owner sweeps them.
        let _ = std::fs::remove_file(&path);
    }
}

/// ADR-0020 §11: rotating file logger at
/// `<config_dir>/logs/voice-typer-rust.log`.
/// Excludes bubble_level (~60 Hz) from the file sink. Session banner is
/// the ONLY sanctioned `session=` line (C-LOG-1). File default WARN+;
/// VOICE_TYPER_RUST_INFO_LOG=1 opts INFO. Hand-rolled writer (not log4rs).
pub(crate) fn init_file_logger(config_dir: &std::path::Path) -> Result<(), String> {
    let logs_dir = config_dir.join("logs");
    std::fs::create_dir_all(&logs_dir).map_err(|e| format!("create logs dir failed: {e}"))?;
    // Sweep BEFORE opening the active file so a stale/oversized one is replaced.
    sweep_stale_logs(&logs_dir);
    // POSIX: logs dir 0o700 (best-effort; soft security, not a hard fail).
    #[cfg(unix)]
    {
        let _ = std::fs::set_permissions(&logs_dir, std::fs::Permissions::from_mode(0o700));
    }
    // Basename `voice-typer-rust` (not `voice-typer`) so a future Python
    // move into logs/ cannot collide on the same file.
    let writer = RotatingFileWriter::new(logs_dir.clone(), "voice-typer-rust");
    let explicit_level = std::env::var("RUST_LOG")
        .ok()
        .and_then(|s| s.parse::<log::LevelFilter>().ok())
        .or_else(|| {
            if is_debug_env_truthy(std::env::var("VOICE_TYPER_DEBUG").ok().as_deref()) {
                Some(log::LevelFilter::Debug)
            } else {
                None
            }
        });
    let file_level = explicit_level.unwrap_or_else(|| {
        if is_truthy_env_var("VOICE_TYPER_RUST_INFO_LOG") {
            log::LevelFilter::Info
        } else {
            log::LevelFilter::Warn
        }
    });
    // TERMINAL sink default: INFO, so a developer tailing stderr still
    // sees the lifecycle lines even though the file (above) is WARN-only.
    let stderr_level = explicit_level.unwrap_or(log::LevelFilter::Info);
    let max_level = if file_level > stderr_level {
        file_level
    } else {
        stderr_level
    };
    let stderr_verbose_init = cfg!(debug_assertions) || is_truthy_env_var("RUST_LOG_STDERR");
    {
        let (file_ts, _) = crate::util::now_timestamps();
        let banner = format!(
            "{}  {:5} [STARTUP] logging initialized: file={}, file_level={}, stderr_level={}, session={}",
            file_ts,
            log::Level::Info,
            logs_dir.display(),
            file_level,
            stderr_level,
            crate::util::session_id()
        );
        let _ = writer.write_line_level(&banner, log::Level::Info);
        let _ = writer.flush();
    }
    let combined = CombinedLogger {
        file_writer: Some(writer),
        level_filter: max_level,
        file_level,
        stderr_verbose: AtomicBool::new(stderr_verbose_init),
    };

    match EarlyLogger::instance() {
        Some(early) => {
            if early.inner.set(combined).is_err() {
                return Err(
                    "init_file_logger called twice (EarlyLogger already upgraded to file sink)"
                        .to_string(),
                );
            }
        }
        None => {
            log::set_logger(Box::leak(Box::new(combined)))
                .map_err(|_| "failed to set logger (already set?)".to_string())?;
        }
    }
    log::set_max_level(max_level);
    Ok(())
}

pub(crate) fn init_file_logger_or_stderr_fallback(config_dir: &std::path::Path) {
    if let Err(e) = init_file_logger(config_dir) {
        eprintln!(
            "[MAIN] file logger init failed (falling back to stderr-only env_logger): {}",
            e
        );
        if let Err(e2) =
            env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info"))
                // C-LOG-1 terminal form: `HH:MM:SS LEVEL msg` (seconds only,
                // no ISO date, no millis, no module path). Mirrors
                // EarlyLogger's pre-init sink so the degraded path stays
                // parseable, and redacts PII (this may be the only sink).
                .format(|buf, record| {
                    writeln!(
                        buf,
                        "{} {:5} {}",
                        crate::util::now_time_only(),
                        record.level(),
                        redact_pii(&record.args().to_string())
                    )
                })
                .try_init()
        {
            eprintln!(
                "[MAIN] env_logger fallback ALSO failed: {}, running with NO logger; all log::*! calls will be dropped",
                e2
            );
        }
    }
}
