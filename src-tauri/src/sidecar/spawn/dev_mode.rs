
use crate::state::SidecarHandle;
use crate::util::SERVER_STARTED_TIMEOUT_MS;
use std::sync::atomic::AtomicBool;

use super::env_allowlist::passthrough_env_allowlist;
use super::handshake::parse_server_started;
use super::handshake_loop::{read_handshake_from_stdout_lines, HandshakeLabels};

pub(crate) fn is_dev_mode() -> bool {
    match std::env::var("VOICE_TYPER_SIDECAR_DEV").ok().as_deref() {
        // Explicit value → the pure predicate decides ("1" = dev, any
        // other value = release/externalBin escape hatch).
        Some(v) => is_dev_mode_for(Some(v)),
        // Unset → a debug host binary is a developer artifact: default
        // to the source sidecar (release builds keep the frozen path).
        None => cfg!(debug_assertions),
    }
}

/// Pure predicate form of `is_dev_mode` for unit testing.
pub(crate) fn is_dev_mode_for(value: Option<&str>) -> bool {
    value == Some("1")
}

pub(crate) async fn spawn_sidecar_dev_mode(
    token: &str,
    shutting_down: Option<&AtomicBool>,
) -> Result<(u16, SidecarHandle), String> {
    let python_bin = if cfg!(target_os = "windows") {
        "python.exe"
    } else {
        "python3"
    };

    let native_dir = std::env::current_dir()
        .map(|p| p.join("voice_typer").join("server").join("native"))
        .map_err(|e| format!("cwd failed: {e}"))?;

    let mut cmd = tokio::process::Command::new(python_bin);
    cmd.args(["-m", "voice_typer.server.ipc_server", "--ws"])
        .env_clear()
        .envs(passthrough_env_allowlist())
        .envs(super::env_allowlist::vt_start_hidden_env())
        .env("TAURI_SIDECAR", "1")
        .env("VOICE_TYPER_IPC_TOKEN", token)
        .env("KMP_DUPLICATE_LIB_OK", "TRUE")
        .env("VOICE_TYPER_SESSION_ID", crate::util::session_id())
        .env(
            "VOICE_TYPER_NATIVE_DIR",
            native_dir.to_string_lossy().to_string(),
        )
        .env(
            "VOICE_TYPER_CONFIG_DIR",
            crate::platform::paths::config_dir()
                .to_string_lossy()
                .to_string(),
        )
        .env("VOICE_TYPER_DEBUG", "1")
        .envs(crate::startup_timeline::sidecar_timeline_envs());
    if std::env::var_os("RUST_LOG").is_none() {
        cmd.env("RUST_LOG", "debug");
    }
    cmd.stdout(std::process::Stdio::piped())
        // Dev mode: inherit stderr so the developer sees Python
        // tracebacks in the `cargo tauri dev` console.
        .stderr(std::process::Stdio::inherit())
        .kill_on_drop(true);

    let mut child = cmd
        .spawn()
        .map_err(|e| format!("failed to spawn dev sidecar ({}): {e}", python_bin))?;

    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "dev sidecar stdout not captured".to_string())?;
    let mut reader = tokio::io::BufReader::new(stdout);

    let port = read_handshake_from_stdout_lines(
        &HandshakeLabels {
            log_tag: "[SIDECAR-DEV]",
            err_noun: "dev sidecar",
            event_name: "server_started",
            kill_target: "child",
            fresh_target: "dev child",
        },
        &mut reader,
        &mut child,
        shutting_down,
        parse_server_started,
        SERVER_STARTED_TIMEOUT_MS,
    )
    .await?;
    Ok((port, SidecarHandle::DevMode(child)))
}
