//! Per-platform config-dir resolution (ADR-0020 §8).

use std::sync::OnceLock;

// ─── ADR-0020 §8: per-platform config-dir resolution ─────────────────

/// ADR-0020 §8: resolve the per-platform config dir for Voice Typer.
/// Returns:
/// - Windows: `%APPDATA%/voice-typer`
/// - macOS:   `~/Library/Application Support/voice-typer`
/// - Linux:   `$XDG_DATA_HOME/voice-typer` (default `~/.local/share/voice-typer`)
///
/// # Why not `app.path().app_config_dir()`?
///
/// Tauri's `app_config_dir()` returns a path derived from the bundle
/// identifier (`com.voicetyper.app`), but the Python sidecar uses the
/// lowercase-hyphenated `voice-typer` directory (per `_paths.py`). To
/// keep the Rust host + Python sidecar reading/writing the SAME paths
/// byte-for-byte, we resolve from env vars directly, matching the
/// Python side's `_paths.config_dir()` resolution.
///
/// # NF-R19-4 (REVISED): `app` parameter dropped
///
/// Previously this function took `app: &tauri::AppHandle` for
/// "forward-compatible migration to `app.path().app_config_dir()`".
/// The param was never used (`let _ = app;`), and the migration never
/// materialized — the env-var resolution is the steady-state path. The
/// dead param was flagged by GT-E3-4 and is now removed; all 3 callers
/// (`main.rs`, `system_cmds.rs`, `migrate.rs`) updated.
///
/// # No Electron userData merge under Tauri
///
/// ADR-0020 §8 mentions an optional one-time migration from the old
/// Electron `userData/voice-typer` directory to `<config_dir>` on
/// first Tauri launch. Under Tauri there is **no Electron main
/// process**, so the `userData/voice-typer` dir is created only by the
/// PRIOR Electron install on the user's machine — IF one exists. The
/// migration step IS implemented (see `crate::migrate::migrate_electron_userdata`)
/// and runs from `main.rs::setup` BEFORE the sidecar spawns. It probes
/// the three legacy Electron `userData` names (`voice-typer-desktop`,
/// `voice-typer`, `Voice Typer`) under the platform's `userData` base,
/// picks the first that exists, and merges per ADR-0020 §8 rules:
/// newest-mtime-wins for `config.json`, append-only for `history.db`,
/// copy-only-absent for `models/`. The merge is idempotent and guarded
/// by a `.migrated-from-electron` sentinel file so it only runs once.
/// (GT-E3-8: the prior doc here said "migration is a no-op and is
/// intentionally NOT implemented here" — that was stale; the
/// migration IS implemented in `migrate.rs` and has been running since
/// session-1's PVT-4 fix.)
///
/// # Python-side `VoiceTyperSingleInstance` Win32 mutex
///
/// The Python side's `VoiceTyperSingleInstance` Win32 named mutex
/// (acquired in `app.py` on Windows to prevent duplicate Electron
/// instances) is **disabled when `TAURI_SIDECAR=1` is set** — the
/// Python sidecar detects the env var and skips the mutex acquire so
/// it doesn't double-lock against the Tauri-side
/// `tauri-plugin-single-instance` gate (§12). The Tauri plugin uses
/// the same Win32 mutex approach under the hood (different name based
/// on the app identifier) so the two gates don't collide.
///
/// # ER-59: caching
///
/// Env vars are invariant for the process lifetime, but every call to
/// this function re-resolved 4 `std::env::var()` lookups. Under FT-1
/// flapping (supervisor respawn loops), `read/write_ft1_restart_counter`
/// in `supervisor.rs` each call this 4 times, summing to ~microseconds
/// per call but adding up. The public `config_dir()` now routes
/// through `config_dir_cached()` (below), which uses a `OnceLock` to
/// resolve the env vars exactly once per process. The pure
/// `config_dir_from_env()` helper remains un-cached so unit tests can
/// exercise the per-platform logic without polluting the process-wide
/// cache.
pub(crate) fn config_dir() -> std::path::PathBuf {
    config_dir_cached().to_path_buf()
}

/// ER-59: process-wide cached config dir. Resolved once on first call;
/// subsequent calls return the cached `PathBuf` via a `OnceLock`. The
/// env vars (`HOME`, `APPDATA`, `XDG_DATA_HOME`, `VOICE_TYPER_CONFIG_DIR`)
/// are invariant for the process lifetime, so caching is safe — a
/// `setenv` mid-process would not be reflected, but that's never
/// legitimate (the Python side reads env vars once at startup too).
///
/// Returns a `&'static Path` (the `OnceLock` holds the `PathBuf` for
/// the process lifetime) so callers can avoid the `PathBuf` clone when
/// they only need to read.
fn config_dir_cached() -> &'static std::path::Path {
    static CACHED: OnceLock<std::path::PathBuf> = OnceLock::new();
    CACHED.get_or_init(|| {
        config_dir_from_env(
            std::env::var("HOME").ok().as_deref(),
            std::env::var("APPDATA").ok().as_deref(),
            std::env::var("XDG_DATA_HOME").ok().as_deref(),
            // CR-39: VOICE_TYPER_CONFIG_DIR env-var override — mirrors
            // Python's _config_dir() resolution order (env var → legacy →
            // platform default).
            std::env::var("VOICE_TYPER_CONFIG_DIR").ok().as_deref(),
        )
    })
}

/// Pure form of `config_dir` for unit testing (no env-var reads).
///
/// # NF-R9-8: graceful fallback when env vars are missing
///
/// The previous implementation `panic!()`ed if `APPDATA` (Windows) /
/// `HOME` (macOS, Linux) was unset. That crashes the host on:
/// - Windows service accounts (no `%APPDATA%` profile).
/// - Linux systemd units without `HOME=` in the unit file (rare but
///   legitimate — the Tauri host could be launched by a user-unit
///   without `Environment=HOME=...`).
/// - Headless CI runners (mostly affects tests, but real failure).
///
/// The graceful fallback returns `<cwd>/voice-typer` (i.e. `./voice-typer`
/// relative to the process's CWD) and logs a warning via `log::warn!`.
/// PVT-G5-085: previously used `eprintln!` (which bypasses the rotating
/// file logger); switched to `log::warn!` so the warning lands in
/// `voice-typer.log` for operators debugging missing-config-dir issues.
/// When `config_dir_from_env` is called before `init_file_logger` (the
/// first call from `main.rs`), the `log::warn!` is a silent no-op (the
/// default `log` crate sink discards records when no logger is set);
/// the subsequent runtime calls (from `config_dir(app)` after logger
/// init) WILL emit the warning to both stderr and the file log. The
/// caller is expected to handle the resulting I/O errors (e.g. log file
/// open fails) at the call site; this function never panics.
///
/// # CR-39: legacy `~/.voice-typer` migration + env-var override
///
/// The function mirrors the Python side's `_config_dir()` resolution
/// order: `VOICE_TYPER_CONFIG_DIR` env var wins, then legacy
/// `~/.voice-typer` (if it exists), then the per-platform default.
/// Without the legacy check, the Tauri host writes log/PID files to
/// the platform default while the Python sidecar reads `config.json`
/// from `~/.voice-typer` — split-brain state for users upgrading from
/// a legacy install.
pub(crate) fn config_dir_from_env(
    home: Option<&str>,
    appdata: Option<&str>,
    xdg_data_home: Option<&str>,
    config_dir_env: Option<&str>,
) -> std::path::PathBuf {
    const APP_NAME: &str = "voice-typer";

    // CR-39: VOICE_TYPER_CONFIG_DIR env-var override. Mirrors the
    // Python side's _config_dir() resolution order: env var wins,
    // then legacy ~/.voice-typer, then platform default. Without this
    // check, a user who sets VOICE_TYPER_CONFIG_DIR (e.g. for a
    // portable / snap install) would have the Tauri host and Python
    // sidecar disagree on the config dir.
    if let Some(custom) = config_dir_env {
        if !custom.is_empty() {
            return std::path::PathBuf::from(custom);
        }
    }

    // CR-39: legacy ~/.voice-typer check. Python's _config_dir() and
    // Electron's computeConfigDir() both check this first; the Tauri
    // host must do the same so the host and Python sidecar agree on
    // the config dir for users upgrading from a legacy install.
    // Without this check, Tauri writes log files / single-instance
    // lock / PID files to the platform default (~/.local/share/voice-
    // typer on Linux) while the Python sidecar reads config.json from
    // ~/.voice-typer — split-brain state where Tauri can't find the
    // backend PID file (single-instance detection fails → duplicate
    // launches) and Tauri-side state (window placement, theme,
    // recent-files) is in a different dir than Python's.
    if let Some(h) = home {
        let legacy = std::path::PathBuf::from(h).join(".voice-typer");
        if legacy.exists() {
            return legacy;
        }
    }

    #[cfg(target_os = "windows")]
    {
        let _ = home;
        let _ = xdg_data_home;
        // NF-R9-8: graceful fallback when APPDATA is missing (Windows
        // service accounts, headless CI). Previously panicked — now
        // returns `./voice-typer` relative to CWD and logs a warning.
        // The Tauri host's log file open at this path will fail loudly
        // if CWD is read-only, which is the correct behavior (better
        // than crashing during config-dir resolution).
        let base = appdata.unwrap_or_else(|| {
            // PVT-G5-085: switched from `eprintln!` to `log::warn!`.
            // GT-80: ALSO eprintln! so it lands on stderr regardless
            // of logger state (first call from main.rs happens BEFORE
            // init_file_logger, so log::warn! alone is silent).
            let warn_msg = format!(
                "[paths] APPDATA env var is not set — falling back to \
                 CWD-relative config dir (./{}). This is expected for \
                 Windows service accounts / headless CI but indicates \
                 a missing user profile in normal desktop sessions.",
                APP_NAME
            );
            eprintln!("{}", warn_msg);
            log::warn!("{}", warn_msg);
            "."
        });
        std::path::PathBuf::from(base).join(APP_NAME)
    }
    #[cfg(target_os = "macos")]
    {
        let _ = appdata;
        let _ = xdg_data_home;
        // NF-R9-8: graceful fallback when HOME is missing on macOS
        // (rare — `launchd` always sets HOME for user sessions, but
        // a system LaunchDaemon runs without it). Falls back to CWD.
        let home = home.unwrap_or_else(|| {
            // PVT-G5-085: `log::warn!` (was `eprintln!`).
            // GT-80: ALSO eprintln! so it lands on stderr regardless
            // of logger state.
            let warn_msg = format!(
                "[paths] HOME env var is not set — falling back to \
                 CWD-relative config dir (./{}). This is expected for \
                 system LaunchDaemons but indicates a missing user \
                 profile in normal desktop sessions.",
                APP_NAME
            );
            eprintln!("{}", warn_msg);
            log::warn!("{}", warn_msg);
            "."
        });
        std::path::PathBuf::from(home)
            .join("Library")
            .join("Application Support")
            .join(APP_NAME)
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        let _ = appdata;
        if let Some(xdg) = xdg_data_home {
            if !xdg.is_empty() {
                return std::path::PathBuf::from(xdg).join(APP_NAME);
            }
        }
        // NF-R9-8: graceful fallback when HOME is missing on Linux
        // (systemd user units without `Environment=HOME=...`, or a
        // bare cron-spawned process). Falls back to CWD with a warning
        // — the XDG spec mandates HOME as a fallback when
        // XDG_DATA_HOME is unset, so the missing-HOME case is
        // technically undefined behavior per the spec; we choose a
        // CWD-relative path rather than panicking.
        let home = home.unwrap_or_else(|| {
            // PVT-G5-085: `log::warn!` (was `eprintln!`).
            // GT-80: ALSO eprintln! so it lands on stderr regardless
            // of logger state.
            let warn_msg = format!(
                "[paths] HOME env var is not set — falling back to \
                 CWD-relative config dir (./{}). This is expected for \
                 systemd user units without `Environment=HOME=...` \
                 but indicates a missing user profile in normal \
                 desktop sessions.",
                APP_NAME
            );
            eprintln!("{}", warn_msg);
            log::warn!("{}", warn_msg);
            "."
        });
        std::path::PathBuf::from(home)
            .join(".local")
            .join("share")
            .join(APP_NAME)
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos", unix)))]
    {
        let _ = (home, appdata, xdg_data_home, config_dir_env);
        std::path::PathBuf::from(".").join(APP_NAME)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── config_dir_from_env (per-platform) ────────────────────────────

    #[cfg(target_os = "linux")]
    #[test]
    fn test_config_dir_linux_default() {
        let p = config_dir_from_env(Some("/home/user"), None, None, None);
        assert_eq!(
            p,
            std::path::PathBuf::from("/home/user/.local/share/voice-typer")
        );
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn test_config_dir_linux_xdg_set() {
        let p = config_dir_from_env(Some("/home/user"), None, Some("/custom/xdg"), None);
        assert_eq!(p, std::path::PathBuf::from("/custom/xdg/voice-typer"));
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn test_config_dir_linux_xdg_empty_falls_back_to_home() {
        // Empty XDG_DATA_HOME should be treated as unset (per XDG spec).
        let p = config_dir_from_env(Some("/home/user"), None, Some(""), None);
        assert_eq!(
            p,
            std::path::PathBuf::from("/home/user/.local/share/voice-typer")
        );
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn test_config_dir_macos() {
        let p = config_dir_from_env(Some("/Users/user"), None, None, None);
        assert_eq!(
            p,
            std::path::PathBuf::from("/Users/user/Library/Application Support/voice-typer")
        );
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn test_config_dir_windows() {
        let p = config_dir_from_env(None, Some(r"C:\Users\user\AppData\Roaming"), None, None);
        assert_eq!(
            p,
            std::path::PathBuf::from(r"C:\Users\user\AppData\Roaming\voice-typer")
        );
    }

    // ── NF-R9-8: graceful fallback when env vars are missing ──────────
    //
    // The previous implementation panicked if APPDATA (Windows) or HOME
    // (macOS, Linux) was unset. These tests pin the new graceful-
    // fallback behavior: when the env var is missing, the function
    // returns `./voice-typer` (CWD-relative) instead of panicking, so
    // the Tauri host can boot under Windows service accounts / Linux
    // systemd user units / headless CI runners.

    #[cfg(target_os = "linux")]
    #[test]
    fn test_config_dir_linux_missing_home_falls_back_to_cwd() {
        // NF-R9-8: when HOME is missing AND XDG_DATA_HOME is unset,
        // the function must NOT panic — it returns `./voice-typer`.
        let p = config_dir_from_env(None, None, None, None);
        assert_eq!(
            p,
            std::path::PathBuf::from("./voice-typer"),
            "missing HOME on Linux should fall back to CWD-relative voice-typer dir (NF-R9-8)"
        );
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn test_config_dir_linux_missing_home_with_empty_xdg_falls_back_to_cwd() {
        // Empty XDG_DATA_HOME is treated as unset (per XDG spec), so
        // the missing-HOME fallback path applies.
        let p = config_dir_from_env(None, None, Some(""), None);
        assert_eq!(
            p,
            std::path::PathBuf::from("./voice-typer"),
            "missing HOME + empty XDG_DATA_HOME on Linux should fall back to CWD (NF-R9-8)"
        );
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn test_config_dir_macos_missing_home_falls_back_to_cwd() {
        // NF-R9-8: when HOME is missing on macOS (system LaunchDaemon),
        // the function must NOT panic — it returns `./Library/Application
        // Support/voice-typer` (CWD-relative).
        let p = config_dir_from_env(None, None, None, None);
        assert_eq!(
            p,
            std::path::PathBuf::from("./Library/Application Support/voice-typer"),
            "missing HOME on macOS should fall back to CWD-relative path (NF-R9-8)"
        );
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn test_config_dir_windows_missing_appdata_falls_back_to_cwd() {
        // NF-R9-8: when APPDATA is missing on Windows (service account),
        // the function must NOT panic — it returns `./voice-typer`.
        let p = config_dir_from_env(None, None, None, None);
        assert_eq!(
            p,
            std::path::PathBuf::from("./voice-typer"),
            "missing APPDATA on Windows should fall back to CWD-relative voice-typer dir (NF-R9-8)"
        );
    }

    // ── CR-39: legacy ~/.voice-typer check + VOICE_TYPER_CONFIG_DIR override ──
    //
    // The Tauri host must mirror Python's _config_dir() resolution
    // order (env var → legacy ~/.voice-typer → platform default) so
    // the host and Python sidecar agree on the config dir for users
    // upgrading from a legacy install. Without the legacy check,
    // Tauri writes log/PID files to the platform default while Python
    // reads config.json from ~/.voice-typer — split-brain state.

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    #[test]
    fn test_config_dir_legacy_voice_typer_wins_over_platform_default() {
        // CR-39: if ~/.voice-typer exists, it should be returned in
        // preference to the platform default.
        use std::fs;
        use std::time::SystemTime;
        let tmp = std::env::temp_dir().join(format!(
            "vt_paths_legacy_test_{}_{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(SystemTime::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(tmp.join(".voice-typer")).unwrap();
        let p = config_dir_from_env(
            Some(tmp.to_str().unwrap()),
            None,
            None,
            None,
        );
        assert_eq!(
            p,
            tmp.join(".voice-typer"),
            "CR-39: existing ~/.voice-typer should win over platform default"
        );
        fs::remove_dir_all(&tmp).ok();
    }

    #[test]
    fn test_config_dir_voice_typer_config_dir_env_override() {
        // CR-39: VOICE_TYPER_CONFIG_DIR env var wins over legacy and
        // platform default.
        let p = config_dir_from_env(
            Some("/home/user"),
            None,
            None,
            Some("/custom/config/dir"),
        );
        assert_eq!(
            p,
            std::path::PathBuf::from("/custom/config/dir"),
            "CR-39: VOICE_TYPER_CONFIG_DIR env var should override platform default"
        );
    }

    #[test]
    fn test_config_dir_env_override_beats_legacy_check() {
        // CR-39: env var wins over legacy ~/.voice-typer check.
        use std::fs;
        use std::time::SystemTime;
        let tmp = std::env::temp_dir().join(format!(
            "vt_paths_env_test_{}_{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(SystemTime::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(tmp.join(".voice-typer")).unwrap();
        let p = config_dir_from_env(
            Some(tmp.to_str().unwrap()),
            None,
            None,
            Some("/explicit/override"),
        );
        assert_eq!(
            p,
            std::path::PathBuf::from("/explicit/override"),
            "CR-39: VOICE_TYPER_CONFIG_DIR env var should win over legacy ~/.voice-typer"
        );
        fs::remove_dir_all(&tmp).ok();
    }

    #[test]
    fn test_config_dir_empty_env_override_falls_through() {
        // CR-39: an empty VOICE_TYPER_CONFIG_DIR value should be
        // treated as unset (mirrors the XDG spec for empty XDG vars).
        let p = config_dir_from_env(
            Some("/nonexistent_home_for_cr39_test"),
            None,
            None,
            Some(""),
        );
        // No legacy dir at /nonexistent_home_for_cr39_test/.voice-typer,
        // so falls through to platform default.
        #[cfg(target_os = "linux")]
        assert_eq!(
            p,
            std::path::PathBuf::from("/nonexistent_home_for_cr39_test/.local/share/voice-typer"),
            "CR-39: empty VOICE_TYPER_CONFIG_DIR should be treated as unset"
        );
        #[cfg(target_os = "macos")]
        assert_eq!(
            p,
            std::path::PathBuf::from("/nonexistent_home_for_cr39_test/Library/Application Support/voice-typer"),
            "CR-39: empty VOICE_TYPER_CONFIG_DIR should be treated as unset"
        );
        #[cfg(target_os = "windows")]
        assert_eq!(
            p,
            std::path::PathBuf::from("/nonexistent_home_for_cr39_test/voice-typer"),
            "CR-39: empty VOICE_TYPER_CONFIG_DIR should be treated as unset"
        );
    }
}
