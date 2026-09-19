
use std::path::PathBuf;

pub(crate) fn legacy_userdata_candidates() -> Vec<PathBuf> {
    /// The three predecessor `userData` directory names ever used, in probe
    /// order. See the module-level docstring for the naming history.
    const CANDIDATE_NAMES: &[&str] = &[
        "voice-typer-desktop",
        crate::platform::paths::APP_SLUG,
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
