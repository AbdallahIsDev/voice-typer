//! Per-platform config-dir resolution (ADR-0020 §8).
//! NOTE: see docs/code-notes/tauri-host.md#config-dir

use std::sync::OnceLock;

/// Machine-readable slug for the config-dir leaf (not the display APP_NAME).
/// Twin: `voice_typer/server/_paths.py::APP_SLUG`.
pub(crate) const APP_SLUG: &str = "lausu";

// ─── ADR-0020 §8: per-platform config-dir resolution ─────────────────

pub(crate) fn config_dir() -> std::path::PathBuf {
    config_dir_cached().to_path_buf()
}

/// Process-wide OnceLock cache. Env vars are invariant for the process.
fn config_dir_cached() -> &'static std::path::Path {
    static CACHED: OnceLock<std::path::PathBuf> = OnceLock::new();
    CACHED.get_or_init(|| {
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
            std::env::var("VOICE_TYPER_CONFIG_DIR").ok().as_deref(),
        )
    })
}

pub(crate) fn config_dir_from_env(
    home: Option<&str>,
    appdata: Option<&str>,
    xdg_data_home: Option<&str>,
    config_dir_env: Option<&str>,
) -> std::path::PathBuf {
    let home = home.filter(|&h| !h.is_empty());
    let appdata = appdata.filter(|&a| !a.is_empty());
    let xdg_data_home = xdg_data_home.filter(|&x| !x.is_empty());
    let config_dir_env = config_dir_env.filter(|&c| !c.is_empty());

    // VOICE_TYPER_CONFIG_DIR env-var override. Mirrors the
    // Python side's _config_dir() resolution order: env var wins,
    // then legacy ~/.lausu, then platform default. Without this
    // check, a user who sets VOICE_TYPER_CONFIG_DIR (e.g. for a
    // portable / snap install) would have the Tauri host and Python
    // sidecar disagree on the config dir.
    //
    // SEC-005 / path-traversal guard: validate that the custom path
    // stays within the user's home directory. Mirrors the Python
    // side's `_validate_path_safety(custom_path, Path.home())` call
    //: a user who sets VOICE_TYPER_CONFIG_DIR=`/etc/passwd` (or a
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
            let warn_msg = format!(
                "[paths] VOICE_TYPER_CONFIG_DIR='{}' set but HOME is unset \
                : cannot validate path safety, falling through to defaults.",
                custom
            );
            eprintln!("{}", warn_msg);
            log::warn!("{}", warn_msg);
        }
    }

    if let Some(h) = home {
        let legacy = std::path::PathBuf::from(h).join(".lausu");
        if legacy.exists() {
            return legacy;
        }
    }

    #[cfg(target_os = "windows")]
    {
        let _ = home;
        let _ = xdg_data_home;
        let base = appdata.unwrap_or_else(|| {
            let warn_msg = format!(
                "[paths] APPDATA env var is not set: falling back to \
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
        let home = home.unwrap_or_else(|| {
            let warn_msg = format!(
                "[paths] HOME env var is not set: falling back to \
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
        if let Some(xdg) = xdg_data_home {
            if !xdg.is_empty() {
                return std::path::PathBuf::from(xdg).join(APP_SLUG);
            }
        }
        let Some(home) = home else {
            let warn_msg = format!(
                "[paths] HOME env var is not set: falling back to \
                 CWD-relative config dir (./{}). This is expected for \
                 systemd user units without `Environment=HOME=...` \
                 but indicates a missing user profile in normal \
                 desktop sessions.",
                APP_SLUG
            );
            eprintln!("{}", warn_msg);
            log::warn!("{}", warn_msg);
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
/// either path cannot be canonicalized, e.g. `home` doesn't exist,
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
/// (default `strict=False`) does NOT require existence, it just
/// canonicalizes what it can. To approximate Python's behavior for
/// the `custom` argument (which often points to a not-yet-existing
/// directory the user wants to set up), we fall back to canonicalizing
/// `custom.parent()` and re-appending the leaf name. The `home` side
/// is required to exist (it's the user's home directory, if it
/// doesn't exist, we have a bigger problem and rejecting is correct).
///
/// # Cross-platform
///
/// On Windows + macOS the default filesystem is case-insensitive, but
/// `Path::starts_with` is case-sensitive (compares `OsStr` byte-by-byte).
/// This is a known limitation, a Windows user who sets
/// `VOICE_TYPER_CONFIG_DIR=C:\\Users\\user\\config` when their
/// `USERPROFILE` is `c:\\Users\\user` (different case) would be
/// incorrectly rejected. We accept this tradeoff for now (case mismatches
/// in env vars are rare in practice) rather than pulling in a
/// case-insensitive path-comparison crate.
pub(crate) fn validate_path_safety(custom: &std::path::Path, home: &std::path::Path) -> bool {
    // Canonicalize `home` first: if it fails (home doesn't exist, or
    // symlink loop), we can't validate, so reject.
    let home_canon = match std::fs::canonicalize(home) {
        Ok(p) => p,
        Err(e) => {
            log::debug!(
                "[paths] validate_path_safety: canonicalize(home='{}') failed: {}, rejecting",
                home.display(),
                e
            );
            return false;
        }
    };
    let custom_canon = match std::fs::canonicalize(custom) {
        Ok(p) => p,
        Err(_) => {
            // Custom doesn't exist: try parent.
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
                                "[paths] validate_path_safety: canonicalize(parent='{}') failed: {}, rejecting",
                                parent.display(),
                                e
                            );
                            return false;
                        }
                    }
                }
                _ => {
                    log::debug!(
                        "[paths] validate_path_safety: custom='{}' has no parent, rejecting",
                        custom.display()
                    );
                    return false;
                }
            }
        }
    };
    let within = custom_canon.starts_with(&home_canon);
    if !within {
        log::debug!(
            "[paths] validate_path_safety: custom='{}' (canonicalized='{}') \
             is NOT within home='{}' (canonicalized='{}'): rejecting",
            custom.display(),
            custom_canon.display(),
            home.display(),
            home_canon.display()
        );
    }
    within
}

// Sibling test module: tests live in `paths_tests.rs` (per C-TEST-5:
// no inline `#[cfg(test)] mod tests` blocks in production source).
#[cfg(test)]
#[path = "paths_tests.rs"]
mod paths_tests;
