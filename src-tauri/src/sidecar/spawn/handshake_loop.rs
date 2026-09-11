//! Shared stdout-handshake read loops for the spawn paths.
//!
//! The four spawn functions (`release_mode::spawn_sidecar_release`,
//! `worker::spawn_worker_release`, `dev_mode::spawn_sidecar_dev_mode`,
//! `worker::spawn_worker_dev_mode`) share one skeleton: read child output
//! until the handshake line (`server_started` / `worker_started`) parses,
//! with a shutting-down short-circuit, the
//! `SERVER_STARTED_POLL_INTERVAL_MS`/`SERVER_STARTED_TIMEOUT_MS` timeout
//! pair, and kill-then-drain cleanup on every failure path. The skeleton
//! splits 2×2 by process machinery, so it lives here as TWO helpers, not
//! one:
//!
//! - [`read_handshake_from_command_events`]: the RELEASE pair (Tauri
//!   shell-plugin `externalBin` children): output arrives as a
//!   `Receiver<CommandEvent>` (Stdout/Stderr/Terminated/Error), kill is the
//!   consuming `CommandChild::kill()`, and the post-kill drain polls
//!   `rx.recv()` for the exit watcher's `Terminated` event.
//! - [`read_handshake_from_stdout_lines`]: the DEV pair
//!   (`tokio::process::Command` children): lines arrive via
//!   `BufReader::read_line`, kill is the async `child.kill().await`
//!   (plus the `kill_on_drop(true)` set at construction), and the
//!   post-kill drain polls `child.wait()`.
//!
//! The per-path differences are pure strings, log tag, the noun used in
//! returned error strings, the handshake event name, and the two nouns in
//! the kill log lines: carried by [`HandshakeLabels`] so every log line
//! and every returned error string stays byte-identical to the
//! pre-extraction per-loop wording.
//!
//! Preserved quirk (do NOT "fix" without a separate decision): the
//! stdout-closed-before-handshake error paths in BOTH helpers return
//! WITHOUT killing the child. The release loop leaks the process on
//! channel-close (the shell-plugin child does not kill on Drop); the dev
//! loop relies on `kill_on_drop(true)`. That is the pre-existing shape of
//! all four loops and is kept exactly.

use crate::util::{SERVER_STARTED_POLL_INTERVAL_MS, SERVER_STARTED_TIMEOUT_MS};
use std::sync::atomic::AtomicBool;
use std::time::{Duration, Instant};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tokio::io::AsyncBufReadExt;
use tokio::sync::mpsc;

use super::handshake::is_shutting_down;

/// How long the spawn paths wait for the killed child's exit signal after
/// sending the kill (both machinery families): the release pair polls the
/// `CommandEvent` receiver for the watcher's `Terminated` event, the dev
/// pair polls `child.wait()`. Bounds the spawn path against a misbehaving
/// process that ignores the kill signal (rare, but possible for
/// uninterruptible kernel waits). Best-effort: errors and timeouts are
/// silently discarded (the kill has already been attempted).
const EXIT_DRAIN_TIMEOUT_MS: u64 = 500;

/// Per-path label set for one stdout-handshake loop.
///
/// Every field exists solely so the shared loop bodies emit the exact
/// per-path wording the four spawn functions had before the extraction —
/// the log lines and returned error strings are behavioral contract
/// (the supervisor's `respawn_inner` matches the `"shutdown"` error
/// string; log greppability is pinned project-wide).
pub(super) struct HandshakeLabels<'a> {
    /// Bracketed log tag: `[SIDECAR]`, `[WORKER]`, `[SIDECAR-DEV]`,
    /// `[WORKER-DEV]`.
    pub(super) log_tag: &'a str,
    /// Noun used in the returned error strings: `sidecar`, `worker`,
    /// `dev sidecar`, `dev worker`.
    pub(super) err_noun: &'a str,
    /// Handshake event name: `server_started` (slim-core sidecar) /
    /// `worker_started` (ML worker).
    pub(super) event_name: &'a str,
    /// Noun in the kill-failure warn lines: `child` (sidecar paths) /
    /// `worker` (worker paths).
    pub(super) kill_target: &'a str,
    /// Noun in the shutting-down info line: `child`, `worker`,
    /// `dev child`, `dev worker`.
    pub(super) fresh_target: &'a str,
}

/// Reap the child's whole process tree, off the async runtime.
///
/// `kill_process_tree` shells out to the platform tool (`pgrep -P`
/// recursive walk + `kill` + 200ms sleeps on Unix, `taskkill /T` on
/// Windows) via blocking `std::process::Command::status()` syscalls —
/// wrapped in `spawn_blocking` so a Tokio worker thread is never stalled
/// for the duration of the walk (mirrors the `SidecarHandle::kill_tree`
/// pattern in `sidecar/handle.rs`).
///
/// Call this BEFORE the direct child kill so the root is still alive when
/// the walk runs: on Unix, killing the root first would reparent the
/// grandchildren to init and break the descendant walk (the native hotkey
/// binary / model subprocesses would be orphaned with the mic + IPC port
/// still held).
pub(super) async fn kill_process_tree_off_thread(pid: u32) {
    let _ = tauri::async_runtime::spawn_blocking(move || {
        crate::platform::process::kill_process_tree(pid)
    })
    .await;
}

/// Register the kill-on-parent-exit guarantee for a freshly-spawned
/// shell-plugin child (release pair), best-effort.
///
/// The shell-plugin child does NOT kill the OS process on Drop, so a host
/// crash (segfault, OOM kill, `kill -9`) would orphan the child. The
/// platform helper implements the OS machinery (POSIX `/bin/sh` reaper
/// subprocess with `setsid()`, Windows Job Object with
/// `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, see
/// `platform::process::register_kill_on_parent_exit`). Errors are logged
/// but do NOT abort the spawn, the child is already running.
///
/// `warn_detail` is the per-path parenthetical in the warn line (the
/// sidecar and worker paths worded it differently pre-extraction; the
/// rendered line must stay byte-identical).
pub(super) fn register_kill_on_parent_exit_best_effort(log_tag: &str, warn_detail: &str, pid: u32) {
    if let Err(e) = crate::platform::process::register_kill_on_parent_exit(pid) {
        log::warn!(
            "{} failed to register kill-on-parent-exit for pid {} \
             (best-effort: {}): {}",
            log_tag,
            pid,
            warn_detail,
            e
        );
    }
}

/// Release-pair stdout handshake: read `CommandEvent`s until the
/// handshake line parses.
///
/// Shared by `spawn_sidecar_release` (slim-core sidecar,
/// `server_started`) and `spawn_worker_release` (ML worker,
/// `worker_started`). On success the child and the event receiver are
/// handed back to the caller, the receiver so `shutdown_sidecar` can
/// poll for `Terminated` instead of sleeping the full
/// `SHUTDOWN_ACK_TIMEOUT_MS`, the child so it can be wrapped in
/// `SidecarHandle::ShellPlugin`.
///
/// Loop semantics (identical for both callers pre-extraction):
///
/// - **shutting-down short-circuit**: checked every iteration. A respawn
///   initiated seconds before the user quits would otherwise block up to
///   `SERVER_STARTED_TIMEOUT_MS` (30s) waiting for a handshake line that
///   will never arrive. `is_shutting_down` uses SeqCst to pair with the
///   `shutting_down.swap(true, SeqCst)` in `shutdown_sidecar_for_exit`
///   (state.rs) so the flag flip is never missed to memory-ordering skid.
///   On detection: reap the tree, kill the child, drain the receiver
///   500ms, return `Err("shutdown")`. The supervisor's `respawn_inner`
///   matches that exact string and treats it as a graceful exit (clears
///   `respawn_in_progress`, returns Ok) instead of retrying.
/// - **`CommandEvent::Terminated` / `CommandEvent::Error`**, reap the
///   tree, kill the child (the shell-plugin handle does not kill on
///   Drop; without this a child that errored at startup but is still
///   running would survive past the Err return), then return the
///   spawn-failure error. Kill errors are logged, never replace the
///   original error.
/// - **`CommandEvent::Stderr`**, logged at `debug!` (the child's stderr
///   can be extremely chatty: Python warning frames, native-binary
///   debug prints, ctranslate2 device dumps, and the child's own log
///   file already carries its warnings/errors) and skipped: never parsed
///   as the handshake line.
/// - **channel closed (`Ok(None)`)**: error return WITHOUT a kill (see
///   the module-level preserved-quirk note).
/// - **per-iteration timeout**: loop and retry until the deadline.
/// - **deadline exceeded**: reap the tree, kill the child, drain the
///   receiver 500ms, return the timeout error with the stdout seen so
///   far.
pub(super) async fn read_handshake_from_command_events(
    labels: &HandshakeLabels<'_>,
    mut rx: mpsc::Receiver<CommandEvent>,
    child: CommandChild,
    shutting_down: Option<&AtomicBool>,
    parse_line: fn(&str) -> Option<u16>,
) -> Result<(u16, CommandChild, mpsc::Receiver<CommandEvent>), String> {
    let deadline = Instant::now() + Duration::from_millis(SERVER_STARTED_TIMEOUT_MS);
    let mut stdout_buf = String::new();

    while Instant::now() < deadline {
        if is_shutting_down(shutting_down) {
            log::info!(
                "{} shutting_down set during stdout-read loop: killing freshly-spawned {}",
                labels.log_tag,
                labels.fresh_target
            );
            let pid = child.pid();
            kill_process_tree_off_thread(pid).await;
            if let Err(kill_err) = child.kill() {
                log::warn!(
                    "{} failed to kill {} after shutting_down detected (best-effort): {}",
                    labels.log_tag,
                    labels.kill_target,
                    kill_err
                );
            }
            let _ =
                tokio::time::timeout(Duration::from_millis(EXIT_DRAIN_TIMEOUT_MS), rx.recv()).await;
            return Err("shutdown".to_string());
        }

        match tokio::time::timeout(
            Duration::from_millis(SERVER_STARTED_POLL_INTERVAL_MS),
            rx.recv(),
        )
        .await
        {
            Ok(Some(event)) => {
                let line =
                    match event {
                        CommandEvent::Stdout(bytes) => {
                            // `.into_owned()` reuses the inner String when the
                            // Cow is Owned (invalid UTF-8 case, the child's
                            // stderr can carry non-UTF-8 bytes from a C
                            // extension traceback) instead of always
                            // allocating.
                            String::from_utf8_lossy(&bytes).into_owned()
                        }
                        CommandEvent::Stderr(bytes) => {
                            let s = String::from_utf8_lossy(&bytes).into_owned();
                            log::debug!("{} stderr: {}", labels.log_tag, s.trim());
                            continue;
                        }
                        CommandEvent::Terminated(payload) => {
                            let pid = child.pid();
                            kill_process_tree_off_thread(pid).await;
                            if let Err(kill_err) = child.kill() {
                                log::warn!(
                                    "{} failed to kill {} after Terminated event (best-effort): {}",
                                    labels.log_tag,
                                    labels.kill_target,
                                    kill_err
                                );
                            }
                            return Err(format!(
                                "{} terminated before {} (code={:?})",
                                labels.err_noun, labels.event_name, payload.code
                            ));
                        }
                        CommandEvent::Error(err) => {
                            let pid = child.pid();
                            kill_process_tree_off_thread(pid).await;
                            if let Err(kill_err) = child.kill() {
                                log::warn!(
                                "{} failed to kill {} after CommandEvent::Error (best-effort): {}",
                                labels.log_tag, labels.kill_target, kill_err
                            );
                            }
                            return Err(format!("{} command error: {}", labels.err_noun, err));
                        }
                        _ => continue,
                    };
                stdout_buf.push_str(&line);
                if let Some(port) = parse_line(&line) {
                    log::info!("{} {} port={}", labels.log_tag, labels.event_name, port);
                    return Ok((port, child, rx));
                }
                // Not the handshake line: could be a stray log (shouldn't
                // happen per ADR-0020 §1, the sidecar sends all
                // non-handshake logs to stderr).
                log::warn!(
                    "{} unexpected stdout line (expected only {}): {}",
                    labels.log_tag,
                    labels.event_name,
                    line.trim()
                );
            }
            Ok(None) => {
                return Err(format!(
                    "{} stdout closed before {}",
                    labels.err_noun, labels.event_name
                ));
            }
            Err(_) => {
                // Timeout on this iteration: loop and retry until deadline.
                continue;
            }
        }
    }
    let pid = child.pid();
    kill_process_tree_off_thread(pid).await;
    // Kill errors are logged for visibility, a stuck child that won't
    // die would otherwise be invisible in the log.
    if let Err(e) = child.kill() {
        log::warn!(
            "{} failed to kill {} after deadline: {}",
            labels.log_tag,
            labels.kill_target,
            e
        );
    }
    // Reap the zombie: `child.kill()` sends the kill signal but does NOT
    // itself waitpid: the shell plugin's internal exit watcher delivers a
    // `CommandEvent::Terminated` to `rx` once the OS reports the process
    // has died. Without draining `rx` here, the `Terminated` event sits
    // unread in the channel buffer until `rx` is dropped on function
    // return, deferring the host-side waitpid and leaving a brief zombie
    // window.
    let _ = tokio::time::timeout(Duration::from_millis(EXIT_DRAIN_TIMEOUT_MS), rx.recv()).await;
    Err(format!(
        "{} did not emit {} within {}ms. stdout so far: {}",
        labels.err_noun, labels.event_name, SERVER_STARTED_TIMEOUT_MS, stdout_buf
    ))
}

/// Dev-pair stdout handshake: read stdout lines until the handshake line
/// parses.
///
/// Shared by `spawn_sidecar_dev_mode` (slim-core sidecar,
/// `server_started`) and `spawn_worker_dev_mode` (ML worker,
/// `worker_started`). On success the port is returned and the caller
/// wraps the (borrowed) child in `SidecarHandle::DevMode`.
///
/// Loop semantics (identical for both callers pre-extraction):
///
/// - **shutting-down short-circuit**: checked every iteration; a cold
///   Python import on the first dev run can take 5-10s, long enough for
///   a user-initiated quit to race the handshake. The dev child was
///   constructed with `kill_on_drop(true)`, so dropping it would
///   eventually kill the process: but we kill explicitly here (and
///   wait via `child.wait()`) so there is no zombie window between this
///   return and the eventual Drop. Returns `Err("shutdown")` (the
///   supervisor's graceful-exit marker: see the release-pair helper).
/// - **`read_line` returns `Ok(0)` (EOF)**, error return WITHOUT an
///   explicit kill (kill_on_drop reaps on return; see the module-level
///   preserved-quirk note).
/// - **`read_line` returns `Err`**. The io error is returned verbatim.
/// - **per-iteration timeout**: loop and retry until the deadline.
/// - **deadline exceeded**: reap the tree (pid via `child.id()`, `None`
///   if the child was already reaped), kill the child (errors logged),
///   wait 500ms for the zombie reap, return the timeout error with the
///   stdout seen so far.
pub(super) async fn read_handshake_from_stdout_lines(
    labels: &HandshakeLabels<'_>,
    reader: &mut tokio::io::BufReader<tokio::process::ChildStdout>,
    child: &mut tokio::process::Child,
    shutting_down: Option<&AtomicBool>,
    parse_line: fn(&str) -> Option<u16>,
) -> Result<u16, String> {
    let deadline = Instant::now() + Duration::from_millis(SERVER_STARTED_TIMEOUT_MS);
    let mut stdout_buf = String::new();
    while Instant::now() < deadline {
        if is_shutting_down(shutting_down) {
            log::info!(
                "{} shutting_down set during stdout-read loop: killing freshly-spawned {}",
                labels.log_tag,
                labels.fresh_target
            );
            let pid_opt = child.id();
            if let Some(pid) = pid_opt {
                kill_process_tree_off_thread(pid).await;
            }
            if let Err(e) = child.kill().await {
                log::warn!(
                    "{} failed to kill {} after shutting_down detected (best-effort): {}",
                    labels.log_tag,
                    labels.kill_target,
                    e
                );
            }
            let _ =
                tokio::time::timeout(Duration::from_millis(EXIT_DRAIN_TIMEOUT_MS), child.wait())
                    .await;
            return Err("shutdown".to_string());
        }
        let mut line = String::new();
        match tokio::time::timeout(
            Duration::from_millis(SERVER_STARTED_POLL_INTERVAL_MS),
            reader.read_line(&mut line),
        )
        .await
        {
            Ok(Ok(0)) => {
                return Err(format!(
                    "{} stdout closed before {}",
                    labels.err_noun, labels.event_name
                ));
            }
            Ok(Ok(_)) => {
                stdout_buf.push_str(&line);
                if let Some(port) = parse_line(&line) {
                    log::info!("{} {} port={}", labels.log_tag, labels.event_name, port);
                    return Ok(port);
                }
                log::warn!(
                    "{} unexpected stdout line (expected only {}): {}",
                    labels.log_tag,
                    labels.event_name,
                    line.trim()
                );
            }
            Ok(Err(e)) => {
                return Err(format!("{} stdout read error: {}", labels.err_noun, e));
            }
            Err(_) => continue, // per-iteration timeout: retry until deadline
        }
    }
    let pid_opt = child.id();
    if let Some(pid) = pid_opt {
        kill_process_tree_off_thread(pid).await;
    }
    // Kill errors are logged for visibility (mirrors the release-pair
    // helper).
    if let Err(e) = child.kill().await {
        log::warn!(
            "{} failed to kill {} after deadline: {}",
            labels.log_tag,
            labels.kill_target,
            e
        );
    }
    // Reap the zombie: `tokio::process::Child::kill` sends SIGKILL but
    // does NOT call waitpid: the killed child stays in the OS process
    // table until `wait()`. `kill_on_drop(true)` ensures Drop eventually
    // reaps, but Drop fires on function return; wait explicitly here to
    // bound the spawn path.
    let _ = tokio::time::timeout(Duration::from_millis(EXIT_DRAIN_TIMEOUT_MS), child.wait()).await;
    Err(format!(
        "{} did not emit {} within {}ms. stdout so far: {}",
        labels.err_noun, labels.event_name, SERVER_STARTED_TIMEOUT_MS, stdout_buf
    ))
}
