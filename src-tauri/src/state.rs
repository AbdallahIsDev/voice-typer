//! Shared state types for the Tauri host (ADR-0020 §1 + §10).

use serde_json::Value;
use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicU64};
use std::sync::{Mutex, OnceLock};
use tauri_plugin_shell::process::CommandEvent;
use tokio::sync::{mpsc, oneshot, Mutex as AsyncMutex, Notify};
use tokio_tungstenite::tungstenite::Message;

// Re-exports so historical `crate::state::*` imports keep resolving
// (create-first split, AGENTS.md E1).
pub(crate) use crate::sidecar::SidecarHandle;
// Test-only: non-test builds have no `crate::state::shutdown_sidecar_for_exit` call sites.
#[cfg(test)]
pub(crate) use crate::sidecar::shutdown_sidecar_for_exit;

pub(crate) use crate::sidecar::lifecycle::{on_host_exit, on_quit_app, on_relaunch_app};

// ─── Poison-safe Mutex helper ───────────────────────────────
//
// `Mutex::lock().unwrap()` re-panics forever once poisoned, which would
// brick the resilience layer. Recover via `into_inner()`: a half-written
// slot reads as `None` (treated as "not connected"), safer than panicking.

/// Recover a poisoned Mutex instead of panicking.
pub(crate) fn lock<T>(m: &std::sync::Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

// ─── Shared state ─────────────────────────────────────────────────────

/// Pending dispatch requests (id → oneshot sender fulfilled by the WS reader).
pub(crate) type PendingMap = AsyncMutex<HashMap<u64, oneshot::Sender<Value>>>;

/// Bounded WS writer channel so a runaway renderer cannot OOM the host.
/// Callers must use `try_send` and handle `TrySendError::{Full,Closed}`.
/// Capacity: `sidecar::ws::WS_WRITER_CHANNEL_CAPACITY`.
pub(crate) type WsWriterTx = mpsc::Sender<Message>;

pub(crate) struct SidecarState {
    /// Child handle for kill_children backstop.
    pub(crate) child: Mutex<Option<SidecarHandle>>,
    /// WS writer channel: None when disconnected.
    pub(crate) ws_tx: Mutex<Option<WsWriterTx>>,
    /// Pending dispatch requests.
    pub(crate) pending: PendingMap,
    /// Next request id.
    pub(crate) next_id: AtomicU64,
    /// Set when quitting so the supervisor doesn't respawn mid-shutdown.
    pub(crate) shutting_down: AtomicBool,
    /// Tray created successfully? Close handler hides-to-tray only when
    /// true; otherwise a hidden last window would strand the user.
    pub(crate) tray_available: AtomicBool,
    /// Respawn serialization: concurrent WS-reader exits must not launch
    /// parallel `respawn` supervisors that corrupt `child`/`ws_tx`.
    pub(crate) respawn_in_progress: AtomicBool,
    /// Sidecar `CommandEvent` stream so `shutdown_sidecar` can exit as
    /// soon as the child terminates. Only set for ShellPlugin (release);
    /// DevMode falls back to bounded sleep polling.
    pub(crate) child_exit_rx: AsyncMutex<Option<mpsc::Receiver<CommandEvent>>>,
    /// Latest heartbeat task handle; without abort, each reconnect leaks
    /// another 10s heartbeat task.
    pub(crate) heartbeat_handle: AsyncMutex<Option<tauri::async_runtime::JoinHandle<()>>>,
    /// Generation counter bumped on every successful `ws_tx` store.
    /// Reader/writer cleanup compares it so an old cleanup cannot clobber
    /// a newer reconnect's sender (would force a flap loop).
    /// `AtomicU64` for headroom; starts at 0 (never-connected).
    pub(crate) ws_generation: AtomicU64,
    /// Backoff-cancel signal for the supervisor. `notify_one()` fires
    /// immediately after `shutting_down` is set; `Notify` stores one
    /// permit so a pre-await notify is not lost.
    pub(crate) shutdown_notify: Notify,
    /// Locale pushed by the main-window renderer via `set_host_locale`
    /// (native dialog titles). `None` until the first push.
    pub(crate) host_locale: Mutex<Option<String>>,
    /// Attached to an already-running backend launched BY the backend
    /// (`VT_PYTHON_PORT` + `VT_IPC_TOKEN`). `state.child` stays None;
    /// supervisor MUST NOT respawn (would create a second backend).
    pub(crate) adopted_backend: AsyncMutex<bool>,
    /// OS suspend flag. Supervisor stands down while suspended so a
    /// mid-sleep sidecar crash does not spawn into a frozen process.
    pub(crate) power_suspended: AtomicBool,
}

impl SidecarState {
    /// Preferred constructor (avoids field-addition drift vs a struct literal).
    pub(crate) fn new() -> Self {
        Self {
            child: Mutex::new(None),
            ws_tx: Mutex::new(None),
            pending: AsyncMutex::new(HashMap::new()),
            next_id: AtomicU64::new(1),
            shutting_down: AtomicBool::new(false),
            respawn_in_progress: AtomicBool::new(false),
            tray_available: AtomicBool::new(false),
            child_exit_rx: AsyncMutex::new(None),
            heartbeat_handle: AsyncMutex::new(None),
            // 0 = never-connected; first reconnect bumps to 1.
            ws_generation: AtomicU64::new(0),
            shutdown_notify: Notify::new(),
            host_locale: Mutex::new(None),
            adopted_backend: AsyncMutex::new(false),
            power_suspended: AtomicBool::new(false),
        }
    }

    /// Mark shutting-down and wake the supervisor's backoff wait.
    /// ALWAYS: `shutting_down.swap(true)` then `notify_one()` back-to-back
    /// — never reorder or separate with I/O (lost wakeup = full backoff
    /// sleep before teardown). Idempotent: returns whether already set.
    pub(crate) fn begin_shutdown(&self) -> bool {
        use std::sync::atomic::Ordering;
        let already_shutting_down = self.shutting_down.swap(true, Ordering::SeqCst);
        self.shutdown_notify.notify_one();
        already_shutting_down
    }

    /// Called from `main.rs` `.setup` when tray creation succeeds.
    pub(crate) fn mark_tray_available(&self) {
        use std::sync::atomic::Ordering;
        self.tray_available.store(true, Ordering::SeqCst);
    }
}

impl Default for SidecarState {
    fn default() -> Self {
        Self::new()
    }
}

// ─── WorkerState (runtime-pack split) ────────────────────────────────
//
// ML worker exe is a SECOND child with its own state so worker respawn
// never trips the sidecar circuit breaker (and vice versa). The slim-core
// sidecar — not the host — is the worker's WS client.
// see docs/code-notes/tauri-host.md#workerstate-runtime-pack-split

/// Worker spawn state, parallel to [`SidecarState`].
pub(crate) struct WorkerState {
    /// Child handle (same enum as sidecar).
    pub(crate) child: Mutex<Option<SidecarHandle>>,
    /// Writer half of the sidecar→worker WS.
    #[allow(dead_code)] // wired when the worker WS bridge is managed (Phase 2c)
    pub(crate) ws_tx: Mutex<Option<WsWriterTx>>,
    /// Pending worker RPC (separate from sidecar so neither blocks the other).
    #[allow(dead_code)] // wired when the worker RPC dispatcher is managed (Phase 2c)
    pub(crate) pending: PendingMap,
    /// Next worker RPC id (independent of sidecar ids).
    #[allow(dead_code)] // wired when the worker RPC dispatcher is managed (Phase 2c)
    pub(crate) next_id: AtomicU64,
    /// Worker shutdown signal (separate from sidecar's).
    pub(crate) shutting_down: AtomicBool,
    /// Worker respawn serialization (same contract as sidecar).
    pub(crate) respawn_in_progress: AtomicBool,
    /// Worker `CommandEvent` stream for terminate-polling.
    pub(crate) child_exit_rx: AsyncMutex<Option<mpsc::Receiver<CommandEvent>>>,
    /// Latest worker heartbeat handle (abort to avoid leak on reconnect).
    #[allow(dead_code)] // wired when the worker supervisor is managed (Phase 2c)
    pub(crate) heartbeat_handle: AsyncMutex<Option<tauri::async_runtime::JoinHandle<()>>>,
    /// Worker WS generation counter (mirrors sidecar).
    #[allow(dead_code)] // wired when the worker supervisor is managed (Phase 2c)
    pub(crate) ws_generation: AtomicU64,
    /// Worker supervisor backoff-cancel signal.
    #[allow(dead_code)] // wired when the worker supervisor is managed (Phase 2c)
    pub(crate) shutdown_notify: Notify,
    /// Per-launch bearer token (`VOICE_TYPER_WORKER_TOKEN`). Generated
    /// once per host launch so the slim-core sidecar can re-auth to a
    /// respawned worker without re-negotiating.
    pub(crate) auth_token: OnceLock<String>,
    /// Single-instance lock path; stale lock detected via PID check.
    pub(crate) lock_file_path: OnceLock<std::path::PathBuf>,
}

impl WorkerState {
    /// Mirrors `SidecarState::new()`. OnceLocks start empty.
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
