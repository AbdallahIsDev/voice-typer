//! OS-required env-var passthrough allowlist for the `.env_clear()`
//! spawn paths — extracted from the former single-file
//! `sidecar/spawn.rs`.

/// Conservative OS-env allowlist passed through to
/// the sidecar after `.env_clear()`. The sidecar process should NOT
/// inherit arbitrary host env vars (e.g. `HF_TOKEN`, `OPENAI_API_KEY`
/// exported from the user's shell, `http_proxy` from a corporate
/// machine) — only the OS-required vars it needs to function plus the
/// voice-typer-specific vars already added explicitly via `.env(...)`
/// calls in `spawn_sidecar_release` / `spawn_sidecar_dev_mode`.
///
/// Conservative by design: when in doubt, prefer to pass FEWER vars.
/// Missing vars produce loud failures (Python can't find its home dir,
/// X11 can't find DISPLAY, etc.) that are easy to debug; leaked vars
/// produce silent security holes.
///
/// Allowlist (mirrors the Python side's similar `os.environ` filtering
/// in `voice_typer/server/app.py:main()` for the renderer-driven
/// restart path — though that filter is more permissive because the
/// Python side runs as the user, not as a sandboxed child):
///
/// Always-pass (cross-platform OS infrastructure):
///   `PATH`, `USER`, `LANG`, `TEMP`, `TMP`, `TMPDIR`
///
/// POSIX-only: `HOME`
/// Windows-only: `USERPROFILE`, `SYSTEMROOT`
///
/// Locale category overrides: any var matching `LC_*` (e.g.
/// `LC_ALL`, `LC_CTYPE`, `LC_MESSAGES`).
///
/// Linux-only (GUI + session bus): `DISPLAY`, `WAYLAND_DISPLAY`,
/// `XDG_RUNTIME_DIR`, `XDG_DATA_HOME`, `XDG_CONFIG_HOME`,
/// `DBUS_SESSION_BUS_ADDRESS`, `XDG_SESSION_TYPE`,
/// `XDG_CURRENT_DESKTOP`. Without these the sidecar's tray icon
/// (Qt/GTK) and audio subsystem (PulseAudio → DBUS) would fail.
///
/// macOS-only (LaunchAgent identity): `XPC_SERVICE_NAME` (only
/// relevant when the host is launched by `launchd`; harmless otherwise
/// — the var is unset in normal Tauri launches).
///
/// Voice-typer-specific vars (`TAURI_SIDECAR`, `VOICE_TYPER_IPC_TOKEN`,
/// `VOICE_TYPER_NATIVE_DIR`,
/// `VOICE_TYPER_CONFIG_DIR`, `VOICE_TYPER_DEBUG`, `RUST_LOG`,
/// `VOICE_TYPER_SESSION_ID` — log-correlation join key) are
/// added explicitly by the spawn callers AFTER this function returns —
/// they are NOT in the allowlist (they take precedence over any host
/// value via the subsequent `.env(...)` call).
///
/// Returns a `Vec<(OsString, OsString)>` (not a `HashMap`) because
/// both `tauri_plugin_shell::process::Command::envs` and
/// `tokio::process::Command::envs` accept an iterator of `(K, V)`
/// pairs and a Vec preserves insertion order for debuggability.
pub(crate) fn passthrough_env_allowlist() -> Vec<(std::ffi::OsString, std::ffi::OsString)> {
    let mut out: Vec<(std::ffi::OsString, std::ffi::OsString)> = Vec::new();

    // ── Always-pass (cross-platform) ───────────────────────────────
    const ALWAYS: &[&str] = &["PATH", "USER", "LANG", "TEMP", "TMP", "TMPDIR"];
    for name in ALWAYS {
        if let Some(val) = std::env::var_os(name) {
            out.push((std::ffi::OsString::from(name), val));
        }
    }

    // ── POSIX: HOME ────────────────────────────────────────────────
    #[cfg(unix)]
    if let Some(val) = std::env::var_os("HOME") {
        out.push((std::ffi::OsString::from("HOME"), val));
    }

    // ── Windows: USERPROFILE + USERNAME + SYSTEMROOT ─────────────
    // USERNAME is required by `config_internals/paths.py`'s ACL
    // enforcement (2026-08-30: "cannot enforce Windows ACL on
    // ~\.voice-typer: USERNAME env var is empty" on every dev-mode
    // spawn because the allowlist previously dropped it).
    #[cfg(windows)]
    if let Some(val) = std::env::var_os("USERPROFILE") {
        out.push((std::ffi::OsString::from("USERPROFILE"), val));
    }
    #[cfg(windows)]
    if let Some(val) = std::env::var_os("USERNAME") {
        out.push((std::ffi::OsString::from("USERNAME"), val));
    }
    #[cfg(windows)]
    if let Some(val) = std::env::var_os("SYSTEMROOT") {
        out.push((std::ffi::OsString::from("SYSTEMROOT"), val));
    }

    // ── Locale category overrides (LC_*) ──────────────────────────
    // Walk the live env so we pick up whatever LC_* categories the
    // user has set (LC_ALL, LC_CTYPE, LC_MESSAGES, LC_TIME, …). The
    // sidecar's Python `locale` module + gettext translations depend
    // on these.
    for (name, val) in std::env::vars_os() {
        if let Some(s) = name.to_str() {
            if s.starts_with("LC_") {
                out.push((name, val));
            }
        }
    }

    // ── Linux: GUI + session bus ──────────────────────────────────
    #[cfg(target_os = "linux")]
    {
        const LINUX_GUI: &[&str] = &[
            "DISPLAY",
            "WAYLAND_DISPLAY",
            "XDG_RUNTIME_DIR",
            "XDG_DATA_HOME",
            "XDG_CONFIG_HOME",
            "DBUS_SESSION_BUS_ADDRESS",
            // XDG_SESSION_TYPE + XDG_CURRENT_DESKTOP: the sidecar's
            // tray icon (Qt/GTK) and audio subsystem (PulseAudio →
            // DBUS) probe these to pick the right backend. Without
            // XDG_SESSION_TYPE, Qt can't tell whether to use the X11
            // or Wayland platform plugin and may fail to create a
            // tray icon under a Wayland session that lacks
            // XWayland. Without XDG_CURRENT_DESKTOP, GTK-based tray
            // implementations (StatusNotifierItem) can't pick the
            // correct desktop integration (KDE/GNOME/Unity) and fall
            // back to a no-op tray. Mirrors the Python side's
            // passthrough list in `voice_typer/server/spawn_helpers.py`.
            "XDG_SESSION_TYPE",
            "XDG_CURRENT_DESKTOP",
        ];
        for name in LINUX_GUI {
            if let Some(val) = std::env::var_os(name) {
                out.push((std::ffi::OsString::from(name), val));
            }
        }
    }

    // ── macOS: LaunchAgent identity ───────────────────────────────
    // Only set when running under launchd (LaunchAgent/LaunchDaemon).
    // Harmless to pass through when unset (None branch is skipped).
    #[cfg(target_os = "macos")]
    if let Some(val) = std::env::var_os("XPC_SERVICE_NAME") {
        out.push((std::ffi::OsString::from("XPC_SERVICE_NAME"), val));
    }

    out
}
