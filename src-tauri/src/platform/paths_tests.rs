//! Sibling tests for `platform::paths` (per C-TEST-5 — sibling test
//! file, no inline tests in production source).
//!
//! Covers three behaviors of `config_dir` / `config_dir_from_env`:
//!
//! - **Path-safety guard**: `validate_path_safety` rejects path-
//!   traversal attempts and accepts legitimate paths within `home`.
//! - **Empty-string env vars treated as unset**: empty `HOME` /
//!   `APPDATA` / `XDG_DATA_HOME` are filtered to `None` at the top of
//!   `config_dir_from_env` (mirrors the XDG spec).
//! - **Per-platform resolution + legacy `~/.voice-typer` migration**:
//!   the pre-existing tests (moved verbatim from the legacy inline
//!   `mod tests` block) pin the per-platform default dir, the
//!   missing-HOME CWD fallback, and the legacy `~/.voice-typer`
//!   override behavior.

use super::*;

// ── validate_path_safety ───────────────────────────────────────────

/// A `custom` path that is a descendant of `home` is accepted.
/// This is the happy path — the user sets
/// `VOICE_TYPER_CONFIG_DIR=~/voice-typer-custom` and we accept it.
#[test]
fn test_validate_path_safety_accepts_descendant() {
    use std::fs;
    use std::time::SystemTime;
    let tmp = std::env::temp_dir().join(format!(
        "vt_paths_gp24_desc_{}_{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(SystemTime::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    fs::create_dir_all(tmp.join("subdir")).unwrap();
    let home = tmp.as_path();
    let custom = tmp.join("subdir");
    assert!(
        validate_path_safety(&custom, home),
        "a descendant of home should be accepted (custom='{}', home='{}')",
        custom.display(),
        home.display()
    );
    fs::remove_dir_all(&tmp).ok();
}

/// A `custom` path that EQUALS `home` is accepted (the user wants
/// their config dir to BE their home — unusual but legitimate).
#[test]
fn test_validate_path_safety_accepts_equal() {
    use std::fs;
    use std::time::SystemTime;
    let tmp = std::env::temp_dir().join(format!(
        "vt_paths_gp24_eq_{}_{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(SystemTime::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    fs::create_dir_all(&tmp).unwrap();
    let home = tmp.as_path();
    assert!(
        validate_path_safety(home, home),
        "custom == home should be accepted (Path::starts_with returns true for self)"
    );
    fs::remove_dir_all(&tmp).ok();
}

/// A `custom` path that ESCAPES `home` is rejected. This is the
/// SEC-005 path-traversal guard — without it, a user could set
/// `VOICE_TYPER_CONFIG_DIR=/etc` and the Tauri host would happily
/// write log/PID files there.
#[test]
fn test_validate_path_safety_rejects_escape() {
    use std::fs;
    use std::time::SystemTime;
    let home_tmp = std::env::temp_dir().join(format!(
        "vt_paths_gp24_home_{}_{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(SystemTime::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    let outside_tmp = std::env::temp_dir().join(format!(
        "vt_paths_gp24_outside_{}_{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(SystemTime::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    fs::create_dir_all(&home_tmp).unwrap();
    fs::create_dir_all(&outside_tmp).unwrap();
    assert!(
        !validate_path_safety(&outside_tmp, &home_tmp),
        "a path outside home should be rejected (custom='{}', home='{}')",
        outside_tmp.display(),
        home_tmp.display()
    );
    fs::remove_dir_all(&home_tmp).ok();
    fs::remove_dir_all(&outside_tmp).ok();
}

/// The classic prefix-match bug: `/home/userX` should NOT be
/// considered "within" `/home/user`. `Path::starts_with` (used after
/// canonicalization) correctly respects path-component boundaries.
/// This pins the SEC-005 fix for the prefix-match regression that
/// Python's prior `str.startswith` had (and which the Python side
/// fixed by switching to `os.path.commonpath`).
#[test]
fn test_validate_path_safety_rejects_prefix_match_bug() {
    use std::fs;
    use std::time::SystemTime;
    let parent = std::env::temp_dir().join(format!(
        "vt_paths_gp24_user_{}_{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(SystemTime::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    // Sibling whose name starts with `parent`'s name + "X".
    let sibling = parent.with_file_name(format!(
        "{}X",
        parent.file_name().unwrap().to_str().unwrap()
    ));
    fs::create_dir_all(&parent).unwrap();
    fs::create_dir_all(&sibling).unwrap();
    assert!(
        !validate_path_safety(&sibling, &parent),
        "prefix-match bug: '{}' should NOT be considered within '{}' \
         (Path::starts_with respects component boundaries)",
        sibling.display(),
        parent.display()
    );
    fs::remove_dir_all(&parent).ok();
    fs::remove_dir_all(&sibling).ok();
}

/// A non-existent `custom` path with an EXISTING parent inside
/// `home` is accepted — the parent-fallback in `validate_path_safety`
/// canonicalizes the parent and re-appends the leaf name. This
/// mirrors Python's `Path.resolve(strict=False)` which doesn't require
/// existence.
#[test]
fn test_validate_path_safety_accepts_nonexistent_custom_with_existing_parent() {
    use std::fs;
    use std::time::SystemTime;
    let home = std::env::temp_dir().join(format!(
        "vt_paths_gp24_ne_{}_{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(SystemTime::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    fs::create_dir_all(&home).unwrap();
    // A non-existent subdir of an existing parent (home).
    let custom = home.join("does-not-exist-yet");
    assert!(
        validate_path_safety(&custom, &home),
        "a non-existent custom with existing parent inside home should be accepted \
         (mirrors Python Path.resolve(strict=False))"
    );
    fs::remove_dir_all(&home).ok();
}

/// A non-existent `home` causes `validate_path_safety` to reject
/// (return false) — `canonicalize(home)` fails. This is the correct
/// behavior: if home doesn't exist, we have a bigger problem and
/// rejecting is safer than guessing.
#[test]
fn test_validate_path_safety_rejects_nonexistent_home() {
    let bogus_home = std::path::Path::new("/this/path/does/not/exist/gp24-test");
    let custom = bogus_home.join("subdir");
    assert!(
        !validate_path_safety(&custom, bogus_home),
        "non-existent home should be rejected (canonicalize fails)"
    );
}

// ── Empty-string env vars treated as unset ─────────────────────────

/// An empty `HOME` value should be treated as unset (filtered to None
/// at the top of `config_dir_from_env`). On Linux, this triggers the
/// missing-HOME → CWD fallback (`./voice-typer`) rather than building
/// `PathBuf::from("").join(".local").join("share").join("voice-typer")`
/// which would produce a relative `.local/share/voice-typer` path
/// (CWD-relative but without the `./` prefix — a subtle inconsistency).
#[cfg(target_os = "linux")]
#[test]
fn test_config_dir_empty_home_treated_as_unset_linux() {
    let p = config_dir_from_env(Some(""), None, None, None);
    assert_eq!(
        p,
        std::path::PathBuf::from("./voice-typer"),
        "empty HOME should be treated as unset (XDG-spec rule) → CWD fallback"
    );
}

/// An empty `APPDATA` on Windows should be treated as unset → CWD
/// fallback (`./voice-typer`).
#[cfg(target_os = "windows")]
#[test]
fn test_config_dir_empty_appdata_treated_as_unset_windows() {
    let p = config_dir_from_env(None, Some(""), None, None);
    assert_eq!(
        p,
        std::path::PathBuf::from(".").join(APP_SLUG),
        "empty APPDATA should be treated as unset (XDG-spec rule) → CWD fallback"
    );
}

/// An empty `XDG_DATA_HOME` on Linux should be treated as unset →
/// fall back to HOME-based path. This was already handled inline in
/// the Linux branch (`if !xdg.is_empty()`), but the empty-string filter moves
/// the filter to the top of `config_dir_from_env` so the rule applies
/// uniformly. The pre-existing test
/// `test_config_dir_linux_xdg_empty_falls_back_to_home` covers the
/// happy path; this test covers the empty-HOME + empty-XDG combo.
#[cfg(target_os = "linux")]
#[test]
fn test_config_dir_empty_home_and_empty_xdg_treated_as_unset_linux() {
    let p = config_dir_from_env(Some(""), None, Some(""), None);
    assert_eq!(
        p,
        std::path::PathBuf::from("./voice-typer"),
        "empty HOME + empty XDG_DATA_HOME → both treated as unset → CWD fallback"
    );
}

// ── VOICE_TYPER_CONFIG_DIR traversal rejection ────────────────────

/// When `VOICE_TYPER_CONFIG_DIR` points to a path OUTSIDE `home`,
/// the override is rejected and the function falls through to the
/// platform default. This is the SEC-005 path-traversal guard —
/// without it, a malicious or confused user could set
/// `VOICE_TYPER_CONFIG_DIR=/etc` and have the Tauri host write
/// log/PID files there.
#[test]
fn test_config_dir_voice_typer_config_dir_traversal_rejected() {
    use std::fs;
    use std::time::SystemTime;
    let home = std::env::temp_dir().join(format!(
        "vt_paths_gp24_trav_home_{}_{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(SystemTime::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    let outside = std::env::temp_dir().join(format!(
        "vt_paths_gp24_trav_outside_{}_{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(SystemTime::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    fs::create_dir_all(&home).unwrap();
    fs::create_dir_all(&outside).unwrap();
    let p = config_dir_from_env(
        Some(home.to_str().unwrap()),
        None,
        None,
        Some(outside.to_str().unwrap()),
    );
    // The override should be REJECTED — falls through to the platform
    // default. On Linux that's `home/.local/share/voice-typer`; on
    // macOS `home/Library/Application Support/voice-typer`; on Windows
    // (where home is USERPROFILE but config_dir_from_env's Windows
    // branch ignores home) it's the CWD fallback `./voice-typer`.
    #[cfg(target_os = "linux")]
    assert_eq!(
        p,
        home.join(".local").join("share").join(APP_SLUG),
        "VOICE_TYPER_CONFIG_DIR traversal should be rejected, \
         falling through to the Linux platform default"
    );
    #[cfg(target_os = "macos")]
    assert_eq!(
        p,
        home.join("Library")
            .join("Application Support")
            .join(APP_SLUG),
        "VOICE_TYPER_CONFIG_DIR traversal should be rejected, \
         falling through to the macOS platform default"
    );
    #[cfg(target_os = "windows")]
    assert_eq!(
        p,
        std::path::PathBuf::from(".").join(APP_SLUG),
        "VOICE_TYPER_CONFIG_DIR traversal should be rejected, \
         falling through to the Windows CWD fallback"
    );
    fs::remove_dir_all(&home).ok();
    fs::remove_dir_all(&outside).ok();
}

/// When `VOICE_TYPER_CONFIG_DIR` is set but `HOME` is None (or
/// empty), validation can't proceed — the function logs a warning
/// and falls through to defaults. This is more graceful than the
/// Python side's behavior (where `Path.home()` raises RuntimeError
/// if home is unset, crashing the host).
#[test]
fn test_config_dir_voice_typer_config_dir_no_home_falls_through() {
    let p = config_dir_from_env(None, None, None, Some("/some/custom/path"));
    // HOME is None → can't validate → fall through to defaults.
    #[cfg(target_os = "linux")]
    assert_eq!(
        p,
        std::path::PathBuf::from("./voice-typer"),
        "VOICE_TYPER_CONFIG_DIR with no HOME should fall through to CWD fallback"
    );
    #[cfg(target_os = "macos")]
    assert_eq!(
        p,
        std::path::PathBuf::from("./Library/Application Support/voice-typer"),
        "VOICE_TYPER_CONFIG_DIR with no HOME should fall through to CWD fallback"
    );
    #[cfg(target_os = "windows")]
    assert_eq!(
        p,
        std::path::PathBuf::from(".").join(APP_SLUG),
        "VOICE_TYPER_CONFIG_DIR with no HOME should fall through to CWD fallback"
    );
}

// ── Pre-existing per-platform resolution tests ─────────────────────
// (Moved verbatim from the legacy inline
//  block to comply with C-TEST-5. These tests were updated to
//  create real tmpdirs when exercising the path-safety override
//  so they exercise the new validate_path_safety guard.)

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

// graceful fallback when env vars are missing ──────────
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
    // when HOME is missing AND XDG_DATA_HOME is unset,
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
    //when HOME is missing on macOS (system LaunchDaemon),
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
    // when APPDATA is missing on Windows (service account),
    // the function must NOT panic — it returns `./voice-typer`.
    let p = config_dir_from_env(None, None, None, None);
    assert_eq!(
        p,
        std::path::PathBuf::from("./voice-typer"),
        "missing APPDATA on Windows should fall back to CWD-relative voice-typer dir (NF-R9-8)"
    );
}

// legacy ~/.voice-typer check + VOICE_TYPER_CONFIG_DIR override ──
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
    // if ~/.voice-typer exists, it should be returned in
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
    let p = config_dir_from_env(Some(tmp.to_str().unwrap()), None, None, None);
    assert_eq!(
        p,
        tmp.join(".voice-typer"),
        "CR-39: existing ~/.voice-typer should win over platform default"
    );
    fs::remove_dir_all(&tmp).ok();
}

#[test]
fn test_config_dir_voice_typer_config_dir_env_override() {
    // VOICE_TYPER_CONFIG_DIR env var wins over legacy and
    // platform default — but ONLY when the custom path is
    // safely within the user's home directory (SEC-005 path-
    // traversal guard). We create a real tmpdir as `home` and
    // a real subdir as `custom` so `validate_path_safety`'s
    // `canonicalize` calls succeed.
    use std::fs;
    use std::time::SystemTime;
    let tmp = std::env::temp_dir().join(format!(
        "vt_paths_override_test_{}_{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(SystemTime::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    let custom = tmp.join("custom-config-dir");
    fs::create_dir_all(&custom).unwrap();
    let p = config_dir_from_env(
        Some(tmp.to_str().unwrap()),
        None,
        None,
        Some(custom.to_str().unwrap()),
    );
    assert_eq!(
        p, custom,
        "VOICE_TYPER_CONFIG_DIR env var should override platform default when path is safe"
    );
    fs::remove_dir_all(&tmp).ok();
}

#[test]
fn test_config_dir_env_override_beats_legacy_check() {
    // env var wins over legacy ~/.voice-typer check — but ONLY
    // when the custom path is safely within the user's home
    // directory (SEC-005 path-traversal guard). We create a
    // real tmpdir as `home` (with a `~/.voice-typer` subdir to
    // exercise the legacy check) and a real subdir as `custom`.
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
    let custom = tmp.join("explicit-override");
    fs::create_dir_all(&custom).unwrap();
    let p = config_dir_from_env(
        Some(tmp.to_str().unwrap()),
        None,
        None,
        Some(custom.to_str().unwrap()),
    );
    assert_eq!(
        p, custom,
        "VOICE_TYPER_CONFIG_DIR env var should win over legacy ~/.voice-typer when path is safe"
    );
    fs::remove_dir_all(&tmp).ok();
}

#[test]
fn test_config_dir_empty_env_override_falls_through() {
    // an empty VOICE_TYPER_CONFIG_DIR value should be
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
        std::path::PathBuf::from(
            "/nonexistent_home_for_cr39_test/Library/Application Support/voice-typer"
        ),
        "CR-39: empty VOICE_TYPER_CONFIG_DIR should be treated as unset"
    );
    #[cfg(target_os = "windows")]
    {
        // Windows ignores `home` — the config dir is APPDATA-based
        // (or the CWD-relative `./voice-typer` fallback when
        // APPDATA is missing, as here). So the empty override falls
        // through to the documented CWD fallback, NOT
        // `home/voice-typer`. Matches the `appdata.unwrap_or_else`
        // path in `config_dir_from_env`.
        assert_eq!(
            p,
            std::path::PathBuf::from(".").join(APP_SLUG),
            "CR-39: empty VOICE_TYPER_CONFIG_DIR should be treated as unset (CWD fallback)"
        );
    }
}
