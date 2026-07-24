//! Sidecar spawn + stdout handshake (ADR-0020 §1 + §4.1 + §14).

use crate::state::SidecarHandle;
use crate::util::SERVER_STARTED_TIMEOUT_MS;
use std::time::{Duration, Instant};
use tauri::Manager;
use tauri_plugin_shell::process::CommandEvent;
use tauri_plugin_shell::ShellExt;
use tokio::io::AsyncBufReadExt;
use tokio::sync::mpsc;
use serde_json::Value;

// ─── Sidecar spawn + stdout handshake (ADR-0020 §1) ───────────────────

/// Spawn the Python sidecar via Tauri's `externalBin` mechanism and
/// read the `server_started` JSON from stdout.
///
/// Returns the bound port + the child handle on success.
pub(crate) async fn spawn_sidecar_and_get_port(
    app: &tauri::AppHandle,
    token: &str,
) -> Result<(u16, SidecarHandle, Option<mpsc::Receiver<CommandEvent>>), String> {
    // ADR-0020 §14: dev mode — when `VOICE_TYPER_SIDECAR_DEV=1` is set,
    // spawn `python -m voice_typer.server.ipc_server --ws` via
    // std::process::Command (tokio::process::Command for async I/O)
    // instead of the frozen `externalBin` binary. This lets UI/
    // transport iterate in seconds (no Nuitka recompile) during dev.
    //
    // CR-2: only the release path (`spawn_sidecar_release`) returns a
    // `CommandEvent` receiver — the dev-mode path spawns via
    // `tokio::process::Command` which has no equivalent stream, so we
    // return `None` and `shutdown_sidecar` falls back to bounded sleep
    // polling.
    if is_dev_mode() {
        let (port, child) = spawn_sidecar_dev_mode(token).await?;
        return Ok((port, child, None));
    }
    let (port, child, rx) = spawn_sidecar_release(app, token).await?;
    Ok((port, child, Some(rx)))
}

/// ADR-0020 §14: returns true when `VOICE_TYPER_SIDECAR_DEV=1` is set.
/// Exposed as a separate function so unit tests can verify the env-var
/// matching logic without polluting the process environment.
pub(crate) fn is_dev_mode() -> bool {
    is_dev_mode_for(std::env::var("VOICE_TYPER_SIDECAR_DEV").ok().as_deref())
}

/// Pure predicate form of `is_dev_mode` for unit testing.
pub(crate) fn is_dev_mode_for(value: Option<&str>) -> bool {
    value == Some("1")
}

/// ADR-0020 §1 + §4.1: release-build spawn via `externalBin`. Wraps
/// the resulting `CommandChild` in `SidecarHandle::ShellPlugin`.
///
/// GT-A2-4 (Low, SKIPPED — documented): the release-mode ShellPlugin
/// sidecar has no kill-on-drop equivalent of the dev-mode
/// `kill_on_drop(true)`. If the host process crashes (segfault, OOM
/// kill, `kill -9`), the sidecar Python process is orphaned and keeps
/// running with the mic / IPC port / native hotkey binary held. The
/// proper fix is platform-specific:
///   - POSIX: `prctl(PR_SET_PDEATHSIG, SIGKILL)` in a `pre_exec` hook.
///   - Windows: assign the sidecar to a Job Object with
///     `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`.
/// Both approaches require a `pre_exec` hook (POSIX) or post-spawn
/// Job-Object syscalls (Windows) that the tauri-plugin-shell
/// `externalBin` API does NOT expose. The fix would require EITHER
/// refactoring the release path to spawn via `std::process::Command`
/// directly OR adding a `platform/*` helper that attaches the
/// just-spawned child's pid to a Job Object / registers prctl —
/// `platform/*` is GT-FIX-20's domain. COORDINATION NOTE for
/// GT-FIX-20: a `platform::process::register_kill_on_parent_exit(pid:
/// u32)` helper would let this file call it right after
/// `cmd.spawn()` below. The dev-mode path is already covered by
/// `kill_on_drop(true)` (see `spawn_sidecar_dev_mode`).
pub(crate) async fn spawn_sidecar_release(
    app: &tauri::AppHandle,
    token: &str,
) -> Result<(u16, SidecarHandle, mpsc::Receiver<CommandEvent>), String> {
    // ADR-0020 §4.1: Tauri's externalBin selects the right binary by
    // matching the Rust target triple at runtime. The binary name
    // (without the triple suffix) is `python-sidecar`.
    let sidecar = app
        .shell()
        .sidecar("python-sidecar")
        .map_err(|e| format!("failed to resolve sidecar binary: {e}"))?;

    // ADR-0020 §2 + §3: pass TAURI_SIDECAR=1 + VOICE_TYPER_IPC_TOKEN
    // + VOICE_TYPER_NATIVE_DIR + VOICE_TYPER_PREWARM_EXE env vars.
    // The sidecar's `ipc_server.py main()` checks TAURI_SIDECAR=1 to
    // skip the Python-side single-instance mutex + heartbeat watchdog.
    let native_dir = app
        .path()
        .resource_dir()
        .map(|p| p.join("native"))
        .map_err(|e| format!("resource_dir failed: {e}"))?;
    let prewarm_exe = prewarm_resource_path(app)?;

    let cmd = sidecar
        .args(["--ws"])
        .env("TAURI_SIDECAR", "1")
        .env("VOICE_TYPER_IPC_TOKEN", token)
        .env("VOICE_TYPER_NATIVE_DIR", native_dir.to_string_lossy().to_string())
        .env("VOICE_TYPER_PREWARM_EXE", prewarm_exe);

    // Tauri v2's shell plugin automatically pipes stdout/stderr —
    // the `spawn()` returns a `Receiver<CommandEvent>` that yields
    // `Stdout`/`Stderr`/`Terminate`/`Error` events. We do NOT call
    // `.stdout(Stdio::piped())` (that's the std::process API, not
    // the tauri-plugin-shell API).
    let (mut rx, child) = cmd
        .spawn()
        .map_err(|e| format!("failed to spawn sidecar: {e}"))?;

    // ADR-0020 §1: read stdout until we parse the server_started JSON.
    // The sidecar force-sets stdout to line-buffered (sidecar_ws.py
    // `_force_line_buffered_stdout`), so each `print(..., flush=True)`
    // arrives as one event.
    let deadline = Instant::now() + Duration::from_millis(SERVER_STARTED_TIMEOUT_MS);
    let mut stdout_buf = String::new();

    while Instant::now() < deadline {
        match tokio::time::timeout(
            Duration::from_millis(500),
            rx.recv(),
        )
        .await
        {
            Ok(Some(event)) => {
                // tauri-plugin-shell yields CommandEvent enums
                // (Stdout/Stderr/Terminated/Error). We only care about
                // Stdout lines for the server_started JSON.
                let line = match event {
                    CommandEvent::Stdout(bytes) => {
                        String::from_utf8_lossy(&bytes).to_string()
                    }
                    CommandEvent::Stderr(bytes) => {
                        // Log stderr but don't parse it as server_started.
                        let s = String::from_utf8_lossy(&bytes).to_string();
                        log::info!("[SIDECAR] stderr: {}", s.trim());
                        continue;
                    }
                    CommandEvent::Terminated(payload) => {
                        // G4-M-61: kill the child before returning Err so
                        // a sidecar that sent Terminated but didn't
                        // actually exit doesn't leak as a zombie. The
                        // Tauri shell-plugin child handle is single-use
                        // after kill (consumes `child`), so we move it
                        // out of the captured variable before the Err.
                        // Best-effort: errors are logged but don't
                        // replace the original spawn-failure error.
                        //
                        // PVT-G5-030 (session 5): also reap grandchildren
                        // via `kill_process_tree` BEFORE `child.kill()`
                        // so the root is still alive when we walk
                        // `pgrep -P <pid>` (on Unix, killing the root
                        // first would reparent the children to init and
                        // break the descendant walk).
                        //
                        // NOTE: `tauri_plugin_shell::process::CommandChild::pid()`
                        // returns `u32` directly (NOT `Option<u32>` —
                        // unlike `tokio::process::Child::id()` in the
                        // dev-mode path below). The shell-plugin child
                        // always has a pid once spawned.
                        //
                        // XV-136 (High): wrap the blocking
                        // `kill_process_tree` (which on Unix spawns
                        // `pgrep` + `kill -TERM` per descendant +
                        // `std::thread::sleep(200ms)` + `kill -KILL` per
                        // descendant — all `std::process::Command::status()`
                        // blocking syscalls) in
                        // `tauri::async_runtime::spawn_blocking` so we
                        // don't stall a Tokio worker thread. Mirrors the
                        // `SidecarHandle::kill_tree` pattern in
                        // `state.rs:148`.
                        let pid = child.pid();
                        let _ = tauri::async_runtime::spawn_blocking(
                            move || crate::state::kill_process_tree(pid),
                        )
                        .await;
                        if let Err(kill_err) = child.kill() {
                            log::warn!(
                                "[SIDECAR] failed to kill child after Terminated event (best-effort): {}",
                                kill_err
                            );
                        }
                        return Err(format!(
                            "sidecar terminated before server_started (code={:?})",
                            payload.code
                        ));
                    }
                    CommandEvent::Error(err) => {
                        // G4-M-61: kill the child before returning Err.
                        // Without this, the sidecar process leaks — the
                        // shell-plugin CommandChild doesn't kill on Drop,
                        // so a sidecar that errored at startup but is
                        // still running would survive past the Err return.
                        // Best-effort: kill errors are logged but don't
                        // replace the original CommandEvent::Error.
                        //
                        // PVT-G5-030 (session 5): reap grandchildren
                        // first.
                        //
                        // NOTE: same as the Terminated arm above —
                        // `child.pid()` here is `u32`, not `Option<u32>`.
                        // XV-136 (High): spawn_blocking wrap — see the
                        // comment in the Terminated arm above.
                        let pid = child.pid();
                        let _ = tauri::async_runtime::spawn_blocking(
                            move || crate::state::kill_process_tree(pid),
                        )
                        .await;
                        if let Err(kill_err) = child.kill() {
                            log::warn!(
                                "[SIDECAR] failed to kill child after CommandEvent::Error (best-effort): {}",
                                kill_err
                            );
                        }
                        return Err(format!("sidecar command error: {err}"));
                    }
                    _ => continue,
                };
                stdout_buf.push_str(&line);
                // Try to parse as the server_started event.
                if let Some(port) = parse_server_started(&line) {
                    log::info!("[SIDECAR] server_started port={}", port);
                    // CR-2: hand the event receiver back to the caller so
                    // `shutdown_sidecar` can poll for `Terminated` instead
                    // of sleeping the full SHUTDOWN_ACK_TIMEOUT_MS.
                    return Ok((port, SidecarHandle::ShellPlugin(child), rx));
                }
                // Not the server_started line — could be a stray log
                // (shouldn't happen per ADR-0020 §1, sidecar sends
                // all non-handshake logs to stderr).
                log::warn!("[SIDECAR] unexpected stdout line (expected only server_started): {}", line.trim());
            }
            Ok(None) => {
                return Err("sidecar stdout closed before server_started".into());
            }
            Err(_) => {
                // Timeout on this iteration — loop and retry until deadline.
                continue;
            }
        }
    }
    let pid = child.pid();
    // PVT-G5-030: reap grandchildren too — the bare `child.kill()` only
    // kills the sidecar root, leaving grandchildren (native hotkey
    // binary, model subprocesses) orphaned. Call `kill_process_tree`
    // BEFORE `child.kill()` so the root is still alive when we walk
    // `pgrep -P <pid>` (on Unix, killing the root first would reparent
    // the children to init and break the descendant walk).
    //
    // XV-136 (High): spawn_blocking wrap — see the comment in the
    // Terminated arm above. The blocking `pgrep` + `kill` walk +
    // 200ms `std::thread::sleep` inside `kill_process_tree` must not
    // stall a Tokio worker thread.
    let _ = tauri::async_runtime::spawn_blocking(move || {
        crate::state::kill_process_tree(pid)
    })
    .await;
    // G4-M-61: log the kill error for visibility (mirrors the dev-mode
    // path's kill-error logging below). The previous `let _ = child.kill();`
    // silently dropped the kill error — a stuck sidecar that won't die
    // was invisible in the log.
    if let Err(e) = child.kill() {
        log::warn!("[SIDECAR] failed to kill child after deadline: {}", e);
    }
    Err(format!(
        "sidecar did not emit server_started within {}ms. stdout so far: {}",
        SERVER_STARTED_TIMEOUT_MS, stdout_buf
    ))
}

/// ADR-0020 §14: dev-mode spawn — runs the Python sidecar as a plain
/// `python -m voice_typer.server.ipc_server --ws` process (no Nuitka
/// freeze, no `externalBin`). The developer must have `voice_typer`
/// importable in their Python environment.
///
/// Per-platform Python binary name:
/// - Windows: `python.exe` (spec §14 says `pythonw.exe` would suppress
///   the console window, but we use `python.exe` to surface logs).
/// - macOS / Linux: `python3`.
pub(crate) async fn spawn_sidecar_dev_mode(token: &str) -> Result<(u16, SidecarHandle), String> {
    let python_bin = if cfg!(target_os = "windows") { "python.exe" } else { "python3" };

    // ADR-0020 §14: `VOICE_TYPER_NATIVE_DIR` points to the source-tree
    // native binary dir so the sidecar finds the dev-mode native
    // binaries (windows-key-listener / macos-key-listener / linux-key-listener).
    // We resolve relative to the current working directory (which is the
    // project root under `cargo tauri dev`).
    let native_dir = std::env::current_dir()
        .map(|p| p.join("voice_typer").join("server").join("native"))
        .map_err(|e| format!("cwd failed: {e}"))?;

    let mut cmd = tokio::process::Command::new(python_bin);
    cmd.args(["-m", "voice_typer.server.ipc_server", "--ws"])
        .env("TAURI_SIDECAR", "1")
        .env("VOICE_TYPER_IPC_TOKEN", token)
        .env("VOICE_TYPER_NATIVE_DIR", native_dir.to_string_lossy().to_string())
        // GT-20: set VOICE_TYPER_DEBUG=1 so the Python sidecar enables
        // verbose debug logging (its `log.py` checks this env var).
        // Previously this set only `RUST_LOG=debug`, which is
        // meaningless for a Python child (Python doesn't read
        // `RUST_LOG`) — it only affected native Rust binaries the
        // sidecar might spawn. Keep `RUST_LOG=debug` too so those
        // native children stay verbose in dev mode.
        .env("VOICE_TYPER_DEBUG", "1")
        .env("RUST_LOG", "debug")
        .stdout(std::process::Stdio::piped())
        // Dev mode: inherit stderr so the developer sees Python
        // tracebacks in the `cargo tauri dev` console.
        .stderr(std::process::Stdio::inherit())
        // Ensure the dev sidecar dies with the host (no zombie python).
        // GT-A2-4: dev-mode equivalent of the release-mode kill-on-drop
        // requirement (see the GT-A2-4 note on `spawn_sidecar_release`).
        .kill_on_drop(true);

    let mut child = cmd
        .spawn()
        .map_err(|e| format!("failed to spawn dev sidecar ({}): {e}", python_bin))?;

    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "dev sidecar stdout not captured".to_string())?;
    let mut reader = tokio::io::BufReader::new(stdout);

    let deadline = Instant::now() + Duration::from_millis(SERVER_STARTED_TIMEOUT_MS);
    let mut stdout_buf = String::new();
    while Instant::now() < deadline {
        let mut line = String::new();
        match tokio::time::timeout(Duration::from_millis(500), reader.read_line(&mut line)).await {
            Ok(Ok(0)) => {
                return Err("dev sidecar stdout closed before server_started".into());
            }
            Ok(Ok(_)) => {
                stdout_buf.push_str(&line);
                if let Some(port) = parse_server_started(&line) {
                    log::info!("[SIDECAR-DEV] server_started port={}", port);
                    return Ok((port, SidecarHandle::DevMode(child)));
                }
                log::warn!(
                    "[SIDECAR-DEV] unexpected stdout line (expected only server_started): {}",
                    line.trim()
                );
            }
            Ok(Err(e)) => {
                return Err(format!("dev sidecar stdout read error: {e}"));
            }
            Err(_) => continue, // per-iteration timeout — retry until deadline
        }
    }
    // PVT-G5-030: same fix as the release path — reap grandchildren
    // before killing the root. `tokio::process::Child::id()` returns
    // `Option<u32>` (None if the child has already been reaped).
    let pid_opt = child.id();
    if let Some(pid) = pid_opt {
        // XV-136 (High): spawn_blocking wrap — see the comment in the
        // release-path Terminated arm above. Mirrors the
        // `SidecarHandle::kill_tree` pattern in `state.rs:148`.
        let _ = tauri::async_runtime::spawn_blocking(move || {
            crate::state::kill_process_tree(pid)
        })
        .await;
    }
    // G4-M-61: log the kill error for visibility (mirrors the release
    // path's kill-error logging above).
    if let Err(e) = child.kill().await {
        log::warn!("[SIDECAR-DEV] failed to kill child after deadline: {}", e);
    }
    Err(format!(
        "dev sidecar did not emit server_started within {}ms. stdout so far: {}",
        SERVER_STARTED_TIMEOUT_MS, stdout_buf
    ))
}

/// Shared stdout-line parser used by both the release-path
/// (`spawn_sidecar_release`) and dev-mode-path (`spawn_sidecar_dev_mode`)
/// stdout-reading loops. Returns the port if `line` is the
/// `{"event":"server_started","port":N}` JSON line, else `None`.
///
/// GT-D3-2: the port field is parsed via `u16::try_from(p).ok()` instead
/// of `p as u16`. The previous `as u16` cast silently truncated any port
/// value above 65535 (e.g. a corrupted `port: 70000` JSON would wrap to
/// `70000_u32 as u16 = 4464`). `try_from` returns `Err` for out-of-range
/// values, which `.ok()` maps to `None`.
pub(crate) fn parse_server_started(line: &str) -> Option<u16> {
    let v: Value = serde_json::from_str(line.trim()).ok()?;
    if v.get("event").and_then(|e| e.as_str()) == Some("server_started") {
        v.get("port")
            .and_then(|p| p.as_u64())
            // GT-D3-2: try_from instead of truncating `as u16`.
            .and_then(|p| u16::try_from(p).ok())
    } else {
        None
    }
}

pub(crate) fn prewarm_resource_path(app: &tauri::AppHandle) -> Result<String, String> {
    let resource = app
        .path()
        .resource_dir()
        .map_err(|e| format!("resource_dir failed: {e}"))?;
    // ADR-0020 §4.1: target triple suffix on the binary name.
    let triple = current_target_triple();
    let suffix = if cfg!(windows) { ".exe" } else { "" };
    let name = format!("prewarm-{}{}", triple, suffix);
    Ok(resource.join(name).to_string_lossy().to_string())
}

pub(crate) fn current_target_triple() -> String {
    target_triple_for(std::env::consts::ARCH, std::env::consts::OS)
}

/// Pure form of `current_target_triple` for unit testing — accepts
/// arch+os as args so tests can verify all (arch, os) combos without
/// running on each platform. Returns the same triple strings the
/// `tauri-plugin-shell` `externalBin` mechanism expects as the binary
/// name suffix (see ADR-0020 §4.1).
pub(crate) fn target_triple_for(arch: &str, os: &str) -> String {
    match (arch, os) {
        ("x86_64", "windows") => "x86_64-pc-windows-msvc".into(),
        ("aarch64", "windows") => "aarch64-pc-windows-msvc".into(),
        ("x86_64", "macos") => "x86_64-apple-darwin".into(),
        ("aarch64", "macos") => "aarch64-apple-darwin".into(),
        ("x86_64", "linux") => "x86_64-unknown-linux-gnu".into(),
        ("aarch64", "linux") => "aarch64-unknown-linux-gnu".into(),
        _ => format!("{}-unknown-{}", arch, os),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── parse_server_started ──────────────────────────────────────────

    #[test]
    fn test_parse_server_started_valid() {
        let line = r#"{"event":"server_started","port":12345}"#;
        assert_eq!(parse_server_started(line), Some(12345));
    }

    #[test]
    fn test_parse_server_started_wrong_event() {
        let line = r#"{"event":"other","port":12345}"#;
        assert_eq!(parse_server_started(line), None);
    }

    #[test]
    fn test_parse_server_started_no_port() {
        let line = r#"{"event":"server_started"}"#;
        assert_eq!(parse_server_started(line), None);
    }

    #[test]
    fn test_parse_server_started_port_zero() {
        // Port 0 is technically valid (sidecar shouldn't emit it, but
        // the parser shouldn't reject it either — port-as-u64 → 0u16).
        let line = r#"{"event":"server_started","port":0}"#;
        assert_eq!(parse_server_started(line), Some(0));
    }

    // ── GT-D3-2: u16::try_from instead of truncating `as u16` ────────

    #[test]
    fn test_parse_server_started_port_above_u16_max_returns_none() {
        // GT-D3-2: a port value above u16::MAX (65535) must return None
        // instead of silently truncating. Previously `p as u16` would
        // wrap 70000 → 4464.
        let line = r#"{"event":"server_started","port":70000}"#;
        assert_eq!(
            parse_server_started(line),
            None,
            "GT-D3-2: port=70000 must return None (not truncate to 4464)"
        );
    }

    #[test]
    fn test_parse_server_started_port_u16_max_passthrough() {
        // u16::MAX (65535) is the upper bound of valid ports.
        let line = r#"{"event":"server_started","port":65535}"#;
        assert_eq!(parse_server_started(line), Some(65535));
    }

    #[test]
    fn test_parse_server_started_port_u64_max_returns_none() {
        // An absurdly large port must also return None.
        let line = r#"{"event":"server_started","port":18446744073709551615}"#;
        assert_eq!(parse_server_started(line), None);
    }

    #[test]
    fn test_parse_server_started_invalid_json() {
        assert_eq!(parse_server_started("not json"), None);
        assert_eq!(parse_server_started(""), None);
        assert_eq!(parse_server_started("  "), None);
    }

    #[test]
    fn test_parse_server_started_extra_fields() {
        let line = r#"{"event":"server_started","port":8080,"pid":1234,"ws_path":"/ws"}"#;
        assert_eq!(parse_server_started(line), Some(8080));
    }

    // ── is_dev_mode_for ───────────────────────────────────────────────

    #[test]
    fn test_is_dev_mode_for() {
        assert!(!is_dev_mode_for(None), "unset → not dev mode");
        assert!(!is_dev_mode_for(Some("0")), "\"0\" → not dev mode");
        assert!(!is_dev_mode_for(Some("")), "empty → not dev mode");
        assert!(!is_dev_mode_for(Some("yes")), "\"yes\" → not dev mode");
        assert!(!is_dev_mode_for(Some("true")), "\"true\" → not dev mode");
        assert!(!is_dev_mode_for(Some("2")), "\"2\" → not dev mode");
        assert!(is_dev_mode_for(Some("1")), "\"1\" → dev mode");
    }

    // ── CR-13: target_triple_for (ADR-0020 §4.1) ─────────────────────

    #[test]
    fn test_target_triple_for_all_known_combos() {
        // ADR-0020 §4.1: every supported (arch, os) combo must map to
        // the exact triple string Tauri's `externalBin` mechanism
        // expects as the per-platform binary name suffix.
        assert_eq!(target_triple_for("x86_64", "windows"), "x86_64-pc-windows-msvc");
        assert_eq!(target_triple_for("aarch64", "windows"), "aarch64-pc-windows-msvc");
        assert_eq!(target_triple_for("x86_64", "macos"), "x86_64-apple-darwin");
        assert_eq!(target_triple_for("aarch64", "macos"), "aarch64-apple-darwin");
        assert_eq!(target_triple_for("x86_64", "linux"), "x86_64-unknown-linux-gnu");
        assert_eq!(target_triple_for("aarch64", "linux"), "aarch64-unknown-linux-gnu");
    }

    #[test]
    fn test_target_triple_for_unknown_combo_fallback() {
        // Unknown (arch, os) combos fall back to the synthetic
        // "<arch>-unknown-<os>" string so a future platform isn't a
        // hard crash at sidecar spawn time (it'll just fail later when
        // Tauri can't find the binary).
        assert_eq!(
            target_triple_for("riscv64", "freebsd"),
            "riscv64-unknown-freebsd"
        );
        assert_eq!(
            target_triple_for("wasm32", "unknown"),
            "wasm32-unknown-unknown"
        );
    }

    #[test]
    fn test_current_target_triple_matches_runtime() {
        // The host's actual (arch, os) at runtime must be one of the
        // known combos (so `externalBin` can resolve the per-platform
        // binary). If this fails, a new platform was added to the build
        // matrix without updating the match in `target_triple_for`.
        let triple = current_target_triple();
        let known = [
            "x86_64-pc-windows-msvc",
            "aarch64-pc-windows-msvc",
            "x86_64-apple-darwin",
            "aarch64-apple-darwin",
            "x86_64-unknown-linux-gnu",
            "aarch64-unknown-linux-gnu",
        ];
        assert!(
            known.contains(&triple.as_str()),
            "current_target_triple returned unknown triple: {} \
             (update target_triple_for's match arms)",
            triple
        );
    }
}
