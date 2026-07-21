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
    pub(crate) async fn kill(self) -> std::io::Result<()> {
        match self {
            SidecarHandle::ShellPlugin(c) => {
                c.kill().map_err(|e| std::io::Error::other(format!("shell-plugin kill: {e}")))
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
    /// (`taskkill /T` on Windows, `pkill -P` walk on Unix). Failures are
    /// logged but do not abort shutdown — the direct child is still
    /// reaped afterwards.
    pub(crate) async fn kill_tree(self) -> std::io::Result<()> {
        if let Some(pid) = self.pid() {
            kill_process_tree(pid);
        }
        self.kill().await
    }
}

/// Kill the process tree rooted at `pid` (the sidecar and its
/// descendants). Platform-native, best-effort — never panics.
fn kill_process_tree(pid: u32) {
    #[cfg(windows)]
    {
        use std::process::Command;
        // /F = force, /T = terminate the whole tree rooted at the pid.
        let _ = Command::new("taskkill")
            .args(["/F", "/T", "/PID", &pid.to_string()])
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status();
    }
    #[cfg(unix)]
    {
        use std::process::Command;
        // Best-effort: signal direct children, then let the OS reap them.
        // `pkill -P <pid>` matches children of `pid`; `-TERM` asks nicely
        // first (the graceful path usually already released resources).
        let _ = Command::new("pkill")
            .args(["-TERM", "-P", &pid.to_string()])
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status();
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
