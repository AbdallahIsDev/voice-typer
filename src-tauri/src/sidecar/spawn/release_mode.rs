//! Release-build sidecar spawn via Tauri's `externalBin` (ADR-0020
//! §1 + §4.1).

use crate::state::SidecarHandle;
use crate::util::SERVER_STARTED_TIMEOUT_MS;
use std::sync::atomic::AtomicBool;
use tauri::Manager;
use tauri_plugin_shell::process::CommandEvent;
use tauri_plugin_shell::ShellExt;
use tokio::sync::mpsc;

use super::env_allowlist::passthrough_env_allowlist;
use super::handshake::parse_server_started;
use super::handshake_loop::{
    read_handshake_from_command_events, register_kill_on_parent_exit_best_effort, HandshakeLabels,
};

pub(crate) async fn spawn_sidecar_release(
    app: &tauri::AppHandle,
    token: &str,
    shutting_down: Option<&AtomicBool>,
) -> Result<(u16, SidecarHandle, mpsc::Receiver<CommandEvent>), String> {
    // Tauri externalBin selects by Rust target triple; base name is
    // `python-sidecar`.
    let sidecar = app
        .shell()
        .sidecar("python-sidecar")
        .map_err(|e| format!("failed to resolve sidecar binary: {e}"))?;

    // Env: TAURI_SIDECAR=1 + VOICE_TYPER_IPC_TOKEN + VOICE_TYPER_NATIVE_DIR
    // (sidecar skips its own single-instance mutex + heartbeat watchdog).
    let native_dir = app
        .path()
        .resource_dir()
        .map(|p| p.join("native"))
        .map_err(|e| format!("resource_dir failed: {e}"))?;

    let cmd = sidecar
        .args(["--ws"])
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
        .envs(crate::startup_timeline::sidecar_timeline_envs());

    let (rx, child) = cmd
        .spawn()
        .map_err(|e| format!("failed to spawn sidecar: {e}"))?;

    register_kill_on_parent_exit_best_effort(
        "[SIDECAR]",
        "sidecar will run but may be orphaned on host crash",
        child.pid(),
    );

    let (port, child, rx) = read_handshake_from_command_events(
        &HandshakeLabels {
            log_tag: "[SIDECAR]",
            err_noun: "sidecar",
            event_name: "server_started",
            kill_target: "child",
            fresh_target: "child",
        },
        rx,
        child,
        shutting_down,
        parse_server_started,
        SERVER_STARTED_TIMEOUT_MS,
    )
    .await?;
    let rx = super::event_drain::spawn_child_event_drain("[SIDECAR]", rx);
    Ok((port, SidecarHandle::ShellPlugin(Some(child)), rx))
}
