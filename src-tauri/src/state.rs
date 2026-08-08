//! Shared state types for the Voice Typer Tauri host (ADR-0020 §1 + §10).

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicU64};
use std::sync::{Arc, Mutex};
use tauri::Manager;
use tauri_plugin_shell::process::{CommandEvent, CommandChild};
use tokio::sync::{mpsc, oneshot, Mutex as AsyncMutex, Notify};
use tokio_tungstenite::tungstenite::Message;
use serde_json::Value;

//Poison-safe Mutex helper () ───────────────────────────────
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
//originally used the inline `.unwrap_or_else(|e| e.into_inner())`
// form in `logging.rs`; the merged version routes through this helper
// for consistency (no circular-dep concern — `state.rs` only uses the
// `log::warn!` macro, which expands to `log`-crate calls, not to
// `logging.rs` calls).
//
//( / ): the `#[allow(dead_code)]` that used to live
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
//removed the redundant outer `Arc`. `SidecarState` itself is
/// always shared via `Arc<SidecarState>`, so the inner `AsyncMutex` is
/// already shared — wrapping it in another `Arc` doubled the indirection
/// without any benefit. `AsyncMutex::lock` takes `&self`, so existing
/// call sites (`state.pending.lock().await`) compile unchanged. The
/// only cross-file impact is `main.rs`'s struct-literal initializer,
//which must drop the `Arc::new(...)` wrapper — see
pub(crate) type PendingMap = AsyncMutex<HashMap<u64, oneshot::Sender<Value>>>;

/// The WS writer half, wrapped in a channel so the dispatch command
/// (which runs on a Tauri async runtime) can send frames without
/// holding the WS writer directly.
///
//previously `mpsc::UnboundedSender<Message>`. Switched
/// to a bounded `mpsc::Sender<Message>` (capacity
/// `sidecar::ws::WS_WRITER_CHANNEL_CAPACITY`, currently 64 — see
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
    // Wraps `Option<CommandChild>` (not a bare `CommandChild`)
    // so the `Drop` impl can `take()` the child out of `&mut self`
    // for a best-effort kill on drop. `CommandChild::kill` consumes
    // `self` (no `&mut self` variant), so without the `Option` wrapper
    // the Drop impl would have no way to move the child out for the
    // kill call. The Option is always `Some(...)` at construction
    // (spawn.rs) and is set to `None` only by `kill()` / `kill_tree()`
    // / `Drop` — all of which consume or `&mut`-borrow the handle, so
    // no external caller can observe the `None` state.
    ShellPlugin(Option<CommandChild>),
    DevMode(tokio::process::Child),
}

impl SidecarHandle {
    /// Return the OS process id of the sidecar, if available. Used by
    /// `kill_tree` (ADR-0020 §10 — recursive "kill_children" backstop)
    /// to also reap grandchildren (native hotkey binary, model processes)
    /// that the Python sidecar does not reap on its own exit.
    ///
    //exposed as `pub(crate)` so the retry-loop tests in
    /// `supervisor.rs` can verify that `state.child` holds the NEW child (not
    /// a stale reference to the old one) after the take-kill-store
    /// pattern runs. Previously private; the visibility bump is the
    //minimal change needed to make the  fix testable without
    /// adding a Drop impl or a new public API.
    pub(crate) fn pid(&self) -> Option<u32> {
        match self {
            // `CommandChild::pid()` returns `u32` directly
            // (always Some once spawned); wrap in Option for the API
            // uniformity with `tokio::process::Child::id()` (which
            // returns None after the child has been reaped). When the
            // Option<CommandChild> has already been `take()`n
            // (post-kill), we return None — `kill_tree` then skips
            // the recursive walk (the process is already dead).
            SidecarHandle::ShellPlugin(c) => c.as_ref().map(|c| c.pid()),
            SidecarHandle::DevMode(c) => c.id(),
        }
    }

    /// Kill the sidecar process. Consumes `self` because
    /// `CommandChild::kill(self)` takes ownership (the shell-plugin
    /// child handle is single-use after kill). The dev-mode variant
    /// (`tokio::process::Child::kill(&mut self)`) only borrows but we
    /// consume the handle anyway for API uniformity.
    ///
    //the shell-plugin kill path preserves the original
    /// error as the `source()` of the returned `io::Error` (via
    /// `io::Error::new(io::ErrorKind::Other, e)`) so callers can
    /// inspect the underlying `tauri_plugin_shell::Error` if needed.
    /// The previous implementation flattened the error to a `format!`
    /// string, discarding the source variant.
    pub(crate) async fn kill(mut self) -> std::io::Result<()> {
        match &mut self {
            // `take()` the inner CommandChild so the subsequent
            // Drop on `self` (which runs after this async fn returns,
            // because `self` was consumed by value) sees `None` and is
            // a no-op — preventing a double-kill.
            SidecarHandle::ShellPlugin(c) => match c.take() {
                Some(child) => child.kill().map_err(|e| {
                    // Preserve the original shell-plugin error variant as
                    //the `source()` of the io::Error (). The
                    // Display impl of io::Error includes both the outer
                    // message and the source's Display, so log lines stay
                    // readable while still being inspectable via
                    // `err.source()` / `err.get_ref()`.
                    std::io::Error::new(
                        std::io::ErrorKind::Other,
                        format!("shell-plugin kill: {e}"),
                    )
                }),
                None => Ok(()),
            },
            SidecarHandle::DevMode(c) => c.kill().await,
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
    //on Unix this performs a cooperative SIGTERM
    /// first, waits a brief grace period, then escalates to SIGKILL for
    /// any survivors. This mirrors the cooperative-shutdown
    /// `SHUTDOWN_ACK_TIMEOUT_MS` pattern (give the sidecar a chance to
    /// release the mic / close IPC sockets before force-killing).
    ///
    //(session 2): wraps the synchronous `kill_process_tree`
    /// (which does `std::process::Command::status()` — a blocking
    /// syscall) in `spawn_blocking` so we don't stall a Tokio worker
    /// thread for the duration of the kill-walk. `taskkill /T` on a
    /// large tree or `pgrep` under load can take >1s.
    ///
    /// The deprecated `state::kill_process_tree` shim that used to
    /// forward to `crate::platform::process::kill_process_tree` has
    /// been removed; this method (and the four `spawn.rs` cleanup
    /// callers) now invoke the platform module path directly. The
    /// implementation (platform shell-out + recursive `pgrep -P` /
    /// `taskkill /T` walk) lives in `crate::platform::process`
    /// alongside the related `register_kill_on_parent_exit` helper.
    pub(crate) async fn kill_tree(self) -> std::io::Result<()> {
        if let Some(pid) = self.pid() {
            //spawn_blocking so the blocking
            // `std::process::Command::status()` calls inside
            // `kill_process_tree` don't stall a Tokio worker thread.
            let _ = tauri::async_runtime::spawn_blocking(move || {
                crate::platform::process::kill_process_tree(pid);
            })
            .await;
        }
        self.kill().await
    }

    /// Non-blocking "has the child exited?" probe for the dev-mode
    /// variant. Returns:
    /// - `Ok(Some(true))` — DevMode child has exited (reaped by the OS).
    /// - `Ok(Some(false))` — DevMode child still running.
    /// - `Ok(None)` — ShellPlugin variant has no `try_wait` equivalent
    ///   (the `CommandEvent` stream is the canonical exit signal);
    ///   callers should fall back to their deadline-based wait.
    /// - `Err(_)` — best-effort: an OS error from the underlying
    ///   `waitpid(WNOHANG)` syscall. Callers treat this the same as
    ///   `Ok(Some(false))` (don't short-circuit; let the deadline +
    ///   force-kill path handle it).
    ///
    /// Used by the dev-mode arm of `shutdown_sidecar_for_exit` to poll
    /// for graceful exit (cutting the teardown from 30s → ~100ms on a
    /// cooperative dev sidecar) instead of unconditionally sleeping the
    /// full `EXIT_SHUTDOWN_ACK_TIMEOUT_MS`.
    pub(crate) fn try_wait(&mut self) -> std::io::Result<Option<bool>> {
        match self {
            SidecarHandle::DevMode(c) => {
                let status = c.try_wait()?;
                Ok(Some(status.is_some()))
            }
            SidecarHandle::ShellPlugin(_) => Ok(None),
        }
    }
}

// Best-effort, fire-and-forget kill on drop. This is the
// SAFETY NET for code paths that forget to call `kill()` / `kill_tree()`
// explicitly (e.g. a panic between `state.child = Some(...)` and the
// eventual `take() + kill_tree()` on shutdown; or a supervisor-replaces-
// child path that drops the old handle without killing it).
//
// For `ShellPlugin`: takes the inner `CommandChild` and calls `kill()`
// on it. `CommandChild::kill` is a cheap synchronous call that sends
// the OS kill signal — safe to run inside Drop. We deliberately do NOT
// call `kill_process_tree` here (the recursive grandchild walk) because
// that walks `pgrep` / `taskkill /T` via blocking
// `std::process::Command::status()` syscalls that could stall a Tokio
// worker thread for >1s. The release-path spawn already registers
// `kill_on_parent_exit` at spawn time (see `spawn_sidecar_release`),
// which is the OS-level guarantee for orphan reaping — Drop's
// `child.kill()` is the redundant fallback for the in-process "I
// forgot to kill this handle" case.
//
// For `DevMode`: no-op. `tokio::process::Child` was constructed with
// `kill_on_drop(true)` in `spawn_sidecar_dev_mode`, so the inner
// `Child`'s own Drop kills the process. Calling `child.kill()` here
// would be a redundant kill signal (and `tokio::process::Child::kill`
// is async, which we can't await from a sync Drop).
//
// After `take()`, the inner Option is `None`, so a subsequent Drop on
// the same handle (impossible in safe Rust — Drop runs once) would be
// a no-op. The `kill()` / `kill_tree()` methods also `take()` the
// inner Option, so when they consume `self` and Drop runs on the
// consumed value, this Drop arm sees `None` and does nothing —
// preventing a double-kill.
impl Drop for SidecarHandle {
    fn drop(&mut self) {
        match self {
            SidecarHandle::ShellPlugin(c) => {
                if let Some(child) = c.take() {
                    log::info!(
                        "[STATE] Drop: killing shell-plugin sidecar child (best-effort, fire-and-forget)"
                    );
                    if let Err(e) = child.kill() {
                        log::warn!(
                            "[STATE] Drop: shell-plugin child.kill() failed (best-effort): {}",
                            e
                        );
                    }
                }
            }
            SidecarHandle::DevMode(_) => {
                // kill_on_drop(true) is set in spawn_sidecar_dev_mode —
                // tokio::process::Child's own Drop kills the process.
                // No-op here to avoid a redundant (and async, which we
                // can't await from sync Drop) kill signal.
            }
        }
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
    //Event receiver from the sidecar's `Command::spawn()`. Used by
    /// `shutdown_sidecar` to poll for `CommandEvent::Terminated` so the
    /// host exits the wait loop as soon as the sidecar acks and exits
    /// (~50ms typical), instead of blocking the full
    /// `SHUTDOWN_ACK_TIMEOUT_MS` (2000ms). Only set for the `ShellPlugin`
    /// variant (release builds); the `DevMode` variant spawns via
    /// `tokio::process::Command` which has no `CommandEvent` stream —
    /// `shutdown_sidecar` falls back to bounded sleep polling for that
    /// path.
    pub(crate) child_exit_rx: AsyncMutex<Option<mpsc::Receiver<CommandEvent>>>,
    //the most recently spawned heartbeat task's
    /// `JoinHandle`. `reconnect_ws` spawns a fresh heartbeat task on
    /// every successful reconnect; without storing + aborting the
    /// previous handle, each reconnect LEAKS the prior task. After N
    /// reconnects you'd have N concurrent heartbeat tasks all
    /// dispatching `heartbeat` frames at 10s intervals.
    ///
    //coordination note: `main.rs`'s `SidecarState { ... }`
    /// struct-literal initializer must add `heartbeat_handle:
    /// AsyncMutex::new(None),` — OR switch to `SidecarState::new()`.
    pub(crate) heartbeat_handle:
        AsyncMutex<Option<tauri::async_runtime::JoinHandle<()>>>,
    /// Monotonic generation counter bumped on every successful
    /// `queue_auth_and_store_ws_tx` (i.e. every time a fresh `ws_tx`
    /// channel is stored into `state.ws_tx`). The reader and writer
    /// cleanup blocks capture the generation at spawn time and, before
    /// clearing `state.ws_tx`, compare it against the current value: if
    /// they differ, a newer reconnect has already stored its own
    /// `ws_tx`, and the cleanup must NOT clobber it (the older task
    /// simply exits without touching shared state).
    ///
    /// The race this closes: the supervisor kills the old sidecar →
    /// the old WS reader's `read.next()` returns `None` → meanwhile
    /// `reconnect_ws` runs and stores a NEW `ws_tx` → the old reader's
    /// cleanup runs `*state.ws_tx = None`, destroying the new
    /// connection's sender. The next `dispatch` then fails with
    /// "sidecar not connected" even though the new sidecar is alive,
    /// forcing another respawn and creating a flap loop.
    ///
    /// `AtomicU64` (not `AtomicU32`) for headroom — even at 1000
    /// reconnects/sec the counter wouldn't wrap for ~584 million years.
    /// Initialized to 0; the first `queue_auth_and_store_ws_tx` bumps
    /// it to 1, so a never-connected state (gen=0) is distinguishable
    /// from any live generation (≥1).
    pub(crate) ws_generation: AtomicU64,
    /// Cancellation signal for the supervisor's backoff sleep.
    /// `shutdown_sidecar_for_exit` calls `notify_one()` immediately
    /// after the `shutting_down` swap so a respawn mid-backoff wakes
    /// up sub-ms (vs the prior 100ms polling loop). `Notify` stores a
    /// single permit, so a `notify_one()` fired BEFORE the supervisor
    /// starts awaiting `notified()` is consumed by the very next
    /// `notified()` call — no race window.
    pub(crate) shutdown_notify: Notify,
}

impl SidecarState {
    //convenience constructor so `main.rs`'s struct
    /// literal can be replaced with `SidecarState::new()`. `pub(crate)`
    //so `main.rs` (owned by ) can switch to it — also
    /// future-proofs against further field additions.
    pub(crate) fn new() -> Self {
        Self {
            child: Mutex::new(None),
            ws_tx: Mutex::new(None),
            //PendingMap no longer wrapped in outer Arc.
            pending: AsyncMutex::new(HashMap::new()),
            next_id: AtomicU64::new(1),
            shutting_down: AtomicBool::new(false),
            respawn_in_progress: AtomicBool::new(false),
            child_exit_rx: AsyncMutex::new(None),
            heartbeat_handle: AsyncMutex::new(None),
            // ws_generation starts at 0; first reconnect bumps to 1.
            ws_generation: AtomicU64::new(0),
            shutdown_notify: Notify::new(),
        }
    }
}

impl Default for SidecarState {
    fn default() -> Self {
        Self::new()
    }
}

//app-exit sidecar teardown ────────────────────────────────

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
/// 3. Wait up to `EXIT_SHUTDOWN_ACK_TIMEOUT_MS` (30s) for the sidecar
///    to exit gracefully (polling the `CommandEvent` receiver if
///    present; bounded sleep for dev-mode). The exit path uses the
///    longer 30s budget (vs the renderer-invoked `shutdown_sidecar`
///    command's 2s `SHUTDOWN_ACK_TIMEOUT_MS`) because the host is
///    already going away and the sidecar's audited worst-case
///    cooperative cleanup (history_db.flush, crash_recovery.flush,
///    native hotkey binary teardown, WAL checkpoint) can legitimately
///    take ~30s on a cold disk. Force-killing mid-cleanup risks WAL
///    corruption + native-binary orphan.
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
    use crate::util::EXIT_SHUTDOWN_ACK_TIMEOUT_MS;

    // Idempotency guard.
    if state.shutting_down.swap(true, Ordering::SeqCst) {
        log::info!("[EXIT-SHUTDOWN] shutting_down already set — skipping duplicate teardown");
        return;
    }
    // Wake any supervisor task that is mid-backoff-sleep. The select!
    // in `respawn_inner` is awaiting either the remaining backoff sleep
    // or this `notified()` future — firing it here cuts the wakeup
    // latency from ≤100ms (prior polling loop) to sub-ms.
    // `notify_one()` stores a single permit if no waiter is currently
    // registered, so this is safe even if the supervisor hasn't yet
    // entered the `select!` (the next `notified()` call will consume
    // the stored permit immediately).
    state.shutdown_notify.notify_one();

    //abort the heartbeat task so it doesn't keep
    // dispatching `heartbeat` frames into the dead WS.
    {
        let mut hb_guard = state.heartbeat_handle.lock().await;
        if let Some(handle) = hb_guard.take() {
            handle.abort();
            log::info!("[EXIT-SHUTDOWN] aborted in-flight heartbeat task");
        }
    }

    // Send the shutdown frame (best-effort).
    //log on failure so a stuck writer task isn't silent.
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

    // Wait up to EXIT_SHUTDOWN_ACK_TIMEOUT_MS (30s) for graceful exit.
    // The exit path uses a longer budget than the renderer-invoked
    // `shutdown_sidecar` command (2s) because the host is going away
    // and the sidecar's cleanup (WAL checkpoint, native hotkey binary
    // teardown) can legitimately take ~30s on a cold disk.
    //mirror the `shutdown_sidecar` Tauri command's logging.
    let deadline = Duration::from_millis(EXIT_SHUTDOWN_ACK_TIMEOUT_MS);
    let mut graceful = false;
    // Take the receiver OUT of the shared slot under a brief lock, then
    // DROP the lock guard before awaiting `rx.recv()`. Holding the
    // `AsyncMutex` guard across the up-to-30s `tokio::time::timeout`
    // await blocked any other code path that needed `child_exit_rx`
    // (e.g. a concurrent `Exit` + `ExitRequested` callback pair, or a
    // late renderer `shutdown_sidecar` command) for the entire grace
    // window — even though the idempotency guard already short-circuits
    // duplicate teardowns, the lock itself was still contended. This
    // path is the app's terminal exit, so leaving the slot `None` after
    // `take()` is fine (no later code needs the receiver back; the
    // process is going away).
    let rx_opt = {
        let mut rx_guard = state.child_exit_rx.lock().await;
        rx_guard.take()
    };
    if let Some(mut rx) = rx_opt {
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
                    EXIT_SHUTDOWN_ACK_TIMEOUT_MS
                );
            }
        }
    } else {
        log::info!(
            "[EXIT-SHUTDOWN] dev-mode sidecar — polling for exit (up to {}ms, 100ms interval) before force-kill",
            EXIT_SHUTDOWN_ACK_TIMEOUT_MS
        );
        // Poll `SidecarHandle::try_wait()` in a bounded loop with a
        // 100ms sleep, breaking early when the dev-mode child has been
        // reaped by the OS. Cuts the dev-mode teardown from 30s →
        // ~100ms on a cooperative dev sidecar (the typical case: the
        // `{"type":"shutdown"}` frame is delivered, the dev sidecar
        // exits within milliseconds, and the next poll observes the
        // reaped pid). The sync `Mutex` lock on `state.child` is held
        // only for the duration of the `try_wait()` syscall
        // (microseconds — `waitpid(WNOHANG)` is a non-blocking kernel
        // call), never across the `tokio::time::sleep` await — so
        // other code paths needing `state.child` (e.g. supervisor
        // respawn, which takes + kills the slot) are not blocked.
        // On ShellPlugin (release builds) this arm is unreachable:
        // `child_exit_rx` is always `Some` for ShellPlugin, so the
        // `if let Some(mut rx) = rx_opt` branch above is taken
        // instead. `try_wait()` returns `Ok(None)` for ShellPlugin
        // anyway, so the poll loop would just wait out the deadline
        // (matching the prior behavior of `tokio::time::sleep(deadline)`).
        let poll_step = Duration::from_millis(100);
        let poll_deadline = tokio::time::Instant::now() + deadline;
        loop {
            let reaped = {
                let mut guard = lock(&state.child);
                match guard.as_mut() {
                    Some(handle) => match handle.try_wait() {
                        Ok(Some(exited)) => exited,
                        Ok(None) => false, // ShellPlugin — no poll, wait for deadline
                        Err(_) => false,   // best-effort, don't fail the shutdown
                    },
                    None => true, // No child — already gone
                }
            };
            if reaped {
                graceful = true;
                log::info!(
                    "[EXIT-SHUTDOWN] dev-mode sidecar exited gracefully (reaped within {}ms budget)",
                    EXIT_SHUTDOWN_ACK_TIMEOUT_MS
                );
                break;
            }
            if tokio::time::Instant::now() >= poll_deadline {
                break;
            }
            let now = tokio::time::Instant::now();
            let remaining = if now < poll_deadline {
                poll_deadline - now
            } else {
                Duration::ZERO
            };
            tokio::time::sleep(std::cmp::min(remaining, poll_step)).await;
        }
    }

    // Force-kill backstop. Gate on `!graceful` — if the sidecar exited
    // cooperatively, the grandchildren (native hotkey binary, model
    // subprocesses) were already reaped by the sidecar itself; we still
    // `take()` the child handle (dropping it cleanly) but skip the
    // recursive `kill_tree`. If `graceful` is false (timeout /
    // unexpected exit), `kill_tree` is the force-kill backstop that
    // also walks the process tree to reap grandchildren the sidecar
    // didn't clean up.
    let child_opt = lock(&state.child).take();
    if let Some(child) = child_opt {
        if !graceful {
            if let Err(e) = child.kill_tree().await {
                log::warn!("[EXIT-SHUTDOWN] kill_tree failed (best-effort): {}", e);
            }
        }
    }

    //final summary line.
    log::info!(
        "[EXIT-SHUTDOWN] sidecar teardown complete graceful={}",
        graceful
    );
}

//Host-entrypoint callbacks (extracted from main.rs, C-) ──────

/// Local override for the host's `RunEvent::Exit` shutdown budget.
///
/// `util::SHUTDOWN_ACK_TIMEOUT_MS` is 2000ms (2s) — but the sidecar's
/// graceful shutdown path can legitimately take 3-4s on a cold disk
/// (WAL checkpoint, native hotkey binary teardown). The 2s budget
/// force-kills the sidecar mid-flush, which can corrupt `history.db`
/// and leak the native hotkey binary child.
///
/// This local constant is the HARD ceiling on the exit-path teardown.
/// It MUST be >= `EXIT_SHUTDOWN_ACK_TIMEOUT_MS` (30s) so that the
/// cooperative shutdown wait inside `shutdown_sidecar_for_exit` is
/// never cut short by the outer `tokio::time::timeout` in
/// `on_host_exit`. The 5s headroom (30s + 5s) covers the
/// force-kill + zombie-reap phase that runs after the cooperative
/// wait expires.
///
/// The renderer-invoked `shutdown_sidecar` command keeps the tighter
/// 2s budget (`SHUTDOWN_ACK_TIMEOUT_MS`) — there a tight budget is
/// appropriate because the UI is still alive and a long block freezes
/// it. The `RunEvent::Exit` path is when the host is going away — it
/// should err on the side of giving the sidecar more time.
const HOST_SHUTDOWN_GRACE_MS: u64 = 35_000;

/// `relaunch_app` Tauri event listener body, extracted from
/// `main.rs`'s inline closure so the host entrypoint stays wiring-only
//(C-).
///
/// Sends a fire-and-forget `relaunch_ack` WS frame back to the Python
/// sidecar (so its `_wait_for_relaunch_ack` short-circuits cleanly
/// instead of blocking for the full 2s timeout), then schedules a
/// delayed `app.restart()` on the async runtime. The 10ms delay (sourced
/// from `util::PRE_RESTART_FLUSH_DELAY_MS`) gives the WS writer task
/// time to flush the ack frame to the socket before `app.restart()`
/// tears down the process.
///
/// The delay is spawned on the async runtime (NOT `tokio::time::sleep`
/// on the event-loop thread) so the Tauri event loop is not blocked.
pub(crate) fn on_relaunch_app(app_handle: &tauri::AppHandle, _event: tauri::Event) {
    use crate::util::PRE_RESTART_FLUSH_DELAY_MS;

    log::info!(
        "[RESTART] relaunch_app event received — sending relaunch_ack + calling app.restart()"
    );
    // Best-effort relaunch_ack: extracted to `send_fire_and_forget_frame`
    // so this function doesn't reference raw WS protocol / frame
    // serialization.
    let state: tauri::State<'_, Arc<SidecarState>> = app_handle.state();
    let state_inner = state.inner().clone();
    let ack_sent = send_fire_and_forget_frame(&state_inner, "relaunch_ack");
    if ack_sent.is_none() {
        log::warn!(
            "[RESTART] ws_tx is None — cannot send relaunch_ack; Python will wait 2s timeout"
        );
    }
    // Brief yield to let the WS writer task flush the ack frame before
    // app.restart() tears down the process. `try_send` enqueues on the
    // bounded channel instantly; the writer task (tokio::spawn'd in
    // `reconnect_ws`) needs only a few microseconds to send it on the
    // socket. 10ms is generous; even on a loaded host the writer task
    // schedules within 1ms.
    let restart_for_async = app_handle.clone();
    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(std::time::Duration::from_millis(PRE_RESTART_FLUSH_DELAY_MS)).await;
        log::info!("[RESTART] calling app.restart()");
        restart_for_async.restart();
    });
}

/// `RunEvent::Exit` / `ExitRequested` callback body, extracted from
/// `main.rs`'s inline `.run(callback)` closure so the host entrypoint
//stays wiring-only (C-).
///
/// Spawns the sidecar teardown on a dedicated std thread (NOT a tokio
/// task) so the Tauri event loop returns immediately — `block_on` can
/// block for up to ~35s on dev-mode shutdowns (the dev-mode sidecar
/// has no `CommandEvent` stream, so `shutdown_sidecar_for_exit`
/// always sleeps the full `EXIT_SHUTDOWN_ACK_TIMEOUT_MS`=30s). The
/// user would otherwise see a non-responsive window / lingering Dock
/// icon during the sleep. The process tears down naturally once the
/// spawned thread completes (Tauri keeps the runtime alive until all
/// spawned tasks / threads resolve on exit paths).
///
/// The teardown is wrapped in `tokio::time::timeout(HOST_SHUTDOWN_GRACE_MS + 1000)`
/// so the run loop never hangs on a misbehaving sidecar.
pub(crate) fn on_host_exit(app_handle: &tauri::AppHandle) {
    use std::time::Duration;

    let sidecar_state = app_handle
        .state::<Arc<SidecarState>>()
        .inner()
        .clone();
    std::thread::spawn(move || {
        tauri::async_runtime::block_on(async move {
            let _ = tokio::time::timeout(
                Duration::from_millis(HOST_SHUTDOWN_GRACE_MS + 1000),
                shutdown_sidecar_for_exit(&sidecar_state),
            )
            .await;
        });
    });
}

//ADR-0020 module-layout gate: fire-and-forget WS frame send
/// used by ``main.rs``'s ``relaunch_app`` listener. Extracted from the
/// host entrypoint so ``main.rs`` stays wiring-only (no direct
/// ``tungstenite::`` reference — see the
/// ``test_main_rs_has_no_business_logic_patterns`` gate in
/// ``tests/tauri/mig19/test_final_glue.py``).
///
/// Assigns the next monotonic id, builds a ``{"type":<frame_type>,
/// "data":{}, "id":<id>}`` frame, and enqueues it on the WS writer
/// channel via ``try_send`` (non-blocking).
///
//# Return value semantics ()
///
/// The returned `Option<u64>` is for **tracing only** — it does NOT
/// indicate whether the frame was successfully delivered to the WS
/// writer task. Specifically:
///
/// - `None`: there is no `ws_tx` (the WS was already torn down — the
///   caller should treat this as "no sidecar connected").
/// - `Some(id)`: an id was assigned for the frame. **The frame may or
///   may not have been enqueued.** If `try_send` failed (e.g. WS
///   channel full or closed), the failure is logged at `warn` level
///   and `Some(id)` is still returned — the id is purely a tracing
///   artifact and the peer will time out waiting for a response.
///
/// Callers that need to know whether the frame was actually sent
/// should use the full `dispatch_frame` path (which returns a
/// `Result`). This helper exists for fire-and-forget frames where the
/// caller cannot react to a send failure anyway (e.g. `relaunch_app`
/// — the process is about to exit regardless).
pub(crate) fn send_fire_and_forget_frame(
    state: &Arc<SidecarState>,
    frame_type: &str,
) -> Option<u64> {
    use std::sync::atomic::Ordering;
    let ws_tx = lock(&state.ws_tx).clone()?;
    let id = state.next_id.fetch_add(1, Ordering::Relaxed);
    let frame = serde_json::json!({
        "type": frame_type,
        "data": {},
        "id": id,
    });
    match ws_tx.try_send(Message::Text(frame.to_string().into())) {
        Ok(_) => log::info!("[WS] {} frame sent (id={})", frame_type, id),
        Err(e) => log::warn!(
            "[WS] failed to send {} frame (id={}): {} — peer will wait for its timeout",
            frame_type,
            id,
            e
        ),
    }
    Some(id)
}
