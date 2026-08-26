//! Shared state types for the Voice Typer Tauri host (ADR-0020 §1 + §10).

use serde_json::Value;
use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicU64};
use std::sync::{Arc, Mutex, OnceLock};
use tauri::Manager;
use tauri_plugin_shell::process::CommandEvent;
use tokio::sync::{mpsc, oneshot, Mutex as AsyncMutex, Notify};
use tokio_tungstenite::tungstenite::Message;

// Process-management + shutdown-machinery split:
// `SidecarHandle` (child-process enum + Drop safety net) and the
// shutdown / fire-and-forget-frame helpers moved out of this module
// into `crate::sidecar::{handle, shutdown}`. The re-exports below keep
// existing `crate::state::SidecarHandle` /
// `crate::state::shutdown_sidecar_for_exit` /
// `crate::state::send_fire_and_forget_frame` imports resolving
// (create-first split — see AGENTS.md E1).
pub(crate) use crate::sidecar::{
    send_fire_and_forget_frame, shutdown_sidecar_for_exit, SidecarHandle,
};

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
    /// Whether the host system tray was successfully created
    /// (`tray::create_tray` in `main.rs`'s setup). The main-window
    /// close handler only hides-to-tray when this is `true` — on a
    /// desktop where the tray could not be created (e.g. Linux Wayland
    /// without StatusNotifierItem support), hiding the last window
    /// would strand the user with no tray icon, no Dock entry, and no
    /// second-instance path to bring the window back (Electron's
    /// close handler guards the same case with
    /// `isLinuxWaylandWithoutSni()`). When `false`, the close flows
    /// through to a real close → last-window-close → app exit.
    pub(crate) tray_available: AtomicBool,
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
    pub(crate) heartbeat_handle: AsyncMutex<Option<tauri::async_runtime::JoinHandle<()>>>,
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
    /// Locale pushed by the main-window renderer via the
    /// `set_host_locale` command (`window.window_.setLocale(locale)`
    /// in the Tauri bridge). Mirrors Electron's `i18n:set-locale`
    /// main-process storage, where the pushed locale localizes native
    /// dialogs (single-instance error, critical-error dialog,
    /// model-folder picker, export save-as dialogs). The host may
    /// later consume this value to localize its own native surfaces;
    /// today it is the parity sink — the renderer's locale push
    /// resolves with the same `{ok: bool}` envelope on both runtimes
    /// instead of being rejected under Tauri. `None` until the first
    /// push arrives.
    pub(crate) host_locale: Mutex<Option<String>>,
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
            tray_available: AtomicBool::new(false),
            child_exit_rx: AsyncMutex::new(None),
            heartbeat_handle: AsyncMutex::new(None),
            // ws_generation starts at 0; first reconnect bumps to 1.
            ws_generation: AtomicU64::new(0),
            shutdown_notify: Notify::new(),
            // Renderer-pushed locale (parity sink for the
            // `window_.setLocale` bridge method); None until pushed.
            host_locale: Mutex::new(None),
        }
    }
}

impl Default for SidecarState {
    fn default() -> Self {
        Self::new()
    }
}

// ─── WorkerState (Phase 2a — runtime-pack split, §7) ───────────────────
//
// The runtime-pack split adds a SECOND spawned child to the host: the
// ML worker exe (`voice-typer-worker-<triple>[.exe]`). The slim-core
// sidecar talks to the worker over a SECOND websocket connection as a
// CLIENT (a new 1-host↔2-processes pattern, NOT "the same bridge" —
// see plan-runtime-pack-split §7.1). WorkerState is the parallel of
// SidecarState for the worker child — a separate struct (NOT an
// extension of SidecarState) so the two children's lifecycles stay
// independent: the worker's respawn scheduler must NOT trip the
// sidecar's circuit breaker (§7.2), and the sidecar's WS disconnect
// must NOT tear down the worker.
//
// WorkerState mirrors the SidecarState field set (child, ws_tx,
// pending, next_id, shutting_down, respawn_in_progress,
// child_exit_rx, heartbeat_handle, ws_generation, shutdown_notify)
// plus worker-specific additions:
//   - `auth_token: OnceLock<String>` — the per-launch bearer token
//     passed to the worker via the `VOICE_TYPER_WORKER_TOKEN` env var
//     (parallel to the sidecar's `VOICE_TYPER_IPC_TOKEN`; same
//     hmac.compare_digest pattern on the Python side — see
//     `voice_typer/server/ipc/auth.py:61-70`). Stored in a `OnceLock`
//     because the token is generated ONCE per host launch (NOT
//     regenerated per worker respawn — the worker inherits the
//     host's token so the slim-core sidecar can authenticate to a
//     respawned worker without re-negotiating).
//   - `lock_file_path: OnceLock<PathBuf>` — single-instance lock
//     file (parallel to `VoiceTyperSingleInstance`) preventing
//     parallel worker spawns across host instances.
//
// The `tray_available` field from SidecarState is NOT mirrored: the
// worker has no UI / tray concern.

/// Worker spawn state — parallel to [`SidecarState`] for the ML worker
/// exe (Phase 2a, plan-runtime-pack-split §7). See the module-level
/// comment above for the architectural rationale.
pub(crate) struct WorkerState {
    /// Child handle for kill_children backstop. Same enum as
    /// `SidecarState::child` (the worker is also spawned via Tauri's
    /// `externalBin` mechanism in release builds, or via
    /// `tokio::process::Command` in dev mode).
    pub(crate) child: Mutex<Option<SidecarHandle>>,
    /// WS writer channel to the worker — `None` when the WS is
    /// disconnected. The slim-core sidecar (NOT the Tauri host) is the
    /// WS client of the worker; this channel is the writer half of
    /// that connection.
    #[allow(dead_code)] // wired when the worker WS bridge is managed (Phase 2c)
    pub(crate) ws_tx: Mutex<Option<WsWriterTx>>,
    /// Pending RPC requests to the worker (id → response sender).
    /// Separate from `SidecarState::pending` so a slow worker response
    /// never blocks a sidecar response (and vice versa).
    #[allow(dead_code)] // wired when the worker RPC dispatcher is managed (Phase 2c)
    pub(crate) pending: PendingMap,
    /// Next worker RPC request id. Independent counter from
    /// `SidecarState::next_id` so worker + sidecar request ids don't
    /// collide on the host's log correlation.
    #[allow(dead_code)] // wired when the worker RPC dispatcher is managed (Phase 2c)
    pub(crate) next_id: AtomicU64,
    /// Worker shutdown signal — set when the host is quitting so the
    /// worker respawn scheduler doesn't restart the worker during
    /// host teardown. SEPARATE from `SidecarState::shutting_down` so
    /// the worker's lifecycle is independent (§7.2).
    pub(crate) shutting_down: AtomicBool,
    /// Worker respawn serialization flag — same contract as
    /// `SidecarState::respawn_in_progress` but for the worker
    /// supervisor. Acquired with `compare_exchange(false → true)` on
    /// entry; cleared on exit (both Ok and restart paths).
    #[allow(dead_code)] // wired when the worker supervisor is managed (Phase 2c)
    pub(crate) respawn_in_progress: AtomicBool,
    /// Event receiver from the worker's `Command::spawn()`. Used by
    /// `shutdown_worker_for_exit` (TBD, parallel to
    /// `shutdown_sidecar_for_exit`) to poll for `CommandEvent::Terminated`.
    pub(crate) child_exit_rx: AsyncMutex<Option<mpsc::Receiver<CommandEvent>>>,
    /// Most recently spawned worker heartbeat task's `JoinHandle`.
    /// Mirrors `SidecarState::heartbeat_handle` — without storing +
    /// aborting the previous handle, each reconnect LEAKS the prior
    /// task.
    #[allow(dead_code)] // wired when the worker supervisor is managed (Phase 2c)
    pub(crate) heartbeat_handle: AsyncMutex<Option<tauri::async_runtime::JoinHandle<()>>>,
    /// Monotonic generation counter bumped on every successful worker
    /// WS reconnect. Mirrors `SidecarState::ws_generation`.
    #[allow(dead_code)] // wired when the worker supervisor is managed (Phase 2c)
    pub(crate) ws_generation: AtomicU64,
    /// Cancellation signal for the worker supervisor's backoff sleep.
    /// Mirrors `SidecarState::shutdown_notify`.
    #[allow(dead_code)] // wired when the worker supervisor is managed (Phase 2c)
    pub(crate) shutdown_notify: Notify,
    /// Per-launch bearer token passed to the worker via the
    /// `VOICE_TYPER_WORKER_TOKEN` env var. Generated ONCE per host
    /// launch (NOT regenerated per worker respawn — the worker
    /// inherits the host's token so the slim-core sidecar can
    /// authenticate to a respawned worker without re-negotiating).
    /// Auth pattern: the worker validates incoming WS frames by
    /// comparing the bearer token via `hmac.compare_digest` on the
    /// Python side (see `voice_typer/server/ipc/auth.py:61-70`).
    pub(crate) auth_token: OnceLock<String>,
    /// Single-instance lock file path (parallel to
    /// `VoiceTyperSingleInstance`). The worker takes this lock at
    /// spawn time + releases it on exit; a stale lock is detected
    /// via PID check.
    pub(crate) lock_file_path: OnceLock<std::path::PathBuf>,
}

impl WorkerState {
    /// Convenience constructor mirroring `SidecarState::new()`. The
    /// `auth_token` and `lock_file_path` `OnceLock`s start empty —
    /// they're populated lazily on first worker spawn.
    pub(crate) fn new() -> Self {
        Self {
            child: Mutex::new(None),
            ws_tx: Mutex::new(None),
            pending: AsyncMutex::new(HashMap::new()),
            next_id: AtomicU64::new(1),
            shutting_down: AtomicBool::new(false),
            respawn_in_progress: AtomicBool::new(false),
            child_exit_rx: AsyncMutex::new(None),
            heartbeat_handle: AsyncMutex::new(None),
            ws_generation: AtomicU64::new(0),
            shutdown_notify: Notify::new(),
            auth_token: OnceLock::new(),
            lock_file_path: OnceLock::new(),
        }
    }
}

impl Default for WorkerState {
    fn default() -> Self {
        Self::new()
    }
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
    ); // Best-effort relaunch_ack: extracted to `send_fire_and_forget_frame`
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

/// `quit_app` Tauri event listener body (mirror of `on_relaunch_app`),
/// wired in `main.rs`'s `.setup` next to the `relaunch_app` listener.
///
/// The Python sidecar publishes `quit_app` when the user picks the tray
/// "Quit" item (see `voice_typer/server/app_lifecycle.py::quit_app` —
/// it pushes the event, then runs its own cleanup and exits).
/// Electron's main process handles the same event by calling
/// `app.quit()` (`client/src/main/python/handle-message.ts`); the Tauri
/// host has no main process, so this listener is the equivalent:
///
/// 1. Set `shutting_down` IMMEDIATELY so the WS-reader cleanup (which
///    fires when the sidecar exits moments later) does NOT trigger a
///    supervisor respawn. Without this flag, tray Quit would just
///    restart the backend instead of quitting the app.
/// 2. Call `app.exit(0)` so the host process terminates. The
///    `RunEvent::Exit` / `ExitRequested` callback (`on_host_exit` →
///    `shutdown_sidecar_for_exit`) then runs the sidecar teardown. That
///    teardown is idempotent — it short-circuits on the already-set
///    `shutting_down` flag; the sidecar exits itself as part of its own
///    quit path, and `SidecarHandle::Drop` is the best-effort kill
///    backstop for the (rare) hung-cleanup case.
///
/// The listener registration lives in `main.rs`'s `.setup`
/// (wiring-only, C-ARCH-1); this function is the body.
pub(crate) fn on_quit_app(app_handle: &tauri::AppHandle) {
    use std::sync::atomic::Ordering;

    let sidecar_state = app_handle.state::<Arc<SidecarState>>().inner().clone();
    if sidecar_state.shutting_down.swap(true, Ordering::SeqCst) {
        log::info!("[QUIT] quit_app event received — shutdown already in progress; exiting host");
    } else {
        log::info!(
            "[QUIT] quit_app event received — setting shutting_down + exiting host (tray Quit → app.exit)"
        );
    }
    // Best-effort: wake a supervisor that may be mid-backoff-sleep so it
    // observes `shutting_down` immediately instead of after its next
    // 100ms poll. `shutdown_sidecar_for_exit` also fires this notify on
    // the `RunEvent::Exit` path — redundant but harmless.
    sidecar_state.shutdown_notify.notify_one();
    app_handle.exit(0);
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

    let sidecar_state = app_handle.state::<Arc<SidecarState>>().inner().clone();
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
