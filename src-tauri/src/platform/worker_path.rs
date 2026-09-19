
use std::sync::OnceLock;

use super::paths::APP_SLUG;

pub(crate) const DEFAULT_PACK_VERSION: &str = "v1";

/// Per-platform sub-directory name under the app-data root that holds
/// the versioned runtime-pack directories (`runtime-pack/<version>/`).
pub(crate) const RUNTIME_PACK_DIR: &str = "runtime-pack";

pub(crate) const WORKER_BIN_BASE_NAME: &str = "voice-typer-worker";

pub(crate) fn worker_exe_path() -> &'static std::path::Path {
    static CACHED: OnceLock<std::path::PathBuf> = OnceLock::new();
    CACHED.get_or_init(|| worker_exe_path_from_env(worker_exe_path_env_args(), &pack_version()))
}

pub(crate) fn pack_version() -> String {
    static CACHED: OnceLock<String> = OnceLock::new();
    CACHED
        .get_or_init(|| {
            std::env::var("VOICE_TYPER_PACK_VERSION")
                .ok()
                .filter(|v| !v.is_empty())
                .unwrap_or_else(|| DEFAULT_PACK_VERSION.to_string())
        })
        .clone()
}

pub(crate) fn worker_exe_path_from_env(
    env: WorkerPathEnv,
    pack_version: &str,
) -> std::path::PathBuf {
    let triple = crate::sidecar::spawn::target_triple::current_target_triple();
    let suffix = if cfg!(windows) { ".exe" } else { "" };
    let name = format!("{}-{}{}", WORKER_BIN_BASE_NAME, triple, suffix);
    pack_dir_from_env(env).join(pack_version).join(name)
}

pub(crate) fn pack_dir_from_env(env: WorkerPathEnv) -> std::path::PathBuf {
    let WorkerPathEnv {
        home,
        local_appdata,
        appdata: _,
        xdg_data_home,
        voice_typer_config_dir: _,
    } = env;

    // Normalize: treat empty-string env values as unset (mirrors the
    // XDG-spec rule applied in `config_dir_from_env`).
    let home = home.filter(|h| !h.is_empty());
    let local_appdata = local_appdata.filter(|a| !a.is_empty());
    let xdg_data_home = xdg_data_home.filter(|x| !x.is_empty());

    #[cfg(target_os = "windows")]
    {
        let _ = home;
        let _ = xdg_data_home;
        let base = local_appdata.unwrap_or_else(|| {
            let warn_msg = format!(
                "[worker_path] LOCALAPPDATA env var is not set: falling back to \
                 CWD-relative runtime-pack dir (./{}/{}). This is expected for \
                 Windows service accounts / headless CI but indicates a missing \
                 user profile in normal desktop sessions.",
                APP_SLUG, RUNTIME_PACK_DIR
            );
            eprintln!("{}", warn_msg);
            log::warn!("{}", warn_msg);
            "."
        });
        std::path::PathBuf::from(base)
            .join(APP_SLUG)
            .join(RUNTIME_PACK_DIR)
    }
    #[cfg(target_os = "macos")]
    {
        let _ = local_appdata;
        let _ = xdg_data_home;
        // macOS: ~/Library/Application Support/voice-typer/runtime-pack/
        let home = home.unwrap_or_else(|| {
            let warn_msg = format!(
                "[worker_path] HOME env var is not set: falling back to \
                 CWD-relative runtime-pack dir (./{}/{}). This is expected for \
                 system LaunchDaemons but indicates a missing user profile \
                 in normal desktop sessions.",
                APP_SLUG, RUNTIME_PACK_DIR
            );
            eprintln!("{}", warn_msg);
            log::warn!("{}", warn_msg);
            "."
        });
        std::path::PathBuf::from(home)
            .join("Library")
            .join("Application Support")
            .join(APP_SLUG)
            .join(RUNTIME_PACK_DIR)
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        let _ = local_appdata;
        // Linux: $XDG_DATA_HOME/voice-typer/runtime-pack/
        // (default ~/.local/share/voice-typer/runtime-pack/)
        if let Some(xdg) = xdg_data_home {
            return std::path::PathBuf::from(xdg)
                .join(APP_SLUG)
                .join(RUNTIME_PACK_DIR);
        }
        let Some(home) = home else {
            let warn_msg = format!(
                "[worker_path] HOME env var is not set: falling back to \
                 CWD-relative runtime-pack dir (./{}/{}). This is expected for \
                 systemd user units without `Environment=HOME=...` but \
                 indicates a missing user profile in normal desktop sessions.",
                APP_SLUG, RUNTIME_PACK_DIR
            );
            eprintln!("{}", warn_msg);
            log::warn!("{}", warn_msg);
            return std::path::PathBuf::from(".")
                .join(APP_SLUG)
                .join(RUNTIME_PACK_DIR);
        };
        std::path::PathBuf::from(home)
            .join(".local")
            .join("share")
            .join(APP_SLUG)
            .join(RUNTIME_PACK_DIR)
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos", unix)))]
    {
        let _ = (home, local_appdata, xdg_data_home);
        std::path::PathBuf::from(".")
            .join(APP_SLUG)
            .join(RUNTIME_PACK_DIR)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct WorkerPathEnv {
    pub(crate) home: Option<&'static str>,
    pub(crate) local_appdata: Option<&'static str>,
    pub(crate) appdata: Option<&'static str>,
    pub(crate) xdg_data_home: Option<&'static str>,
    pub(crate) voice_typer_config_dir: Option<&'static str>,
}

/// Collect the live env vars into a [`WorkerPathEnv`] bundle. Used by
/// [`worker_exe_path`] (the cached process-wide resolver).
fn worker_exe_path_env_args() -> WorkerPathEnv {
    // On Windows read USERPROFILE first, then fall back to HOME (mirrors
    // `config_dir_cached`'s home-resolution order).
    #[cfg(target_os = "windows")]
    let home: Option<String> = std::env::var("USERPROFILE")
        .ok()
        .or_else(|| std::env::var("HOME").ok());
    #[cfg(not(target_os = "windows"))]
    let home: Option<String> = std::env::var("HOME").ok();

    fn leak(s: String) -> &'static str {
        Box::leak(s.into_boxed_str())
    }
    WorkerPathEnv {
        home: home.map(leak),
        local_appdata: std::env::var("LOCALAPPDATA").ok().map(leak),
        appdata: std::env::var("APPDATA").ok().map(leak),
        xdg_data_home: std::env::var("XDG_DATA_HOME").ok().map(leak),
        voice_typer_config_dir: std::env::var("VOICE_TYPER_CONFIG_DIR").ok().map(leak),
    }
}

// Sibling test module: tests live in `worker_path_tests.rs` (per
// C-TEST-5: no inline `#[cfg(test)] mod tests` blocks in production
// source).
#[cfg(test)]
#[path = "worker_path_tests.rs"]
mod worker_path_tests;
