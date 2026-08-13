//! Per-platform worker-exe path resolution (Phase 2a — runtime-pack split,
//! plan-runtime-pack-split §4.4 + §4.7).
//!
//! Modeled on [`super::paths`] (`config_dir` + `config_dir_from_env`):
//!
//! - [`worker_exe_path`] — process-wide cached worker exe path. Resolves
//!   the runtime-pack directory (per-platform) + the worker exe name
//!   (`voice-typer-worker-<triple>[.exe]`), reading the active pack
//!   version from [`pack_version`].
//! - [`worker_exe_path_from_env`] — pure form for unit testing (no
//!   `std::env` reads; takes all env vars + the pack version as args).
//! - [`pack_version`] — reads `VOICE_TYPER_PACK_VERSION` env var (set
//!   by the slim-core sidecar after the pack is downloaded + verified),
//!   falling back to [`DEFAULT_PACK_VERSION`] when unset. The version
//!   is the directory leaf name under the per-platform runtime-pack
//!   root (`%LOCALAPPDATA%\voice-typer\runtime-pack\<version>\` etc).
//! - [`pack_dir_from_env`] — per-platform runtime-pack root (without
//!   the version leaf + worker exe name), used by the integrity
//!   verifier (`verify_pack_or_skip`, future) + the downloader.
//!
//! # Path table (§4.7)
//!
//! | Platform | Path |
//! |---|---|
//! | Windows | `%LOCALAPPDATA%\voice-typer\runtime-pack\<version>\voice-typer-worker-<triple>.exe` |
//! | Linux   | `$XDG_DATA_HOME/voice-typer/runtime-pack/<version>/voice-typer-worker-<triple>` (default `~/.local/share/voice-typer/runtime-pack/<version>/`) |
//! | macOS   | `~/Library/Application Support/voice-typer/runtime-pack/<version>/voice-typer-worker-<triple>` |
//!
//! Note: Windows uses `%LOCALAPPDATA%` (NOT `%APPDATA%` — the sidecar's
//! `config_dir` uses `%APPDATA%` for roaming config, but the runtime
//! pack is a per-machine cache that should NOT roam with the user
//! profile). The slim-core sidecar's `config_dir` resolution stays on
//! `%APPDATA%` for backwards compat with the legacy `~/.voice-typer`
//! migration; the runtime-pack dir is a NEW concern with no legacy
//! path to honor.
//!
//! # Why a separate module (not extend `paths.rs`)
//!
//! `paths.rs` resolves the CONFIG dir (roaming, legacy-aware, single
//! leaf `voice-typer`). The runtime-pack dir is a separate concern:
//! - Different base env var (`LOCALAPPDATA` on Windows, not `APPDATA`).
//! - Versioned leaf (`runtime-pack/<version>/`).
//! - Different child file (`voice-typer-worker-<triple>[.exe]`, not
//!   a Tauri-bundled resource).
//! - No legacy `~/.voice-typer` migration probe — the pack is a
//!   Phase 2a addition with no pre-existing on-disk state to honor.
//!
//! Keeping it in a separate module avoids entangling the two concerns
//! in `paths.rs` (which already has 479 LOC of legacy + caching logic).
//!
//! # Caching
//!
//! Env vars + pack version are invariant for the process lifetime
//! (the pack is downloaded BEFORE the worker spawns; the version is
//! fixed once the worker starts). [`worker_exe_path`] caches the
//! resolved `PathBuf` in a `OnceLock` so the env-var + version reads
//! happen exactly once per process — mirrors the `config_dir_cached`
//! pattern in `paths.rs`.

use std::sync::OnceLock;

use super::paths::APP_SLUG;

/// Default pack version used when `VOICE_TYPER_PACK_VERSION` is unset
/// (e.g. dev mode before the pack downloader is wired up, or a fresh
/// install before the first pack download completes). The version is
/// a directory leaf name under the per-platform runtime-pack root.
///
/// `v1` is the initial pack version (Phase 2a skeleton). Real pack
/// releases use semver-style versions like `v1.2.0`; the bare `v1`
/// default is a dev-only sentinel — production code paths set the
/// env var explicitly via `spawn_worker_*` after `verify_pack_or_skip`.
pub(crate) const DEFAULT_PACK_VERSION: &str = "v1";

/// Per-platform sub-directory name under the app-data root that holds
/// the versioned runtime-pack directories (`runtime-pack/<version>/`).
pub(crate) const RUNTIME_PACK_DIR: &str = "runtime-pack";

/// Worker exe base name (without the target-triple suffix + `.exe`).
/// Tauri's `externalBin` mechanism appends the target triple at runtime
/// (see ADR-0020 §4.1 + plan-runtime-pack-split §4.4).
pub(crate) const WORKER_BIN_BASE_NAME: &str = "voice-typer-worker";

/// Process-wide cached worker exe path. Resolves once on first call;
/// subsequent calls return the cached `Path` via a `OnceLock`. The
/// env vars + pack version are invariant for the process lifetime
/// (the pack is downloaded BEFORE the worker spawns), so caching is
/// safe — a `setenv` mid-process would not be reflected, but that's
/// never legitimate (the Python side reads env vars once at startup
/// too; the worker is a long-lived child started once after pack
/// verification).
///
/// Returns a `&'static Path` so callers can avoid the `PathBuf` clone
/// when they only need to read.
pub(crate) fn worker_exe_path() -> &'static std::path::Path {
    static CACHED: OnceLock<std::path::PathBuf> = OnceLock::new();
    CACHED.get_or_init(|| worker_exe_path_from_env(
        worker_exe_path_env_args(),
        &pack_version(),
    ))
}

/// Returns the active pack version (the directory leaf name under the
/// per-platform runtime-pack root). Reads `VOICE_TYPER_PACK_VERSION`
/// once and caches it (the version is fixed once the worker starts).
///
/// Mirrors `config_dir_cached`'s caching pattern — env vars are
/// invariant for the process lifetime.
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

/// Pure form of [`worker_exe_path`] for unit testing — accepts all
/// env vars + the pack version as args so tests can verify the
/// per-platform resolution without polluting the process environment.
///
/// The `env` tuple is `(home, local_appdata, appdata, xdg_data_home,
/// voice_typer_config_dir)` — a superset of `config_dir_from_env`'s
/// args (we accept all of them so the same env-var mocking helper can
/// drive both). `pack_version` is the version leaf under
/// `runtime-pack/`.
///
/// Returns the absolute worker exe path, e.g. on Windows:
///   `C:\Users\Alice\AppData\Local\voice-typer\runtime-pack\v1\voice-typer-worker-x86_64-pc-windows-msvc.exe`
///
/// On all platforms the path is constructed by joining
/// `pack_dir_from_env(env)` + `pack_version` + the worker exe name
/// (with the target-triple suffix + optional `.exe`).
pub(crate) fn worker_exe_path_from_env(
    env: WorkerPathEnv,
    pack_version: &str,
) -> std::path::PathBuf {
    let triple = crate::sidecar::spawn::target_triple::current_target_triple();
    let suffix = if cfg!(windows) { ".exe" } else { "" };
    let name = format!("{}-{}{}", WORKER_BIN_BASE_NAME, triple, suffix);
    pack_dir_from_env(env)
        .join(pack_version)
        .join(name)
}

/// Per-platform runtime-pack directory (the parent of the versioned
/// pack leaf — i.e. `…/voice-typer/runtime-pack/`, WITHOUT the
/// `<version>/voice-typer-worker-<triple>` tail). Used by the
/// integrity verifier + the downloader to enumerate installed packs.
///
/// Pure form for unit testing. Mirrors `config_dir_from_env`'s
/// signature shape (takes the env vars as args) but resolves to the
/// LOCALAPPDATA / XDG_DATA_HOME / macOS Application Support base
/// (NOT the same dir as `config_dir`, which uses APPDATA on Windows).
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
        // Windows: %LOCALAPPDATA%\voice-typer\runtime-pack\
        // Falls back to `./voice-typer/runtime-pack` (CWD-relative)
        // when LOCALAPPDATA is unset (Windows service accounts,
        // headless CI). Mirrors `config_dir_from_env`'s APPDATA
        // fallback philosophy — never panic during path resolution.
        let base = local_appdata.unwrap_or_else(|| {
            let warn_msg = format!(
                "[worker_path] LOCALAPPDATA env var is not set — falling back to \
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
                "[worker_path] HOME env var is not set — falling back to \
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
                "[worker_path] HOME env var is not set — falling back to \
                 CWD-relative runtime-pack dir (./{}/{}). This is expected for \
                 systemd user units without `Environment=HOME=...` but \
                 indicates a missing user profile in normal desktop sessions.",
                APP_SLUG, RUNTIME_PACK_DIR
            );
            eprintln!("{}", warn_msg);
            log::warn!("{}", warn_msg);
            return std::path::PathBuf::from(".").join(APP_SLUG).join(RUNTIME_PACK_DIR);
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
        std::path::PathBuf::from(".").join(APP_SLUG).join(RUNTIME_PACK_DIR)
    }
}

/// Env-var bundle consumed by [`pack_dir_from_env`] / [`worker_exe_path_from_env`].
///
/// Field order matches the call shape of `config_dir_from_env` (home,
/// appdata, xdg_data_home) with two additions: `local_appdata` (Windows
/// LOCALAPPDATA — the runtime-pack base, distinct from APPDATA used by
/// the config dir) and `voice_typer_config_dir` (the env-var override,
/// reserved for future use — the runtime-pack dir does NOT honor
/// `VOICE_TYPER_CONFIG_DIR` today because the pack is a cache, not
/// user-tunable config, but we accept it for forward-compat + so the
/// same test helper can drive both resolvers).
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

    // Leak the env-var strings to `'static` so they fit `WorkerPathEnv`'s
    // `&'static str` fields. This is a one-time, process-lifetime
    // allocation under the `OnceLock` in `worker_exe_path` — the env
    // vars are invariant for the process lifetime, so the leak is
    // bounded (5 strings × ~50 bytes each, exactly once per process).
    // The alternative is to make `WorkerPathEnv` own `Option<String>`s
    // + clone them on every test call, but the `'static` form keeps
    // the test-API signature simple (no `String` allocations in test
    // assertions).
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

// Sibling test module — tests live in `worker_path_tests.rs` (per
// C-TEST-5: no inline `#[cfg(test)] mod tests` blocks in production
// source).
#[cfg(test)]
#[path = "worker_path_tests.rs"]
mod worker_path_tests;
