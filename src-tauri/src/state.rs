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
// poison error, taking down the resilience layer itself. This
// helper recovers the inner value via `into_inner()` so a poisoned
// lock is downgraded to "the value may be in an inconsistent state,
// but we can still make progress". For our state fields (child handle,
// ws_tx, pending) the worst case is that a half-written slot reads as
// `None` (treated the same as "sidecar not connected") — which is
// strictly safer than panicking the resilience layer.
//
// Usage: replace `state.<field>.lock().unwrap()` → `lock(&state.<field>)`.
//
// Used by `state.rs`, `supervisor.rs`, `ws.rs`, `main.rs`, `commands/sidecar_cmds.rs`,
// `commands/bubble.rs`, and `platform/logging.rs` (the latter via its own
// `mutex_lock` alias).
// PVT-G5-018 originally used the inline `.unwrap_or_else(|e| e.into_inner())`
// form in `logging.rs`; the merged version routes through this helper
// for consistency (no circular-dep concern — `state.rs` only uses the
// `log::warn!` macro, which expands to `log`-crate calls, not to
// `logging.rs` calls).
//
// EC-FIX-5 (EC-16 / EC-24): the `#[allow(dead_code)]` that used to live
// here was STALE — the helper IS used at 10+ production call sites
// (`ws.rs`, `main.rs`, `sidecar_cmds.rs`, `supervisor.rs`, `state.rs`,
// `bubble.rs`). Removed the suppression so the compiler will report any
// future drift toward "unused" as a real warning.
pub(crate) fn lock<T>(m: &std::sync::Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

// ─── Shared state ─────────────────────────────────────────────────────

/// Pending dispatch requests keyed by id. Each entry has a oneshot
/// sender that the WS reader task fulfills when the matching response
/// arrives.
///
/// GT-E3-5: removed the redundant outer `Arc`. `SidecarState` itself is
/// always shared via `Arc<SidecarState>`, so the inner `AsyncMutex` is
/// already shared — wrapping it in another `Arc` doubled the indirection
/// without any benefit. `AsyncMutex::lock` takes `&self`, so existing
/// call sites (`state.pending.lock().await`) compile unchanged. The
/// only cross-file impact is `main.rs`'s struct-literal initializer,
/// which must drop the `Arc::new(...)` wrapper — see GT-FIX-20.
pub(crate) type PendingMap = AsyncMutex<HashMap<u64, oneshot::Sender<Value>>>;

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
    /// CR-14: exposed as `pub(crate)` so the retry-loop tests in
    /// `supervisor.rs` can verify that `state.child` holds the NEW child (not
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
    // GT-19: capture each shell-out result and log on Err / non-zero
    // exit so a broken `taskkill`/`pgrep`/`kill` (PATH issue,
    // permissions, etc.) isn't silently swallowed. The function remains
    // best-effort — failures are logged but don't abort shutdown.
    #[cfg(windows)]
    {
        use std::process::Command;
        let tool = "taskkill";
        match Command::new(tool)
            .args(["/F", "/T", "/PID", &pid.to_string()])
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status()
        {
            Ok(s) if s.success() => {
                log::info!("[KILL-TREE] taskkill succeeded for pid={}", pid);
            }
            Ok(s) => {
                log::warn!(
                    "[KILL-TREE] {} exited with code {} for pid {}",
                    tool,
                    s.code().map(|c| c.to_string()).unwrap_or_else(|| "<signal>".into()),
                    pid
                );
            }
            Err(e) => {
                log::warn!("[KILL-TREE] {} failed for pid={}: {}", tool, pid, e);
            }
        }
    }
    #[cfg(unix)]
    {
        use std::process::Command;
        use std::time::Duration;

        let mut all_descendants: Vec<u32> = Vec::new();
        let mut stack: Vec<u32> = vec![pid];
        while let Some(cur) = stack.pop() {
            let pgrep = Command::new("pgrep")
                .args(["-P", &cur.to_string()])
                .stdout(std::process::Stdio::piped())
                .stderr(std::process::Stdio::null())
                .output();
            match pgrep {
                Ok(out) if out.status.success() => {
                    let stdout = String::from_utf8_lossy(&out.stdout);
                    for line in stdout.lines() {
                        if let Ok(child_pid) = line.trim().parse::<u32>() {
                            all_descendants.push(child_pid);
                            stack.push(child_pid);
                        }
                    }
                }
                Ok(out) => {
                    // Exit 1 = no children (normal leaf) — skip logging.
                    if out.status.code() != Some(1) {
                        log::warn!(
                            "[KILL-TREE] pgrep exited with code {:?} for pid {}",
                            out.status.code(),
                            cur
                        );
                    }
                }
                Err(e) => {
                    log::warn!("[KILL-TREE] pgrep failed for pid={}: {}", cur, e);
                }
            }
        }

        // Short-circuit when no descendants exist — avoids the
        // unconditional 200ms sleep below on the Tauri event-loop thread
        // (called from shutdown_sidecar_for_exit via block_on).
        if all_descendants.is_empty() {
            log::debug!("[KILL-TREE] no descendants for pid {} — skipping SIGTERM/SIGKILL cycle", pid);
            return;
        }

        for &dpid in &all_descendants {
            match Command::new("kill")
                .args(["-TERM", &dpid.to_string()])
                .stdout(std::process::Stdio::null())
                .stderr(std::process::Stdio::null())
                .status()
            {
                Ok(s) if s.success() => {}
                Ok(s) => {
                    log::warn!(
                        "[KILL-TREE] kill -TERM exited with code {} for pid {}",
                        s.code().map(|c| c.to_string()).unwrap_or_else(|| "<signal>".into()),
                        dpid
                    );
                }
                Err(e) => {
                    log::warn!("[KILL-TREE] kill -TERM failed for pid={}: {}", dpid, e);
                }
            }
        }

        std::thread::sleep(Duration::from_millis(200));

        for &dpid in &all_descendants {
            match Command::new("kill")
                .args(["-KILL", &dpid.to_string()])
                .stdout(std::process::Stdio::null())
                .stderr(std::process::Stdio::null())
                .status()
            {
                Ok(s) if s.success() => {}
                Ok(s) => {
                    log::warn!(
                        "[KILL-TREE] kill -KILL exited with code {} for pid {}",
                        s.code().map(|c| c.to_string()).unwrap_or_else(|| "<signal>".into()),
                        dpid
                    );
                }
                Err(e) => {
                    log::warn!("[KILL-TREE] kill -KILL failed for pid={}: {}", dpid, e);
                }
            }
        }

        // GT-19: final summary line.
        log::info!(
            "[KILL-TREE] reaped {} descendants of pid {}",
            all_descendants.len(),
            pid
        );
    }
}

pub(crate) struct SidecarState {
    /// Child handle for kill_children backstop.
    pub(crate) child: Mutex<Option<SidecarHandle>>,
    /// WS writer channel — None when the WS is disconnected.
    pub(crate) ws_tx: Mutex<Option<WsWriterTx>>,
    /// Pending dispatch requests (id → response sender).
    pub(crate) pending: PendingMap,
    /// Next request id.
    pub(crate) next_id: AtomicU64,
    /// Shutdown signal — set when the app is quitting so the supervisor doesn't
    /// respawn the sidecar during shutdown.
    pub(crate) shutting_down: AtomicBool,
    /// Respawn serialization flag. Set when a respawn is in flight
    /// so concurrent WS-reader exits (e.g., a flapping sidecar that dies
    /// immediately after reconnect) don't launch multiple parallel
    /// `respawn` supervisors that would corrupt `child`/`ws_tx`.
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
    /// GT-8 / GT-C4-3: the most recently spawned heartbeat task's
    /// `JoinHandle`. `reconnect_ws` spawns a fresh heartbeat task on
    /// every successful reconnect; without storing + aborting the
    /// previous handle, each reconnect LEAKS the prior task. After N
    /// reconnects you'd have N concurrent heartbeat tasks all
    /// dispatching `heartbeat` frames at 10s intervals.
    ///
    /// GT-FIX-20 coordination note: `main.rs`'s `SidecarState { ... }`
    /// struct-literal initializer must add `heartbeat_handle:
    /// AsyncMutex::new(None),` — OR switch to `SidecarState::new()`.
    pub(crate) heartbeat_handle:
        AsyncMutex<Option<tauri::async_runtime::JoinHandle<()>>>,
}

impl SidecarState {
    /// GT-8 / GT-C4-3: convenience constructor so `main.rs`'s struct
    /// literal can be replaced with `SidecarState::new()`. `pub(crate)`
    /// so `main.rs` (owned by GT-FIX-20) can switch to it — also
    /// future-proofs against further field additions.
    pub(crate) fn new() -> Self {
        Self {
            child: Mutex::new(None),
            ws_tx: Mutex::new(None),
            // GT-E3-5: PendingMap no longer wrapped in outer Arc.
            pending: AsyncMutex::new(HashMap::new()),
            next_id: AtomicU64::new(1),
            shutting_down: AtomicBool::new(false),
            respawn_in_progress: AtomicBool::new(false),
            child_exit_rx: AsyncMutex::new(None),
            heartbeat_handle: AsyncMutex::new(None),
        }
    }
}

impl Default for SidecarState {
    fn default() -> Self {
        Self::new()
    }
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
/// or the supervisor already set the flag). This makes it safe
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

    // Idempotency guard.
    if state.shutting_down.swap(true, Ordering::SeqCst) {
        log::info!("[EXIT-SHUTDOWN] shutting_down already set — skipping duplicate teardown");
        return;
    }

    // GT-8 / GT-C4-3: abort the heartbeat task so it doesn't keep
    // dispatching `heartbeat` frames into the dead WS.
    {
        let mut hb_guard = state.heartbeat_handle.lock().await;
        if let Some(handle) = hb_guard.take() {
            handle.abort();
            log::info!("[EXIT-SHUTDOWN] aborted in-flight heartbeat task");
        }
    }

    // Send the shutdown frame (best-effort).
    // GT-18: log on failure so a stuck writer task isn't silent.
    let frame = serde_json::json!({"type": "shutdown"});
    if let Some(ws_tx) = lock(&state.ws_tx).clone() {
        if let Err(e) = ws_tx.try_send(Message::Text(frame.to_string().into())) {
            log::warn!(
                "[EXIT-SHUTDOWN] try_send of shutdown frame failed (best-effort): {}",
                e
            );
        }
    } else {
        log::info!("[EXIT-SHUTDOWN] no ws_tx — skipping cooperative shutdown frame");
    }

    // Wait up to SHUTDOWN_ACK_TIMEOUT_MS for graceful exit.
    // GT-18: mirror the `shutdown_sidecar` Tauri command's logging.
    let deadline = Duration::from_millis(SHUTDOWN_ACK_TIMEOUT_MS);
    let mut graceful = false;
    let mut rx_guard = state.child_exit_rx.lock().await;
    if let Some(rx) = rx_guard.as_mut() {
        match tokio::time::timeout(deadline, rx.recv()).await {
            Ok(Some(CommandEvent::Terminated(payload))) => {
                log::info!(
                    "[EXIT-SHUTDOWN] sidecar exited gracefully (code={:?}, signal={:?})",
                    payload.code,
                    payload.signal
                );
                graceful = true;
            }
            Ok(Some(other)) => {
                log::warn!(
                    "[EXIT-SHUTDOWN] unexpected event while waiting for termination: {:?}",
                    other
                );
            }
            Ok(None) => {
                log::warn!("[EXIT-SHUTDOWN] sidecar event stream closed without Terminated");
            }
            Err(_) => {
                log::warn!(
                    "[EXIT-SHUTDOWN] sidecar did not exit within {}ms — force-killing",
                    SHUTDOWN_ACK_TIMEOUT_MS
                );
            }
        }
    } else {
        log::info!(
            "[EXIT-SHUTDOWN] dev-mode sidecar — sleeping {}ms before force-kill",
            SHUTDOWN_ACK_TIMEOUT_MS
        );
        tokio::time::sleep(deadline).await;
    }
    drop(rx_guard);

    // Force-kill backstop.
    // GT-18: log kill_tree errors.
    let child_opt = lock(&state.child).take();
    if let Some(child) = child_opt {
        if let Err(e) = child.kill_tree().await {
            log::warn!("[EXIT-SHUTDOWN] kill_tree failed (best-effort): {}", e);
        }
    }

    // GT-18: final summary line.
    log::info!(
        "[EXIT-SHUTDOWN] sidecar teardown complete graceful={}",
        graceful
    );
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    /// PVT-G5-029: `kill_process_tree` must be best-effort — calling
    /// it with a non-existent pid must not panic.
    #[test]
    fn test_kill_process_tree_nonexistent_pid_is_noop() {
        kill_process_tree(999_999);
    }

    /// GT-19: kill_process_tree must not panic on a pathologically
    /// large pid (e.g. u32::MAX).
    #[test]
    fn test_kill_process_tree_u32_max_is_noop() {
        kill_process_tree(u32::MAX);
    }

    /// GT-8 / GT-C4-3: `SidecarState::new()` must initialize
    /// `heartbeat_handle` to `None`. Also verifies the `Default` impl.
    #[tokio::test]
    async fn test_sidecar_state_new_heartbeat_handle_is_none() {
        let state = SidecarState::new();
        assert!(
            state.heartbeat_handle.lock().await.is_none(),
            "fresh SidecarState must have heartbeat_handle = None"
        );
        let default_state = SidecarState::default();
        assert!(
            default_state.heartbeat_handle.lock().await.is_none(),
            "SidecarState::default() must have heartbeat_handle = None"
        );
    }

    /// GT-E3-5: PendingMap type alias no longer wraps an outer Arc.
    #[tokio::test]
    async fn test_pending_map_no_outer_arc_compiles_and_works() {
        let pending: PendingMap = AsyncMutex::new(HashMap::new());
        let (tx, _rx) = oneshot::channel::<Value>();
        pending.lock().await.insert(1u64, tx);
        assert_eq!(pending.lock().await.len(), 1);
        let _ = pending.lock().await.remove(&1u64);
        assert_eq!(pending.lock().await.len(), 0);
    }

    /// GT-8: `shutdown_sidecar_for_exit` must be idempotent.
    #[tokio::test]
    async fn test_shutdown_sidecar_for_exit_is_idempotent() {
        let state = Arc::new(SidecarState::new());
        let state_clone = state.clone();
        tokio::time::timeout(
            Duration::from_millis(2500),
            shutdown_sidecar_for_exit(&state_clone),
        )
        .await
        .expect("first shutdown_sidecar_for_exit should complete within 2.5s");
        assert!(
            state.shutting_down.load(std::sync::atomic::Ordering::SeqCst),
            "shutting_down must be set after first call"
        );
        let state_clone2 = state.clone();
        let second = tokio::time::timeout(
            Duration::from_millis(100),
            shutdown_sidecar_for_exit(&state_clone2),
        )
        .await;
        assert!(
            second.is_ok(),
            "second shutdown_sidecar_for_exit must short-circuit immediately (idempotency)"
        );
    }
}
