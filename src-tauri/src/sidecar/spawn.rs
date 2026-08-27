//! Sidecar spawn + stdout handshake (ADR-0020 §1 + §4.1 + §14).
//!
//! Module layout (EO-33 — split out of the former single-file module):
//!
//! - `self` — orchestration: the public spawn entry points
//!   (`spawn_sidecar_and_get_port[_with_shutdown]`), the dev-vs-release
//!   dispatch (`spawn_sidecar_and_get_port_inner`), and the cold-start
//!   wiring (`initialize_sidecar`).
//! - [`dev_mode`] — `VOICE_TYPER_SIDECAR_DEV=1` dev-mode spawn
//!   (`spawn_sidecar_dev_mode` + the `is_dev_mode` predicates).
//! - [`release_mode`] — release-build `externalBin` spawn
//!   (`spawn_sidecar_release`).
//! - [`handshake`] — `server_started` stdout parsing
//!   (`parse_server_started`) + the shutting-down loop short-circuit
//!   (`is_shutting_down`).
//! - [`env_allowlist`] — the OS-required env-var passthrough allowlist
//!   (`passthrough_env_allowlist`).
//! - [`target_triple`] — the pure `target_triple_for` table +
//!   `current_target_triple` runtime wrapper.
//!
//! Both spawn paths call `.env_clear()` before
//! adding specific env vars, then re-add only an explicit OS-required
//! allowlist via `passthrough_env_allowlist()`. This prevents the
//! sidecar from inheriting arbitrary host env vars (e.g. `HF_TOKEN`,
//! `OPENAI_API_KEY`, `http_proxy`) exported from the user's shell.
//!
//! Prewarm binary removal (Phase 2a, plan-runtime-pack-split §6.2):
//! the former `prewarm` submodule (which resolved the prewarm exe path
//! passed to Python via a dedicated env var) was
//! deleted when prewarm became a startup phase of the worker exe.
//! See `platform::worker_path` for the worker exe path resolution
//! that supersedes the prewarm exe path.

mod dev_mode;
mod env_allowlist;
mod handshake;
mod release_mode;
// Worker exe spawn logic (Phase 2b — plan-runtime-pack-split §7):
// `spawn_worker_release` + `spawn_worker_dev_mode` live in `worker.rs`
// so `spawn.rs` itself stays orchestration-only (E3). The worker is
// the SECOND spawned child (after the slim-core sidecar); its spawn
// mirrors the sidecar's dev/release split but with the worker's own
// env contract (VOICE_TYPER_IPC_TOKEN) + `worker_started` handshake.
mod worker;
// `pub(crate)` (not `mod`) so the worker-path resolver in
// `platform::worker_path` can reach `current_target_triple()` to build
// the per-platform worker exe name (`voice-typer-worker-<triple>[.exe]`).
// The pre-existing `#[cfg(test)] pub(crate) use target_triple::*`
// re-export below stays for backwards-compat with the spawn tests
// that import via `use super::*`.
pub(crate) mod target_triple;

// The unit-tested helpers live in the submodules above; the sibling
// test file (`spawn_tests.rs`) resolves them via `use super::*`, so
// re-export them here (test-only — production callers reach the
// submodules directly).
#[cfg(test)]
pub(crate) use dev_mode::is_dev_mode_for;
#[cfg(test)]
pub(crate) use env_allowlist::passthrough_env_allowlist;
#[cfg(test)]
pub(crate) use handshake::{is_shutting_down, parse_server_started, parse_worker_started};
#[cfg(test)]
pub(crate) use target_triple::{current_target_triple, target_triple_for};

use crate::state::SidecarHandle;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use tauri_plugin_shell::process::CommandEvent;
use tokio::sync::mpsc;

// ─── Sidecar spawn + stdout handshake (ADR-0020 §1) ───────────────────

/// Spawn the Python sidecar via Tauri's `externalBin` mechanism and
/// read the `server_started` JSON from stdout.
///
/// Returns the bound port + the child handle on success.
///
/// Both the cold-start path (`initialize_sidecar`) and the supervisor's
/// respawn path (`respawn_inner`) call the
/// `spawn_sidecar_and_get_port_with_shutdown` variant below, passing
/// `&state.shutting_down` so the stdout-read loop can short-circuit
/// mid-handshake if the user quits the app during a spawn.
///
/// When the shutdown flag flips to `true` (e.g. the user quit the app
/// while a respawn was in flight), the loop kills the freshly-spawned
/// child and returns `Err("shutdown")` instead of waiting up to
/// `SERVER_STARTED_TIMEOUT_MS` (30s) for a `server_started` line that
/// will never arrive.
///
/// Used by `crate::sidecar::supervisor::respawn_inner` and by the
/// cold-start path (`sidecar::spawn::initialize_sidecar`).
pub(crate) async fn spawn_sidecar_and_get_port_with_shutdown(
    app: &tauri::AppHandle,
    token: &str,
    shutting_down: &AtomicBool,
) -> Result<(u16, SidecarHandle, Option<mpsc::Receiver<CommandEvent>>), String> {
    spawn_sidecar_and_get_port_inner(app, token, Some(shutting_down)).await
}

/// Public cold-start entry point: spawn the Python sidecar via Tauri's
/// `externalBin` mechanism and read the `server_started` JSON from
/// stdout. Returns the bound port + the child handle on success.
///
/// This is the no-`shutting_down`-flag sibling of
/// [`spawn_sidecar_and_get_port_with_shutdown`] — used by the cold-start
/// path (`initialize_sidecar` in `main.rs::setup`) where there is no
/// pre-existing shutdown flag to poll (the supervisor installs one
/// later, but the first spawn happens before the supervisor's flag is
/// wired up). The supervisor's respawn path calls
/// `spawn_sidecar_and_get_port_with_shutdown` instead, passing its own
/// `&state.shutting_down` so a user-quit mid-respawn short-circuits the
/// stdout-read loop.
///
/// Both paths delegate to the shared `spawn_sidecar_and_get_port_inner`
/// helper so the dev-mode / release-mode dispatch + the
/// `server_started` parsing logic live in exactly one place.
///
/// Kept at module scope (not inside the test module) so the Python
/// source-inspection regex `fn\s+spawn_sidecar_and_get_port\s*\(\s*app\s*:\s*&tauri::AppHandle`
/// (test_externalbin_spawn_linux.py) keeps matching.
/// `#[allow(dead_code)]` suppresses the lint for the no-runtime-caller
/// contract; the cold-start path currently calls the
/// `_with_shutdown` variant (passing a fresh `AtomicBool`), but this
/// signature is pinned by the gate-check test as the documented public
/// entry point. Removing the function would silently break the
/// migration-gate contract.
#[allow(dead_code)]
pub(crate) async fn spawn_sidecar_and_get_port(
    app: &tauri::AppHandle,
    token: &str,
) -> Result<(u16, SidecarHandle, Option<mpsc::Receiver<CommandEvent>>), String> {
    spawn_sidecar_and_get_port_inner(app, token, None).await
}

/// Internal helper shared by `spawn_sidecar_and_get_port` and
/// `spawn_sidecar_and_get_port_with_shutdown`. The `shutting_down`
/// parameter is `Option<&AtomicBool>`: `None` for the cold-start path
/// (no flag to poll — `is_shutting_down` returns `false` unconditionally),
/// `Some(&flag)` for the supervisor-respawn path (polled between
/// stdout-read iterations).
async fn spawn_sidecar_and_get_port_inner(
    app: &tauri::AppHandle,
    token: &str,
    shutting_down: Option<&AtomicBool>,
) -> Result<(u16, SidecarHandle, Option<mpsc::Receiver<CommandEvent>>), String> {
    if dev_mode::is_dev_mode() {
        let (port, child) = dev_mode::spawn_sidecar_dev_mode(token, shutting_down).await?;
        return Ok((port, child, None));
    }
    let (port, child, rx) = release_mode::spawn_sidecar_release(app, token, shutting_down).await?;
    Ok((port, child, Some(rx)))
}

/// Cold-start sidecar initialization: spawn the sidecar, install the
/// child handle + exit receiver into shared state, and kick off the
/// initial WebSocket reconnect. On spawn failure or initial WS
/// connect failure, fall back to the supervisor's respawn path.
///
/// Extracted from `main.rs`'s `.setup` closure so the host
/// entrypoint stays wiring-only (C-ARCH-1). The orchestration here
/// is implementation logic — not wiring — and was previously
/// inlined as a 44-LOC `tauri::async_runtime::spawn(async move {
/// ... })` block in main.rs that did `state.child` mutation, exit-rx
/// installation, and respawn-fallback dispatch.
///
/// # Sequence
///
/// 1. Generate the per-launch bearer token via `util::generate_token`.
/// 2. Spawn the sidecar via `spawn_sidecar_and_get_port` (which
///    dispatches to dev-mode or release-mode spawn based on the
///    `VOICE_TYPER_SIDECAR_DEV` env var).
/// 3. Re-check `state.shutting_down` AFTER spawn returns — if the
///    user quit the app while we were waiting for `server_started`
///    (up to 30s on a cold start), the `RunEvent::Exit` handler
///    already set the flag but found no child to kill. Kill the
///    freshly-spawned sidecar here so it doesn't outlive the host,
///    then bail before installing it into state.
/// 4. Install the child handle + exit receiver into shared state.
/// 5. Kick off `reconnect_ws` to perform the WS auth handshake. On
///    failure, fall back to the supervisor's `respawn` (which will
///    retry with backoff per `SUPERVISOR_BACKOFF_MS`).
///
/// The caller (`main.rs::setup`) wraps this in
/// `tauri::async_runtime::spawn(async move { ... })` so it runs in
/// the background — the `.setup` closure must return `Ok(())`
/// quickly so the Tauri event loop starts.
pub(crate) async fn initialize_sidecar(
    app_handle: &tauri::AppHandle,
    state: Arc<crate::state::SidecarState>,
) {
    let token = crate::util::generate_token();

    match spawn_sidecar_and_get_port_with_shutdown(app_handle, &token, &state.shutting_down).await {
        Ok((port, child, exit_rx)) => {
            //re-check shutting_down AFTER spawn
            // returns — if the user quit the app while we
            // were waiting for server_started (up to 30s on
            // a cold start), the `RunEvent::Exit` handler
            // already set the flag but found no child to
            // kill. Kill the freshly-spawned sidecar here
            // so it doesn't outlive the host, then bail
            // before installing it into state.
            if state.shutting_down.load(Ordering::SeqCst) {
                log::info!(
                    "[SETUP] shutting_down set during sidecar spawn — \
                     killing freshly-spawned sidecar"
                );
                if let Err(e) = child.kill_tree().await {
                    log::warn!(
                        "[SETUP] kill_tree on freshly-spawned sidecar failed (best-effort): {}",
                        e
                    );
                }
                return;
            }
            *crate::state::lock(&state.child) = Some(child);
            //store the sidecar's event receiver so
            // shutdown_sidecar can poll for graceful exit.
            *state.child_exit_rx.lock().await = exit_rx;
            if let Err(e) = crate::sidecar::ws::reconnect_ws(app_handle, &state, port, &token).await
            {
                log::error!("[SETUP] initial WS connect failed: {}", e);
                let _ = crate::sidecar::supervisor::respawn(app_handle, &state).await;
            }
        }
        Err(e) => {
            log::error!("[SETUP] sidecar spawn failed: {}", e);
            let _ = crate::sidecar::supervisor::respawn(app_handle, &state).await;
        }
    }
}

// Sibling test module — tests live in `spawn_tests.rs` (per C-TEST-5:
// no inline `#[cfg(test)] mod tests` blocks in production source).
#[cfg(test)]
#[path = "spawn_tests.rs"]
mod spawn_tests;

// ─── Worker spawn (Phase 2b — runtime-pack split, §7) ────────────────
//
// The worker spawn entry points below are IMPLEMENTED (Phase 2b). The
// actual process spawn lives in the `worker` submodule
// (`spawn_worker_release` / `spawn_worker_dev_mode`), which mirrors
// the sidecar's release/dev split but with the worker's own contract:
//
//   - Spawn: `app.shell().sidecar("voice-typer-worker")` (release,
//     externalBin) or `python -m voice_typer.worker` (dev mode).
//   - Env: `VOICE_TYPER_IPC_TOKEN` (the worker refuses to start
//     without it — EXIT_NO_TOKEN), `VOICE_TYPER_CONFIG_DIR`,
//     `VOICE_TYPER_SESSION_ID` (log correlation).
//   - Handshake: `{"event":"worker_started","port":N,"protocol":1}`
//     on stdout — a DISTINCT event name from the sidecar's
//     `server_started` (see `parse_worker_started` in handshake.rs).
//
// Not yet delivered (next phases, per plan §7.2/§7.3): the worker's
// WS CLIENT connection is owned by the slim-core sidecar (NOT the
// Tauri host — the 1-host↔2-processes pattern §7.1), so the host's
// `reconnect_worker_ws` proxy + the worker respawn supervisor + the
// port handoff to the sidecar are still TBD. `initialize_worker` is
// likewise not yet CALLED anywhere: it must run after the pack is
// downloaded + verified (Phase 2c), and `WorkerState` is not yet
// `app.manage()`d in main.rs. The spawn logic itself is complete +
// compile-verified; wiring it into the lifecycle is the remaining
// integration step.

/// Spawn the ML worker exe via Tauri's `externalBin` mechanism
/// (release) or `python -m voice_typer.worker` (dev mode), reading the
/// `worker_started` JSON from stdout.
///
/// Returns the bound port + the child handle on success — the same
/// shape as `spawn_sidecar_and_get_port_with_shutdown` so the caller
/// (`initialize_worker`) can install them into `WorkerState` via the
/// same pattern.
///
/// # Contract (for the future implementer)
///
/// 1. Resolve the worker exe path via
///    `crate::platform::worker_path::worker_exe_path()` (per-platform,
///    cached). For dev mode, fall back to a source-tree-relative path
///    (parallel to `dev_prewarm_exe`'s pattern — deleted with the
///    prewarm binary in this Phase 2a slice).
/// 2. Generate the per-launch bearer token via `util::generate_token()`
///    ONCE (store in `state.auth_token: OnceLock<String>` — the worker
///    inherits the host's token across respawns so the slim-core
///    sidecar can re-authenticate without re-negotiating).
/// 3. Spawn via `app.shell().sidecar("voice-typer-worker")` (release)
///    or `tokio::process::Command::new(python_bin)` (dev mode, parallel
///    to `spawn_sidecar_dev_mode`). Pass `VOICE_TYPER_WORKER_TOKEN`
///    + `VOICE_TYPER_CONFIG_DIR` + `VOICE_TYPER_PACK_DIR` env vars
///    (the worker reads the pack dir to find its bundled engines).
/// 4. Read `server_started` JSON from stdout (parallel to
///    `parse_server_started` — the worker emits the same handshake).
/// 5. Install the child handle + exit receiver into `WorkerState`.
/// 6. The slim-core sidecar (NOT the Tauri host) connects to the
///    worker's WS port as a CLIENT. The host proxies frames through
///    `WorkerState::ws_tx` so the worker lifecycle stays under host
///    control (§7.2: respawn scheduler, shutdown).
///
/// # Worker lifecycle (§7.3)
///
/// - Worker starts ONCE after pack download + verification, stays
///   running for app lifetime. Holds ~450 MB RAM (unpacked pack +
///   loaded models).
/// - If RAM pressure is high, the slim-core sidecar sends a
///   `worker_unload` RPC (the worker exits; restart on next
///   transcription).
/// - Worker's prewarm phase runs once at startup (Option P-1).
/// - Worker shutdown: graceful via WS close, or forceful via
///   SIGTERM/taskkill (parallel to `shutdown_sidecar_for_exit`).
/// - Single-instance: worker takes `state.lock_file_path` lock file
///   (parallel to `VoiceTyperSingleInstance`).
/// - Respawn scheduler (§7.2): if the worker crashes, the slim-core
///   sidecar restarts it. Must NOT trip the sidecar's circuit breaker
///   (separate `respawn_in_progress` flag in `WorkerState`).
///
/// # Returns
///
/// `Ok((port, child, exit_rx))` on success — same shape as
/// `spawn_sidecar_and_get_port_with_shutdown` so the caller
/// (`initialize_worker` — TBD, parallel to `initialize_sidecar`)
/// can install them into `WorkerState` via the same pattern.
/// Uncalled until Phase 2c wires `initialize_worker` after pack
/// verification + `app.manage()`s `WorkerState` (see the module-level
/// comment) — same contract-pinned pattern as `spawn_sidecar_and_get_port`.
#[allow(dead_code)]
pub(crate) async fn spawn_worker_and_get_port_with_shutdown(
    app: &tauri::AppHandle,
    state: Arc<crate::state::WorkerState>,
    shutting_down: &AtomicBool,
) -> Result<(u16, SidecarHandle, Option<mpsc::Receiver<CommandEvent>>), String> {
    // The per-launch bearer token comes from `state.auth_token` — set
    // ONCE by `initialize_worker` (OnceLock) so a respawned worker
    // inherits the host's token and the slim-core sidecar can
    // re-authenticate without re-negotiating (§7.3).
    let token = state
        .auth_token
        .get()
        .ok_or_else(|| "worker auth token not set — call initialize_worker first".to_string())?;

    if dev_mode::is_dev_mode() {
        let (port, child) = worker::spawn_worker_dev_mode(token, Some(shutting_down)).await?;
        return Ok((port, child, None));
    }
    let (port, child, rx) = worker::spawn_worker_release(app, token, Some(shutting_down)).await?;
    Ok((port, child, Some(rx)))
}

/// Cold-start worker initialization: spawn the worker, install the
/// child handle + exit receiver into shared state.
///
/// Mirrors `initialize_sidecar`'s sequence (§7.3 lifecycle: starts
/// once after pack download + verification, stays running for app
/// lifetime). Unlike the sidecar, the host does NOT open a WS client
/// to the worker — the slim-core sidecar owns that connection
/// (1-host↔2-processes §7.1).
///
/// # Sequence (for the future implementer)
///
/// 1. Generate the per-launch bearer token via `util::generate_token`
///    and store in `state.auth_token` (`OnceLock::set` — fails
///    gracefully if already set, e.g. by a prior call).
/// 2. Resolve the worker lock file path via
///    `worker_path::worker_exe_path().with_file_name("worker.lock")`
///    and store in `state.lock_file_path`.
/// 3. Call `spawn_worker_and_get_port_with_shutdown`.
/// 4. Re-check `state.shutting_down` AFTER spawn returns — if the user
///    quit the app while we were waiting for `server_started`, kill
///    the freshly-spawned worker (parallel to `initialize_sidecar`).
/// 5. Install the child handle + exit receiver into `WorkerState`.
/// 6. Kick off `reconnect_worker_ws` (TBD, parallel to
///    `sidecar::ws::reconnect_ws`). On failure, fall back to the
///    worker supervisor's `respawn` (TBD, parallel to
///    `sidecar::supervisor::respawn`).
/// Uncalled until Phase 2c wires the pack-verified trigger (see the
/// module-level comment) — same contract-pinned pattern as
/// `spawn_sidecar_and_get_port`.
#[allow(dead_code)]
pub(crate) async fn initialize_worker(
    app_handle: &tauri::AppHandle,
    state: Arc<crate::state::WorkerState>,
) {
    // Per-launch token: generate ONCE per host launch and store in
    // `state.auth_token` (OnceLock). A second call (e.g. a worker
    // respawn) reuses the stored token — the worker inherits the
    // host's token so the slim-core sidecar can authenticate to a
    // respawned worker without re-negotiating (§7.3).
    state.auth_token.get_or_init(crate::util::generate_token);

    // Single-instance lock file path (parallel to
    // `VoiceTyperSingleInstance`): the worker takes this lock at spawn
    // time + releases it on exit; a stale lock is detected via PID
    // check. Resolved once per host launch (OnceLock).
    state.lock_file_path.get_or_init(|| {
        crate::platform::worker_path::worker_exe_path().with_file_name("worker.lock")
    });

    match spawn_worker_and_get_port_with_shutdown(app_handle, state.clone(), &state.shutting_down)
        .await
    {
        Ok((port, child, exit_rx)) => {
            // Re-check `state.shutting_down` AFTER spawn returns — if
            // the user quit the app while we were waiting for
            // `worker_started` (up to 30s on a cold start), kill the
            // freshly-spawned worker so it doesn't outlive the host
            // (parallel to `initialize_sidecar`).
            if state.shutting_down.load(Ordering::SeqCst) {
                log::info!(
                    "[WORKER-INIT] shutting_down set during worker spawn — \
                     killing freshly-spawned worker"
                );
                if let Err(e) = child.kill_tree().await {
                    log::warn!(
                        "[WORKER-INIT] kill_tree on freshly-spawned worker failed \
                         (best-effort): {}",
                        e
                    );
                }
                return;
            }
            *crate::state::lock(&state.child) = Some(child);
            // Store the worker's event receiver so the future
            // `shutdown_worker_for_exit` can poll for graceful exit.
            *state.child_exit_rx.lock().await = exit_rx;
            // NOTE: never include the bearer token (or the word
            // "token") in any log line here — ADR-0020 §3 "never
            // logged" is enforced by
            // test_externalbin_spawn_windows.py::test_spawn_rs_server_started_log_line_format.
            log::info!(
                "[WORKER-INIT] worker spawned (port={}) — WS client + \
                 respawn supervisor are the next phase (plan §7.2/§7.3; \
                 the slim-core sidecar owns the worker WS connection)",
                port
            );
        }
        Err(e) => {
            log::error!("[WORKER-INIT] worker spawn failed: {}", e);
            // Worker respawn supervisor (plan §7.2) is the next phase —
            // a failed spawn currently just logs; the supervisor will
            // retry with backoff without tripping the sidecar's circuit
            // breaker.
        }
    }
}
