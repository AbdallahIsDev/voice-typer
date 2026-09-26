
use crate::state::SidecarHandle;
use crate::state::WorkerState;
use crate::state::{lock as state_lock, SidecarState};
use crate::util::SERVER_STARTED_TIMEOUT_MS;
use serde_json::{json, Value};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use tauri::Manager;
use tauri_plugin_shell::process::CommandEvent;
use tauri_plugin_shell::ShellExt;
use tokio::sync::mpsc;
use tokio_tungstenite::tungstenite::Message;

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

/// Pure start gate: run only while the host is alive AND a worker
/// binary exists. Pure so both triggers stay unit-testable.
pub(crate) fn should_start_worker(shutting_down: bool, binary_present: bool) -> bool {
    !shutting_down && binary_present
}

/// Shared start sequence for cold boot + pack-verified events:
/// stop-first (frees the pack dir on Windows) then
/// `initialize_worker`. Serialized on the restart slot; a missing
/// binary or a quitting host skips quietly (C-WS-3 generation
/// stamping lands with the worker WS bridge, no WS yet to stamp).
pub(crate) async fn start_worker_if_ready(app_handle: &tauri::AppHandle, state: Arc<WorkerState>) {
    if !try_claim_restart_slot(&state.respawn_in_progress) {
        log::info!("[WORKER-INIT] worker (re)start already in flight: skipping duplicate");
        return;
    }
    stop_worker_child(&state).await;
    if !should_start_worker(
        state.shutting_down.load(Ordering::SeqCst),
        worker_binary_present(),
    ) {
        if !state.shutting_down.load(Ordering::SeqCst) {
            log::info!("[WORKER-INIT] no worker binary on disk: skipping worker start");
        }
        state.respawn_in_progress.store(false, Ordering::SeqCst);
        return;
    }
    initialize_worker(app_handle, state.clone()).await;
    state.respawn_in_progress.store(false, Ordering::SeqCst);
}

/// ADR-0024 Step 2 port relay: host pushes `worker_started
/// {pid, version, port}` to the sidecar over the existing host↔sidecar
/// WS hop (fire-and-forget TEXT frame + numeric id, same envelope as
/// `send_fire_and_forget_frame`). `port` is additive: old
/// {pid, version} readers keep working, no allowlist churn.
/// NOTE: see docs/code-notes/worker-port-relay.md#host-emit

/// Pure frame constructor. `port` is u16 on both sides of the hop
/// (E9); the sidecar validates 1..=65535 and drops anything else.
pub(crate) fn worker_started_relay_frame(pid: u32, version: &str, port: u16, id: u64) -> Value {
    json!({"type": "worker_started", "data": {"pid": pid, "version": version, "port": port}, "id": id})
}

/// Best-effort immediate send. `Some(id)` on queued, `None` when the
/// sidecar link is down (caller falls back to the bounded retry).
fn send_worker_started_frame(
    state: &Arc<SidecarState>,
    pid: u32,
    version: &str,
    port: u16,
) -> Option<u64> {
    let ws_tx = state_lock(&state.ws_tx).clone()?;
    let id = state.next_id.fetch_add(1, Ordering::Relaxed);
    let frame = worker_started_relay_frame(pid, version, port, id);
    match ws_tx.try_send(Message::Text(frame.to_string().into())) {
        Ok(_) => {
            log::info!(
                "[WORKER-INIT] worker_started relay sent (pid={}, port={}, id={})",
                pid,
                port,
                id
            );
            Some(id)
        }
        Err(e) => {
            log::warn!(
                "[WORKER-INIT] worker_started relay try_send failed (id={}): {}",
                id,
                e
            );
            None
        }
    }
}

// Cold-start race cover: worker and sidecar spawn in parallel, so the
// link may be down when the worker bind lands. Bounded so a dead link
// can never retry forever (respawn policy is Step 5 territory).
const RELAY_RETRY_ATTEMPTS: u32 = 20;
const RELAY_RETRY_INTERVAL_MS: u64 = 500;

/// Relay the worker bind to the sidecar. Skips (warn) when the pid is
/// unknown rather than sending a garbage pid; retries briefly when the
/// sidecar link is not up yet.
pub(crate) fn relay_worker_started_to_sidecar(
    app: &tauri::AppHandle,
    pid: Option<u32>,
    port: u16,
) {
    let pid = match pid {
        Some(pid) => pid,
        None => {
            log::warn!(
                "[WORKER-INIT] worker pid unknown, skipping worker_started relay (port={})",
                port
            );
            return;
        }
    };
    let version = crate::platform::worker_path::pack_version();
    let state: tauri::State<'_, Arc<SidecarState>> = app.state();
    let state = state.inner().clone();
    if send_worker_started_frame(&state, pid, &version, port).is_some() {
        return;
    }
    log::info!(
        "[WORKER-INIT] sidecar link down, retrying worker_started relay (port={})",
        port
    );
    tauri::async_runtime::spawn(async move {
        for _ in 0..RELAY_RETRY_ATTEMPTS {
            tokio::time::sleep(std::time::Duration::from_millis(RELAY_RETRY_INTERVAL_MS)).await;
            if state.shutting_down.load(Ordering::SeqCst) {
                return;
            }
            if send_worker_started_frame(&state, pid, &version, port).is_some() {
                return;
            }
        }
        log::warn!(
            "[WORKER-INIT] worker_started relay undelivered after retries (port={})",
            port
        );
    });
}

/// `offline_pack_verified` trigger (called from the WS reader, sync
/// context: the async work runs on a spawned task, never `block_on`:
/// C-TOKIO-1). Delegates to the shared start sequence so a bad pack
/// can never trip a respawn loop (no supervisor yet, plan §7.2).
pub(crate) fn on_pack_verified(app: &tauri::AppHandle) {
    let app_handle = app.clone();
    tauri::async_runtime::spawn(async move {
        let state = app_handle.state::<Arc<WorkerState>>().inner().clone();
        start_worker_if_ready(&app_handle, state).await;
    });
}
