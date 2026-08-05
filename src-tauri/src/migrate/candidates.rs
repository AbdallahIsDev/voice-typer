//! Platform probe for the OLD Electron `userData` directory candidates.
//!
//! Extracted from the original `migrate.rs` monolith as part of the
//! Phase 4.5 split. Pure file move — no behavior change. See
//! `mod.rs` for the migration orchestration that consumes these
//! candidates.
//!
//! The caller probes each candidate in turn and uses the first one
//! that exists on disk. Returns an empty `Vec` if the platform's
//! relevant env vars are missing (caller treats that as "nothing to
//! migrate" — safe no-op).

use std::path::PathBuf;

/// Resolve the OLD Electron `userData` directory candidates per platform.
///
/// Returns a list of candidate paths in probe order (most-likely first).
/// The caller probes each in turn and uses the first one that exists on
/// disk. Returns an empty `Vec` if the platform's relevant env vars are
/// missing (caller treats that as "nothing to migrate" — safe no-op).
///
//fix: the previous implementation only probed `Voice Typer`
/// (capital+space), which was NEVER the actual Electron `userData` name.
/// `voice_typer/client/package.json:2` declares `"name": "voice-typer-desktop"`
/// (lowercase, hyphen) and `bootstrap.ts:52-67` `setupUserData` overrides
/// the path to `computeConfigDir()` which returns `voice-typer` (lowercase,
/// hyphen). The old migration was dead code: it always returned "nothing
/// to do" and wrote the sentinel marker immediately, silently losing any
/// old Electron config that DID exist under `voice-typer-desktop`.
pub(crate) fn electron_userdata_candidates() -> Vec<PathBuf> {
    /// The three Electron `userData` directory names ever used, in probe
    /// order. See the module-level docstring for the naming history.
    const CANDIDATE_NAMES: &[&str] = &[
        // 1. Very old Electron builds (no `setupUserData`): Electron
        // derived the default `userData` path from `package.json`
        // `name` = `voice-typer-desktop`.
        "voice-typer-desktop",
        // 2. Newer Electron builds with `setupUserData` (bootstrap.ts:52-67):
        // `app.setPath("userData", computeConfigDir())` → `voice-typer`.
        // This is the SAME path Tauri now uses as its `config_dir`, so
        // the caller skips it when it equals the Tauri target.
        crate::platform::paths::APP_SLUG,
        // 3. Defensive third probe — the human-readable brand name with a
        // space, in case some ancient unreleased build used it as the
        // userData directory name. Uses `crate::branding::APP_NAME`
        // (const-context) so the probe stays in lockstep with the rest
        // of the UI's brand string.
        crate::branding::APP_NAME,
    ];

    #[cfg(target_os = "windows")]
    {
        let Some(appdata) = std::env::var("APPDATA").ok() else {
            return Vec::new();
        };
        let base = PathBuf::from(appdata);
        CANDIDATE_NAMES.iter().map(|n| base.join(n)).collect()
    }
    #[cfg(target_os = "macos")]
    {
        let Some(home) = std::env::var("HOME").ok() else {
            return Vec::new();
        };
        let base = PathBuf::from(home)
            .join("Library")
            .join("Application Support");
        CANDIDATE_NAMES.iter().map(|n| base.join(n)).collect()
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        // Linux: Electron's userData defaults to `~/.config/<name>` when
        // XDG_CONFIG_HOME is unset; honor it if present.
        //fix: collapse dead conditional (both arms returned the
        // same value — `PathBuf::from(X).join(".config")` where X was
        // `.` or `h`).
        let Some(h) = std::env::var("XDG_CONFIG_HOME")
            .ok()
            .filter(|b| !b.is_empty())
            .or_else(|| std::env::var("HOME").ok())
        else {
            return Vec::new();
        };
        let base = PathBuf::from(h).join(".config");
        CANDIDATE_NAMES.iter().map(|n| base.join(n)).collect()
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos", unix)))]
    {
        Vec::new()
    }
}
