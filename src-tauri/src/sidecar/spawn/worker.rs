
use crate::state::SidecarHandle;
use crate::state::WorkerState;
use crate::util::SERVER_STARTED_TIMEOUT_MS;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use tauri::Manager;
use tauri_plugin_shell::process::CommandEvent;
use tauri_plugin_shell::ShellExt;
use tokio::sync::mpsc;

use super::dev_mode::is_dev_mode;
use super::env_allowlist::passthrough_env_allowlist;
use super::handshake::parse_worker_started;
use super::handshake_loop::{
    read_handshake_from_command_events, read_handshake_from_stdout_lines,
    register_kill_on_parent_exit_best_effort, HandshakeLabels,
};
use super::initialize_worker;

pub(crate) fn worker_shared_env(token: &str) -> Vec<(&'static str, String)> {
    vec![
        ("VOICE_TYPER_IPC_TOKEN", token.to_string()),
        // Share the host's per-process session ID so the
        // worker's log lines correlate with the host + sidecar.
        (
            "VOICE_TYPER_SESSION_ID",
            crate::util::session_id().to_string(),
        ),
        // The worker reads the shared config dir for `fast_startup`
        // (prewarm toggle) + its log location.
        (
            "VOICE_TYPER_CONFIG_DIR",
            crate::platform::paths::config_dir()
                .to_string_lossy()
                .to_string(),
        ),
    ]
}

pub(crate) async fn spawn_worker_release(
    app: &tauri::AppHandle,
    token: &str,
    shutting_down: Option<&AtomicBool>,
) -> Result<(u16, SidecarHandle, mpsc::Receiver<CommandEvent>), String> {
    let worker = app
        .shell()
        .sidecar("voice-typer-worker")
        .map_err(|e| format!("failed to resolve worker binary: {e}"))?;

    let cmd = worker
        .env_clear()
        .envs(passthrough_env_allowlist())
        .envs(worker_shared_env(token))
        .env("KMP_DUPLICATE_LIB_OK", "TRUE");

    let (rx, child) = cmd
        .spawn()
        .map_err(|e| format!("failed to spawn worker: {e}"))?;

    // Kill-on-parent-exit so the OS reaps the orphan worker when the
    // host dies (mirrors spawn_sidecar_release). Best-effort.
    let worker_pid = child.pid();
    register_kill_on_parent_exit_best_effort(
        "[WORKER]",
        "worker may be orphaned on host crash",
        worker_pid,
    );

    let (port, child, rx) = read_handshake_from_command_events(
        &HandshakeLabels {
            log_tag: "[WORKER]",
            err_noun: "worker",
            event_name: "worker_started",
            kill_target: "worker",
            fresh_target: "worker",
        },
        rx,
        child,
        shutting_down,
        parse_worker_started,
        SERVER_STARTED_TIMEOUT_MS,
    )
    .await?;
    Ok((port, SidecarHandle::ShellPlugin(Some(child)), rx))
}

pub(crate) async fn spawn_worker_dev_mode(
    token: &str,
    shutting_down: Option<&AtomicBool>,
) -> Result<(u16, SidecarHandle), String> {
    let python_bin = if cfg!(target_os = "windows") {
        "python.exe"
    } else {
        "python3"
    };

    let mut cmd = tokio::process::Command::new(python_bin);
    cmd.args(["-m", "voice_typer.worker"])
        .env_clear()
        .envs(passthrough_env_allowlist())
        .envs(worker_shared_env(token))
        .env("KMP_DUPLICATE_LIB_OK", "TRUE")
        .env("VOICE_TYPER_DEBUG", "1")
        .stdout(std::process::Stdio::piped())
        // Dev mode: inherit stderr so the developer sees Python
        // tracebacks in the `cargo tauri dev` console.
        .stderr(std::process::Stdio::inherit())
        // Ensure the dev worker dies with the host (no zombie python).
        .kill_on_drop(true);

    let mut child = cmd
        .spawn()
        .map_err(|e| format!("failed to spawn dev worker ({}): {e}", python_bin))?;

    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "dev worker stdout not captured".to_string())?;
    let mut reader = tokio::io::BufReader::new(stdout);

    let port = read_handshake_from_stdout_lines(
        &HandshakeLabels {
            log_tag: "[WORKER-DEV]",
            err_noun: "dev worker",
            event_name: "worker_started",
            kill_target: "worker",
            fresh_target: "dev worker",
        },
        &mut reader,
        &mut child,
        shutting_down,
        parse_worker_started,
        SERVER_STARTED_TIMEOUT_MS,
    )
    .await?;
    Ok((port, SidecarHandle::DevMode(child)))
}


pub(crate) fn try_claim_restart_slot(flag: &AtomicBool) -> bool {
    flag.compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
        .is_ok()
}

fn worker_binary_present() -> bool {
    is_dev_mode() || crate::platform::worker_path::worker_exe_path().exists()
}

pub(crate) async fn stop_worker_child(state: &Arc<WorkerState>) {
    let taken = crate::state::lock(&state.child).take();
    if let Some(child) = taken {
        if let Err(e) = child.kill_tree().await {
            log::warn!(
                "[WORKER-INIT] stop worker child kill_tree failed (best-effort): {}",
                e
            );
        } else {
            log::info!("[WORKER-INIT] stopped previous worker child");
        }
    }
}

/// `offline_pack_verified` trigger (called from the WS reader, sync
/// context: the async work runs on a spawned task, never `block_on`:
/// C-TOKIO-1). Restarts the worker against the just-verified pack:
/// stop-first (frees the pack dir on Windows) then
/// `initialize_worker`. Concurrent events serialize on the restart
/// slot; a missing binary or a quitting host skips quietly. Spawn
/// failure only logs (no supervisor yet, plan §7.2, so a bad pack
/// can never trip a respawn loop).
pub(crate) fn on_pack_verified(app: &tauri::AppHandle) {
    let app_handle = app.clone();
    tauri::async_runtime::spawn(async move {
        let state = app_handle.state::<Arc<WorkerState>>().inner().clone();
        if !try_claim_restart_slot(&state.respawn_in_progress) {
            log::info!(
                "[WORKER-INIT] pack verified while a worker (re)start is in flight: skipping duplicate"
            );
            return;
        }
        stop_worker_child(&state).await;
        if state.shutting_down.load(Ordering::SeqCst) {
            state.respawn_in_progress.store(false, Ordering::SeqCst);
            return;
        }
        if !worker_binary_present() {
            log::info!(
                "[WORKER-INIT] pack verified but no worker binary on disk: skipping worker start"
            );
            state.respawn_in_progress.store(false, Ordering::SeqCst);
            return;
        }
        initialize_worker(&app_handle, state.clone()).await;
        state.respawn_in_progress.store(false, Ordering::SeqCst);
    });
}
