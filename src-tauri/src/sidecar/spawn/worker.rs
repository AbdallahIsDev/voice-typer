//! ML worker exe spawn (Phase 2b — runtime-pack split, plan-runtime-pack-split
//! §7.1/§7.3). Mirrors the sidecar spawn paths (`release_mode` /
//! `dev_mode`) but for the second spawned child: `voice-typer-worker`.
//!
//! # Worker spawn contract (from the Python side)
//!
//! The worker (`voice_typer/worker/__main__.py`) is spawned WITHOUT
//! positional args (it parses only `--version` / `--debug` internally;
//! the OS assigns the WS port). It requires:
//!
//! - `VOICE_TYPER_IPC_TOKEN` — the per-launch bearer token. The worker
//!   REFUSES to start without it (`EXIT_NO_TOKEN`). This is the SAME
//!   env var name as the slim-core sidecar uses (`IPC_TOKEN_ENV_VAR`
//!   in `voice_typer/server/_paths.py`), so the host passes the same
//!   per-launch token to both children — the slim-core sidecar uses it
//!   to authenticate its WS CLIENT connection to the worker
//!   (1-host↔2-processes pattern, plan §7.1).
//! - `VOICE_TYPER_CONFIG_DIR` — the shared config dir (the worker reads
//!   `fast_startup` for its prewarm phase + its log location).
//! - `VOICE_TYPER_SESSION_ID` — cross-process log correlation
//!   (same join key as the host + sidecar).
//!
//! # Handshake
//!
//! The worker emits `{"event":"worker_started","port":N,"protocol":1}`
//! on stdout (`_WORKER_STARTED_EVENT` in
//! `voice_typer/worker/_ws_server.py`) — NOT `server_started` (that
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
use std::sync::atomic::AtomicBool;
use tauri_plugin_shell::process::CommandEvent;
use tauri_plugin_shell::ShellExt;
use tokio::sync::mpsc;

use super::env_allowlist::passthrough_env_allowlist;
use super::handshake::parse_worker_started;
use super::handshake_loop::{
    read_handshake_from_command_events, read_handshake_from_stdout_lines,
    register_kill_on_parent_exit_best_effort, HandshakeLabels,
};

/// Env pairs shared by BOTH worker spawn paths (release + dev):
/// the per-launch bearer token, the cross-process session id, and the
/// shared config dir — the `# Worker spawn contract` section above.
/// Applied AFTER `.env_clear()` + `passthrough_env_allowlist()`; the
/// dev path additionally sets `VOICE_TYPER_DEBUG=1`.
#[allow(dead_code)] // called by spawn_worker_release / spawn_worker_dev_mode once WorkerState is managed (Phase 2c)
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
/// — the ShellPlugin child does NOT kill the OS process on Drop, so a
/// host crash would orphan the worker (which holds the loaded models).
/// `register_kill_on_parent_exit` (Job Object on Windows, the
/// `/bin/sh` reaper subprocess on POSIX — see the note on
/// `spawn_sidecar_release`) reaps it. Best-effort: errors are logged,
/// spawn proceeds.
///
/// The stdout-handshake read loop (shutting-down short-circuit,
/// CommandEvent arms, kill/drain ordering, deadline) lives in
/// `super::handshake_loop::read_handshake_from_command_events` —
/// shared with the sidecar release path. The labels below pin this
/// path's exact log/error wording.
#[allow(dead_code)] // called by spawn_worker_and_get_port_with_shutdown once WorkerState is managed (Phase 2c)
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

/// Dev-mode worker spawn — runs `python -m voice_typer.worker` (no
/// Nuitka freeze, no `externalBin`), parallel to
/// `spawn_sidecar_dev_mode`. The developer must have `voice_typer`
/// importable in their Python environment.
///
/// The stdout-handshake read loop lives in
/// `super::handshake_loop::read_handshake_from_stdout_lines` — shared
/// with the sidecar dev path. The labels below pin this path's exact
/// log/error wording.
#[allow(dead_code)] // called by spawn_worker_and_get_port_with_shutdown once WorkerState is managed (Phase 2c)
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
