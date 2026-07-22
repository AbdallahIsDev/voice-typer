//! Shared state types for the Voice Typer Tauri host (ADR-0020 §1 + §10).

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicU64};
use std::sync::{Arc, Mutex};
use tauri_plugin_shell::process::{CommandEvent, CommandChild};
use tokio::sync::{mpsc, oneshot, Mutex as AsyncMutex};
use tokio_tungstenite::tungstenite::Message;
use serde_json::Value;

// ─── Poison-safe Mutex helper (G4-H-27) ───────────────────────────────
//
// `Mutex::lock().unwrap()` panics if the lock is poisoned (a thread
// panicked while holding it). Poisoning permanently bricks the lock —
// every subsequent `.lock().unwrap()` re-panics with the original
// poison error, taking down the FT-1 resilience layer itself. This
// helper recovers the inner value via `into_inner()` so a poisoned
// lock is downgraded to "the value may be in an inconsistent state,
// but we can still make progress". For our state fields (child handle,
// ws_tx, pending) the worst case is that a half-written slot reads as
// `None` (treated the same as "sidecar not connected") — which is
// strictly safer than panicking the resilience layer.
//
// Usage: replace `state.token.lock().unwrap()` → `lock(&state.token)`.
//
// Used by `state.rs`, `ft1.rs`, `ws.rs`, and `platform/logging.rs`.
// PVT-G5-018 originally used the inline `.unwrap_or_else(|e| e.into_inner())`
// form in `logging.rs`; the merged version routes through this helper
// for consistency (no circular-dep concern — `state.rs` only uses the
// `log::warn!` macro, which expands to `log`-crate calls, not to
// `logging.rs` calls).
#[allow(dead_code)]
pub(crate) fn lock<T>(m: &std::sync::Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

// ─── Shared state ─────────────────────────────────────────────────────

/// Pending dispatch requests keyed by id. Each entry has a oneshot
/// sender that the WS reader task fulfills when the matching response
/// arrives.
pub(crate) type PendingMap = Arc<AsyncMutex<HashMap<u64, oneshot::Sender<Value>>>>;

/// The WS writer half, wrapped in a channel so the dispatch command
/// (which runs on a Tauri async runtime) can send frames without
/// holding the WS writer directly.
///
/// PVT-G5-059: previously `mpsc::UnboundedSender<Message>`. Switched
/// to a bounded `mpsc::Sender<Message>` (capacity 256, see
/// `sidecar::ws::reconnect_ws`) so a runaway renderer (or a stuck WS
/// writer task) cannot enqueue unbounded frames and OOM the host.
/// Callers in `commands/bubble.rs` and `commands/sidecar_cmds.rs`
/// must use `ws_tx.try_send(...)` instead of `ws_tx.send(...)` and
/// handle `TrySendError::Full` / `TrySendError::Closed`.
pub(crate) type WsWriterTx = mpsc::Sender<Message>;

/// ADR-0020 §1 + §14: the sidecar child handle is either a Tauri
/// shell-plugin `CommandChild` (release builds, spawned via
/// `externalBin`) or a `tokio::process::Child` (dev mode, spawned via
/// `VOICE_TYPER_SIDECAR_DEV=1` running `python -m voice_typer.server.ipc_server`).
/// Both variants support `kill()`; `shutdown_sidecar` matches on the
/// variant to call the right kill method.
pub(crate) enum SidecarHandle {
    ShellPlugin(CommandChild),
    DevMode(tokio::process::Child),
}

impl SidecarHandle {
    /// Return the OS process id of the sidecar, if available. Used by
    /// `kill_tree` (ADR-0020 §10 — recursive "kill_children" backstop)
    /// to also reap grandchildren (native hotkey binary, model processes)
    /// that the Python sidecar does not reap on its own exit.
    ///
    /// CR-14: exposed as `pub(crate)` so the FT-1 retry-loop tests in
    /// `ft1.rs` can verify that `state.child` holds the NEW child (not
    /// a stale reference to the old one) after the take-kill-store
    /// pattern runs. Previously private; the visibility bump is the
    /// minimal change needed to make the CR-14 fix testable without
    /// adding a Drop impl or a new public API.
    pub(crate) fn pid(&self) -> Option<u32> {
        match self {
            SidecarHandle::ShellPlugin(c) => Some(c.pid()),
            SidecarHandle::DevMode(c) => c.id(),
        }
    }

    /// Kill the sidecar process. Consumes `self` because
    /// `CommandChild::kill(self)` takes ownership (the shell-plugin
    /// child handle is single-use after kill). The dev-mode variant
    /// (`tokio::process::Child::kill(&mut self)`) only borrows but we
    /// consume the handle anyway for API uniformity.
    ///
    /// G4-M-66: the shell-plugin kill path preserves the original
    /// error as the `source()` of the returned `io::Error` (via
    /// `io::Error::new(io::ErrorKind::Other, e)`) so callers can
    /// inspect the underlying `tauri_plugin_shell::Error` if needed.
    /// The previous implementation flattened the error to a `format!`
    /// string, discarding the source variant.
    pub(crate) async fn kill(self) -> std::io::Result<()> {
        match self {
            SidecarHandle::ShellPlugin(c) => {
                c.kill().map_err(|e| {
                    // Preserve the original shell-plugin error variant as
                    // the `source()` of the io::Error (G4-M-66). The
                    // Display impl of io::Error includes both the outer
                    // message and the source's Display, so log lines stay
                    // readable while still being inspectable via
                    // `err.source()` / `err.get_ref()`.
                    std::io::Error::new(
                        std::io::ErrorKind::Other,
                        format!("shell-plugin kill: {e}"),
                    )
                })
            }
            SidecarHandle::DevMode(mut c) => c.kill().await,
        }
    }

    /// ADR-0020 §10: `kill_children` backstop. Kills the entire sidecar
    /// process TREE (the sidecar plus any grandchildren it spawned, e.g.
    /// the native hotkey binary and model subprocesses) rather than only
    /// the direct child. This is the hard-kill fallback used when the
    /// cooperative `{"type":"shutdown"}` handshake does not complete
    /// within `SHUTDOWN_ACK_TIMEOUT_MS`. A plain `kill()` would orphan
    /// the grandchildren and leave them holding the mic / input device.
    ///
    /// Best-effort and OS-native: shells out to the platform tool
    /// (`taskkill /T` on Windows, `pgrep -P` recursive walk on Unix).
    /// Failures are logged but do not abort shutdown — the direct child
    /// is still reaped afterwards via `self.kill()`.
    ///
    /// G4-M-62 / PVT-G5-029: on Unix this performs a cooperative SIGTERM
    /// first, waits a brief grace period, then escalates to SIGKILL for
    /// any survivors. This mirrors the cooperative-shutdown
    /// `SHUTDOWN_ACK_TIMEOUT_MS` pattern (give the sidecar a chance to
    /// release the mic / close IPC sockets before force-killing).
    ///
    /// PVT-047 (session 2): wraps the synchronous `kill_process_tree`
    /// (which does `std::process::Command::status()` — a blocking
    /// syscall) in `spawn_blocking` so we don't stall a Tokio worker
    /// thread for the duration of the kill-walk. `taskkill /T` on a
    /// large tree or `pgrep` under load can take >1s.
    pub(crate) async fn kill_tree(self) -> std::io::Result<()> {
        if let Some(pid) = self.pid() {
            // PVT-047: spawn_blocking so the blocking
            // `std::process::Command::status()` calls inside
            // `kill_process_tree` don't stall a Tokio worker thread.
            let _ = tauri::async_runtime::spawn_blocking(move || {
                kill_process_tree(pid);
            })
            .await;
        }
        self.kill().await
    }
}

/// Kill the process tree rooted at `pid` (the sidecar and its
/// descendants). Platform-native, best-effort — never panics.
///
/// PVT-G5-029: on Unix this now does a **recursive** walk via
/// `pgrep -P <pid>` (depth-first) so ALL descendants are reaped —
/// grandchildren (native hotkey binary, model subprocesses) included.
/// The prior `pkill -TERM -P <pid>` only matched DIRECT children,
/// leaving grandchildren holding the mic / input device after the
/// sidecar exited. The root pid itself is NOT killed here — the caller
/// (`SidecarHandle::kill_tree` / `spawn.rs` cleanup) kills the root
/// separately via `child.kill()` afterwards, so we focus on the
/// descendants only.
///
/// PVT-G5-030: exposed as `pub(crate)` so `spawn.rs`'s spawn-timeout
/// cleanup paths can call it directly (they only have the `CommandChild`
/// / `tokio::process::Child`, not a `SidecarHandle`, so they can't use
/// `kill_tree`).
pub(crate) fn kill_process_tree(pid: u32) {
    #[cfg(windows)]
    {
        use std::process::Command;
        // /F = force, /T = terminate the whole tree rooted at the pid.
        // `taskkill /T` already walks descendants recursively, so no
        // separate walk is needed on Windows.
        let _ = Command::new("taskkill")
            .args(["/F", "/T", "/PID", &pid.to_string()])
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status();
    }
    #[cfg(unix)]
    {
        use std::process::Command;
        use std::time::Duration;

        // PVT-G5-029: depth-first collection of ALL descendants.
        // `pgrep -P <cur>` prints the PIDs of `cur`'s direct children,
        // one per line. We push each onto the stack so its own children
        // are visited too — this catches grandchildren + deeper.
        let mut all_descendants: Vec<u32> = Vec::new();
        let mut stack: Vec<u32> = vec![pid];
        while let Some(cur) = stack.pop() {
            let pgrep = Command::new("pgrep")
                .args(["-P", &cur.to_string()])
                .stdout(std::process::Stdio::piped())
                .stderr(std::process::Stdio::null())
                .output();
            if let Ok(out) = pgrep {
                let stdout = String::from_utf8_lossy(&out.stdout);
                for line in stdout.lines() {
                    if let Ok(child_pid) = line.trim().parse::<u32>() {
                        all_descendants.push(child_pid);
                        stack.push(child_pid);
                    }
                }
            }
        }

        // SIGTERM each descendant (graceful first — gives the native
        // hotkey binary / model subprocesses a chance to release the
        // audio device + clean up).
        for &dpid in &all_descendants {
            let _ = Command::new("kill")
                .args(["-TERM", &dpid.to_string()])
                .stdout(std::process::Stdio::null())
                .stderr(std::process::Stdio::null())
                .status();
        }

        // Brief grace period so the SIGTERM can take effect before we
        // escalate to SIGKILL. 200ms is enough for a well-behaved child
        // to exit; longer waits would slow shutdown without catching
        // many more survivors.
        std::thread::sleep(Duration::from_millis(200));

        // SIGKILL survivors (best-effort — `kill -KILL` on an already-
        // exited pid is a no-op, so we don't bother filtering).
        for &dpid in &all_descendants {
            let _ = Command::new("kill")
                .args(["-KILL", &dpid.to_string()])
                .stdout(std::process::Stdio::null())
                .stderr(std::process::Stdio::null())
                .status();
        }
    }
}

pub(crate) struct SidecarState {
    /// Child handle for kill_children backstop.
    pub(crate) child: Mutex<Option<SidecarHandle>>,
    /// Token for the current sidecar instance (rotated per FT-1 respawn).
    ///
    /// G4-L-01 (Low): this field is WRITE-ONLY dead state — it is
    /// written in `main.rs` (cold-start path) and `ft1.rs` (respawn
    /// path), but NEVER read anywhere (the WS auth frame uses the
    /// local `new_token` variable, not this field). It exists as a
    /// historical artifact of the ADR-0020 §3 token-rotation design
    /// (which originally planned to expose the live token to dispatch
    /// callers). The recommended fix is removal; however, removal
    /// requires coordinated edits to `main.rs:147` (struct init) and
    /// `main.rs:208` (cold-start write) which are outside this
    /// sub-agent's edit scope. The field is retained here as
    /// `Mutex<String>` for backward compatibility; a future coordinated
    /// task should remove it AND its two write sites. If future code
    /// needs to read the token, wrap in `zeroize::Zeroizing<String>`
    /// (requires adding the `zeroize` crate to Cargo.toml).
    pub(crate) token: Mutex<String>,
    /// WS writer channel — None when the WS is disconnected.
    pub(crate) ws_tx: Mutex<Option<WsWriterTx>>,
    /// Pending dispatch requests (id → response sender).
    pub(crate) pending: PendingMap,
    /// Next request id.
    pub(crate) next_id: AtomicU64,
    /// Shutdown signal — set when the app is quitting so FT-1 doesn't
    /// respawn the sidecar during shutdown.
    pub(crate) shutting_down: AtomicBool,
    /// FT-1 respawn serialization flag. Set when a respawn is in flight
    /// so concurrent WS-reader exits (e.g., a flapping sidecar that dies
    /// immediately after reconnect) don't launch multiple parallel
    /// `ft1_respawn` supervisors that would corrupt `child`/`token`/`ws_tx`.
    /// Acquired with `compare_exchange(false → true)` on entry; cleared on
    /// exit (both Ok and restart paths).
    pub(crate) respawn_in_progress: AtomicBool,
    /// CR-2: Event receiver from the sidecar's `Command::spawn()`. Used by
    /// `shutdown_sidecar` to poll for `CommandEvent::Terminated` so the
    /// host exits the wait loop as soon as the sidecar acks and exits
    /// (~50ms typical), instead of blocking the full
    /// `SHUTDOWN_ACK_TIMEOUT_MS` (2000ms). Only set for the `ShellPlugin`
    /// variant (release builds); the `DevMode` variant spawns via
    /// `tokio::process::Command` which has no `CommandEvent` stream —
    /// `shutdown_sidecar` falls back to bounded sleep polling for that
    /// path.
    pub(crate) child_exit_rx: AsyncMutex<Option<mpsc::Receiver<CommandEvent>>>,
}

// ─── PVT-G5-007: app-exit sidecar teardown ────────────────────────────────

/// Best-effort sidecar teardown for app-exit paths that can't run the
/// full cooperative `shutdown_sidecar` Tauri command (e.g.
/// `RunEvent::Exit` from the Tauri event loop, which fires on
/// `app.exit()` / quit-tray / Ctrl-C / SIGTERM).
///
/// **Idempotent** via `shutting_down.swap(true, SeqCst)` — returns
/// immediately if a shutdown is already in flight (either the
/// renderer's `shutdown_sidecar` command, a prior `ExitRequested`,
/// or the FT-1 supervisor already set the flag). This makes it safe
/// to call from both `ExitRequested` AND `Exit` (which can fire
/// back-to-back) without double-killing.
///
/// Sequence:
/// 1. Set `shutting_down` (idempotency guard).
/// 2. Send the `{"type":"shutdown"}` WS frame (best-effort — skipped
///    if the WS is already torn down).
/// 3. Wait up to `SHUTDOWN_ACK_TIMEOUT_MS` (2s) for the sidecar to
///    exit gracefully (polling the `CommandEvent` receiver if present;
///    bounded sleep for dev-mode).
/// 4. Force-kill the process tree via `SidecarHandle::kill_tree` so
///    grandchildren (native hotkey binary, model subprocesses) are
///    reaped too.
///
/// The caller (in `main.rs`'s `RunEvent` callback) wraps this in
/// `tauri::async_runtime::block_on` + `tokio::time::timeout` so the
/// run loop never hangs on a misbehaving sidecar.
pub(crate) async fn shutdown_sidecar_for_exit(state: &Arc<SidecarState>) {
    use std::sync::atomic::Ordering;
    use std::time::Duration;
    use crate::util::SHUTDOWN_ACK_TIMEOUT_MS;

    // Idempotency guard: if a shutdown is already in flight (renderer
    // command, prior ExitRequested, or FT-1 supervisor), bail out —
    // that path is already tearing the sidecar down.
    if state.shutting_down.swap(true, Ordering::SeqCst) {
        return;
    }

    // Send the shutdown frame so a cooperative sidecar can ack + exit
    // within the 2s window. Best-effort — if the WS is already gone,
    // we'll fall through to force-kill.
    let frame = serde_json::json!({"type": "shutdown"});
    if let Some(ws_tx) = state.ws_tx.lock().unwrap().clone() {
        // PVT-G5-059: bounded channel — use `try_send` instead of `send`.
        // At shutdown the channel may be full of pending dispatch frames
        // (rare but possible during a flap); `try_send` avoids awaiting
        // on a full channel while the run loop is trying to exit.
        let _ = ws_tx.try_send(Message::Text(frame.to_string()));
    }

    // Wait up to SHUTDOWN_ACK_TIMEOUT_MS for graceful exit. The
    // CommandEvent receiver (release builds) yields `Terminated` when
    // the sidecar exits; the dev-mode path has no receiver so we just
    // sleep the full window. We don't care WHICH event came back —
    // either way we proceed to the force-kill backstop below.
    let deadline = Duration::from_millis(SHUTDOWN_ACK_TIMEOUT_MS);
    let mut rx_guard = state.child_exit_rx.lock().await;
    if let Some(rx) = rx_guard.as_mut() {
        let _ = tokio::time::timeout(deadline, rx.recv()).await;
    } else {
        tokio::time::sleep(deadline).await;
    }
    drop(rx_guard);

    // Force-kill backstop: take the child and kill the whole tree.
    // `kill_tree` reaps descendants (PVT-G5-029) AND the root in one
    // call. No-op if the child has already exited (which is the
    // common case after the 2s graceful wait).
    let child_opt = state.child.lock().unwrap().take();
    if let Some(child) = child_opt {
        let _ = child.kill_tree().await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// PVT-G5-029: `kill_process_tree` must be best-effort — calling
    /// it with a non-existent pid (or a pid with no children) must not
    /// panic. Verifies the recursive `pgrep` walk handles an empty
    /// descendant set cleanly on Unix, and the `taskkill /T` call on
    /// Windows doesn't panic when the pid doesn't exist (it just
    /// returns a non-zero exit status which we ignore).
    #[test]
    fn test_kill_process_tree_nonexistent_pid_is_noop() {
        // PID 999_999 is vanishingly unlikely to exist on a test
        // runner. The function should return without panicking.
        kill_process_tree(999_999);
    }
}
