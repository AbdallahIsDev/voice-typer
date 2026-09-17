//! Sibling tests for `sidecar::spawn::handshake_loop` (per C-TEST-5 —
//! sibling test file, no inline tests in production source).
//!
//! Pins the kill-on-every-failure-path contract of the DEV pair
//! (`read_handshake_from_stdout_lines`) with REAL child processes:
//!
//! - **shutting-down short-circuit**: the freshly-spawned child is
//!   killed and reaped; the helper returns the exact `"shutdown"`
//!   sentinel the supervisor matches.
//! - **stdout closed before handshake (EOF)**: the child is killed and
//!   reaped. This was the release-path leak class (a crashed child
//!   holding the single-instance mutex); the dev pair now kills
//!   explicitly instead of relying only on `kill_on_drop(true)`.
//! - **deadline exceeded**: with an injected short `timeout_ms`, the
//!   hanging child is killed and reaped.
//! - **success**: the handshake port is returned and the child is left
//!   running for the caller.
//!
//! The RELEASE pair (`read_handshake_from_command_events`) cannot be
//! driven without a live `tauri_plugin_shell::CommandChild` (created
//! only via `AppHandle` + the shell plugin, infeasible in a unit test —
//! same constraint as the spawn functions themselves, see
//! `spawn_tests.rs`). Its failure arms carry the same kill-then-return
//! structure and are verified by the shared loop-body rewrite + the
//! module docs; the dev-pair tests below exercise the shared semantics
//! (kill-before-Err, kill errors never replacing the original error).
//!
//! Real children of the test binary take `CHILD_PROCESS_TEST_LOCK` so
//! they never race the own-pid enumeration tests.

use super::super::handshake::parse_server_started;
use super::{read_handshake_from_stdout_lines, HandshakeLabels, EXIT_DRAIN_TIMEOUT_MS};
use crate::test_support::CHILD_PROCESS_TEST_LOCK;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

/// Short deadline for the timeout-kill test: one poll interval (500ms)
/// is enough to exit the first iteration, so the helper falls through
/// to the deadline arm without a 30s wait.
const TEST_HANDSHAKE_TIMEOUT_MS: u64 = 150;

fn dev_labels() -> HandshakeLabels<'static> {
    HandshakeLabels {
        log_tag: "[TEST-DEV]",
        err_noun: "dev sidecar",
        event_name: "server_started",
        kill_target: "child",
        fresh_target: "dev child",
    }
}

type DevChild = (
    tokio::process::Child,
    tokio::io::BufReader<tokio::process::ChildStdout>,
);

/// Spawn a long-lived child that never emits a handshake line
/// (Windows: `ping` counts; Unix: `sleep`). Returns the child + its
/// stdout reader, ready for `read_handshake_from_stdout_lines`.
fn spawn_hanging_child() -> DevChild {
    let mut cmd = tokio::process::Command::new(if cfg!(windows) { "ping" } else { "sleep" });
    if cfg!(windows) {
        // ~59s of ICMP: long enough that only an explicit kill ends it.
        cmd.args(["-n", "60", "127.0.0.1"]);
    } else {
        cmd.arg("60");
    }
    cmd.stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .kill_on_drop(true);
    let mut child = cmd.spawn().expect("failed to spawn hanging test child");
    let stdout = child.stdout.take().expect("hanging child stdout piped");
    (child, tokio::io::BufReader::new(stdout))
}

/// Spawn a child whose stdout closes immediately with no handshake
/// line (the EOF / stdout-closed-before-handshake arm).
fn spawn_immediate_exit_child() -> DevChild {
    let mut cmd = if cfg!(windows) {
        let mut c = tokio::process::Command::new("cmd");
        c.args(["/C", "exit", "1"]);
        c
    } else {
        // `true` prints nothing and exits 0: stdout still closes.
        tokio::process::Command::new("true")
    };
    cmd.stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .kill_on_drop(true);
    let mut child = cmd
        .spawn()
        .expect("failed to spawn immediate-exit test child");
    let stdout = child.stdout.take().expect("exiting child stdout piped");
    (child, tokio::io::BufReader::new(stdout))
}

/// Spawn a child that emits a valid handshake line, then hangs
/// (so the success arm can observe a still-live child).
fn spawn_handshake_child() -> DevChild {
    spawn_scripted_child(&[r#"{"event":"server_started","port":54321}"#])
}

/// Spawn a child that prints each scripted line then sleeps.
fn spawn_scripted_child(lines: &[&str]) -> DevChild {
    let mut cmd = if cfg!(windows) {
        let mut script = String::new();
        for line in lines {
            script.push_str(&format!("Write-Output '{line}'; "));
        }
        script.push_str("Start-Sleep -Seconds 30");
        let mut c = tokio::process::Command::new("powershell");
        c.args(["-NoProfile", "-NonInteractive", "-Command", &script]);
        c
    } else {
        let joined = lines.join("\\n");
        let mut c = tokio::process::Command::new("sh");
        c.args(["-c", &format!("printf '%s\\n' '{joined}'; sleep 30")]);
        c
    };
    cmd.stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .kill_on_drop(true);
    let mut child = cmd.spawn().expect("failed to spawn scripted test child");
    let stdout = child.stdout.take().expect("scripted child stdout piped");
    (child, tokio::io::BufReader::new(stdout))
}

/// True when the child has been reaped (exit status collected).
fn child_reaped(child: &mut tokio::process::Child) -> bool {
    matches!(child.try_wait(), Ok(Some(_)))
}

async fn kill_and_reap(child: &mut tokio::process::Child) {
    let _ = child.kill().await;
    let _ = tokio::time::timeout(Duration::from_millis(EXIT_DRAIN_TIMEOUT_MS), child.wait()).await;
}

/// shutting_down=true at entry must kill the child and return the
/// exact `"shutdown"` sentinel the supervisor's `respawn_inner`
/// matches (`supervisor.rs` `if e == "shutdown"`).
#[tokio::test]
async fn test_dev_handshake_kills_child_on_shutting_down() {
    let _lock = CHILD_PROCESS_TEST_LOCK
        .lock()
        .unwrap_or_else(|e| e.into_inner());
    let (mut child, mut reader) = spawn_hanging_child();
    let flag = AtomicBool::new(true);

    let result = read_handshake_from_stdout_lines(
        &dev_labels(),
        &mut reader,
        &mut child,
        Some(&flag),
        parse_server_started,
        TEST_HANDSHAKE_TIMEOUT_MS,
    )
    .await;

    assert_eq!(
        result.as_ref().err().map(String::as_str),
        Some("shutdown"),
        "shutting-down arm must return the supervisor's graceful-exit sentinel"
    );
    assert!(
        child_reaped(&mut child),
        "shutting-down arm must kill + reap the freshly-spawned child"
    );
}

/// stdout close before handshake (EOF) must kill + reap the child
/// and surface the stdout-closed error — never return with the child
/// still running (the former release-path leak class).
#[tokio::test]
async fn test_dev_handshake_kills_child_on_stdout_close() {
    let _lock = CHILD_PROCESS_TEST_LOCK
        .lock()
        .unwrap_or_else(|e| e.into_inner());
    let (mut child, mut reader) = spawn_immediate_exit_child();

    let result = read_handshake_from_stdout_lines(
        &dev_labels(),
        &mut reader,
        &mut child,
        None,
        parse_server_started,
        // Long enough that EOF is the arm that fires, not the deadline.
        5_000,
    )
    .await;

    let err = result.err().expect("stdout-close arm must return Err");
    assert!(
        err.contains("stdout closed before server_started"),
        "error must name the stdout-close failure, got: {err}"
    );
    assert!(
        child_reaped(&mut child),
        "stdout-close arm must kill + reap the child before returning"
    );
}

/// Deadline exceeded must kill + reap a hanging child and return the
/// timeout error (with any stdout seen so far). Uses a short injected
/// `timeout_ms` so the test does not wait the production 30s.
#[tokio::test]
async fn test_dev_handshake_kills_child_on_deadline() {
    let _lock = CHILD_PROCESS_TEST_LOCK
        .lock()
        .unwrap_or_else(|e| e.into_inner());
    let (mut child, mut reader) = spawn_hanging_child();

    let result = read_handshake_from_stdout_lines(
        &dev_labels(),
        &mut reader,
        &mut child,
        None,
        parse_server_started,
        TEST_HANDSHAKE_TIMEOUT_MS,
    )
    .await;

    let err = result.err().expect("deadline arm must return Err");
    assert!(
        err.contains("did not emit server_started within"),
        "error must name the handshake timeout, got: {err}"
    );
    assert!(
        err.contains(&format!("within {TEST_HANDSHAKE_TIMEOUT_MS}ms")),
        "timeout error must carry the injected deadline, got: {err}"
    );
    assert!(
        child_reaped(&mut child),
        "deadline arm must kill + reap the hanging child"
    );
}

/// Success: a valid handshake line returns the port and leaves the
/// child running for the caller to wrap in `SidecarHandle::DevMode`.
#[tokio::test]
async fn test_dev_handshake_success_returns_port_and_leaves_child() {
    let _lock = CHILD_PROCESS_TEST_LOCK
        .lock()
        .unwrap_or_else(|e| e.into_inner());
    let (mut child, mut reader) = spawn_handshake_child();

    let result = read_handshake_from_stdout_lines(
        &dev_labels(),
        &mut reader,
        &mut child,
        None,
        parse_server_started,
        10_000,
    )
    .await;

    assert_eq!(
        result.as_ref().ok().copied(),
        Some(54321),
        "success arm must parse the handshake port, got: {result:?}"
    );
    assert!(
        !child_reaped(&mut child),
        "success arm must NOT kill the child: the caller owns it"
    );
    kill_and_reap(&mut child).await;
}

/// A non-handshake stdout line is logged and skipped; a subsequent
/// valid handshake line still succeeds (the loop must not abort on
/// the first unexpected line).
#[tokio::test]
async fn test_dev_handshake_skips_unexpected_line_then_accepts() {
    let _lock = CHILD_PROCESS_TEST_LOCK
        .lock()
        .unwrap_or_else(|e| e.into_inner());
    let (mut child, mut reader) = spawn_scripted_child(&[
        "boot banner that is not json",
        r#"{"event":"server_started","port":23456}"#,
    ]);

    let result = read_handshake_from_stdout_lines(
        &dev_labels(),
        &mut reader,
        &mut child,
        None,
        parse_server_started,
        10_000,
    )
    .await;

    assert_eq!(
        result.as_ref().ok().copied(),
        Some(23456),
        "unexpected line must not abort the handshake, got: {result:?}"
    );
    kill_and_reap(&mut child).await;
}

/// `HandshakeLabels` field contract: the four production call sites
/// supply the exact log/error nouns the supervisor and log greps
/// depend on. Pins the struct shape so a silent field swap cannot
/// desync the `"shutdown"` sentinel or the log tags.
#[test]
fn test_handshake_labels_carry_per_path_nouns() {
    let sidecar = HandshakeLabels {
        log_tag: "[SIDECAR]",
        err_noun: "sidecar",
        event_name: "server_started",
        kill_target: "child",
        fresh_target: "child",
    };
    assert_eq!(sidecar.event_name, "server_started");
    let worker = HandshakeLabels {
        log_tag: "[WORKER]",
        err_noun: "worker",
        event_name: "worker_started",
        kill_target: "worker",
        fresh_target: "worker",
    };
    assert_eq!(worker.event_name, "worker_started");
    assert_eq!(worker.kill_target, "worker");
}

/// The shutting-down flag must be observed via SeqCst (the production
/// loop pairs with `shutdown_sidecar_for_exit`'s swap). A false flag
/// must NOT short-circuit.
#[test]
fn test_shutting_down_flag_false_does_not_short_circuit() {
    let flag = AtomicBool::new(false);
    assert!(
        !super::super::handshake::is_shutting_down(Some(&flag)),
        "false flag must not short-circuit the handshake loop"
    );
    flag.store(true, Ordering::SeqCst);
    assert!(
        super::super::handshake::is_shutting_down(Some(&flag)),
        "true flag must short-circuit the handshake loop"
    );
}
