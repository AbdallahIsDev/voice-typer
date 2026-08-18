//! Sidecar child-process handle (ADR-0020 §1 + §10 + §14).
//!
//! Extracted from `state.rs` so the shared-state module stays focused on
//! `SidecarState`/`WorkerState` data, while the process-management
//! `SidecarHandle` enum + `Drop` safety net lives next to the other
//! sidecar-lifecycle modules. `state.rs` re-exports `SidecarHandle` so
//! existing `crate::state::SidecarHandle` imports keep resolving.

use tauri_plugin_shell::process::CommandChild;

/// ADR-0020 §1 + §14: the sidecar child handle is either a Tauri
/// shell-plugin `CommandChild` (release builds, spawned via
/// `externalBin`) or a `tokio::process::Child` (dev mode, spawned via
/// `VOICE_TYPER_SIDECAR_DEV=1` running `python -m voice_typer.server.ipc_server`).
/// Both variants support `kill()`; `shutdown_sidecar` matches on the
/// variant to call the right kill method.
#[allow(clippy::large_enum_variant)] // both variants embed full child-process handles by design
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
                    std::io::Error::other(format!("shell-plugin kill: {e}"))
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
