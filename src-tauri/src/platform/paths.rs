//! Per-platform config-dir resolution (ADR-0020 §8).

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
/// # No Electron userData merge under Tauri
///
/// ADR-0020 §8 mentions an optional one-time migration from the old
/// Electron `userData/voice-typer` directory to `<config_dir>` on
/// first Tauri launch. Under Tauri there is **no Electron main
/// process**, so no `userData/voice-typer` dir ever exists — the
/// migration step is a no-op and is intentionally NOT implemented
/// here. (If a future hybrid build ever needs it, the merge rules in
/// ADR-0020 §8 apply — newest-mtime-wins for `config.json`, append-
/// only for `history.db`, copy-only-absent for `models/`.)
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
pub(crate) fn config_dir(app: &tauri::AppHandle) -> std::path::PathBuf {
    let _ = app; // not used — env-var resolution matches Python `_paths.py`
    config_dir_from_env(
        std::env::var("HOME").ok().as_deref(),
        std::env::var("APPDATA").ok().as_deref(),
        std::env::var("XDG_DATA_HOME").ok().as_deref(),
    )
}

/// Pure form of `config_dir` for unit testing (no env-var reads).
pub(crate) fn config_dir_from_env(
    home: Option<&str>,
    appdata: Option<&str>,
    xdg_data_home: Option<&str>,
) -> std::path::PathBuf {
    const APP_NAME: &str = "voice-typer";
    #[cfg(target_os = "windows")]
    {
        let _ = home;
        let _ = xdg_data_home;
        let base = appdata.unwrap_or_else(|| {
            panic!("APPDATA env var must be set on Windows (config dir resolution)")
        });
        std::path::PathBuf::from(base).join(APP_NAME)
    }
    #[cfg(target_os = "macos")]
    {
        let _ = appdata;
        let _ = xdg_data_home;
        let home = home.unwrap_or_else(|| {
            panic!("HOME env var must be set on macOS (config dir resolution)")
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
        let home = home.unwrap_or_else(|| {
            panic!("HOME env var must be set on Linux (config dir resolution)")
        });
        std::path::PathBuf::from(home)
            .join(".local")
            .join("share")
            .join(APP_NAME)
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos", unix)))]
    {
        let _ = (home, appdata, xdg_data_home);
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
        let p = config_dir_from_env(Some("/home/user"), None, None);
        assert_eq!(
            p,
            std::path::PathBuf::from("/home/user/.local/share/voice-typer")
        );
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn test_config_dir_linux_xdg_set() {
        let p = config_dir_from_env(Some("/home/user"), None, Some("/custom/xdg"));
        assert_eq!(p, std::path::PathBuf::from("/custom/xdg/voice-typer"));
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn test_config_dir_linux_xdg_empty_falls_back_to_home() {
        // Empty XDG_DATA_HOME should be treated as unset (per XDG spec).
        let p = config_dir_from_env(Some("/home/user"), None, Some(""));
        assert_eq!(
            p,
            std::path::PathBuf::from("/home/user/.local/share/voice-typer")
        );
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn test_config_dir_macos() {
        let p = config_dir_from_env(Some("/Users/user"), None, None);
        assert_eq!(
            p,
            std::path::PathBuf::from("/Users/user/Library/Application Support/voice-typer")
        );
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn test_config_dir_windows() {
        let p = config_dir_from_env(None, Some(r"C:\Users\user\AppData\Roaming"), None);
        assert_eq!(
            p,
            std::path::PathBuf::from(r"C:\Users\user\AppData\Roaming\voice-typer")
        );
    }
}
