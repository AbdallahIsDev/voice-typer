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

use crate::state::SidecarHandle;
use crate::util::{SERVER_STARTED_POLL_INTERVAL_MS, SERVER_STARTED_TIMEOUT_MS};
use std::sync::atomic::AtomicBool;
use std::time::{Duration, Instant};
use tauri_plugin_shell::process::CommandEvent;
use tauri_plugin_shell::ShellExt;
use tokio::io::AsyncBufReadExt;
use tokio::sync::mpsc;

use super::env_allowlist::passthrough_env_allowlist;
use super::handshake::{is_shutting_down, parse_worker_started};

/// Release-build worker spawn via Tauri's `externalBin`
/// (`bin/voice-typer-worker` in tauri.conf.json). Wraps the resulting
/// `CommandChild` in `SidecarHandle::ShellPlugin`.
///
/// Kill-on-parent-exit: identical guarantee to `spawn_sidecar_release`
/// — the ShellPlugin child does NOT kill the OS process on Drop, so a
/// host crash would orphan the worker (which holds the loaded models).
/// `register_kill_on_parent_exit` (Job Object on Windows, PR_SET_PDEATHSIG
/// on POSIX) reaps it. Best-effort: errors are logged, spawn proceeds.
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
        // Worker auth: same token env var as the sidecar
        // (VOICE_TYPER_IPC_TOKEN). The worker refuses to start without
        // it (EXIT_NO_TOKEN). The slim-core sidecar re-uses this token
        // for its WS client connection to the worker.
        .env("VOICE_TYPER_IPC_TOKEN", token)
        // Share the host's per-process session ID so the
        // worker's log lines correlate with the host + sidecar.
        .env("VOICE_TYPER_SESSION_ID", crate::util::session_id())
        // The worker reads the shared config dir for `fast_startup`
        // (prewarm toggle) + its log location.
        .env(
            "VOICE_TYPER_CONFIG_DIR",
            crate::platform::paths::config_dir()
                .to_string_lossy()
                .to_string(),
        );

    let (mut rx, child) = cmd
        .spawn()
        .map_err(|e| format!("failed to spawn worker: {e}"))?;

    // Kill-on-parent-exit so the OS reaps the orphan worker when the
    // host dies (mirrors spawn_sidecar_release). Best-effort.
    let worker_pid = child.pid();
    if let Err(e) = crate::platform::process::register_kill_on_parent_exit(worker_pid) {
        log::warn!(
            "[WORKER] failed to register kill-on-parent-exit for pid {} \
             (best-effort — worker may be orphaned on host crash): {}",
            worker_pid,
            e
        );
    }

    // Read stdout until the `worker_started` JSON is parsed. The
    // worker force-sets line-buffered stdout (worker/__main__.py
    // `_force_line_buffered_stdout`), so each `print(flush=True)` lands
    // as one CommandEvent.
    let deadline = Instant::now() + Duration::from_millis(SERVER_STARTED_TIMEOUT_MS);
    let mut stdout_buf = String::new();

    while Instant::now() < deadline {
        // Same shutting-down short-circuit as the sidecar paths: a
        // respawn initiated seconds before the user quits would
        // otherwise block up to SERVER_STARTED_TIMEOUT_MS waiting for
        // a `worker_started` line that will never arrive.
        if is_shutting_down(shutting_down) {
            log::info!(
                "[WORKER] shutting_down set during stdout-read loop — killing freshly-spawned worker"
            );
            let pid = child.pid();
            let _ = tauri::async_runtime::spawn_blocking(move || {
                crate::platform::process::kill_process_tree(pid)
            })
            .await;
            if let Err(kill_err) = child.kill() {
                log::warn!(
                    "[WORKER] failed to kill worker after shutting_down detected (best-effort): {}",
                    kill_err
                );
            }
            let _ = tokio::time::timeout(Duration::from_millis(500), rx.recv()).await;
            return Err("shutdown".to_string());
        }

        match tokio::time::timeout(
            Duration::from_millis(SERVER_STARTED_POLL_INTERVAL_MS),
            rx.recv(),
        )
        .await
        {
            Ok(Some(event)) => {
                let line = match event {
                    CommandEvent::Stdout(bytes) => String::from_utf8_lossy(&bytes).into_owned(),
                    CommandEvent::Stderr(bytes) => {
                        let s = String::from_utf8_lossy(&bytes).into_owned();
                        log::debug!("[WORKER] stderr: {}", s.trim());
                        continue;
                    }
                    CommandEvent::Terminated(payload) => {
                        let pid = child.pid();
                        let _ = tauri::async_runtime::spawn_blocking(move || {
                            crate::platform::process::kill_process_tree(pid)
                        })
                        .await;
                        if let Err(kill_err) = child.kill() {
                            log::warn!(
                                "[WORKER] failed to kill worker after Terminated event (best-effort): {}",
                                kill_err
                            );
                        }
                        return Err(format!(
                            "worker terminated before worker_started (code={:?})",
                            payload.code
                        ));
                    }
                    CommandEvent::Error(err) => {
                        let pid = child.pid();
                        let _ = tauri::async_runtime::spawn_blocking(move || {
                            crate::platform::process::kill_process_tree(pid)
                        })
                        .await;
                        if let Err(kill_err) = child.kill() {
                            log::warn!(
                                "[WORKER] failed to kill worker after CommandEvent::Error (best-effort): {}",
                                kill_err
                            );
                        }
                        return Err(format!("worker command error: {err}"));
                    }
                    _ => continue,
                };
                stdout_buf.push_str(&line);
                if let Some(port) = parse_worker_started(&line) {
                    log::info!("[WORKER] worker_started port={}", port);
                    return Ok((port, SidecarHandle::ShellPlugin(Some(child)), rx));
                }
                log::warn!(
                    "[WORKER] unexpected stdout line (expected only worker_started): {}",
                    line.trim()
                );
            }
            Ok(None) => {
                return Err("worker stdout closed before worker_started".into());
            }
            Err(_) => {
                // Per-iteration timeout — loop and retry until deadline.
                continue;
            }
        }
    }

    let pid = child.pid();
    let _ = tauri::async_runtime::spawn_blocking(move || {
        crate::platform::process::kill_process_tree(pid)
    })
    .await;
    if let Err(e) = child.kill() {
        log::warn!("[WORKER] failed to kill worker after deadline: {}", e);
    }
    let _ = tokio::time::timeout(Duration::from_millis(500), rx.recv()).await;
    Err(format!(
        "worker did not emit worker_started within {}ms. stdout so far: {}",
        SERVER_STARTED_TIMEOUT_MS, stdout_buf
    ))
}

/// Dev-mode worker spawn — runs `python -m voice_typer.worker` (no
/// Nuitka freeze, no `externalBin`), parallel to
/// `spawn_sidecar_dev_mode`. The developer must have `voice_typer`
/// importable in their Python environment.
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
        .env("VOICE_TYPER_IPC_TOKEN", token)
        .env("VOICE_TYPER_SESSION_ID", crate::util::session_id())
        .env(
            "VOICE_TYPER_CONFIG_DIR",
            crate::platform::paths::config_dir()
                .to_string_lossy()
                .to_string(),
        )
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

    let deadline = Instant::now() + Duration::from_millis(SERVER_STARTED_TIMEOUT_MS);
    let mut stdout_buf = String::new();
    while Instant::now() < deadline {
        // Same shutting-down short-circuit as the sidecar dev path.
        if is_shutting_down(shutting_down) {
            log::info!(
                "[WORKER-DEV] shutting_down set during stdout-read loop — killing freshly-spawned dev worker"
            );
            let pid_opt = child.id();
            if let Some(pid) = pid_opt {
                let _ = tauri::async_runtime::spawn_blocking(move || {
                    crate::platform::process::kill_process_tree(pid)
                })
                .await;
            }
            if let Err(e) = child.kill().await {
                log::warn!(
                    "[WORKER-DEV] failed to kill worker after shutting_down detected (best-effort): {}",
                    e
                );
            }
            let _ = tokio::time::timeout(Duration::from_millis(500), child.wait()).await;
            return Err("shutdown".to_string());
        }
        let mut line = String::new();
        match tokio::time::timeout(
            Duration::from_millis(SERVER_STARTED_POLL_INTERVAL_MS),
            reader.read_line(&mut line),
        )
        .await
        {
            Ok(Ok(0)) => {
                return Err("dev worker stdout closed before worker_started".into());
            }
            Ok(Ok(_)) => {
                stdout_buf.push_str(&line);
                if let Some(port) = parse_worker_started(&line) {
                    log::info!("[WORKER-DEV] worker_started port={}", port);
                    return Ok((port, SidecarHandle::DevMode(child)));
                }
                log::warn!(
                    "[WORKER-DEV] unexpected stdout line (expected only worker_started): {}",
                    line.trim()
                );
            }
            Ok(Err(e)) => {
                return Err(format!("dev worker stdout read error: {e}"));
            }
            Err(_) => continue, // per-iteration timeout — retry until deadline
        }
    }
    let pid_opt = child.id();
    if let Some(pid) = pid_opt {
        let _ = tauri::async_runtime::spawn_blocking(move || {
            crate::platform::process::kill_process_tree(pid)
        })
        .await;
    }
    if let Err(e) = child.kill().await {
        log::warn!("[WORKER-DEV] failed to kill worker after deadline: {}", e);
    }
    let _ = tokio::time::timeout(Duration::from_millis(500), child.wait()).await;
    Err(format!(
        "dev worker did not emit worker_started within {}ms. stdout so far: {}",
        SERVER_STARTED_TIMEOUT_MS, stdout_buf
    ))
}
