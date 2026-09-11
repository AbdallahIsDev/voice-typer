//! ML worker exe spawn (Phase 2b, runtime-pack split, plan-runtime-pack-split
//! §7.1/§7.3). Mirrors the sidecar spawn paths (`release_mode` /
//! `dev_mode`) but for the second spawned child: `voice-typer-worker`.
//!
//! # Worker spawn contract (from the Python side)
//!
//! The worker (`voice_typer/worker/__main__.py`) is spawned WITHOUT
//! positional args (it parses only `--version` / `--debug` internally;
//! the OS assigns the WS port). It requires:
//!
//! - `VOICE_TYPER_IPC_TOKEN`: the per-launch bearer token. The worker
//!   REFUSES to start without it (`EXIT_NO_TOKEN`). This is the SAME
//!   env var name as the slim-core sidecar uses (`IPC_TOKEN_ENV_VAR`
//!   in `voice_typer/server/_paths.py`), so the host passes the same
//!   per-launch token to both children, the slim-core sidecar uses it
//!   to authenticate its WS CLIENT connection to the worker
//!   (1-host↔2-processes pattern, plan §7.1).
//! - `VOICE_TYPER_CONFIG_DIR`: the shared config dir (the worker reads
//!   `fast_startup` for its prewarm phase + its log location).
//! - `VOICE_TYPER_SESSION_ID`: cross-process log correlation
//!   (same join key as the host + sidecar).
//!
//! # Handshake
//!
//! The worker emits `{"event":"worker_started","port":N,"protocol":1}`
//! on stdout (`_WORKER_STARTED_EVENT` in
//! `voice_typer/worker/_ws_server.py`): NOT `server_started` (that
//! name belongs to the slim-core sidecar). `parse_worker_started`
//! (handshake.rs) routes the line to this spawn path.
//!
//! # Env hygiene
//!
//! Both paths `.env_clear()` first, then re-add only the OS-required
//! allowlist (`passthrough_env_allowlist`) + the explicit vars above —
//! identical to the sidecar spawn paths, so the worker never inherits
//! arbitrary host env (e.g. `HF_TOKEN`, `OPENAI_API_KEY`, `http_proxy`).
//!
//! # Shared loop bodies
//!
//! The stdout-handshake read loops are shared with the sidecar paths
//! (`super::handshake_loop`): the release path uses
//! `read_handshake_from_command_events` (same as
//! `spawn_sidecar_release`), the dev path uses
//! `read_handshake_from_stdout_lines` (same as
//! `spawn_sidecar_dev_mode`). Only the labels (log tag, error wording,
//! handshake event name) and the env contract differ per path.

use crate::state::SidecarHandle;
use crate::state::WorkerState;
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

/// Env pairs shared by BOTH worker spawn paths (release + dev):
/// the per-launch bearer token, the cross-process session id, and the
/// shared config dir: the `# Worker spawn contract` section above.
/// Applied AFTER `.env_clear()` + `passthrough_env_allowlist()`; the
/// dev path additionally sets `VOICE_TYPER_DEBUG=1`.
pub(crate) fn worker_shared_env(token: &str) -> Vec<(&'static str, String)> {
    vec![
        // Worker auth: same token env var as the sidecar
        // (VOICE_TYPER_IPC_TOKEN). The worker refuses to start without
        // it (EXIT_NO_TOKEN). The slim-core sidecar re-uses this token
        // for its WS client connection to the worker.
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

/// Release-build worker spawn via Tauri's `externalBin`
/// (`bin/voice-typer-worker` in tauri.conf.json). Wraps the resulting
/// `CommandChild` in `SidecarHandle::ShellPlugin`.
///
/// Kill-on-parent-exit: identical guarantee to `spawn_sidecar_release`
///: the ShellPlugin child does NOT kill the OS process on Drop, so a
/// host crash would orphan the worker (which holds the loaded models).
/// `register_kill_on_parent_exit` (Job Object on Windows, the
/// `/bin/sh` reaper subprocess on POSIX, see the note on
/// `spawn_sidecar_release`) reaps it. Best-effort: errors are logged,
/// spawn proceeds.
///
/// The stdout-handshake read loop (shutting-down short-circuit,
/// CommandEvent arms, kill/drain ordering, deadline) lives in
/// `super::handshake_loop::read_handshake_from_command_events` —
/// shared with the sidecar release path. The labels below pin this
/// path's exact log/error wording.
pub(crate) async fn spawn_worker_release(
    app: &tauri::AppHandle,
    token: &str,
    shutting_down: Option<&AtomicBool>,
) -> Result<(u16, SidecarHandle, mpsc::Receiver<CommandEvent>), String> {
    // ADR-0020 §4.1: externalBin selects the per-triple binary at
    // runtime. Base name (without the triple suffix) is
    // `voice-typer-worker` (matches src-tauri/bin/voice-typer-worker-<triple>[.exe]).
    let worker = app
        .shell()
        .sidecar("voice-typer-worker")
        .map_err(|e| format!("failed to resolve worker binary: {e}"))?;

    let cmd = worker
        .env_clear()
        .envs(passthrough_env_allowlist())
        .envs(worker_shared_env(token));

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

    // Read stdout until the `worker_started` JSON is parsed. The
    // worker force-sets line-buffered stdout (worker/__main__.py
    // `_force_line_buffered_stdout`), so each `print(flush=True)` lands
    // as one CommandEvent.
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
    )
    .await?;
    Ok((port, SidecarHandle::ShellPlugin(Some(child)), rx))
}

/// Dev-mode worker spawn: runs `python -m voice_typer.worker` (no
/// Nuitka freeze, no `externalBin`), parallel to
/// `spawn_sidecar_dev_mode`. The developer must have `voice_typer`
/// importable in their Python environment.
///
/// The stdout-handshake read loop lives in
/// `super::handshake_loop::read_handshake_from_stdout_lines`: shared
/// with the sidecar dev path. The labels below pin this path's exact
/// log/error wording.
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
    // Mirror the sidecar dev-mode env set: clear inherited host env,
    // re-add the OS-required allowlist + the worker's explicit vars.
    // `VOICE_TYPER_DEBUG=1` surfaces verbose worker logging under
    // `cargo tauri dev` (the worker's log.py reads it).
    cmd.args(["-m", "voice_typer.worker"])
        .env_clear()
        .envs(passthrough_env_allowlist())
        .envs(worker_shared_env(token))
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
    )
    .await?;
    Ok((port, SidecarHandle::DevMode(child)))
}

// ─── Phase-2c lifecycle triggers (BP-33) ────────────────────────────
//
// The WS reader calls `on_pack_verified` when the sidecar publishes
// `offline_pack_verified` (pack passed SHA256 + signature checks);
// the exit path calls `stop_worker_child` on host teardown.

/// Try to claim the one-at-a-time worker (re)start slot. Returns true
/// iff this caller won (the winner MUST clear `respawn_in_progress`
/// when done). Serializes concurrent `offline_pack_verified` events
/// (a download completion + the startup re-check can race) so two
/// `initialize_worker` runs can never spawn two workers and orphan
/// one's handle.
pub(crate) fn try_claim_restart_slot(flag: &AtomicBool) -> bool {
    flag.compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
        .is_ok()
}

/// Release-build gate: only attempt the spawn when the worker binary
/// is actually on disk. In dev mode there is no frozen binary (the
/// spawn runs `python -m voice_typer.worker`), so always attempt —
/// a missing module surfaces in the dev console where it is actionable.
fn worker_binary_present() -> bool {
    is_dev_mode() || crate::platform::worker_path::worker_exe_path().exists()
}

/// Stop the running worker child, if any (take + kill_tree,
/// best-effort). Called BEFORE a (re)start so the just-swapped pack
/// files are never held open by a running worker.exe on Windows
/// (W6-R3 stop hook), and on host teardown so no worker outlives the
/// host. A missing child is a silent no-op (fresh launch).
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
        let state = app_handle
            .state::<Arc<WorkerState>>()
            .inner()
            .clone();
        if !try_claim_restart_slot(&state.respawn_in_progress) {
            log::info!(
                "[WORKER-INIT] pack verified while a worker (re)start is in flight: skipping duplicate"
            );
            return;
        }
        // Stop-first: the verified event fires right after the
        // atomic swap, so a still-running worker may hold the OLD
        // pack files open (Windows file-lock swap failure).
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
