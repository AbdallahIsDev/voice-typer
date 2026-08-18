//! Dev-mode sidecar spawn (ADR-0020 §14) — extracted from the former
//! single-file `sidecar/spawn.rs` (EO-33 split).

use crate::state::SidecarHandle;
use crate::util::{SERVER_STARTED_POLL_INTERVAL_MS, SERVER_STARTED_TIMEOUT_MS};
use std::sync::atomic::AtomicBool;
use std::time::{Duration, Instant};
use tokio::io::AsyncBufReadExt;

use super::env_allowlist::passthrough_env_allowlist;
use super::handshake::{is_shutting_down, parse_server_started};

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

/// ADR-0020 §14: dev-mode spawn — runs the Python sidecar as a plain
/// `python -m voice_typer.server.ipc_server --ws` process (no Nuitka
/// freeze, no `externalBin`). The developer must have `voice_typer`
/// importable in their Python environment.
///
/// Per-platform Python binary name:
/// - Windows: `python.exe` (spec §14 says `pythonw.exe` would suppress
///   the console window, but we use `python.exe` to surface logs).
/// - macOS / Linux: `python3`.
pub(crate) async fn spawn_sidecar_dev_mode(
    token: &str,
    shutting_down: Option<&AtomicBool>,
) -> Result<(u16, SidecarHandle), String> {
    let python_bin = if cfg!(target_os = "windows") {
        "python.exe"
    } else {
        "python3"
    };

    // ADR-0020 §14: `VOICE_TYPER_NATIVE_DIR` points to the source-tree
    // native binary dir so the sidecar finds the dev-mode native
    // binaries (windows-key-listener / macos-key-listener / linux-key-listener).
    // We resolve relative to the current working directory (which is the
    // project root under `cargo tauri dev`).
    let native_dir = std::env::current_dir()
        .map(|p| p.join("voice_typer").join("server").join("native"))
        .map_err(|e| format!("cwd failed: {e}"))?;

    let mut cmd = tokio::process::Command::new(python_bin);
    // Clear inherited host env BEFORE adding the
    // voice-typer-specific vars (mirrors the release path above).
    // Dev mode also adds `VOICE_TYPER_DEBUG=1` + (when unset)
    // `RUST_LOG=debug` for verbose native-child logging during
    // `cargo tauri dev`.
    cmd.args(["-m", "voice_typer.server.ipc_server", "--ws"])
        .env_clear()
        .envs(passthrough_env_allowlist())
        .env("TAURI_SIDECAR", "1")
        .env("VOICE_TYPER_IPC_TOKEN", token)
        // GT-68: share the host's per-process session ID so the
        // Python sidecar's log lines carry the same join key as the
        // Rust host's (cross-process log correlation). The Python
        // `log/__init__.py` prefers this env var when set, falling
        // back to generating its own.
        .env("VOICE_TYPER_SESSION_ID", crate::util::session_id())
        .env(
            "VOICE_TYPER_NATIVE_DIR",
            native_dir.to_string_lossy().to_string(),
        )
        // mirror the release-path env-var set so dev mode
        // doesn't silently diverge. Previously dev mode was missing
        // the prewarm exe env var (so the prewarm scheduled-task
        // integration couldn't be exercised under `cargo tauri dev`).
        //
        // Prewarm binary removal (Phase 2a, plan-runtime-pack-split
        // 6.2): the prewarm exe env var is no longer
        // set in either release or dev mode - the prewarm binary is
        // deleted (Sub-agent 6), the Rust-side `dev_prewarm_exe` /
        // `prewarm_resource_path` helpers are deleted, and the
        // prewarm phase moved INTO the worker exe (Option P-1).
        // `VOICE_TYPER_CONFIG_DIR` still propagates so the dev-mode
        // Python sidecar reads the same config dir as the release
        // sidecar.
        .env(
            "VOICE_TYPER_CONFIG_DIR",
            crate::platform::paths::config_dir()
                .to_string_lossy()
                .to_string(),
        )
        // set VOICE_TYPER_DEBUG=1 so the Python sidecar enables
        // verbose debug logging (its `log.py` checks this env var).
        // Previously this set only `RUST_LOG=debug`, which is
        // meaningless for a Python child (Python doesn't read
        // `RUST_LOG`) — it only affected native Rust binaries the
        // sidecar might spawn. Keep `RUST_LOG=debug` too so those
        // native children stay verbose in dev mode.
        //
        // only set `RUST_LOG=debug` when the env var is
        // unset, so a developer who exports `RUST_LOG=warn` (or any
        // other level) from their shell to silence a noisy crate
        // doesn't have their preference clobbered by the dev spawn
        // path. The release path doesn't set `RUST_LOG` at all (it's
        // not in the explicit env list at line 241-244 above), so
        // this dev-only default is the only place the override could
        // previously fire.
        .env("VOICE_TYPER_DEBUG", "1");
    if std::env::var_os("RUST_LOG").is_none() {
        cmd.env("RUST_LOG", "debug");
    }
    cmd.stdout(std::process::Stdio::piped())
        // Dev mode: inherit stderr so the developer sees Python
        // tracebacks in the `cargo tauri dev` console.
        .stderr(std::process::Stdio::inherit())
        // Ensure the dev sidecar dies with the host (no zombie python).
        // dev-mode equivalent of the release-mode kill-on-drop
        // requirement (see the note on `spawn_sidecar_release`).
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
        // Same shutting_down short-circuit as the release path
        // (see `spawn_sidecar_release`). The dev-mode sidecar is
        // typically faster to emit `server_started` (no Nuitka
        // unpack), but a cold Python import on the first run can take
        // 5-10s — long enough for a user-initiated quit to race the
        // handshake. The dev-mode `tokio::process::Child` was
        // constructed with `kill_on_drop(true)`, so dropping it would
        // eventually kill the process — but we kill explicitly here
        // (and wait via `child.wait()`) so the test environment
        // doesn't see a zombie between this return and the eventual
        // Drop.
        if is_shutting_down(shutting_down) {
            log::info!(
                "[SIDECAR-DEV] shutting_down set during stdout-read loop — killing freshly-spawned dev child"
            );
            let pid_opt = child.id();
            if let Some(pid) = pid_opt {
                let _ = tauri::async_runtime::spawn_blocking(move || {
                    crate::platform::process::kill_process_tree(pid)
                })
                .await;
            }
            if let Err(e) = child.kill().await {
                log::warn!(
                    "[SIDECAR-DEV] failed to kill child after shutting_down detected (best-effort): {}",
                    e
                );
            }
            let _ = tokio::time::timeout(Duration::from_millis(500), child.wait()).await;
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
    // same fix as the release path — reap grandchildren
    // before killing the root. `tokio::process::Child::id()` returns
    // `Option<u32>` (None if the child has already been reaped).
    let pid_opt = child.id();
    if let Some(pid) = pid_opt {
        // (High): spawn_blocking wrap — see the comment in the
        // release-path Terminated arm above. Mirrors the
        // `SidecarHandle::kill_tree` pattern in `state.rs:148`.
        let _ = tauri::async_runtime::spawn_blocking(move || {
            crate::platform::process::kill_process_tree(pid)
        })
        .await;
    }
    // log the kill error for visibility (mirrors the release
    // path's kill-error logging above).
    if let Err(e) = child.kill().await {
        log::warn!("[SIDECAR-DEV] failed to kill child after deadline: {}", e);
    }
    // reap the zombie. `tokio::process::Child::kill(&mut self)`
    // sends SIGKILL but does NOT call waitpid — the killed child remains
    // a zombie in the OS process table until `wait()` (or `try_wait()`)
    // is invoked. `kill_on_drop(true)` ensures Drop will eventually reap,
    // but Drop fires on function return; if the function panics or the
    // async runtime is shut down before return, the zombie leaks. Call
    // `wait()` explicitly with a 500ms timeout to bound the spawn path
    // against a misbehaving process that ignores SIGKILL (rare, but
    // possible for uninterruptible kernel waits). Best-effort: errors
    // and timeouts are silently discarded (the kill has already been
    // attempted).
    let _ = tokio::time::timeout(Duration::from_millis(500), child.wait()).await;
    Err(format!(
        "dev sidecar did not emit server_started within {}ms. stdout so far: {}",
        SERVER_STARTED_TIMEOUT_MS, stdout_buf
    ))
}
