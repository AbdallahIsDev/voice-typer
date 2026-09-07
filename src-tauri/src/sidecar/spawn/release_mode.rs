//! Release-build sidecar spawn via Tauri's `externalBin` (ADR-0020
//! §1 + §4.1) — extracted from the former single-file
//! `sidecar/spawn.rs`.

use crate::state::SidecarHandle;
use crate::util::{SERVER_STARTED_POLL_INTERVAL_MS, SERVER_STARTED_TIMEOUT_MS};
use std::sync::atomic::AtomicBool;
use std::time::{Duration, Instant};
use tauri::Manager;
use tauri_plugin_shell::process::CommandEvent;
use tauri_plugin_shell::ShellExt;
use tokio::sync::mpsc;

use super::env_allowlist::passthrough_env_allowlist;
use super::handshake::{is_shutting_down, parse_server_started};

/// ADR-0020 §1 + §4.1: release-build spawn via `externalBin`. Wraps
/// the resulting `CommandChild` in `SidecarHandle::ShellPlugin`.
///
/// Kill-on-parent-exit guarantee: the release-mode ShellPlugin sidecar's
/// `CommandChild` does NOT kill the OS process on Drop. If the host
/// crashes (segfault, OOM kill, `kill -9`), the sidecar Python process
/// would be orphaned and keep running with the mic / IPC port / native
/// hotkey binary held. To prevent this, `spawn_sidecar_release`
/// registers a kill-on-parent-exit guarantee via the platform helper
/// `crate::platform::process::register_kill_on_parent_exit(pid)` right
/// after `cmd.spawn()` below. The platform helper implements the
/// OS-specific machinery:
///   - POSIX: a "reaper" subprocess spawned via `/bin/sh` (detached
///     into its own session with `setsid()`) that polls the host pid
///     once per second with `kill -0` and sends `kill -9` to the
///     sidecar pid once the host is gone (see
///     `platform/process/posix.rs` — NOT `prctl(PR_SET_PDEATHSIG)`,
///     which can only be set inside the child after fork, and
///     Tauri's `externalBin` API exposes no pre-exec hook).
///   - Windows: assign the sidecar to a Job Object with
///     `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`.
///
/// Best-effort: errors are logged but do NOT abort the spawn (the
/// sidecar is already running — killing the host's spawn path wouldn't
/// help). The dev-mode path is already covered by `kill_on_drop(true)`
/// (see `spawn_sidecar_dev_mode`).
pub(crate) async fn spawn_sidecar_release(
    app: &tauri::AppHandle,
    token: &str,
    shutting_down: Option<&AtomicBool>,
) -> Result<(u16, SidecarHandle, mpsc::Receiver<CommandEvent>), String> {
    // ADR-0020 §4.1: Tauri's externalBin selects the right binary by
    // matching the Rust target triple at runtime. The binary name
    // (without the triple suffix) is `python-sidecar`.
    let sidecar = app
        .shell()
        .sidecar("python-sidecar")
        .map_err(|e| format!("failed to resolve sidecar binary: {e}"))?;

    // ADR-0020 §2 + §3: pass TAURI_SIDECAR=1 + VOICE_TYPER_IPC_TOKEN
    // + VOICE_TYPER_NATIVE_DIR env vars.
    // The sidecar's `ipc_server.py main()` checks TAURI_SIDECAR=1 to
    // skip the Python-side single-instance mutex + heartbeat watchdog.
    //
    // Prewarm binary removal (Phase 2a, plan-runtime-pack-split §6.2):
    // the `prewarm exe env var is no longer set — the
    // prewarm binary is deleted (Sub-agent 6) and the prewarm phase
    // moved INTO the worker exe (Option P-1). The Rust-side
    // `prewarm_resource_path` helper that resolved the prewarm exe
    // path is also deleted. The slim-core sidecar no longer needs to
    // know the prewarm exe path.
    let native_dir = app
        .path()
        .resource_dir()
        .map(|p| p.join("native"))
        .map_err(|e| format!("resource_dir failed: {e}"))?;

    // Clear inherited host env BEFORE adding the
    // voice-typer-specific vars. Without this, the sidecar inherits
    // arbitrary host env vars (e.g. `HF_TOKEN`, `OPENAI_API_KEY`,
    // `http_proxy`) — a leak surface for credentials + a configuration
    // surprise surface (the sidecar would see unrelated host exports).
    // The `passthrough_env_allowlist()` re-adds only the OS-required
    // vars the sidecar needs to function (PATH, HOME, locale, etc.).
    let cmd = sidecar
        .args(["--ws"])
        .env_clear()
        .envs(passthrough_env_allowlist())
        .env("TAURI_SIDECAR", "1")
        .env("VOICE_TYPER_IPC_TOKEN", token)
        // Share the host's per-process session ID so the
        // Python sidecar's log lines carry the same join key as the
        // Rust host's (cross-process log correlation). The Python
        // `log/__init__.py` prefers this env var when set, falling
        // back to generating its own.
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
        // Launch-timeline markers for the sidecar's startup log
        // (startup_timeline.py): host boot epoch (recorded once at
        // host start) + THIS spawn's epoch — read at call time,
        // immediately before the spawn below, so the measured
        // "backend init" phase stays honest. Fresh on every respawn.
        .envs(crate::startup_timeline::sidecar_timeline_envs());

    // Tauri v2's shell plugin automatically pipes stdout/stderr —
    // the `spawn()` returns a `Receiver<CommandEvent>` that yields
    // `Stdout`/`Stderr`/`Terminate`/`Error` events. We do NOT call
    // `.stdout(Stdio::piped())` (that's the std::process API, not
    // the tauri-plugin-shell API).
    let (mut rx, child) = cmd
        .spawn()
        .map_err(|e| format!("failed to spawn sidecar: {e}"))?;

    // Register a kill-on-parent-exit guarantee so the OS reaps the
    // orphan sidecar when the host dies. Best-effort: errors are logged
    // but do NOT abort the spawn (the sidecar is already running —
    // killing the host's spawn path wouldn't help). The dev-mode path
    // is already covered by `kill_on_drop(true)` (see
    // `spawn_sidecar_dev_mode`).
    //
    // NOTE: `child.pid()` returns `u32` directly (NOT `Option<u32>`)
    // for the shell-plugin child — it always has a pid once spawned.
    let sidecar_pid = child.pid();
    if let Err(e) = crate::platform::process::register_kill_on_parent_exit(sidecar_pid) {
        log::warn!(
            "[SIDECAR] failed to register kill-on-parent-exit for pid {} \
             (best-effort — sidecar will run but may be orphaned on host crash): {}",
            sidecar_pid,
            e
        );
    }

    // ADR-0020 §1: read stdout until we parse the server_started JSON.
    // The sidecar force-sets stdout to line-buffered (sidecar_ws.py
    // `_force_line_buffered_stdout`), so each `print(..., flush=True)`
    // arrives as one event.
    let deadline = Instant::now() + Duration::from_millis(SERVER_STARTED_TIMEOUT_MS);
    let mut stdout_buf = String::new();

    while Instant::now() < deadline {
        // Short-circuit the stdout-read loop if the host is
        // shutting down. Without this check, a respawn initiated
        // seconds before the user quits the app would block here for
        // up to SERVER_STARTED_TIMEOUT_MS (30s) waiting for a
        // `server_started` line that will never arrive — the user
        // would see a 30s "app won't quit" hang. The check uses
        // SeqCst to pair with the `shutting_down.swap(true, SeqCst)`
        // in `shutdown_sidecar_for_exit` (state.rs) — we want a total
        // order between the swap and this load so we never miss the
        // flag flip due to memory-ordering skid.
        //
        // On shutdown detection we kill the freshly-spawned child
        // (reap grandchildren via `kill_process_tree` first, then
        // `child.kill()`) and return Err("shutdown"). The supervisor's
        // `respawn_inner` checks for this specific error string and
        // treats it as a graceful exit (clears `respawn_in_progress`,
        // returns Ok) instead of retrying — see the match arm in
        // `supervisor.rs`.
        if is_shutting_down(shutting_down) {
            log::info!(
                "[SIDECAR] shutting_down set during stdout-read loop — killing freshly-spawned child"
            );
            let pid = child.pid();
            let _ = tauri::async_runtime::spawn_blocking(move || {
                crate::platform::process::kill_process_tree(pid)
            })
            .await;
            if let Err(kill_err) = child.kill() {
                log::warn!(
                    "[SIDECAR] failed to kill child after shutting_down detected (best-effort): {}",
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
                // tauri-plugin-shell yields CommandEvent enums
                // (Stdout/Stderr/Terminated/Error). We only care about
                // Stdout lines for the server_started JSON.
                let line = match event {
                    CommandEvent::Stdout(bytes) => {
                        // `.into_owned()` reuses the inner String
                        // when the Cow is Owned (invalid UTF-8 case —
                        // Python sidecar stderr can contain non-UTF-8
                        // bytes from a C extension traceback). The prior
                        // `.to_string()` form always allocated a new
                        // String, even when the Cow was already Owned.
                        String::from_utf8_lossy(&bytes).into_owned()
                    }
                    CommandEvent::Stderr(bytes) => {
                        // Log stderr but don't parse it as server_started.
                        // same `.into_owned()` rationale as above.
                        // demoted from `info!` to `debug!`. The
                        // sidecar's stderr can be extremely chatty (Python
                        // warning frames, native hotkey binary debug
                        // prints, ctranslate2 device-info dumps). Logging
                        // every line at INFO swamped the host log and
                        // drowned genuinely actionable diagnostics.
                        // The Python side's own `log.py` already routes
                        // warnings/errors to its separate log file — the
                        // host-side INFO echo was redundant with that.
                        // `debug!` keeps the lines available under
                        // `RUST_LOG=debug` without polluting the default
                        // INFO stream.
                        let s = String::from_utf8_lossy(&bytes).into_owned();
                        log::debug!("[SIDECAR] stderr: {}", s.trim());
                        continue;
                    }
                    CommandEvent::Terminated(payload) => {
                        // kill the child before returning Err so
                        // a sidecar that sent Terminated but didn't
                        // actually exit doesn't leak as a zombie. The
                        // Tauri shell-plugin child handle is single-use
                        // after kill (consumes `child`), so we move it
                        // out of the captured variable before the Err.
                        // Best-effort: errors are logged but don't
                        // replace the original spawn-failure error.
                        //
                        // also reap grandchildren
                        // via `kill_process_tree` BEFORE `child.kill()`
                        // so the root is still alive when we walk
                        // `pgrep -P <pid>` (on Unix, killing the root
                        // first would reparent the children to init and
                        // break the descendant walk).
                        //
                        // NOTE: `tauri_plugin_shell::process::CommandChild::pid()`
                        // returns `u32` directly (NOT `Option<u32>` —
                        // unlike `tokio::process::Child::id()` in the
                        // dev-mode path below). The shell-plugin child
                        // always has a pid once spawned.
                        //
                        // (High): wrap the blocking
                        // `kill_process_tree` (which on Unix spawns
                        // `pgrep` + `kill -TERM` per descendant +
                        // `std::thread::sleep(200ms)` + `kill -KILL` per
                        // descendant — all `std::process::Command::status()`
                        // blocking syscalls) in
                        // `tauri::async_runtime::spawn_blocking` so we
                        // don't stall a Tokio worker thread. Mirrors the
                        // `SidecarHandle::kill_tree` pattern in
                        // `state.rs:148`.
                        let pid = child.pid();
                        let _ = tauri::async_runtime::spawn_blocking(move || {
                            crate::platform::process::kill_process_tree(pid)
                        })
                        .await;
                        if let Err(kill_err) = child.kill() {
                            log::warn!(
                                "[SIDECAR] failed to kill child after Terminated event (best-effort): {}",
                                kill_err
                            );
                        }
                        return Err(format!(
                            "sidecar terminated before server_started (code={:?})",
                            payload.code
                        ));
                    }
                    CommandEvent::Error(err) => {
                        // kill the child before returning Err.
                        // Without this, the sidecar process leaks — the
                        // shell-plugin CommandChild doesn't kill on Drop,
                        // so a sidecar that errored at startup but is
                        // still running would survive past the Err return.
                        // Best-effort: kill errors are logged but don't
                        // replace the original CommandEvent::Error.
                        //
                        // reap grandchildren
                        // first.
                        //
                        // NOTE: same as the Terminated arm above —
                        // `child.pid()` here is `u32`, not `Option<u32>`.
                        // (High): spawn_blocking wrap — see the
                        // comment in the Terminated arm above.
                        let pid = child.pid();
                        let _ = tauri::async_runtime::spawn_blocking(move || {
                            crate::platform::process::kill_process_tree(pid)
                        })
                        .await;
                        if let Err(kill_err) = child.kill() {
                            log::warn!(
                                "[SIDECAR] failed to kill child after CommandEvent::Error (best-effort): {}",
                                kill_err
                            );
                        }
                        return Err(format!("sidecar command error: {err}"));
                    }
                    _ => continue,
                };
                stdout_buf.push_str(&line);
                // Try to parse as the server_started event.
                if let Some(port) = parse_server_started(&line) {
                    log::info!("[SIDECAR] server_started port={}", port);
                    // hand the event receiver back to the caller so
                    // `shutdown_sidecar` can poll for `Terminated` instead
                    // of sleeping the full SHUTDOWN_ACK_TIMEOUT_MS.
                    //
                    // The ShellPlugin variant wraps
                    // `Option<CommandChild>` so the `Drop` impl in
                    // `state.rs` can `take()` the child out of `&mut self`
                    // for a best-effort kill on drop. At construction time
                    // the Option is always `Some(...)`.
                    return Ok((port, SidecarHandle::ShellPlugin(Some(child)), rx));
                }
                // Not the server_started line — could be a stray log
                // (shouldn't happen per ADR-0020 §1, sidecar sends
                // all non-handshake logs to stderr).
                log::warn!(
                    "[SIDECAR] unexpected stdout line (expected only server_started): {}",
                    line.trim()
                );
            }
            Ok(None) => {
                return Err("sidecar stdout closed before server_started".into());
            }
            Err(_) => {
                // Timeout on this iteration — loop and retry until deadline.
                continue;
            }
        }
    }
    let pid = child.pid();
    // reap grandchildren too — the bare `child.kill()` only
    // kills the sidecar root, leaving grandchildren (native hotkey
    // binary, model subprocesses) orphaned. Call `kill_process_tree`
    // BEFORE `child.kill()` so the root is still alive when we walk
    // `pgrep -P <pid>` (on Unix, killing the root first would reparent
    // the children to init and break the descendant walk).
    //
    // (High): spawn_blocking wrap — see the comment in the
    // Terminated arm above. The blocking `pgrep` + `kill` walk +
    // 200ms `std::thread::sleep` inside `kill_process_tree` must not
    // stall a Tokio worker thread.
    let _ = tauri::async_runtime::spawn_blocking(move || {
        crate::platform::process::kill_process_tree(pid)
    })
    .await;
    // log the kill error for visibility (mirrors the dev-mode
    // path's kill-error logging below). The previous `let _ = child.kill();`
    // silently dropped the kill error — a stuck sidecar that won't die
    // was invisible in the log.
    if let Err(e) = child.kill() {
        log::warn!("[SIDECAR] failed to kill child after deadline: {}", e);
    }
    // reap the zombie. `child.kill()` sends the kill signal but
    // does NOT itself waitpid — the Tauri shell-plugin's internal exit
    // watcher delivers a `CommandEvent::Terminated` to `rx` once the OS
    // reports the process has died. Without draining `rx` here, the
    // `Terminated` event sits unread in the channel buffer until `rx`
    // is dropped on function return; depending on the shell-plugin
    // version's exit-watcher scheduling, the host-side waitpid may be
    // deferred until that drain happens — leaving a brief zombie window.
    // The 500ms timeout bounds the spawn path against a misbehaving
    // process that ignores the kill signal (rare, but possible for
    // uninterruptible kernel waits). Best-effort: errors and timeouts
    // are silently discarded (the kill has already been attempted; we
    // cannot do anything more useful with a drain failure).
    let _ = tokio::time::timeout(Duration::from_millis(500), rx.recv()).await;
    Err(format!(
        "sidecar did not emit server_started within {}ms. stdout so far: {}",
        SERVER_STARTED_TIMEOUT_MS, stdout_buf
    ))
}
