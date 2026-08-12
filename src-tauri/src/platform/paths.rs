//! Per-platform config-dir resolution (ADR-0020 §8).

use std::sync::OnceLock;

/// Machine-readable application slug used for the per-platform config-
/// dir leaf name (e.g. ``%APPDATA%/voice-typer`` on Windows). Distinct
/// from `crate::branding::APP_NAME` which is the human-readable display
/// name (``"Voice Typer"``). Kept here so the Tauri host and the Python
/// sidecar (`voice_typer/server/_paths.py::APP_SLUG`) can both
/// reference the same canonical slug — change it once here and all
/// paths stay in lockstep.
pub(crate) const APP_SLUG: &str = "voice-typer";

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
/// identifier (`com.voicetyper.desktop`), but the Python sidecar uses the
/// lowercase-hyphenated `voice-typer` directory (per `_paths.py`). To
/// keep the Rust host + Python sidecar reading/writing the SAME paths
/// byte-for-byte, we resolve from env vars directly, matching the
/// Python side's `_paths.config_dir()` resolution.
///
/// `app` parameter dropped
///
/// Previously this function took `app: &tauri::AppHandle` for
/// "forward-compatible migration to `app.path().app_config_dir()`".
/// The param was never used (`let _ = app;`), and the migration never
/// materialized — the env-var resolution is the steady-state path. The
/// dead param was flagged by and is now removed; all 3 callers
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
/// the prior doc here said "migration is a no-op and is
/// intentionally NOT implemented here" — that was stale; the
/// migration IS implemented in `migrate.rs` and has been running since
/// session-1's  fix.)
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
/// caching
///
/// Env vars are invariant for the process lifetime, but every call to
/// this function re-resolved 4 `std::env::var()` lookups. Under
/// flapping (supervisor respawn loops), `read/write_restart_counter`
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

/// process-wide cached config dir. Resolved once on first call;
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
        // On Windows read USERPROFILE first, then fall back to HOME.
        // The legacy `~/.voice-typer` migration check inside
        // `config_dir_from_env` uses `home` to probe for a leftover
        // `%USERPROFILE%\.voice-typer` dir from prior Electron
        // installs — if we only read `HOME` (which is usually unset
        // on Windows), the legacy check silently no-ops on Windows
        // and users upgrading from the Electron app lose their
        // config / history (split-brain: Tauri writes to the platform
        // default while the Python sidecar reads from `~/.voice-typer`).
        // Mirrors the Python side's `_config_dir()` order which uses
        // `Path.home()` (= USERPROFILE on Windows) for the legacy check.
        #[cfg(target_os = "windows")]
        let home: Option<String> = std::env::var("USERPROFILE")
            .ok()
            .or_else(|| std::env::var("HOME").ok());
        #[cfg(not(target_os = "windows"))]
        let home: Option<String> = std::env::var("HOME").ok();
        config_dir_from_env(
            home.as_deref(),
            std::env::var("APPDATA").ok().as_deref(),
            std::env::var("XDG_DATA_HOME").ok().as_deref(),
            // VOICE_TYPER_CONFIG_DIR env-var override — mirrors
            // Python's _config_dir() resolution order (env var → legacy →
            // platform default).
            std::env::var("VOICE_TYPER_CONFIG_DIR").ok().as_deref(),
        )
    })
}

/// Pure form of `config_dir` for unit testing (no env-var reads).
///
/// graceful fallback when env vars are missing
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
/// previously used `eprintln!` (which bypasses the rotating
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
/// legacy `~/.voice-typer` migration + env-var override
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
    // Normalize: treat empty-string env values as unset (mirrors the
    // XDG spec — an empty `XDG_DATA_HOME` is "as if unset"; we apply
    // the same rule to HOME / APPDATA / VOICE_TYPER_CONFIG_DIR so a
    // shell that does `export HOME=` (rare but possible — a broken
    // systemd unit, a misconfigured docker image) doesn't make us
    // build `PathBuf::from("").join(...)` which silently produces a
    // CWD-relative path. Without this filter the legacy
    // `~/.voice-typer` check would probe `.voice-typer` (relative to
    // CWD) instead of `<home>/.voice-typer` — a likely-nonexistent
    // path that quietly no-ops.
    let home = home.filter(|&h| !h.is_empty());
    let appdata = appdata.filter(|&a| !a.is_empty());
    let xdg_data_home = xdg_data_home.filter(|&x| !x.is_empty());
    let config_dir_env = config_dir_env.filter(|&c| !c.is_empty());

    // VOICE_TYPER_CONFIG_DIR env-var override. Mirrors the
    // Python side's _config_dir() resolution order: env var wins,
    // then legacy ~/.voice-typer, then platform default. Without this
    // check, a user who sets VOICE_TYPER_CONFIG_DIR (e.g. for a
    // portable / snap install) would have the Tauri host and Python
    // sidecar disagree on the config dir.
    //
    // SEC-005 / path-traversal guard: validate that the custom path
    // stays within the user's home directory. Mirrors the Python
    // side's `_validate_path_safety(custom_path, Path.home())` call
    // — a user who sets VOICE_TYPER_CONFIG_DIR=`/etc/passwd` (or a
    // `..` traversal) would otherwise be able to redirect config /
    // log / PID file writes outside their home directory. On
    // validation failure (traversal detected, custom doesn't exist,
    // home is None / doesn't exist), log a warning and fall through
    // to defaults rather than returning the unsafe path.
    if let Some(custom) = config_dir_env {
        let custom_path = std::path::PathBuf::from(custom);
        if let Some(h) = home {
            let home_path = std::path::Path::new(h);
            if validate_path_safety(&custom_path, home_path) {
                return custom_path;
            } else {
                let warn_msg = format!(
                    "[paths] VOICE_TYPER_CONFIG_DIR path traversal rejected: \
                     custom='{}' escapes home='{}' (or canonicalize failed). \
                     Falling through to defaults.",
                    custom, h
                );
                eprintln!("{}", warn_msg);
                log::warn!("{}", warn_msg);
            }
        } else {
            // `home` is None — we can't validate path safety. The
            // Python side's `Path.home()` raises RuntimeError in
            // this case; we instead log + fall through to defaults
            // (better than crashing during config-dir resolution).
            let warn_msg = format!(
                "[paths] VOICE_TYPER_CONFIG_DIR='{}' set but HOME is unset \
                 — cannot validate path safety, falling through to defaults.",
                custom
            );
            eprintln!("{}", warn_msg);
            log::warn!("{}", warn_msg);
        }
    }

    // legacy ~/.voice-typer check. Python's _config_dir() and
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
        // graceful fallback when APPDATA is missing (Windows
        // service accounts, headless CI). Previously panicked — now
        // returns `./voice-typer` relative to CWD and logs a warning.
        // The Tauri host's log file open at this path will fail loudly
        // if CWD is read-only, which is the correct behavior (better
        // than crashing during config-dir resolution).
        //
        // Empty-string APPDATA is treated as unset (filtered above) —
        // mirrors the XDG spec's "empty = unset" rule. Without this,
        // `PathBuf::from("").join(APP_SLUG)` would produce a relative
        // path `voice-typer` (CWD-relative, but without the `./` prefix
        // that `unwrap_or_else`'s `"."` fallback produces — a subtle
        // inconsistency).
        let base = appdata.unwrap_or_else(|| {
            // switched from `eprintln!` to `log::warn!`.
            // ALSO eprintln! so it lands on stderr regardless
            // of logger state (first call from main.rs happens BEFORE
            // init_file_logger, so log::warn! alone is silent).
            let warn_msg = format!(
                "[paths] APPDATA env var is not set — falling back to \
                 CWD-relative config dir (./{}). This is expected for \
                 Windows service accounts / headless CI but indicates \
                 a missing user profile in normal desktop sessions.",
                APP_SLUG
            );
            eprintln!("{}", warn_msg);
            log::warn!("{}", warn_msg);
            "."
        });
        std::path::PathBuf::from(base).join(APP_SLUG)
    }
    #[cfg(target_os = "macos")]
    {
        let _ = appdata;
        let _ = xdg_data_home;
        // graceful fallback when HOME is missing on macOS
        // (rare — `launchd` always sets HOME for user sessions, but
        // a system LaunchDaemon runs without it). Falls back to CWD.
        // Empty-string HOME is filtered to None above (XDG-spec rule).
        let home = home.unwrap_or_else(|| {
            // `log::warn!` (was `eprintln!`).
            // ALSO eprintln! so it lands on stderr regardless
            // of logger state.
            let warn_msg = format!(
                "[paths] HOME env var is not set — falling back to \
                 CWD-relative config dir (./{}). This is expected for \
                 system LaunchDaemons but indicates a missing user \
                 profile in normal desktop sessions.",
                APP_SLUG
            );
            eprintln!("{}", warn_msg);
            log::warn!("{}", warn_msg);
            "."
        });
        std::path::PathBuf::from(home)
            .join("Library")
            .join("Application Support")
            .join(APP_SLUG)
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        let _ = appdata;
        // XDG_DATA_HOME empty-string filter is now applied at the
        // top of `config_dir_from_env`, so the explicit `if !xdg.is_empty()`
        // check here is redundant — but kept defensively in case a
        // future refactor moves the normalization. (Defense in depth.)
        if let Some(xdg) = xdg_data_home {
            if !xdg.is_empty() {
                return std::path::PathBuf::from(xdg).join(APP_SLUG);
            }
        }
        // graceful fallback when HOME is missing on Linux
        // (systemd user units without `Environment=HOME=...`, or a
        // bare cron-spawned process). Falls back to CWD with a warning
        // — the XDG spec mandates HOME as a fallback when
        // XDG_DATA_HOME is unset, so the missing-HOME case is
        // technically undefined behavior per the spec; we choose a
        // CWD-relative path rather than panicking.
        // Empty-string HOME is filtered to None above (XDG-spec rule).
        let Some(home) = home else {
            // `log::warn!` (was `eprintln!`).
            // ALSO eprintln! so it lands on stderr regardless
            // of logger state.
            let warn_msg = format!(
                "[paths] HOME env var is not set — falling back to \
                 CWD-relative config dir (./{}). This is expected for \
                 systemd user units without `Environment=HOME=...` \
                 but indicates a missing user profile in normal \
                 desktop sessions.",
                APP_SLUG
            );
            eprintln!("{}", warn_msg);
            log::warn!("{}", warn_msg);
            // Mirrors the Windows / macOS branches: a missing HOME
            // falls back to the CWD-relative `./voice-typer`, NOT
            // `./.local/share/voice-typer` (the XDG default requires
            // HOME). Pinned by the paths_tests missing-HOME tests.
            return std::path::PathBuf::from(".").join(APP_SLUG);
        };
        std::path::PathBuf::from(home)
            .join(".local")
            .join("share")
            .join(APP_SLUG)
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos", unix)))]
    {
        let _ = (home, appdata, xdg_data_home, config_dir_env);
        std::path::PathBuf::from(".").join(APP_SLUG)
    }
}

/// Path-traversal guard for user-supplied env vars (SEC-005).
///
/// Ports Python's `_validate_path_safety(path, parent)` from
/// `voice_typer/server/config_internals/paths.py`. Returns `true` if
/// `custom` (after canonicalization) is equal to OR a descendant of
/// `home` (after canonicalization); `false` otherwise (including when
/// either path cannot be canonicalized — e.g. `home` doesn't exist,
/// or `custom` is a non-existent path whose parent doesn't exist
/// either).
///
/// # Why not `str::starts_with`?
///
/// The naive `str(custom).starts_with(str(home))` check is the classic
/// prefix-match bug: `/home/userX/secret` would be considered "within"
/// `/home/user` because the string starts with the prefix. Rust's
/// `Path::starts_with` correctly respects path-component boundaries
/// (`Path::new("/home/userX").starts_with("/home/user")` returns
/// `false`), so we use it after canonicalizing both sides.
///
/// # Canonicalization caveat
///
/// `std::fs::canonicalize` requires the path to EXIST (it resolves
/// symlinks by walking the filesystem). Python's `Path.resolve()`
/// (default `strict=False`) does NOT require existence — it just
/// canonicalizes what it can. To approximate Python's behavior for
/// the `custom` argument (which often points to a not-yet-existing
/// directory the user wants to set up), we fall back to canonicalizing
/// `custom.parent()` and re-appending the leaf name. The `home` side
/// is required to exist (it's the user's home directory — if it
/// doesn't exist, we have a bigger problem and rejecting is correct).
///
/// # Cross-platform
///
/// On Windows + macOS the default filesystem is case-insensitive, but
/// `Path::starts_with` is case-sensitive (compares `OsStr` byte-by-byte).
/// This is a known limitation — a Windows user who sets
/// `VOICE_TYPER_CONFIG_DIR=C:\\Users\\user\\config` when their
/// `USERPROFILE` is `c:\\Users\\user` (different case) would be
/// incorrectly rejected. We accept this tradeoff for now (case mismatches
/// in env vars are rare in practice) rather than pulling in a
/// case-insensitive path-comparison crate.
pub(crate) fn validate_path_safety(custom: &std::path::Path, home: &std::path::Path) -> bool {
    // Canonicalize `home` first — if it fails (home doesn't exist, or
    // symlink loop), we can't validate, so reject.
    let home_canon = match std::fs::canonicalize(home) {
        Ok(p) => p,
        Err(e) => {
            log::debug!(
                "[paths] validate_path_safety: canonicalize(home='{}') failed: {} — rejecting",
                home.display(),
                e
            );
            return false;
        }
    };
    // Canonicalize `custom`. If custom doesn't exist (the common case
    // for VOICE_TYPER_CONFIG_DIR pointing to a new dir the user wants
    // to create), fall back to canonicalizing the parent and re-
    // appending the leaf name. If the parent ALSO doesn't exist,
    // reject (the path is in uncharted territory).
    let custom_canon = match std::fs::canonicalize(custom) {
        Ok(p) => p,
        Err(_) => {
            // Custom doesn't exist — try parent.
            match custom.parent() {
                Some(parent) if !parent.as_os_str().is_empty() => {
                    match std::fs::canonicalize(parent) {
                        Ok(parent_canon) => {
                            // Re-append the leaf name.
                            match custom.file_name() {
                                Some(name) => parent_canon.join(name),
                                None => parent_canon,
                            }
                        }
                        Err(e) => {
                            log::debug!(
                                "[paths] validate_path_safety: canonicalize(parent='{}') failed: {} — rejecting",
                                parent.display(),
                                e
                            );
                            return false;
                        }
                    }
                }
                _ => {
                    log::debug!(
                        "[paths] validate_path_safety: custom='{}' has no parent — rejecting",
                        custom.display()
                    );
                    return false;
                }
            }
        }
    };
    // `Path::starts_with` respects path-component boundaries (unlike
    // `str::starts_with`). Returns true if `custom_canon` is equal to
    // OR a descendant of `home_canon`.
    let within = custom_canon.starts_with(&home_canon);
    if !within {
        log::debug!(
            "[paths] validate_path_safety: custom='{}' (canonicalized='{}') \
             is NOT within home='{}' (canonicalized='{}') — rejecting",
            custom.display(),
            custom_canon.display(),
            home.display(),
            home_canon.display()
        );
    }
    within
}

// Sibling test module — tests live in `paths_tests.rs` (per C-TEST-5:
// no inline `#[cfg(test)] mod tests` blocks in production source).
#[cfg(test)]
#[path = "paths_tests.rs"]
mod paths_tests;
