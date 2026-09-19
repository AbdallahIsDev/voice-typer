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
//! Former quirk, now fixed: the stdout-closed-before-handshake error
//! paths in BOTH helpers used to return WITHOUT killing the child. The
//! release loop leaked the process on channel-close (the shell-plugin
//! child does not kill on Drop, so a sidecar that crashed mid-startup
//! held the single-instance mutex and blocked relaunch); the dev loop
//! relied on `kill_on_drop(true)`. Both arms now reap the tree + kill
//! explicitly (kill errors logged, never replacing the original error),
//! matching every other failure arm in these helpers. The same contract
//! covers the remaining failure arms (shutting-down, Terminated/Error,
//! stdout-read `Err`, deadline): every non-success return kills first.
//!
//! Supervisor interaction (C-WS-3 / no-ping-pong): handshake-time kill
//! happens INSIDE `spawn_sidecar_*` / `spawn_worker_*`, BEFORE
//! `reconnect_ws` installs a WS connection and before any
//! `ws_generation` bump. A failed handshake returns a spawn error to
//! `respawn_inner`, which either retries with backoff or short-circuits
//! on the exact `"shutdown"` sentinel — it never enqueues a
//! generation-tagged respawn request. The dequeue-time stale-generation
//! re-check in `ws/respawn_scheduler.rs` therefore cannot be confused
//! by a handshake-time kill: those kills have no generation to carry
//! and never reach the scheduler.

use crate::util::SERVER_STARTED_POLL_INTERVAL_MS;
use std::sync::atomic::AtomicBool;
use std::time::{Duration, Instant};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tokio::io::AsyncBufReadExt;
use tokio::sync::mpsc;

use super::handshake::is_shutting_down;
use crate::sidecar::child_log::{should_tee, tee_child_output, ChildStream};

const EXIT_DRAIN_TIMEOUT_MS: u64 = 500;

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

pub(super) async fn kill_process_tree_off_thread(pid: u32) {
    let _ = tauri::async_runtime::spawn_blocking(move || {
        crate::platform::process::kill_process_tree(pid)
    })
    .await;
}

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

pub(super) async fn read_handshake_from_command_events(
    labels: &HandshakeLabels<'_>,
    mut rx: mpsc::Receiver<CommandEvent>,
    child: CommandChild,
    shutting_down: Option<&AtomicBool>,
    parse_line: fn(&str) -> Option<u16>,
    timeout_ms: u64,
) -> Result<(u16, CommandChild, mpsc::Receiver<CommandEvent>), String> {
    let deadline = Instant::now() + Duration::from_millis(timeout_ms);
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
                            if should_tee(labels.log_tag) {
                                tee_child_output(ChildStream::Stdout, &bytes);
                            }
                            String::from_utf8_lossy(&bytes).into_owned()
                        }
                        CommandEvent::Stderr(bytes) => {
                            if should_tee(labels.log_tag) {
                                tee_child_output(ChildStream::Stderr, &bytes);
                            }
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
                log::warn!(
                    "{} unexpected stdout line (expected only {}): {}",
                    labels.log_tag,
                    labels.event_name,
                    line.trim()
                );
            }
            Ok(None) => {
                let pid = child.pid();
                kill_process_tree_off_thread(pid).await;
                if let Err(kill_err) = child.kill() {
                    log::warn!(
                        "{} failed to kill {} after stdout closed before handshake (best-effort): {}",
                        labels.log_tag,
                        labels.kill_target,
                        kill_err
                    );
                }
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
    let _ = tokio::time::timeout(Duration::from_millis(EXIT_DRAIN_TIMEOUT_MS), rx.recv()).await;
    Err(format!(
        "{} did not emit {} within {}ms. stdout so far: {}",
        labels.err_noun, labels.event_name, timeout_ms, stdout_buf
    ))
}

pub(super) async fn read_handshake_from_stdout_lines(
    labels: &HandshakeLabels<'_>,
    reader: &mut tokio::io::BufReader<tokio::process::ChildStdout>,
    child: &mut tokio::process::Child,
    shutting_down: Option<&AtomicBool>,
    parse_line: fn(&str) -> Option<u16>,
    timeout_ms: u64,
) -> Result<u16, String> {
    let deadline = Instant::now() + Duration::from_millis(timeout_ms);
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
                let pid_opt = child.id();
                if let Some(pid) = pid_opt {
                    kill_process_tree_off_thread(pid).await;
                }
                if let Err(e) = child.kill().await {
                    log::warn!(
                        "{} failed to kill {} after stdout closed before handshake (best-effort): {}",
                        labels.log_tag,
                        labels.kill_target,
                        e
                    );
                }
                // Reap the zombie (mirrors the dev deadline arm):
                // `kill()` sends the signal but does NOT call waitpid.
                let _ =
                    tokio::time::timeout(Duration::from_millis(EXIT_DRAIN_TIMEOUT_MS), child.wait())
                        .await;
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
                let pid_opt = child.id();
                if let Some(pid) = pid_opt {
                    kill_process_tree_off_thread(pid).await;
                }
                if let Err(kill_err) = child.kill().await {
                    log::warn!(
                        "{} failed to kill {} after stdout read error (best-effort): {}",
                        labels.log_tag,
                        labels.kill_target,
                        kill_err
                    );
                }
                let _ =
                    tokio::time::timeout(Duration::from_millis(EXIT_DRAIN_TIMEOUT_MS), child.wait())
                        .await;
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
    let _ = tokio::time::timeout(Duration::from_millis(EXIT_DRAIN_TIMEOUT_MS), child.wait()).await;
    Err(format!(
        "{} did not emit {} within {}ms. stdout so far: {}",
        labels.err_noun, labels.event_name, timeout_ms, stdout_buf
    ))
}

// Sibling test module: tests live in `handshake_loop_tests.rs` (per
// C-TEST-5: no inline `#[cfg(test)] mod tests` blocks in production
// source).
#[cfg(test)]
#[path = "handshake_loop_tests.rs"]
mod handshake_loop_tests;
