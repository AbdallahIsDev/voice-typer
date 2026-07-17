//! Shared state types for the Voice Typer Tauri host (ADR-0020 §1 + §10).

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicU64};
use std::sync::{Arc, Mutex};
use tauri_plugin_shell::process::{CommandEvent, CommandChild};
use tokio::sync::{mpsc, oneshot, Mutex as AsyncMutex};
use tokio_tungstenite::tungstenite::Message;
use serde_json::Value;

// ─── Shared state ─────────────────────────────────────────────────────

/// Pending dispatch requests keyed by id. Each entry has a oneshot
/// sender that the WS reader task fulfills when the matching response
/// arrives.
pub(crate) type PendingMap = Arc<AsyncMutex<HashMap<u64, oneshot::Sender<Value>>>>;

/// The WS writer half, wrapped in a channel so the dispatch command
/// (which runs on a Tauri async runtime) can send frames without
/// holding the WS writer directly.
pub(crate) type WsWriterTx = mpsc::UnboundedSender<Message>;

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
    /// Kill the sidecar process. Consumes `self` because
    /// `CommandChild::kill(self)` takes ownership (the shell-plugin
    /// child handle is single-use after kill). The dev-mode variant
    /// (`tokio::process::Child::kill(&mut self)`) only borrows but we
    /// consume the handle anyway for API uniformity.
    pub(crate) async fn kill(self) -> std::io::Result<()> {
        match self {
            SidecarHandle::ShellPlugin(c) => {
                c.kill().map_err(|e| std::io::Error::other(format!("shell-plugin kill: {e}")))
            }
            SidecarHandle::DevMode(mut c) => c.kill().await,
        }
    }
}

pub(crate) struct SidecarState {
    /// Child handle for kill_children backstop.
    pub(crate) child: Mutex<Option<SidecarHandle>>,
    /// Token for the current sidecar instance (rotated per FT-1 respawn).
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
