//! Release-build sidecar spawn via Tauri's `externalBin` (ADR-0020
//! §1 + §4.1): extracted from the former single-file
//! `sidecar/spawn.rs`.

use crate::state::SidecarHandle;
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

/// ADR-0020 §1 + §4.1: release-build spawn via `externalBin`. Wraps
/// the resulting `CommandChild` in `SidecarHandle::ShellPlugin`.
///
/// Kill-on-parent-exit guarantee: the release-mode ShellPlugin sidecar's
/// `CommandChild` does NOT kill the OS process on Drop. If the host
/// crashes (segfault, OOM kill, `kill -9`), the sidecar Python process
/// would be orphaned and keep running with the mic / IPC port / native
/// hotkey binary held. To prevent this, this spawn path registers a
/// kill-on-parent-exit guarantee via the platform helper
/// `crate::platform::process::register_kill_on_parent_exit(pid)` right
/// after `cmd.spawn()` below. The platform helper implements the
/// OS-specific machinery:
///   - POSIX: a "reaper" subprocess spawned via `/bin/sh` (detached
///     into its own session with `setsid()`) that polls the host pid
///     once per second with `kill -0` and sends `kill -9` to the
///     sidecar pid once the host is gone (see
///     `platform/process/posix.rs`: NOT `prctl(PR_SET_PDEATHSIG)`,
///     which can only be set inside the child after fork, and
///     Tauri's `externalBin` API exposes no pre-exec hook).
///   - Windows: assign the sidecar to a Job Object with
///     `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`.
///
/// Best-effort: errors are logged but do NOT abort the spawn (the
/// sidecar is already running: killing the host's spawn path wouldn't
/// help). The dev-mode path is already covered by `kill_on_drop(true)`
/// (see `spawn_sidecar_dev_mode`).
///
/// The stdout-handshake read loop (shutting-down short-circuit,
/// CommandEvent arms, kill/drain ordering, deadline) lives in
/// `super::handshake_loop::read_handshake_from_command_events`: shared
/// with the worker release path. The labels below pin this path's exact
/// log/error wording.
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
    // the `prewarm exe env var is no longer set, the
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
    // `http_proxy`): a leak surface for credentials + a configuration
    // surprise surface (the sidecar would see unrelated host exports).
    // The `passthrough_env_allowlist()` re-adds only the OS-required
    // vars the sidecar needs to function (PATH, HOME, locale, etc.).
    let cmd = sidecar
        .args(["--ws"])
        .env_clear()
        .envs(passthrough_env_allowlist())
        // Forward the host's hidden-start launch flag (set by the
        // autostart launcher when a hidden autostart launches the app)
        // so the sidecar's hidden-start privacy gates see the same
        // launch state as the host window. No-op (empty iterator) on
        // normal visible launches.
        .envs(super::env_allowlist::vt_start_hidden_env())
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
        // host start) + THIS spawn's epoch, read at call time,
        // immediately before the spawn below, so the measured
        // "backend init" phase stays honest. Fresh on every respawn.
        .envs(crate::startup_timeline::sidecar_timeline_envs());

    // Tauri v2's shell plugin automatically pipes stdout/stderr —
    // the `spawn()` returns a `Receiver<CommandEvent>` that yields
    // `Stdout`/`Stderr`/`Terminate`/`Error` events. We do NOT call
    // `.stdout(Stdio::piped())` (that's the std::process API, not
    // the tauri-plugin-shell API).
    let (rx, child) = cmd
        .spawn()
        .map_err(|e| format!("failed to spawn sidecar: {e}"))?;

    // Register a kill-on-parent-exit guarantee so the OS reaps the
    // orphan sidecar when the host dies. Best-effort: errors are
    // logged but do NOT abort the spawn (the sidecar is already
    // running: killing the host's spawn path wouldn't help). The
    // dev-mode path is already covered by `kill_on_drop(true)` (see
    // `spawn_sidecar_dev_mode`).
    //
    // NOTE: `child.pid()` returns `u32` directly (NOT `Option<u32>`)
    // for the shell-plugin child: it always has a pid once spawned.
    register_kill_on_parent_exit_best_effort(
        "[SIDECAR]",
        "sidecar will run but may be orphaned on host crash",
        child.pid(),
    );

    // ADR-0020 §1: read stdout until we parse the server_started JSON.
    // The sidecar force-sets stdout to line-buffered (sidecar_ws.py
    // `_force_line_buffered_stdout`), so each `print(..., flush=True)`
    // arrives as one event.
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
    )
    .await?;
    // Hand the event receiver back to the caller so
    // `shutdown_sidecar` can poll for `Terminated` instead of sleeping
    // the full SHUTDOWN_ACK_TIMEOUT_MS. The ShellPlugin variant wraps
    // `Option<CommandChild>` so the `Drop` impl in `state.rs` can
    // `take()` the child out of `&mut self` for a best-effort kill on
    // drop; at construction time the Option is always `Some(...)`.
    //
    // Before returning it, install the permanent child-event drain:
    // the receiver handed to callers is the FORWARDED view (yields the
    // Terminated exit event), while the drain task owns the real
    // receiver and keeps it drained for the child's whole lifetime.
    // Without this, the bounded backpressured event channel sits
    // undrained between the handshake and the app-exit wait —
    // post-handshake stderr beyond the OS pipe buffer blocks the
    // child's writer threads (engine device dumps on model load are
    // the classic producer), and the first event the exit wait sees is
    // a stale Stderr line that force-kills instead of waiting for the
    // cooperative exit.
    let rx = super::event_drain::spawn_child_event_drain("[SIDECAR]", rx);
    Ok((port, SidecarHandle::ShellPlugin(Some(child)), rx))
}
