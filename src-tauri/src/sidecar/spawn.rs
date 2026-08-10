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
//! - [`prewarm`] — prewarm binary path resolution (`prewarm_resource_path`
//!   + the dev-mode `dev_prewarm_exe`).
//! - [`target_triple`] — the pure `target_triple_for` table +
//!   `current_target_triple` runtime wrapper.
//!
//! Both spawn paths call `.env_clear()` before
//! adding specific env vars, then re-add only an explicit OS-required
//! allowlist via `passthrough_env_allowlist()`. This prevents the
//! sidecar from inheriting arbitrary host env vars (e.g. `HF_TOKEN`,
//! `OPENAI_API_KEY`, `http_proxy`) exported from the user's shell.

mod dev_mode;
mod env_allowlist;
mod handshake;
mod prewarm;
mod release_mode;
mod target_triple;

// The unit-tested helpers live in the submodules above; the sibling
// test file (`spawn_tests.rs`) resolves them via `use super::*`, so
// re-export them here (test-only — production callers reach the
// submodules directly).
#[cfg(test)]
pub(crate) use dev_mode::is_dev_mode_for;
#[cfg(test)]
pub(crate) use env_allowlist::passthrough_env_allowlist;
#[cfg(test)]
pub(crate) use handshake::{is_shutting_down, parse_server_started};
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
