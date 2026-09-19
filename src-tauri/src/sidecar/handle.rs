//! Sidecar child-process handle (ADR-0020 §1 + §10 + §14).
//! Drop safety net: docs/code-notes/tauri-host.md#sidecar-handle-drop-safety-net

use tauri_plugin_shell::process::CommandChild;

/// Sidecar child: Tauri shell-plugin `CommandChild` (release/externalBin)
/// or `tokio::process::Child` (dev mode). Both support `kill()`.
#[allow(clippy::large_enum_variant)] // both variants embed full child-process handles by design
pub(crate) enum SidecarHandle {
    // Option wrapper: `CommandChild::kill` consumes `self`, so Drop must
    // `take()` the child out of `&mut self` for a best-effort kill.
    ShellPlugin(Option<CommandChild>),
    DevMode(tokio::process::Child),
}

impl SidecarHandle {
    /// OS pid if available (used by `kill_tree` to reap grandchildren).
    /// `None` after the child was taken/killed or already reaped.
    pub(crate) fn pid(&self) -> Option<u32> {
        match self {
            SidecarHandle::ShellPlugin(c) => c.as_ref().map(|c| c.pid()),
            SidecarHandle::DevMode(c) => c.id(),
        }
    }

    /// Kill the sidecar process. Consumes `self` (`CommandChild::kill`
    /// takes ownership). Shell-plugin error kept as `io::Error` source
    /// so callers can inspect the underlying variant.
    pub(crate) async fn kill(mut self) -> std::io::Result<()> {
        match &mut self {
            // `take()` so Drop sees `None` and cannot double-kill.
            SidecarHandle::ShellPlugin(c) => match c.take() {
                Some(child) => child.kill().map_err(|e| {
                    std::io::Error::other(format!("shell-plugin kill: {e}"))
                }),
                None => Ok(()),
            },
            SidecarHandle::DevMode(c) => c.kill().await,
        }
    }

    /// `kill_children` backstop (ADR-0020 §10): kill the entire process
    /// TREE (native hotkey binary, model subprocesses) — a plain `kill()`
    /// would orphan grandchildren holding the mic. Best-effort; blocking
    /// `taskkill /T` / `pgrep` runs on `spawn_blocking` (C-TOKIO-1).
    /// Implementation: `crate::platform::process::kill_process_tree`.
    pub(crate) async fn kill_tree(self) -> std::io::Result<()> {
        if let Some(pid) = self.pid() {
            // spawn_blocking: tree walk can take >1s under load.
            let _ = tauri::async_runtime::spawn_blocking(move || {
                crate::platform::process::kill_process_tree(pid);
            })
            .await;
        }
        self.kill().await
    }

    /// Non-blocking exit probe. DevMode: `Ok(Some(exited))`.
    /// ShellPlugin: `Ok(None)` (no try_wait; EventStream is the signal).
    /// Used by the dev-mode arm of `shutdown_sidecar_for_exit`.
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

// Best-effort Drop kill: safety net when code forgets explicit kill.
// ShellPlugin: `child.kill()` only — never the blocking tree walk
// (release spawn already registered `kill_on_parent_exit`).
// DevMode: no-op (tokio `kill_on_drop(true)` handles it).
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
                // kill_on_drop(true) in spawn_sidecar_dev_mode.
            }
        }
    }
}
