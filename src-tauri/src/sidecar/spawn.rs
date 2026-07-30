//! Sidecar spawn + stdout handshake (ADR-0020 §1 + §4.1 + §14).
//!
//! Both spawn paths call `.env_clear()` before
//! adding specific env vars, then re-add only an explicit OS-required
//! allowlist via `passthrough_env_allowlist()`. This prevents the
//! sidecar from inheriting arbitrary host env vars (e.g. `HF_TOKEN`,
//! `OPENAI_API_KEY`, `http_proxy`) exported from the user's shell.

use crate::state::SidecarHandle;
// DT-44: 500ms server-started poll interval is now the named constant
// `SERVER_STARTED_POLL_INTERVAL_MS` in `util.rs` (was duplicated inline
// at `spawn.rs:280` and `spawn.rs:495`).
use crate::util::{SERVER_STARTED_POLL_INTERVAL_MS, SERVER_STARTED_TIMEOUT_MS};
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

/// Conservative OS-env allowlist passed through to
/// the sidecar after `.env_clear()`. The sidecar process should NOT
/// inherit arbitrary host env vars (e.g. `HF_TOKEN`, `OPENAI_API_KEY`
/// exported from the user's shell, `http_proxy` from a corporate
/// machine) — only the OS-required vars it needs to function plus the
/// voice-typer-specific vars already added explicitly via `.env(...)`
/// calls in `spawn_sidecar_release` / `spawn_sidecar_dev_mode`.
///
/// Conservative by design: when in doubt, prefer to pass FEWER vars.
/// Missing vars produce loud failures (Python can't find its home dir,
/// X11 can't find DISPLAY, etc.) that are easy to debug; leaked vars
/// produce silent security holes.
///
/// Allowlist (mirrors the Python side's similar `os.environ` filtering
/// in `voice_typer/server/app.py:main()` for the renderer-driven
/// restart path — though that filter is more permissive because the
/// Python side runs as the user, not as a sandboxed child):
///
/// Always-pass (cross-platform OS infrastructure):
///   `PATH`, `USER`, `LANG`, `TEMP`, `TMP`, `TMPDIR`
///
/// POSIX-only: `HOME`
/// Windows-only: `USERPROFILE`, `SYSTEMROOT`
///
/// Locale category overrides: any var matching `LC_*` (e.g.
/// `LC_ALL`, `LC_CTYPE`, `LC_MESSAGES`).
///
/// Linux-only (GUI + session bus): `DISPLAY`, `WAYLAND_DISPLAY`,
/// `XDG_RUNTIME_DIR`, `XDG_DATA_HOME`, `XDG_CONFIG_HOME`,
/// `DBUS_SESSION_BUS_ADDRESS`. Without these the sidecar's tray icon
/// (Qt/GTK) and audio subsystem (PulseAudio → DBUS) would fail.
///
/// macOS-only (LaunchAgent identity): `XPC_SERVICE_NAME` (only
/// relevant when the host is launched by `launchd`; harmless otherwise
/// — the var is unset in normal Tauri launches).
///
/// Voice-typer-specific vars (`TAURI_SIDECAR`, `VOICE_TYPER_IPC_TOKEN`,
/// `VOICE_TYPER_NATIVE_DIR`, `VOICE_TYPER_PREWARM_EXE`,
/// `VOICE_TYPER_CONFIG_DIR`, `VOICE_TYPER_DEBUG`, `RUST_LOG`) are
/// added explicitly by the spawn callers AFTER this function returns —
/// they are NOT in the allowlist (they take precedence over any host
/// value via the subsequent `.env(...)` call).
///
/// Returns a `Vec<(OsString, OsString)>` (not a `HashMap`) because
/// both `tauri_plugin_shell::process::Command::envs` and
/// `tokio::process::Command::envs` accept an iterator of `(K, V)`
/// pairs and a Vec preserves insertion order for debuggability.
pub(crate) fn passthrough_env_allowlist(
) -> Vec<(std::ffi::OsString, std::ffi::OsString)> {
    let mut out: Vec<(std::ffi::OsString, std::ffi::OsString)> = Vec::new();

    // ── Always-pass (cross-platform) ───────────────────────────────
    const ALWAYS: &[&str] = &[
        "PATH",
        "USER",
        "LANG",
        "TEMP",
        "TMP",
        "TMPDIR",
    ];
    for name in ALWAYS {
        if let Some(val) = std::env::var_os(name) {
            out.push((std::ffi::OsString::from(name), val));
        }
    }

    // ── POSIX: HOME ────────────────────────────────────────────────
    #[cfg(unix)]
    if let Some(val) = std::env::var_os("HOME") {
        out.push((std::ffi::OsString::from("HOME"), val));
    }

    // ── Windows: USERPROFILE + SYSTEMROOT ──────────────────────────
    #[cfg(windows)]
    if let Some(val) = std::env::var_os("USERPROFILE") {
        out.push((std::ffi::OsString::from("USERPROFILE"), val));
    }
    #[cfg(windows)]
    if let Some(val) = std::env::var_os("SYSTEMROOT") {
        out.push((std::ffi::OsString::from("SYSTEMROOT"), val));
    }

    // ── Locale category overrides (LC_*) ──────────────────────────
    // Walk the live env so we pick up whatever LC_* categories the
    // user has set (LC_ALL, LC_CTYPE, LC_MESSAGES, LC_TIME, …). The
    // sidecar's Python `locale` module + gettext translations depend
    // on these.
    for (name, val) in std::env::vars_os() {
        if let Some(s) = name.to_str() {
            if s.starts_with("LC_") {
                out.push((name, val));
            }
        }
    }

    // ── Linux: GUI + session bus ──────────────────────────────────
    #[cfg(target_os = "linux")]
    {
        const LINUX_GUI: &[&str] = &[
            "DISPLAY",
            "WAYLAND_DISPLAY",
            "XDG_RUNTIME_DIR",
            "XDG_DATA_HOME",
            "XDG_CONFIG_HOME",
            "DBUS_SESSION_BUS_ADDRESS",
        ];
        for name in LINUX_GUI {
            if let Some(val) = std::env::var_os(name) {
                out.push((std::ffi::OsString::from(name), val));
            }
        }
    }

    // ── macOS: LaunchAgent identity ───────────────────────────────
    // Only set when running under launchd (LaunchAgent/LaunchDaemon).
    // Harmless to pass through when unset (None branch is skipped).
    #[cfg(target_os = "macos")]
    if let Some(val) = std::env::var_os("XPC_SERVICE_NAME") {
        out.push((std::ffi::OsString::from("XPC_SERVICE_NAME"), val));
    }

    out
}

/// ADR-0020 §1 + §4.1: release-build spawn via `externalBin`. Wraps
/// the resulting `CommandChild` in `SidecarHandle::ShellPlugin`.
///
/// Kill-on-parent-exit guarantee: the release-mode ShellPlugin sidecar's
/// `CommandChild` does NOT kill the OS process on Drop. If the host
/// crashes (segfault, OOM kill, `kill -9`), the sidecar Python process
/// would be orphaned and keep running with the mic / IPC port / native
/// hotkey binary held. To prevent this, `spawn_sidecar_release`
/// registers a kill-on-parent-exit guarantee via the platform helper
/// `crate::platform::process::register_kill_on_parent_exit(pid)` right
/// after `cmd.spawn()` below. The platform helper implements the
/// OS-specific machinery:
///   - POSIX: `prctl(PR_SET_PDEATHSIG, SIGKILL)` via a `pre_exec`-style
///     post-spawn syscall on the child's pid.
///   - Windows: assign the sidecar to a Job Object with
///     `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`.
/// Best-effort: errors are logged but do NOT abort the spawn (the
/// sidecar is already running — killing the host's spawn path wouldn't
/// help). The dev-mode path is already covered by `kill_on_drop(true)`
/// (see `spawn_sidecar_dev_mode`).
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

    // Clear inherited host env BEFORE adding the
    // voice-typer-specific vars. Without this, the sidecar inherits
    // arbitrary host env vars (e.g. `HF_TOKEN`, `OPENAI_API_KEY`,
    // `http_proxy`) — a leak surface for credentials + a configuration
    // surprise surface (the sidecar would see unrelated host exports).
    // The `passthrough_env_allowlist()` re-adds only the OS-required
    // vars the sidecar needs to function (PATH, HOME, locale, etc.).
    let cmd = sidecar
        .args(["--ws"])
        .env_clear()
        .envs(passthrough_env_allowlist())
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

    // Register a kill-on-parent-exit guarantee so the OS reaps the
    // orphan sidecar when the host dies. Best-effort: errors are logged
    // but do NOT abort the spawn (the sidecar is already running —
    // killing the host's spawn path wouldn't help). The dev-mode path
    // is already covered by `kill_on_drop(true)` (see
    // `spawn_sidecar_dev_mode`).
    //
    // NOTE: `child.pid()` returns `u32` directly (NOT `Option<u32>`)
    // for the shell-plugin child — it always has a pid once spawned.
    let sidecar_pid = child.pid();
    if let Err(e) = crate::platform::process::register_kill_on_parent_exit(sidecar_pid) {
        log::warn!(
            "[SIDECAR] failed to register kill-on-parent-exit for pid {} \
             (best-effort — sidecar will run but may be orphaned on host crash): {}",
            sidecar_pid,
            e
        );
    }

    // ADR-0020 §1: read stdout until we parse the server_started JSON.
    // The sidecar force-sets stdout to line-buffered (sidecar_ws.py
    // `_force_line_buffered_stdout`), so each `print(..., flush=True)`
    // arrives as one event.
    let deadline = Instant::now() + Duration::from_millis(SERVER_STARTED_TIMEOUT_MS);
    let mut stdout_buf = String::new();

    while Instant::now() < deadline {
        match tokio::time::timeout(
            Duration::from_millis(SERVER_STARTED_POLL_INTERVAL_MS),
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
                        // ER-66: `.into_owned()` reuses the inner String
                        // when the Cow is Owned (invalid UTF-8 case —
                        // Python sidecar stderr can contain non-UTF-8
                        // bytes from a C extension traceback). The prior
                        // `.to_string()` form always allocated a new
                        // String, even when the Cow was already Owned.
                        String::from_utf8_lossy(&bytes).into_owned()
                    }
                    CommandEvent::Stderr(bytes) => {
                        // Log stderr but don't parse it as server_started.
                        // ER-66: same `.into_owned()` rationale as above.
                        // UE-3-F9: demoted from `info!` to `debug!`. The
                        // sidecar's stderr can be extremely chatty (Python
                        // warning frames, native hotkey binary debug
                        // prints, ctranslate2 device-info dumps). Logging
                        // every line at INFO swamped the host log and
                        // drowned genuinely actionable diagnostics.
                        // The Python side's own `log.py` already routes
                        // warnings/errors to its separate log file — the
                        // host-side INFO echo was redundant with that.
                        // `debug!` keeps the lines available under
                        // `RUST_LOG=debug` without polluting the default
                        // INFO stream.
                        let s = String::from_utf8_lossy(&bytes).into_owned();
                        log::debug!("[SIDECAR] stderr: {}", s.trim());
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
    // UE-3-F4: reap the zombie. `child.kill()` sends the kill signal but
    // does NOT itself waitpid — the Tauri shell-plugin's internal exit
    // watcher delivers a `CommandEvent::Terminated` to `rx` once the OS
    // reports the process has died. Without draining `rx` here, the
    // `Terminated` event sits unread in the channel buffer until `rx`
    // is dropped on function return; depending on the shell-plugin
    // version's exit-watcher scheduling, the host-side waitpid may be
    // deferred until that drain happens — leaving a brief zombie window.
    // The 500ms timeout bounds the spawn path against a misbehaving
    // process that ignores the kill signal (rare, but possible for
    // uninterruptible kernel waits). Best-effort: errors and timeouts
    // are silently discarded (the kill has already been attempted; we
    // cannot do anything more useful with a drain failure).
    let _ = tokio::time::timeout(Duration::from_millis(500), rx.recv()).await;
    Err(format!(
        "sidecar did not emit server_started within {}ms. stdout so far: {}",
        SERVER_STARTED_TIMEOUT_MS, stdout_buf
    ))
}

/// XZ-R4-011: dev-mode counterpart of `prewarm_resource_path` (which
/// needs an `AppHandle` and so can't be called from
/// `spawn_sidecar_dev_mode`). Resolves the prewarm exe path relative
/// to the source-tree root (the dev-mode cwd under `cargo tauri dev`)
/// so the Python sidecar's prewarm scheduled-task integration sees the
/// same env var the release path provides.
///
/// Returns the empty string when the path can't be constructed (only
/// fails when `current_dir()` itself errors, which is rare). An empty
/// string is a safe sentinel — the Python side's prewarm integration
/// treats a missing/empty `VOICE_TYPER_PREWARM_EXE` as "prewarm
/// disabled" (it checks `Path(exe).is_file()` before spawning), so no
/// crash follows. A warning is logged in that case so the developer
/// knows prewarm is disabled in this dev session.
fn dev_prewarm_exe() -> String {
    let triple = current_target_triple();
    let suffix = if cfg!(windows) { ".exe" } else { "" };
    let name = format!("prewarm-{}{}", triple, suffix);
    match std::env::current_dir() {
        Ok(cwd) => cwd
            .join("voice_typer")
            .join("server")
            .join("native")
            .join(name)
            .to_string_lossy()
            .to_string(),
        Err(e) => {
            log::warn!(
                "[SIDECAR-DEV] could not resolve cwd for VOICE_TYPER_PREWARM_EXE (prewarm disabled): {}",
                e
            );
            String::new()
        }
    }
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
        .env("VOICE_TYPER_NATIVE_DIR", native_dir.to_string_lossy().to_string())
        // XZ-R4-011: mirror the release-path env-var set so dev mode
        // doesn't silently diverge. Previously dev mode was missing
        // `VOICE_TYPER_PREWARM_EXE` (so the prewarm scheduled-task
        // integration couldn't be exercised under `cargo tauri dev`)
        // and hardcoded `RUST_LOG=debug` (which overrode any user-set
        // `RUST_LOG` value, breaking the developer's ability to
        // silence noisy crates via `RUST_LOG=warn`). We resolve the
        // prewarm exe via `dev_prewarm_exe()` (the dev-mode
        // counterpart of `prewarm_resource_path`) so the dev
        // sidecar's prewarm integration sees a real path; if the
        // path can't be resolved (rare — only fails when `cwd()`
        // errors), `dev_prewarm_exe()` returns an empty string and
        // logs a warning so the developer knows prewarm is disabled
        // in this dev session.
        .env("VOICE_TYPER_PREWARM_EXE", dev_prewarm_exe())
        // GT-20: set VOICE_TYPER_DEBUG=1 so the Python sidecar enables
        // verbose debug logging (its `log.py` checks this env var).
        // Previously this set only `RUST_LOG=debug`, which is
        // meaningless for a Python child (Python doesn't read
        // `RUST_LOG`) — it only affected native Rust binaries the
        // sidecar might spawn. Keep `RUST_LOG=debug` too so those
        // native children stay verbose in dev mode.
        //
        // XZ-R4-011: only set `RUST_LOG=debug` when the env var is
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
        match tokio::time::timeout(Duration::from_millis(SERVER_STARTED_POLL_INTERVAL_MS), reader.read_line(&mut line)).await {
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
    // UE-3-F4: reap the zombie. `tokio::process::Child::kill(&mut self)`
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
            // UE-3-F7: reject port 0. A sidecar that has successfully
            // bound a real port never reports 0 in its `server_started`
            // handshake (the value comes from `socket.getsockname()[1]`
            // AFTER bind succeeds). A `port: 0` is therefore always a
            // bug (uninitialized field, JSON-schema drift, or a
            // hostile/malformed input). Returning `None` here forces the
            // spawn loop to time out and surface a clear error rather
            // than handing a `0` back to `reconnect_ws` which would
            // then attempt to dial `127.0.0.1:0` and get an OS-assigned
            // unrelated connection (or an EADDRNOTAVAIL on platforms
            // that reject port 0 for connect).
            .filter(|p| *p != 0)
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
        // UE-3-F7: port 0 must be rejected (return None). A sidecar that
        // has successfully bound a real port never reports 0 in the
        // `server_started` handshake (the value comes from
        // `socket.getsockname()[1]` AFTER bind succeeds). A `port: 0` is
        // always a bug — returning None forces the spawn loop to time out
        // and surface a clear error rather than handing 0 back to
        // `reconnect_ws` which would dial `127.0.0.1:0` and get an
        // unrelated OS-assigned connection.
        let line = r#"{"event":"server_started","port":0}"#;
        assert_eq!(
            parse_server_started(line),
            None,
            "UE-3-F7: port=0 must be rejected (a real sidecar never reports 0 in the handshake)"
        );
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

    // ── passthrough_env_allowlist ──────────────────

    #[test]
    fn test_passthrough_env_allowlist_excludes_unrelated_vars() {
        // A sentinel "secret" env var set in the host process
        // must NOT appear in the allowlist (regression guard for the
        // env_clear + allowlist pattern). We set it via std::env::set_var
        // for the duration of this test — Cargo runs tests in the same
        // process by default, so the var is visible to
        // passthrough_env_allowlist's std::env::vars_os() walk.
        //
        // SAFETY: std::env::set_var is process-global and unsafe in
        // Rust 2024 (mutex concerns), but tests run single-threaded by
        // default unless `--test-threads=N` is used. The var name is
        // unique enough that it won't collide with another test's env.
        let sentinel = "VOICE_TYPER_PI2_TEST_SENTINEL_SECRET";
        std::env::set_var(sentinel, "should-not-leak");
        let allowlist = passthrough_env_allowlist();
        std::env::remove_var(sentinel);

        let names: Vec<String> = allowlist
            .iter()
            .map(|(k, _)| k.to_string_lossy().to_string())
            .collect();
        assert!(
            !names.iter().any(|n| n == sentinel),
            "PI-2 regression: sentinel env var leaked into allowlist: {:?}",
            names
        );
    }

    #[test]
    fn test_passthrough_env_allowlist_includes_path() {
        // PATH is in the ALWAYS list — it must be present (the sidecar
        // needs it to find `python3` / native hotkey binaries).
        let allowlist = passthrough_env_allowlist();
        let names: Vec<String> = allowlist
            .iter()
            .map(|(k, _)| k.to_string_lossy().to_string())
            .collect();
        // PATH is set on every sane OS; if it's somehow unset in the
        // test env, skip this assertion (don't fail the test).
        if std::env::var_os("PATH").is_some() {
            assert!(
                names.iter().any(|n| n == "PATH"),
                "PI-2: PATH missing from allowlist: {:?}",
                names
            );
        }
    }

    #[test]
    fn test_passthrough_env_allowlist_includes_lc_categories_when_set() {
        // When LC_ALL is set in the host env, it must appear in
        // the allowlist (locale category pass-through). When unset,
        // the allowlist must NOT contain it (no spurious empty values).
        let lc_all = "VOICE_TYPER_PI2_LC_ALL_DOES_NOT_EXIST";
        // Sanity: lc_all is not a real LC_* var name — use a real one.
        let real_lc = "LC_ALL";
        let was_set = std::env::var_os(real_lc).is_some();
        if !was_set {
            std::env::set_var(real_lc, "C");
        }
        let allowlist = passthrough_env_allowlist();
        if !was_set {
            std::env::remove_var(real_lc);
        }

        let names: Vec<String> = allowlist
            .iter()
            .map(|(k, _)| k.to_string_lossy().to_string())
            .collect();
        // LC_ALL should be present (either because the host had it set,
        // or because this test set it temporarily).
        assert!(
            names.iter().any(|n| n == real_lc),
            "PI-2: LC_ALL missing from allowlist (it was set during the call): {:?}",
            names
        );
        // Sentinel must NOT leak (mirrors the unrelated-vars test).
        assert!(
            !names.iter().any(|n| n == lc_all),
            "PI-2 regression: sentinel leaked: {:?}",
            names
        );
    }

    #[test]
    fn test_passthrough_env_allowlist_no_duplicates() {
        // The allowlist must not contain duplicate entries —
        // duplicates would silently override each other in the child
        // process's env map (the last write wins). The `LC_*` walk
        // could in principle produce a duplicate if a manually-added
        // var name happened to start with "LC_", but the ALWAYS list
        // is uppercase non-LC_* names so there's no overlap.
        let allowlist = passthrough_env_allowlist();
        let mut seen = std::collections::HashSet::new();
        for (k, _) in &allowlist {
            let key = k.to_string_lossy().to_string();
            assert!(
                seen.insert(key.clone()),
                "PI-2: duplicate env var in allowlist: {}",
                key
            );
        }
    }
}
