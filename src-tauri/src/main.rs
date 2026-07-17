//! Voice Typer — Tauri v2 host (ADR-0020 implementation)
//!
//! This is the Rust shell that replaces the Electron main process. It:
//!
//! 1. Generates a 256-bit bearer token (`secrets.token_bytes(32)`
//!    equivalent — see `Cargo.toml` note: despite the ADR's "HMAC"
//!    wording, the host uses bearer-token auth, not HMAC) and spawns
//!    the Python sidecar via Tauri's `externalBin` mechanism, passing
//!    `VOICE_TYPER_IPC_TOKEN` + `TAURI_SIDECAR=1` env vars.
//! 2. Reads the sidecar's stdout until it sees the
//!    `{"event":"server_started","port":N}` JSON line, then opens a
//!    WebSocket client to `ws://127.0.0.1:N`.
//! 3. Performs the bearer-token auth handshake (`{"type":"auth","token":...}`).
//! 4. Exposes ONE generic `dispatch` command to the webview:
//!    `invoke('dispatch', {cmd, data})` → Rust forwards it as a WS
//!    frame, awaits the per-id response, returns it.
//! 5. Subscribes to server-initiated events (channel 2) and re-emits
//!    them as Tauri events the React UI already subscribes to.
//! 6. Runs the FT-1 supervisor: on unexpected WS-close / sidecar
//!    exit, respawns with backoff (500ms → 1s → 2s, cap 5 retries),
//!    then falls back to full-app relaunch.
//! 7. Coalesces `bubble_level` events from ~60 Hz to ≤30 Hz to
//!    prevent WebView jank (ADR-0020 §9).
//! 8. Single-instance gate at the top of `main` — runs BEFORE any
//!    sidecar init so a second launch doesn't spawn a zombie sidecar.
//!
//! # Cross-platform
//!
//! - Windows: WebView2 (Chromium-based, system-installed on Win10+).
//! - macOS: WKWebView (Safari-based, system).
//! - Linux: webkit2gtk (system; requires `libwebkit2gtk-4.1-0`).
//!
//! The native hotkey binaries (`windows-key-listener`,
//! `macos-key-listener`, `linux-key-listener`) are NOT spawned by
//! this host — they are spawned by the Python sidecar via the
//! existing `hotkeys.py::create_hotkey_backend()` factory, which
//! preserves ADR-0007 + ADR-0008 (key suppression, Fn/Globe key,
//! Wayland support). Tauri's `global-shortcut` plugin is NOT used
//! for the dictation toggle (see ADR-0020 §6.4).

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use futures_util::{SinkExt, StreamExt};
use rand::RngCore;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::fs::OpenOptions;
use std::io::Write;
use tauri::{Emitter, Manager, PhysicalPosition, WindowEvent};
use tauri_plugin_shell::process::{CommandEvent, CommandChild};
use tauri_plugin_shell::ShellExt;
use tokio::io::AsyncBufReadExt;
use tokio::sync::{mpsc, oneshot, Mutex as AsyncMutex};
use tokio_tungstenite::{connect_async_with_config, tungstenite::Message};

// ─── Constants (ADR-0020) ─────────────────────────────────────────────

/// ADR-0020 §3: 256-bit bearer token (despite the ADR's "HMAC" wording,
/// the host uses bearer-token auth — see `Cargo.toml` note). Regenerated
/// per launch + per FT-1 respawn; never logged.
const TOKEN_BYTES: usize = 32;

/// ADR-0020 §10: FT-1 supervisor backoff schedule (ms). Cap 5 retries
/// before falling back to full-app relaunch.
const FT1_BACKOFF_MS: &[u64] = &[500, 1000, 2000, 4000, 8000];
const FT1_MAX_RETRIES: u32 = 5;

/// ADR-0020 §10: cooperative shutdown hard timeout. The sidecar must
/// ack `{"type":"shutdown"}` and exit within this window; if it
/// doesn't, the host force-kills the process tree.
const SHUTDOWN_ACK_TIMEOUT_MS: u64 = 2000;

/// ADR-0020 §1: time to wait for the `server_started` JSON on the
/// sidecar's stdout before giving up.
const SERVER_STARTED_TIMEOUT_MS: u64 = 30_000;

/// ADR-0020 §9: `bubble_level` coalesce target rate (Hz). The sidecar
/// emits at ~60 Hz; we keep only the latest {rms, peak} and emit at
/// ≤30 Hz.
const BUBBLE_LEVEL_COALESCE_HZ: u64 = 30;

/// ADR-0020 §10: 1 MiB WS frame cap. Enforced at WS-connect time via
/// `connect_async_with_config(WebSocketConfig { max_message_size:
/// Some(MAX_FRAME_BYTES), .. })`. Guards against memory-exhaustion
/// attacks from a compromised sidecar.
const MAX_FRAME_BYTES: usize = 1024 * 1024;

/// ADR-0020 §7: per-dispatch response timeout. The sidecar must respond
/// within this window or the host returns a timeout error to the webview
/// (so the UI can show a retry banner instead of hanging indefinitely).
const DISPATCH_TIMEOUT_SECS: u64 = 120;

/// ADR-0020 §10: brief delay between emitting `ft1_relaunching` and
/// calling `app.restart()`, so the webview has time to render the
/// "restarting…" banner before the process exits.
const PRE_RESTART_DELAY_MS: u64 = 500;

/// Polling interval for the cooperative-shutdown waiter in
/// `shutdown_sidecar`. We sleep in increments of this duration until
/// `SHUTDOWN_ACK_TIMEOUT_MS` elapses, then force-kill the child.
const SHUTDOWN_POLL_INTERVAL_MS: u64 = 100;

/// ADR-0020 §6.2: paste-text short/long threshold (characters). Short
/// text is injected via `enigo.text()` (IME-safe); long text is copied
/// to the clipboard then Ctrl/Cmd+V is pressed.
const PASTE_SHORT_THRESHOLD: usize = 300;

// ─── Shared state ─────────────────────────────────────────────────────

/// Pending dispatch requests keyed by id. Each entry has a oneshot
/// sender that the WS reader task fulfills when the matching response
/// arrives.
type PendingMap = Arc<AsyncMutex<HashMap<u64, oneshot::Sender<Value>>>>;

/// The WS writer half, wrapped in a channel so the dispatch command
/// (which runs on a Tauri async runtime) can send frames without
/// holding the WS writer directly.
type WsWriterTx = mpsc::UnboundedSender<Message>;

/// ADR-0020 §1 + §14: the sidecar child handle is either a Tauri
/// shell-plugin `CommandChild` (release builds, spawned via
/// `externalBin`) or a `tokio::process::Child` (dev mode, spawned via
/// `VOICE_TYPER_SIDECAR_DEV=1` running `python -m voice_typer.server.ipc_server`).
/// Both variants support `kill()`; `shutdown_sidecar` matches on the
/// variant to call the right kill method.
enum SidecarHandle {
    ShellPlugin(CommandChild),
    DevMode(tokio::process::Child),
}

impl SidecarHandle {
    /// Kill the sidecar process. Consumes `self` because
    /// `CommandChild::kill(self)` takes ownership (the shell-plugin
    /// child handle is single-use after kill). The dev-mode variant
    /// (`tokio::process::Child::kill(&mut self)`) only borrows but we
    /// consume the handle anyway for API uniformity.
    async fn kill(self) -> std::io::Result<()> {
        match self {
            SidecarHandle::ShellPlugin(c) => {
                c.kill().map_err(|e| std::io::Error::other(format!("shell-plugin kill: {e}")))
            }
            SidecarHandle::DevMode(mut c) => c.kill().await,
        }
    }
}

struct SidecarState {
    /// Child handle for kill_children backstop.
    child: Mutex<Option<SidecarHandle>>,
    /// Token for the current sidecar instance (rotated per FT-1 respawn).
    token: Mutex<String>,
    /// WS writer channel — None when the WS is disconnected.
    ws_tx: Mutex<Option<WsWriterTx>>,
    /// Pending dispatch requests (id → response sender).
    pending: PendingMap,
    /// Next request id.
    next_id: AtomicU64,
    /// Shutdown signal — set when the app is quitting so FT-1 doesn't
    /// respawn the sidecar during shutdown.
    shutting_down: AtomicBool,
    /// FT-1 respawn serialization flag. Set when a respawn is in flight
    /// so concurrent WS-reader exits (e.g., a flapping sidecar that dies
    /// immediately after reconnect) don't launch multiple parallel
    /// `ft1_respawn` supervisors that would corrupt `child`/`token`/`ws_tx`.
    /// Acquired with `compare_exchange(false → true)` on entry; cleared on
    /// exit (both Ok and restart paths).
    respawn_in_progress: AtomicBool,
    /// CR-2: Event receiver from the sidecar's `Command::spawn()`. Used by
    /// `shutdown_sidecar` to poll for `CommandEvent::Terminated` so the
    /// host exits the wait loop as soon as the sidecar acks and exits
    /// (~50ms typical), instead of blocking the full
    /// `SHUTDOWN_ACK_TIMEOUT_MS` (2000ms). Only set for the `ShellPlugin`
    /// variant (release builds); the `DevMode` variant spawns via
    /// `tokio::process::Command` which has no `CommandEvent` stream —
    /// `shutdown_sidecar` falls back to bounded sleep polling for that
    /// path.
    child_exit_rx: AsyncMutex<Option<mpsc::Receiver<CommandEvent>>>,
}

// ─── Token generation (ADR-0020 §3) ───────────────────────────────────

fn generate_token() -> String {
    let mut bytes = [0u8; TOKEN_BYTES];
    rand::thread_rng().fill_bytes(&mut bytes);
    hex::encode(&bytes)
}

mod hex {
    pub fn encode(bytes: &[u8]) -> String {
        let mut s = String::with_capacity(bytes.len() * 2);
        for b in bytes {
            s.push_str(&format!("{:02x}", b));
        }
        s
    }
}

// ─── Sidecar spawn + stdout handshake (ADR-0020 §1) ───────────────────

/// Spawn the Python sidecar via Tauri's `externalBin` mechanism and
/// read the `server_started` JSON from stdout.
///
/// Returns the bound port + the child handle on success.
async fn spawn_sidecar_and_get_port(
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
fn is_dev_mode() -> bool {
    is_dev_mode_for(std::env::var("VOICE_TYPER_SIDECAR_DEV").ok().as_deref())
}

/// Pure predicate form of `is_dev_mode` for unit testing.
fn is_dev_mode_for(value: Option<&str>) -> bool {
    value == Some("1")
}

/// ADR-0020 §1 + §4.1: release-build spawn via `externalBin`. Wraps
/// the resulting `CommandChild` in `SidecarHandle::ShellPlugin`.
async fn spawn_sidecar_release(
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
                        return Err(format!(
                            "sidecar terminated before server_started (code={:?})",
                            payload.code
                        ));
                    }
                    CommandEvent::Error(err) => {
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
    let _ = child.kill();
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
async fn spawn_sidecar_dev_mode(token: &str) -> Result<(u16, SidecarHandle), String> {
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
        .env("RUST_LOG", "debug")
        .stdout(std::process::Stdio::piped())
        // Dev mode: inherit stderr so the developer sees Python
        // tracebacks in the `cargo tauri dev` console.
        .stderr(std::process::Stdio::inherit())
        // Ensure the dev sidecar dies with the host (no zombie python).
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
    let _ = child.kill().await;
    Err(format!(
        "dev sidecar did not emit server_started within {}ms. stdout so far: {}",
        SERVER_STARTED_TIMEOUT_MS, stdout_buf
    ))
}

/// Shared stdout-line parser used by both the release-path
/// (`spawn_sidecar_release`) and dev-mode-path (`spawn_sidecar_dev_mode`)
/// stdout-reading loops. Returns the port if `line` is the
/// `{"event":"server_started","port":N}` JSON line, else `None`.
fn parse_server_started(line: &str) -> Option<u16> {
    let v: Value = serde_json::from_str(line.trim()).ok()?;
    if v.get("event").and_then(|e| e.as_str()) == Some("server_started") {
        v.get("port").and_then(|p| p.as_u64()).map(|p| p as u16)
    } else {
        None
    }
}

fn prewarm_resource_path(app: &tauri::AppHandle) -> Result<String, String> {
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

fn current_target_triple() -> String {
    target_triple_for(std::env::consts::ARCH, std::env::consts::OS)
}

/// Pure form of `current_target_triple` for unit testing — accepts
/// arch+os as args so tests can verify all (arch, os) combos without
/// running on each platform. Returns the same triple strings the
/// `tauri-plugin-shell` `externalBin` mechanism expects as the binary
/// name suffix (see ADR-0020 §4.1).
fn target_triple_for(arch: &str, os: &str) -> String {
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

// ─── FT-1 supervisor (ADR-0020 §10) ───────────────────────────────────

async fn ft1_respawn(
    app: &tauri::AppHandle,
    state: &Arc<SidecarState>,
) -> Result<(), String> {
    // Serialize: only one ft1_respawn may run at a time. If a previous
    // respawn is still in flight (e.g., the sidecar died again mid-
    // reconnect), bail out — the in-flight supervisor owns the recovery.
    if state
        .respawn_in_progress
        .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
        .is_err()
    {
        log::info!("[FT-1] respawn already in progress — skipping");
        return Ok(());
    }
    // Scope the respawn body so we can clear the flag on every exit path
    // (including the `app.restart()` paths, which are `-> !` so the
    // clear is unreachable but harmless; the Ok() paths need it).
    let result = ft1_respawn_inner(app, state).await;
    state.respawn_in_progress.store(false, Ordering::SeqCst);
    result
}

async fn ft1_respawn_inner(
    app: &tauri::AppHandle,
    state: &Arc<SidecarState>,
) -> Result<(), String> {
    for (attempt, delay_ms) in FT1_BACKOFF_MS.iter().enumerate() {
        if attempt as u32 >= FT1_MAX_RETRIES {
            log::error!(
                "[FT-1] exhausted {} retries — falling back to full-app relaunch",
                FT1_MAX_RETRIES
            );
            // ADR-0020 §10: full-app relaunch. Emit a Tauri event so
            // the UI can show a "restarting…" banner, then call
            // app.restart() which exits the current process and
            // relaunches a fresh one.
            let _ = app.emit("ft1_relaunching", json!({"reason": "exhausted_retries"}));
            // Small delay so the UI event can render before restart.
            tokio::time::sleep(Duration::from_millis(PRE_RESTART_DELAY_MS)).await;
            // ADR-0020 §10: `app.restart()` is defined on the core
            // `tauri::AppHandle` directly (tauri-2.11.5/src/app.rs:588).
            // It exits with RESTART_EXIT_CODE so the Tauri launcher
            // spawns a fresh instance before the old one fully exits.
            // Returns `!` (never type) so following code is unreachable.
            app.restart();
        }
        if state.shutting_down.load(Ordering::SeqCst) {
            log::info!("[FT-1] shutting down — skipping respawn");
            return Ok(());
        }
        log::warn!("[FT-1] respawn attempt {} after {}ms", attempt + 1, delay_ms);
        tokio::time::sleep(Duration::from_millis(*delay_ms)).await;

        // Rotate token + respawn.
        let new_token = generate_token();
        match spawn_sidecar_and_get_port(app, &new_token).await {
            Ok((port, child, exit_rx)) => {
                // Drop the MutexGuards BEFORE awaiting reconnect_ws so
                // the future is Send (std::sync::MutexGuard is !Send).
                {
                    let mut child_guard = state.child.lock().unwrap();
                    *child_guard = Some(child);
                }
                {
                    let mut token_guard = state.token.lock().unwrap();
                    *token_guard = new_token.clone();
                }
                // CR-2: rotate the event receiver so the next
                // shutdown_sidecar call polls the new sidecar's exit.
                {
                    let mut rx_guard = state.child_exit_rx.lock().await;
                    *rx_guard = exit_rx;
                }
                // Reconnect WS + re-auth.
                match reconnect_ws(app, state, port, &new_token).await {
                    Ok(()) => {
                        log::info!("[FT-1] respawn succeeded on attempt {}", attempt + 1);
                        // Emit a Tauri event so the UI can clear its
                        // "reconnecting…" banner.
                        let _ = app.emit("ft1_reconnected", json!({}));
                        return Ok(());
                    }
                    Err(e) => {
                        log::warn!("[FT-1] WS reconnect failed: {}", e);
                        continue;
                    }
                }
            }
            Err(e) => {
                log::warn!("[FT-1] sidecar spawn failed: {}", e);
                continue;
            }
        }
    }
    // Loop exited without returning — this happens if FT1_BACKOFF_MS
    // is shorter than FT1_MAX_RETRIES. Treat as exhaustion.
    log::error!("[FT-1] backoff schedule exhausted — full-app relaunch");
    let _ = app.emit("ft1_relaunching", json!({"reason": "backoff_exhausted"}));
    tokio::time::sleep(Duration::from_millis(PRE_RESTART_DELAY_MS)).await;
    app.restart();
}

// ─── Bubble-level coalesce predicate (ADR-0020 §9) ───────────────────

/// Pure form of the bubble_level coalesce decision used by the WS
/// reader task (ADR-0020 §9). Returns `true` if the current event
/// should be emitted given the last-emitted timestamp and the target
/// Hz rate. Extracted from `reconnect_ws`'s inline coalesce logic so
/// unit tests can verify the 30 Hz cap without spinning up a Tauri
/// runtime + mock WS server.
///
/// The min interval is `Duration::from_millis(1000 / hz)` — for the
/// default `BUBBLE_LEVEL_COALESCE_HZ = 30`, that's 33ms (integer
/// division), so a 60 Hz input stream emits every other event = 30 Hz.
fn bubble_coalesce_should_emit(
    last_emitted: Option<Instant>,
    now: Instant,
    hz: u64,
) -> bool {
    last_emitted.map_or(true, |t| {
        now.duration_since(t) >= Duration::from_millis(1000 / hz)
    })
}

async fn reconnect_ws(
    _app: &tauri::AppHandle,
    state: &Arc<SidecarState>,
    port: u16,
    token: &str,
) -> Result<(), String> {
    let url = format!("ws://127.0.0.1:{}", port);
    // ADR-0020 §10: enforce 1 MiB WS frame cap.
    let ws_config = tokio_tungstenite::tungstenite::protocol::WebSocketConfig {
        max_message_size: Some(MAX_FRAME_BYTES),
        max_frame_size: Some(MAX_FRAME_BYTES),
        ..Default::default()
    };
    let (ws, _) = connect_async_with_config(&url, Some(ws_config), false)
        .await
        .map_err(|e| format!("WS reconnect failed: {e}"))?;
    let (write, mut read) = ws.split();

    // Set up the WS writer channel + reader task.
    let (ws_tx, mut ws_rx) = mpsc::unbounded_channel::<Message>();
    // Send the auth frame via the channel so the writer task sends it.
    let auth = json!({"type": "auth", "token": token});
    ws_tx
        .send(Message::Text(auth.to_string()))
        .map_err(|_| "failed to queue auth frame".to_string())?;
    // Drop the MutexGuard before spawning tasks (MutexGuard is !Send).
    {
        let mut ws_tx_guard = state.ws_tx.lock().unwrap();
        *ws_tx_guard = Some(ws_tx);
    }
    let state_clone = state.clone();
    let app_handle = _app.clone();

    // Writer task: drain ws_rx → write.send.
    tokio::spawn(async move {
        let mut write = write;
        while let Some(msg) = ws_rx.recv().await {
            if write.send(msg).await.is_err() {
                break;
            }
        }
    });

    // Reader task: parse incoming frames, fulfill pending dispatch
    // requests by id, emit Tauri events for server-initiated events.
    let app_for_reader = app_handle.clone();
    let state_for_reader = state_clone.clone();
    tokio::spawn(async move {
        let mut last_bubble_level: Option<Instant> = None;
        #[allow(unused_assignments)]
        let mut last_bubble_payload: Option<Value> = None;
        while let Some(msg) = read.next().await {
            match msg {
                Ok(Message::Text(text)) => {
                    let v: Value = match serde_json::from_str(&text) {
                        Ok(v) => v,
                        Err(_) => {
                            log::warn!("[WS-READER] invalid JSON frame: {}", text);
                            continue;
                        }
                    };
                    // If the frame has an `id`, it's a dispatch
                    // response — fulfill the pending oneshot.
                    if let Some(id) = v.get("id").and_then(|i| i.as_u64()) {
                        let mut pending = state_for_reader.pending.lock().await;
                        if let Some(tx) = pending.remove(&id) {
                            let _ = tx.send(v);
                        }
                        continue;
                    }
                    // Otherwise it's a server-initiated event
                    // (channel 2). Emit it as a Tauri event.
                    let event_type = v.get("type").and_then(|t| t.as_str()).unwrap_or("unknown");
                    let payload = v.get("data").cloned().unwrap_or(json!({}));

                    // ADR-0020 §9: coalesce bubble_level from ~60 Hz
                    // to ≤30 Hz.
                    if event_type == "bubble_level" {
                        last_bubble_payload = Some(payload);
                        let now = Instant::now();
                        if bubble_coalesce_should_emit(last_bubble_level, now, BUBBLE_LEVEL_COALESCE_HZ) {
                            last_bubble_level = Some(now);
                            let p = last_bubble_payload.take().unwrap();
                            // ADR-0020 §6.3: emit BOTH the specific event
                            // (for direct listeners like the bubble window)
                            // AND the generic `python-event` (for the
                            // usePython hook's onEvent catch-all, matching
                            // the Electron path's ipcRenderer.on("python-event")).
                            let _ = app_for_reader.emit("bubble_level", p.clone());
                            let _ = app_for_reader.emit("python-event", json!({"type": "bubble_level", "data": p}));
                        }
                        continue;
                    }

                    // ADR-0020 §6.1: rename `relaunch_electron` →
                    // `relaunch_app` (Tauri's `app.restart()` API).
                    //
                    // CR-8 (this change): the `electron_notification` →
                    // `notification` rename was REMOVED from this match
                    // arm — the Python sidecar now publishes the event
                    // under the platform-agnostic `notification` name
                    // directly (see `system_handlers.py` +
                    // `startup_sequence.py`), so it passes through
                    // unchanged via the `other => other` arm. A
                    // backward-compat alias BELOW handles old Python
                    // sidecars that still emit `electron_notification`
                    // during a rolling upgrade — emit BOTH names for
                    // one release cycle, then drop the alias.
                    let emit_name = match event_type {
                        "relaunch_electron" => "relaunch_app",
                        other => other,
                    };
                    // ADR-0020 §6.3: emit BOTH the specific event (for
                    // direct listeners) AND the generic `python-event`
                    // (for the usePython hook's onEvent catch-all).
                    let _ = app_for_reader.emit(emit_name, payload.clone());
                    let _ = app_for_reader.emit("python-event", json!({"type": emit_name, "data": payload}));

                    // CR-8 backward-compat alias: if an older Python
                    // sidecar still emits the legacy `electron_notification`
                    // event name (rolling upgrade), also emit it under
                    // the new canonical `notification` name so new UI
                    // code subscribing to `notification` keeps working.
                    // The legacy `electron_notification` emit above
                    // (via the `other => other` pass-through) keeps any
                    // old UI listeners working too. Drop this alias
                    // after one release cycle once all sidecars are
                    // upgraded to emit `notification` directly.
                    if event_type == "electron_notification" {
                        let _ = app_for_reader.emit("notification", payload.clone());
                    }
                }
                Ok(Message::Close(_)) => {
                    log::info!("[WS-READER] sidecar closed the WS");
                    break;
                }
                Ok(_) => {} // binary/ping/pong — ignore
                Err(e) => {
                    log::warn!("[WS-READER] error: {}", e);
                    break;
                }
            }
        }
        // WS reader exited — drain pending dispatch requests + clear
        // ws_tx so new dispatch calls fail fast instead of queueing
        // onto a dead channel (CR-Finding 1 + 3). Then trigger FT-1
        // respawn (unless we're shutting down).
        {
            // Clear ws_tx first so new dispatch calls return
            // "sidecar not connected" immediately.
            let mut ws_tx_guard = state_for_reader.ws_tx.lock().unwrap();
            *ws_tx_guard = None;
        }
        {
            // Drain pending requests — reject each with an error so
            // callers don't wait the full 120s timeout.
            let mut pending = state_for_reader.pending.lock().await;
            let count = pending.len();
            for (_id, tx) in pending.drain() {
                let _ = tx.send(json!({
                    "type": "error",
                    "data": {
                        "code": "sidecar_disconnected",
                        "message": "sidecar WS disconnected (FT-1 respawn in progress)"
                    }
                }));
            }
            if count > 0 {
                log::warn!("[WS-READER] drained {} pending dispatch requests", count);
            }
        }
        if !state_for_reader.shutting_down.load(Ordering::SeqCst) {
            // CR-5 (ADR-0020 §10): emit `ft1_relaunching` IMMEDIATELY
            // at disconnect start so the UI can show a "reconnecting…"
            // banner before the backoff schedule runs. The eventual
            // `ft1_reconnected` (on success) or second `ft1_relaunching`
            // (on exhaustion) supersedes this event.
            let _ = app_for_reader.emit(
                "ft1_relaunching",
                json!({"reason": "disconnected"}),
            );
            log::warn!("[WS-READER] unexpected close — triggering FT-1");
            // Spawn FT-1 on a separate thread via std::thread::spawn +
            // a block_on, so the non-Send WS stream half doesn't
            // poison the tokio::spawn Send requirement. The FT-1
            // supervisor itself uses tokio::spawn internally for the
            // respawn attempts, so this is just a bridge.
            let app_clone = app_for_reader.clone();
            let state_clone = state_for_reader.clone();
            std::thread::spawn(move || {
                tauri::async_runtime::block_on(async move {
                    let _ = ft1_respawn(&app_clone, &state_clone).await;
                });
            });
        }
    });

    Ok(())
}

// ─── Tauri command: generic dispatch (ADR-0020 §7) ────────────────────

#[derive(Serialize, Deserialize)]
struct DispatchArgs {
    cmd: String,
    data: Option<Value>,
}

#[tauri::command]
async fn dispatch(
    args: DispatchArgs,
    state: tauri::State<'_, Arc<SidecarState>>,
) -> Result<Value, String> {
    let id = state.next_id.fetch_add(1, Ordering::SeqCst);
    let frame = json!({
        "type": args.cmd,
        "data": args.data.unwrap_or(json!({})),
        "id": id,
    });

    let (tx, rx) = oneshot::channel::<Value>();
    {
        let mut pending = state.pending.lock().await;
        pending.insert(id, tx);
    }

    // Send the frame via the WS writer channel.
    let ws_tx_opt = state.ws_tx.lock().unwrap().clone();
    let ws_tx = ws_tx_opt.ok_or_else(|| "sidecar not connected".to_string())?;
    ws_tx.send(Message::Text(frame.to_string()))
        .map_err(|e| format!("WS send failed: {e}"))?;

    // Await the response with a timeout.
    match tokio::time::timeout(Duration::from_secs(DISPATCH_TIMEOUT_SECS), rx).await {
        Ok(Ok(response)) => {
            // ADR-0020 §2: if the response is a `type:"error"` envelope,
            // surface it as a Rust error so the webview's `invoke()`
            // rejects (this is the NEW-IPC-107 fix — the Electron path
            // silently treated `type:"error"` as success).
            if response.get("type").and_then(|t| t.as_str()) == Some("error") {
                let code = response
                    .get("data")
                    .and_then(|d| d.get("code"))
                    .and_then(|c| c.as_str())
                    .unwrap_or("unknown");
                let msg = response
                    .get("data")
                    .and_then(|d| d.get("message"))
                    .and_then(|m| m.as_str())
                    .unwrap_or("server error");
                return Err(format!("server error [{}]: {}", code, msg));
            }
            Ok(response.get("data").cloned().unwrap_or(json!({})))
        }
        Ok(Err(_)) => Err("dispatch response channel closed".into()),
        Err(_) => {
            // Timeout — remove the pending entry.
            let mut pending = state.pending.lock().await;
            pending.remove(&id);
            Err(format!("dispatch timeout ({}s)", DISPATCH_TIMEOUT_SECS))
        }
    }
}

// ─── Tauri command: paste_text (ADR-0020 §6.2) ────────────────────────

#[derive(Serialize, Deserialize)]
struct PasteTextArgs {
    text: String,
}

/// ADR-0020 §6.2: paste transcribed text into the foreground window.
///
/// - Short text (< ~300 chars): inject via `enigo.text()` (IME-safe).
/// - Long text: copy via `tauri-plugin-clipboard-manager` then send
///   Ctrl+V (Windows/Linux) or Cmd+V (macOS) via enigo.
///
/// The Python sidecar emits a `paste_text` event when a transcription
/// completes; this command (invoked by the React UI on that event)
/// performs the actual paste.
#[tauri::command]
async fn paste_text(
    args: PasteTextArgs,
    app: tauri::AppHandle,
) -> Result<(), String> {
    let text = args.text;
    if text.is_empty() {
        return Ok(());
    }

    use enigo::{Enigo, Key, Keyboard, Settings};
    if text.chars().count() < PASTE_SHORT_THRESHOLD {
        // Short text — inject via enigo.text() (IME-safe).
        let mut enigo = Enigo::new(&Settings::default())
            .map_err(|e| format!("enigo init failed: {e}"))?;
        enigo.text(&text)
            .map_err(|e| format!("enigo.text failed: {e}"))?;
        log::info!("[PASTE] injected {} chars via enigo", text.chars().count());
    } else {
        // Long text — clipboard + Ctrl+V / Cmd+V.
        // tauri-plugin-clipboard-manager exposes write_text via the
        // ClipboardExt trait.
        use tauri_plugin_clipboard_manager::ClipboardExt;
        app.clipboard()
            .write_text(text.clone())
            .map_err(|e| format!("clipboard write failed: {e}"))?;
        let mut enigo = Enigo::new(&Settings::default())
            .map_err(|e| format!("enigo init failed: {e}"))?;
        let mod_key = if cfg!(target_os = "macos") {
            Key::Meta
        } else {
            Key::Control
        };
        enigo.key(mod_key, enigo::Direction::Press)
            .map_err(|e| format!("enigo mod press failed: {e}"))?;
        enigo.key(Key::Unicode('v'), enigo::Direction::Click)
            .map_err(|e| format!("enigo v click failed: {e}"))?;
        enigo.key(mod_key, enigo::Direction::Release)
            .map_err(|e| format!("enigo mod release failed: {e}"))?;
        log::info!("[PASTE] injected {} chars via clipboard + Ctrl/Cmd+V", text.chars().count());
    }
    Ok(())
}

// ─── Tauri command: cooperative shutdown (ADR-0020 §10) ───────────────

#[tauri::command]
async fn shutdown_sidecar(
    app: tauri::AppHandle,
    state: tauri::State<'_, Arc<SidecarState>>,
) -> Result<(), String> {
    state.shutting_down.store(true, Ordering::SeqCst);
    // Send the shutdown frame.
    let frame = json!({"type": "shutdown"});
    if let Some(ws_tx) = state.ws_tx.lock().unwrap().clone() {
        let _ = ws_tx.send(Message::Text(frame.to_string()));
    }
    // CR-2: Wait up to SHUTDOWN_ACK_TIMEOUT_MS for the sidecar to exit.
    // Use the `CommandEvent` receiver captured at spawn time to detect
    // `Terminated` and return immediately (typical sidecar acks+exits in
    // ~50ms), instead of sleeping the full deadline unconditionally.
    // Falls back to bounded sleep polling for the dev-mode path (which
    // has no event receiver).
    let deadline_dur = Duration::from_millis(SHUTDOWN_ACK_TIMEOUT_MS);
    let mut graceful = false;
    let mut rx_guard = state.child_exit_rx.lock().await;
    if let Some(rx) = rx_guard.as_mut() {
        match tokio::time::timeout(deadline_dur, rx.recv()).await {
            Ok(Some(CommandEvent::Terminated(payload))) => {
                log::info!(
                    "[SHUTDOWN] sidecar exited gracefully (code={:?}, signal={:?})",
                    payload.code, payload.signal
                );
                graceful = true;
            }
            Ok(Some(other)) => {
                log::warn!(
                    "[SHUTDOWN] unexpected event while waiting for termination: {:?}",
                    other
                );
            }
            Ok(None) => {
                log::warn!("[SHUTDOWN] sidecar event stream closed without Terminated");
            }
            Err(_) => {
                log::warn!(
                    "[SHUTDOWN] sidecar did not exit within {}ms — force-killing",
                    SHUTDOWN_ACK_TIMEOUT_MS
                );
            }
        }
    } else {
        // Dev-mode path (tokio::process::Child) — no CommandEvent
        // receiver. Fall back to the original bounded sleep polling.
        log::info!(
            "[SHUTDOWN] dev-mode sidecar — sleeping {}ms before force-kill",
            SHUTDOWN_ACK_TIMEOUT_MS
        );
        let deadline = Instant::now() + deadline_dur;
        while Instant::now() < deadline {
            tokio::time::sleep(Duration::from_millis(SHUTDOWN_POLL_INTERVAL_MS)).await;
        }
    }
    // Drop the rx guard before locking state.child (avoid holding the
    // async mutex across the sync mutex lock + async kill await).
    drop(rx_guard);
    // Force-kill backstop — no-op if the child has already exited, but
    // guarantees we never leak a zombie.
    let child_opt = state.child.lock().unwrap().take();
    if let Some(child) = child_opt {
        let _ = child.kill().await;
    }
    log::info!("[SHUTDOWN] sidecar kill completed (graceful={})", graceful);
    let _ = app;
    Ok(())
}

// ─── Tauri command: export_history (MIG-1.1) ─────────────────────────

/// ADR-0020 §6 + MIG-1.1: export the transcription history to a file
/// chosen by the user via `tauri-plugin-dialog`'s save dialog.
///
/// - `data` is the history payload (array of records) from the Python
///   sidecar's `export_history` command.
/// - `format` is `"json"` or `"csv"`.
/// - Returns `{"canceled": true}` if the user dismissed the dialog,
///   `{"success": true, "path": "<chosen path>"}` on success, or
///   `Err(message)` on I/O / encode failure.
#[tauri::command]
async fn export_history(
    data: Value,
    format: String,
    app: tauri::AppHandle,
) -> Result<Value, String> {
    export_data(data, format, app, "voice-typer-history", "Export History").await
}

// ─── Tauri command: export_vocabulary (MIG-1.1) ──────────────────────

/// ADR-0020 §6 + MIG-1.1: export the user's custom vocabulary to a
/// file chosen by the user. Same shape as `export_history` with a
/// different default filename + dialog title.
#[tauri::command]
async fn export_vocabulary(
    data: Value,
    format: String,
    app: tauri::AppHandle,
) -> Result<Value, String> {
    export_data(data, format, app, "voice-typer-vocabulary", "Export Vocabulary").await
}

/// Shared helper for `export_history` + `export_vocabulary`. Opens a
/// `tauri-plugin-dialog` save-file dialog, then writes the data as
/// pretty-printed JSON or CSV to the chosen path. Returns
/// `{"canceled": true}` when the user cancels the dialog.
async fn export_data(
    data: Value,
    format: String,
    app: tauri::AppHandle,
    default_filename: &str,
    title: &str,
) -> Result<Value, String> {
    use tauri_plugin_dialog::DialogExt;
    let file_path = app
        .dialog()
        .file()
        .set_title(title)
        .add_filter("JSON", &["json"])
        .add_filter("CSV", &["csv"])
        .set_file_name(default_filename)
        .blocking_save_file();
    let path = match file_path {
        Some(fp) => fp.into_path().map_err(|e| format!("invalid path: {e}"))?,
        None => return Ok(json!({"canceled": true})),
    };
    let content = match format.as_str() {
        "json" => serde_json::to_string_pretty(&data)
            .map_err(|e| format!("JSON encode failed: {e}"))?,
        "csv" => json_to_csv(&data)?,
        other => return Err(format!("unsupported format: {}", other)),
    };
    std::fs::write(&path, content).map_err(|e| format!("write failed: {e}"))?;
    Ok(json!({"success": true, "path": path.to_string_lossy().to_string()}))
}

/// Convert a JSON array of flat objects to CSV. The first object's keys
/// (in insertion order) form the header row; missing keys in later
/// rows are emitted as empty cells. Nested values are serialized as
/// their `to_string()` form (no recursion).
fn json_to_csv(data: &Value) -> Result<String, String> {
    let arr = data
        .as_array()
        .ok_or_else(|| "CSV export requires an array of objects".to_string())?;
    if arr.is_empty() {
        return Ok(String::new());
    }
    // Collect all keys (preserve insertion order from the first object
    // that contains them; subsequent objects may add new keys at the
    // end, which keeps the header stable for the common case of
    // homogeneous records).
    let mut keys: Vec<String> = Vec::new();
    for item in arr {
        if let Some(obj) = item.as_object() {
            for k in obj.keys() {
                if !keys.contains(k) {
                    keys.push(k.clone());
                }
            }
        }
    }
    let mut out = String::new();
    out.push_str(
        &keys
            .iter()
            .map(|k| csv_escape(k))
            .collect::<Vec<_>>()
            .join(","),
    );
    out.push('\n');
    for item in arr {
        let empty_map = serde_json::Map::new();
        let obj = item.as_object().unwrap_or(&empty_map);
        let row: Vec<String> = keys
            .iter()
            .map(|k| {
                let v = obj.get(k).map(value_to_string).unwrap_or_default();
                csv_escape(&v)
            })
            .collect();
        out.push_str(&row.join(","));
        out.push('\n');
    }
    Ok(out)
}

/// Render a JSON value as a single CSV cell (no quoting).
fn value_to_string(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Number(n) => n.to_string(),
        Value::Bool(b) => b.to_string(),
        Value::Null => String::new(),
        other => other.to_string(),
    }
}

/// RFC 4180 CSV cell escaping: wrap in double quotes if the cell
/// contains a comma, double-quote, newline, or carriage return; double
/// any embedded double-quotes.
fn csv_escape(s: &str) -> String {
    if s.contains(',') || s.contains('"') || s.contains('\n') || s.contains('\r') {
        format!("\"{}\"", s.replace('"', "\"\""))
    } else {
        s.to_string()
    }
}

// ─── Tauri commands: bubble window (MIG-1.2, ADR-0020 §9) ────────────

/// Show the bubble window (ADR-0020 §9 + MIG-1.2).
#[tauri::command]
async fn bubble_show(app: tauri::AppHandle) -> Result<(), String> {
    app.get_webview_window("bubble")
        .ok_or("bubble window not found")?
        .show()
        .map_err(|e| e.to_string())
}

/// Emit `bubble:ready` to the bubble window — the bubble renderer
/// listens for this and signals back to the Python sidecar that it's
/// ready to receive `bubble_level` events (ADR-0020 §9 + MIG-1.2).
#[tauri::command]
async fn bubble_signal_ready(app: tauri::AppHandle) -> Result<(), String> {
    app.emit_to("bubble", "bubble:ready", ())
        .map_err(|e| e.to_string())
}

/// Move the bubble window to `(x, y)` in physical pixels (ADR-0020 §9
/// + MIG-1.2). The TS bridge calls this with the cursor position
/// (offset by a small delta) so the bubble appears under the cursor.
#[tauri::command]
async fn bubble_set_position(
    x: i32,
    y: i32,
    app: tauri::AppHandle,
) -> Result<(), String> {
    let window = app
        .get_webview_window("bubble")
        .ok_or("bubble window not found")?;
    window
        .set_position(PhysicalPosition::new(x, y))
        .map_err(|e| e.to_string())
}

/// Toggle the bubble window's draggable state (ADR-0020 §9 + MIG-1.2).
///
/// Tauri v2 does NOT expose a direct `set_draggable` on `WebviewWindow`.
/// Instead, we emit a `bubble:draggable` event to the bubble window
/// with the bool payload; the bubble renderer listens for this event
/// and calls `start_dragging()` on mouse-down (or unbinds the
/// listener when `false`). This keeps the drag logic in the renderer
/// where it can be throttled to the animation frame.
#[tauri::command]
async fn bubble_set_draggable(
    draggable: bool,
    app: tauri::AppHandle,
) -> Result<(), String> {
    app.emit_to("bubble", "bubble:draggable", draggable)
        .map_err(|e| e.to_string())
}

/// Move the bubble window by `(dx, dy)` physical pixels relative to
/// its current `outer_position` (ADR-0020 §9 + MIG-1.2). Returns the
/// new `{x, y}` so the TS bridge can cache it without a round-trip.
#[tauri::command]
async fn bubble_move_by(
    dx: i32,
    dy: i32,
    app: tauri::AppHandle,
) -> Result<Value, String> {
    let window = app
        .get_webview_window("bubble")
        .ok_or("bubble window not found")?;
    let pos = window.outer_position().map_err(|e| e.to_string())?;
    let new_x = pos.x + dx;
    let new_y = pos.y + dy;
    window
        .set_position(PhysicalPosition::new(new_x, new_y))
        .map_err(|e| e.to_string())?;
    Ok(json!({"x": new_x, "y": new_y}))
}

/// Hide the bubble window and emit `bubble:hide_complete` so the
/// renderer can run cleanup (e.g., stop the level animation) before
/// the window becomes invisible (ADR-0020 §9 + MIG-1.2).
#[tauri::command]
async fn bubble_hide_complete(app: tauri::AppHandle) -> Result<(), String> {
    let window = app
        .get_webview_window("bubble")
        .ok_or("bubble window not found")?;
    window.hide().map_err(|e| e.to_string())?;
    app.emit_to("bubble", "bubble:hide_complete", ())
        .map_err(|e| e.to_string())
}

// ─── ADR-0020 §8: per-platform config-dir resolution ─────────────────

/// ADR-0020 §8: resolve the per-platform config dir for Voice Typer.
/// Returns:
/// - Windows: `%APPDATA%/voice-typer`
/// - macOS:   `~/Library/Application Support/voice-typer`
/// - Linux:   `$XDG_DATA_HOME/voice-typer` (default `~/.local/share/voice-typer`)
///
/// # Why not `app.path().app_config_dir()`?
///
/// Tauri's `app_config_dir()` returns a path derived from the bundle
/// identifier (`com.voicetyper.app`), but the Python sidecar uses the
/// lowercase-hyphenated `voice-typer` directory (per `_paths.py`). To
/// keep the Rust host + Python sidecar reading/writing the SAME paths
/// byte-for-byte, we resolve from env vars directly, matching the
/// Python side's `_paths.config_dir()` resolution.
///
/// # No Electron userData merge under Tauri
///
/// ADR-0020 §8 mentions an optional one-time migration from the old
/// Electron `userData/voice-typer` directory to `<config_dir>` on
/// first Tauri launch. Under Tauri there is **no Electron main
/// process**, so no `userData/voice-typer` dir ever exists — the
/// migration step is a no-op and is intentionally NOT implemented
/// here. (If a future hybrid build ever needs it, the merge rules in
/// ADR-0020 §8 apply — newest-mtime-wins for `config.json`, append-
/// only for `history.db`, copy-only-absent for `models/`.)
///
/// # Python-side `VoiceTyperSingleInstance` Win32 mutex
///
/// The Python side's `VoiceTyperSingleInstance` Win32 named mutex
/// (acquired in `app.py` on Windows to prevent duplicate Electron
/// instances) is **disabled when `TAURI_SIDECAR=1` is set** — the
/// Python sidecar detects the env var and skips the mutex acquire so
/// it doesn't double-lock against the Tauri-side
/// `tauri-plugin-single-instance` gate (§12). The Tauri plugin uses
/// the same Win32 mutex approach under the hood (different name based
/// on the app identifier) so the two gates don't collide.
fn config_dir(app: &tauri::AppHandle) -> std::path::PathBuf {
    let _ = app; // not used — env-var resolution matches Python `_paths.py`
    config_dir_from_env(
        std::env::var("HOME").ok().as_deref(),
        std::env::var("APPDATA").ok().as_deref(),
        std::env::var("XDG_DATA_HOME").ok().as_deref(),
    )
}

/// Pure form of `config_dir` for unit testing (no env-var reads).
fn config_dir_from_env(
    home: Option<&str>,
    appdata: Option<&str>,
    xdg_data_home: Option<&str>,
) -> std::path::PathBuf {
    const APP_NAME: &str = "voice-typer";
    #[cfg(target_os = "windows")]
    {
        let _ = home;
        let _ = xdg_data_home;
        let base = appdata.unwrap_or_else(|| {
            panic!("APPDATA env var must be set on Windows (config dir resolution)")
        });
        std::path::PathBuf::from(base).join(APP_NAME)
    }
    #[cfg(target_os = "macos")]
    {
        let _ = appdata;
        let _ = xdg_data_home;
        let home = home.unwrap_or_else(|| {
            panic!("HOME env var must be set on macOS (config dir resolution)")
        });
        std::path::PathBuf::from(home)
            .join("Library")
            .join("Application Support")
            .join(APP_NAME)
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        let _ = appdata;
        if let Some(xdg) = xdg_data_home {
            if !xdg.is_empty() {
                return std::path::PathBuf::from(xdg).join(APP_NAME);
            }
        }
        let home = home.unwrap_or_else(|| {
            panic!("HOME env var must be set on Linux (config dir resolution)")
        });
        std::path::PathBuf::from(home)
            .join(".local")
            .join("share")
            .join(APP_NAME)
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos", unix)))]
    {
        let _ = (home, appdata, xdg_data_home);
        std::path::PathBuf::from(".").join(APP_NAME)
    }
}

// ─── ADR-0020 §11: rotating file logger (5MB × 5, no bubble_level) ───

/// ADR-0020 §11: max bytes per log file before rotation.
const ROTATE_MAX_BYTES: u64 = 5 * 1024 * 1024; // 5 MB

/// ADR-0020 §11: max rotated files to keep (current + N-1 rotated).
/// Total disk cap ≈ 5 MB × 5 files = 25 MB.
const ROTATE_MAX_FILES: usize = 5;

/// ADR-0020 §11: initialize a rotating file logger writing to
/// `<config_dir>/logs/voice-typer.log` (5 MB × 5 files ≈ 25 MB cap).
///
/// **Excludes `bubble_level` events** from the file log: at ~60 Hz
/// they would fill disk fast even with rotation. The Rust WS-reader
/// already coalesces them to ≤30 Hz for the UI (§9); the file path
/// drops them entirely so file logs capture events/errors, not the
/// level stream.
///
/// Replaces the prior `env_logger::Builder::init()` call — this
/// logger writes to BOTH stderr (matching the prior env_logger
/// output) AND the rotating file. If file init fails, the caller
/// should fall back to `env_logger` for stderr-only output.
///
/// # Implementation choice: hand-rolled, not `log4rs`
///
/// `log4rs` is a heavy dep (~30 transitive crates) for a feature
/// that just needs "rotate at N bytes, keep N files". This
/// hand-rolled `RotatingFileWriter` is ~80 lines and has no deps
/// beyond `log` (already required) + `std::fs`. The rotation is
/// triggered lazily on the write that crosses the size threshold
/// (not on a timer), which is fine for our write volume.
fn init_file_logger(config_dir: &std::path::Path) -> Result<(), String> {
    let logs_dir = config_dir.join("logs");
    std::fs::create_dir_all(&logs_dir)
        .map_err(|e| format!("create logs dir failed: {e}"))?;
    let writer = RotatingFileWriter::new(logs_dir, "voice-typer");
    let logger = CombinedLogger {
        file_writer: Some(writer),
        level_filter: log::LevelFilter::Info,
    };
    // `Box::leak` is safe here: the logger lives for the program's
    // lifetime (we never want to tear it down). `log::set_logger`
    // requires a `&'static dyn Log`.
    log::set_logger(Box::leak(Box::new(logger)))
        .map_err(|_| "failed to set logger (already set?)".to_string())?;
    log::set_max_level(log::LevelFilter::Info);
    Ok(())
}

/// Combined stderr + rotating-file logger. Replaces `env_logger` so
/// we can add the file sink without a multiplexer crate.
struct CombinedLogger {
    file_writer: Option<RotatingFileWriter>,
    level_filter: log::LevelFilter,
}

impl log::Log for CombinedLogger {
    fn enabled(&self, metadata: &log::Metadata) -> bool {
        metadata.level() <= self.level_filter
    }

    fn log(&self, record: &log::Record) {
        if !self.enabled(record.metadata()) {
            return;
        }
        let msg = record.args().to_string();
        let ts = now_timestamp();
        let line = format!(
            "{} {:5} {} -- {}",
            ts,
            record.level(),
            record.target(),
            msg
        );
        // Always log to stderr (env_logger-style output for `cargo tauri dev`).
        eprintln!("{}", line);
        // ADR-0020 §11: exclude `bubble_level` from the file log
        // (60 Hz would fill disk fast even with rotation). Match by
        // substring so any `log::*!("[...] bubble_level ...")` is
        // dropped — the WS reader's bubble_level coalesce path uses
        // `log::warn!`/`log::info!` with "bubble_level" in the message
        // when logging unexpected payloads.
        if let Some(writer) = &self.file_writer {
            if !msg.contains("bubble_level") {
                let _ = writer.write_line(&line);
            }
        }
    }

    fn flush(&self) {
        if let Some(writer) = &self.file_writer {
            let _ = writer.flush();
        }
    }
}

/// Minimal rotating-file writer: appends to
/// `<dir>/<base_name>.log` until the file exceeds `ROTATE_MAX_BYTES`,
/// then rotates (`.log` → `.log.1` → `.log.2` → … → `.log.4` → delete).
/// Thread-safe via a single `Mutex<Option<File>>`.
struct RotatingFileWriter {
    dir: std::path::PathBuf,
    base_name: String,
    inner: Mutex<Option<std::fs::File>>,
}

impl RotatingFileWriter {
    fn new(dir: std::path::PathBuf, base_name: &str) -> Self {
        Self {
            dir,
            base_name: base_name.to_string(),
            inner: Mutex::new(None),
        }
    }

    fn current_path(&self) -> std::path::PathBuf {
        self.dir.join(format!("{}.log", self.base_name))
    }

    fn write_line(&self, line: &str) -> std::io::Result<()> {
        let mut guard = self.inner.lock().unwrap();
        // Open the file lazily so we don't create `voice-typer.log`
        // until the first log line is emitted.
        if guard.is_none() {
            std::fs::create_dir_all(&self.dir)?;
            let file = OpenOptions::new()
                .create(true)
                .append(true)
                .open(self.current_path())?;
            *guard = Some(file);
        }
        let file = guard.as_mut().unwrap();
        file.write_all(line.as_bytes())?;
        file.write_all(b"\n")?;
        file.flush()?;
        // Check size; rotate if we've crossed the threshold.
        let len = file.metadata()?.len();
        if len > ROTATE_MAX_BYTES {
            // Drop the file handle BEFORE renaming (Windows refuses to
            // rename a file that's open by another handle).
            *guard = None;
            self.rotate()?;
        }
        Ok(())
    }

    /// Rotate: `.log.(N-1)` → `.log.N`, …, `.log` → `.log.1`.
    /// Files at index `ROTATE_MAX_FILES - 1` (the oldest) are deleted.
    fn rotate(&self) -> std::io::Result<()> {
        for i in (1..ROTATE_MAX_FILES).rev() {
            let from = self.dir.join(format!("{}.log.{}", self.base_name, i));
            let to = self
                .dir
                .join(format!("{}.log.{}", self.base_name, i + 1));
            if from.exists() {
                if i + 1 >= ROTATE_MAX_FILES {
                    // Oldest slot — delete what's there before renaming
                    // (best-effort; ignore errors if the file is gone).
                    let _ = std::fs::remove_file(&to);
                }
                let _ = std::fs::rename(&from, &to);
            }
        }
        let from = self.current_path();
        let to = self.dir.join(format!("{}.log.1", self.base_name));
        if from.exists() {
            let _ = std::fs::rename(from, to);
        }
        Ok(())
    }

    fn flush(&self) -> std::io::Result<()> {
        if let Some(f) = self.inner.lock().unwrap().as_mut() {
            f.flush()?;
        }
        Ok(())
    }
}

/// Format the current time as `YYYY-MM-DD HH:MM:SS.mmm` (UTC).
///
/// Uses Howard Hinnant's `civil_from_days` algorithm to convert days-
/// since-Unix-epoch to a (y, m, d) triple without pulling in `chrono`
/// or `time` (keeping the dep tree minimal per ADR-0020 §11's "prefer
/// minimal deps" guidance). UTC is fine for log timestamps — the
/// Python side also logs in UTC (`log.py` uses `gmtime()`).
fn now_timestamp() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    let secs = now.as_secs();
    let millis = now.subsec_millis();
    let days = (secs / 86_400) as i64;
    let rem = secs % 86_400;
    let hour = rem / 3600;
    let min = (rem % 3600) / 60;
    let sec = rem % 60;
    // Howard Hinnant's civil_from_days (http://howardhinnant.github.io/date_algorithms.html).
    let z = days + 719468;
    let era = if z >= 0 { z } else { z - 146096 } / 146097;
    let doe = (z - era * 146097) as u64; // [0, 146096]
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365; // [0, 399]
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100); // [0, 365]
    let mp = (5 * doy + 2) / 153; // [0, 11]
    let d = doy - (153 * mp + 2) / 5 + 1; // [1, 31]
    let m = if mp < 10 { mp + 3 } else { mp - 9 }; // [1, 12]
    let y = if m <= 2 { y + 1 } else { y };
    format!(
        "{:04}-{:02}-{:02} {:02}:{:02}:{:02}.{:03}",
        y, m, d, hour, min, sec, millis
    )
}

// ─── App entry point ──────────────────────────────────────────────────

fn main() {
    // ADR-0020 §11: init the rotating file logger BEFORE the Tauri
    // builder runs so early startup errors are captured. Falls back to
    // `env_logger` (stderr-only) if file init fails — non-fatal.
    let config_dir_path = config_dir_from_env(
        std::env::var("HOME").ok().as_deref(),
        std::env::var("APPDATA").ok().as_deref(),
        std::env::var("XDG_DATA_HOME").ok().as_deref(),
    );
    if let Err(e) = init_file_logger(&config_dir_path) {
        eprintln!(
            "[MAIN] file logger init failed (falling back to stderr-only env_logger): {}",
            e
        );
        // Best-effort: env_logger for stderr only (no file sink).
        // `try_init` avoids panic if `log::set_logger` was already called.
        let _ = env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info"))
            .format_timestamp_millis()
            .try_init();
    }

    tauri::Builder::default()
        // ADR-0020 §12: single-instance MUST be the FIRST plugin so its
        // duplicate-instance check runs before any sidecar spawn (which
        // would otherwise leave a zombie python process on a double-
        // launch). The plugin's callback focuses the existing main
        // window; the second instance exits immediately after the
        // callback returns. The Python side's `VoiceTyperSingleInstance`
        // Win32 mutex is disabled when `TAURI_SIDECAR=1` is set (see
        // `config_dir` doc comment) so the two gates don't collide.
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            // ADR-0020 §12: second launch — focus the existing main window.
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_clipboard_manager::init())
        // MIG-1.1: dialog plugin for export_history / export_vocabulary
        // save-file dialogs (invoked from Rust, not TS).
        .plugin(tauri_plugin_dialog::init())
        .manage(Arc::new(SidecarState {
            child: Mutex::new(None),
            token: Mutex::new(String::new()),
            ws_tx: Mutex::new(None),
            pending: Arc::new(AsyncMutex::new(HashMap::new())),
            next_id: AtomicU64::new(1),
            shutting_down: AtomicBool::new(false),
            respawn_in_progress: AtomicBool::new(false),
            child_exit_rx: AsyncMutex::new(None),
        }))
        // MIG-1.1 + MIG-1.2: register export + bubble commands alongside
        // the existing dispatch/paste_text/shutdown_sidecar.
        .invoke_handler(tauri::generate_handler![
            dispatch,
            paste_text,
            shutdown_sidecar,
            export_history,
            export_vocabulary,
            bubble_show,
            bubble_signal_ready,
            bubble_set_position,
            bubble_set_draggable,
            bubble_move_by,
            bubble_hide_complete,
        ])
        .setup(|app| {
            let app_handle = app.handle().clone();
            // ADR-0020 §8: log the resolved config_dir so users/devs
            // can find their logs / history.db / models without reading
            // code. (The same path is used by the Python sidecar via
            // `voice_typer/server/_paths.py`.)
            log::info!(
                "[SETUP] config_dir resolved to: {}",
                config_dir(&app_handle).display()
            );
            // Spawn the sidecar + WS bridge in a background tokio task.
            tauri::async_runtime::spawn(async move {
                let state: tauri::State<'_, Arc<SidecarState>> = app_handle.state();
                let state = state.inner().clone();

                let token = generate_token();
                *state.token.lock().unwrap() = token.clone();

                match spawn_sidecar_and_get_port(&app_handle, &token).await {
                    Ok((port, child, exit_rx)) => {
                        *state.child.lock().unwrap() = Some(child);
                        // CR-2: store the sidecar's event receiver so
                        // shutdown_sidecar can poll for graceful exit.
                        *state.child_exit_rx.lock().await = exit_rx;
                        if let Err(e) = reconnect_ws(&app_handle, &state, port, &token).await {
                            log::error!("[SETUP] initial WS connect failed: {}", e);
                            let _ = ft1_respawn(&app_handle, &state).await;
                        }
                    }
                    Err(e) => {
                        log::error!("[SETUP] sidecar spawn failed: {}", e);
                        let _ = ft1_respawn(&app_handle, &state).await;
                    }
                }
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            // ADR-0020 §10: on main window close, shutdown the sidecar.
            if let WindowEvent::CloseRequested { .. } = event {
                if window.label() == "main" {
                    let app = window.app_handle().clone();
                    tauri::async_runtime::spawn(async move {
                        let state: tauri::State<'_, Arc<SidecarState>> = app.state();
                        let _ = shutdown_sidecar(app.clone(), state).await;
                    });
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

// ─── Unit tests ───────────────────────────────────────────────────────

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

    // ── csv_escape ────────────────────────────────────────────────────

    #[test]
    fn test_csv_escape_plain() {
        assert_eq!(csv_escape("hello"), "hello");
        assert_eq!(csv_escape("123"), "123");
        assert_eq!(csv_escape(""), "");
    }

    #[test]
    fn test_csv_escape_comma() {
        assert_eq!(csv_escape("hello,world"), "\"hello,world\"");
    }

    #[test]
    fn test_csv_escape_double_quote() {
        assert_eq!(csv_escape("hello\"world"), "\"hello\"\"world\"");
    }

    #[test]
    fn test_csv_escape_newline() {
        assert_eq!(csv_escape("hello\nworld"), "\"hello\nworld\"");
    }

    #[test]
    fn test_csv_escape_carriage_return() {
        assert_eq!(csv_escape("hello\rworld"), "\"hello\rworld\"");
    }

    #[test]
    fn test_csv_escape_all_special() {
        assert_eq!(csv_escape("a,b\"c\nd\re"), "\"a,b\"\"c\nd\re\"");
    }

    // ── json_to_csv ───────────────────────────────────────────────────

    #[test]
    fn test_json_to_csv_empty_array() {
        let data = json!([]);
        assert_eq!(json_to_csv(&data).unwrap(), "");
    }

    #[test]
    fn test_json_to_csv_not_array() {
        let data = json!({"a": 1});
        let err = json_to_csv(&data).unwrap_err();
        assert!(err.contains("requires an array"), "err: {}", err);
    }

    #[test]
    fn test_json_to_csv_homogeneous() {
        let data = json!([
            {"id": 1, "text": "hello"},
            {"id": 2, "text": "world"},
        ]);
        let csv = json_to_csv(&data).unwrap();
        let lines: Vec<&str> = csv.lines().collect();
        assert_eq!(lines.len(), 3);
        // Header preserves first object's key order.
        assert_eq!(lines[0], "id,text");
        assert_eq!(lines[1], "1,hello");
        assert_eq!(lines[2], "2,world");
    }

    #[test]
    fn test_json_to_csv_missing_keys() {
        let data = json!([
            {"id": 1, "text": "hello"},
            {"id": 2},
        ]);
        let csv = json_to_csv(&data).unwrap();
        let lines: Vec<&str> = csv.lines().collect();
        assert_eq!(lines[0], "id,text");
        assert_eq!(lines[1], "1,hello");
        assert_eq!(lines[2], "2,"); // missing "text" → empty cell
    }

    #[test]
    fn test_json_to_csv_special_chars() {
        let data = json!([
            {"text": "hello, world"},
            {"text": "quote\"inside"},
        ]);
        let csv = json_to_csv(&data).unwrap();
        let lines: Vec<&str> = csv.lines().collect();
        assert_eq!(lines[0], "text");
        assert_eq!(lines[1], "\"hello, world\"");
        assert_eq!(lines[2], "\"quote\"\"inside\"");
    }

    #[test]
    fn test_json_to_csv_extra_keys_in_later_rows() {
        let data = json!([
            {"id": 1},
            {"id": 2, "extra": "x"},
        ]);
        let csv = json_to_csv(&data).unwrap();
        let lines: Vec<&str> = csv.lines().collect();
        // Extra key is appended to the header.
        assert_eq!(lines[0], "id,extra");
        assert_eq!(lines[1], "1,"); // first row had no "extra"
        assert_eq!(lines[2], "2,x");
    }

    // ── value_to_string ───────────────────────────────────────────────

    #[test]
    fn test_value_to_string() {
        assert_eq!(value_to_string(&json!("hello")), "hello");
        assert_eq!(value_to_string(&json!(42)), "42");
        assert_eq!(value_to_string(&json!(3.14)), "3.14");
        assert_eq!(value_to_string(&json!(true)), "true");
        assert_eq!(value_to_string(&json!(false)), "false");
        assert_eq!(value_to_string(&json!(null)), "");
    }

    // ── config_dir_from_env (per-platform) ────────────────────────────

    #[cfg(target_os = "linux")]
    #[test]
    fn test_config_dir_linux_default() {
        let p = config_dir_from_env(Some("/home/user"), None, None);
        assert_eq!(
            p,
            std::path::PathBuf::from("/home/user/.local/share/voice-typer")
        );
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn test_config_dir_linux_xdg_set() {
        let p = config_dir_from_env(Some("/home/user"), None, Some("/custom/xdg"));
        assert_eq!(p, std::path::PathBuf::from("/custom/xdg/voice-typer"));
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn test_config_dir_linux_xdg_empty_falls_back_to_home() {
        // Empty XDG_DATA_HOME should be treated as unset (per XDG spec).
        let p = config_dir_from_env(Some("/home/user"), None, Some(""));
        assert_eq!(
            p,
            std::path::PathBuf::from("/home/user/.local/share/voice-typer")
        );
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn test_config_dir_macos() {
        let p = config_dir_from_env(Some("/Users/user"), None, None);
        assert_eq!(
            p,
            std::path::PathBuf::from("/Users/user/Library/Application Support/voice-typer")
        );
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn test_config_dir_windows() {
        let p = config_dir_from_env(None, Some(r"C:\Users\user\AppData\Roaming"), None);
        assert_eq!(
            p,
            std::path::PathBuf::from(r"C:\Users\user\AppData\Roaming\voice-typer")
        );
    }

    // ── now_timestamp ─────────────────────────────────────────────────

    #[test]
    fn test_now_timestamp_format() {
        let ts = now_timestamp();
        // Expected: "YYYY-MM-DD HH:MM:SS.mmm" → 23 chars.
        assert_eq!(ts.len(), 23, "unexpected timestamp length: \"{}\"", ts);
        assert_eq!(ts.chars().nth(4), Some('-'), "year-month sep: {}", ts);
        assert_eq!(ts.chars().nth(7), Some('-'), "month-day sep: {}", ts);
        assert_eq!(ts.chars().nth(10), Some(' '), "date-time sep: {}", ts);
        assert_eq!(ts.chars().nth(13), Some(':'), "hour-min sep: {}", ts);
        assert_eq!(ts.chars().nth(16), Some(':'), "min-sec sep: {}", ts);
        assert_eq!(ts.chars().nth(19), Some('.'), "sec-ms sep: {}", ts);
    }

    #[test]
    fn test_now_timestamp_increases() {
        let t1 = now_timestamp();
        std::thread::sleep(std::time::Duration::from_millis(10));
        let t2 = now_timestamp();
        // The timestamp should not decrease (compare lexicographically
        // since the format is fixed-width sortable).
        assert!(t2 >= t1, "timestamp went backwards: t1={} t2={}", t1, t2);
    }

    // ── RotatingFileWriter ────────────────────────────────────────────

    #[test]
    fn test_rotating_file_writer_basic_write() {
        let tmp = std::env::temp_dir().join(format!(
            "voice-typer-test-{}-basic",
            std::process::id()
        ));
        std::fs::remove_dir_all(&tmp).ok();
        let writer = RotatingFileWriter::new(tmp.clone(), "test-log");
        writer.write_line("hello").unwrap();
        writer.write_line("world").unwrap();
        let content =
            std::fs::read_to_string(tmp.join("test-log.log")).unwrap();
        assert_eq!(content, "hello\nworld\n");
        std::fs::remove_dir_all(&tmp).ok();
    }

    #[test]
    fn test_rotating_file_writer_rotation() {
        // Use a tiny threshold by writing many large lines.
        // ROTATE_MAX_BYTES is 5 MB; writing 6 MB should trigger at
        // least one rotation.
        let tmp = std::env::temp_dir().join(format!(
            "voice-typer-test-{}-rotate",
            std::process::id()
        ));
        std::fs::remove_dir_all(&tmp).ok();
        let writer = RotatingFileWriter::new(tmp.clone(), "test-log");
        // 6 MB total, 100 KB per line → ~60 lines.
        let big_line = "x".repeat(100_000);
        for _ in 0..60 {
            writer.write_line(&big_line).unwrap();
        }
        // After rotation, `.log` should exist (the current file) and
        // `.log.1` should exist (the first rotated file).
        assert!(tmp.join("test-log.log").exists(), "current log missing");
        assert!(
            tmp.join("test-log.log.1").exists(),
            "first rotated log missing"
        );
        std::fs::remove_dir_all(&tmp).ok();
    }

    #[test]
    fn test_rotating_file_writer_thread_safety() {
        // Spawn multiple threads writing to the same writer — should
        // not panic or corrupt (Mutex protects the inner File).
        let tmp = std::env::temp_dir().join(format!(
            "voice-typer-test-{}-threads",
            std::process::id()
        ));
        std::fs::remove_dir_all(&tmp).ok();
        let writer = std::sync::Arc::new(RotatingFileWriter::new(tmp.clone(), "test-log"));
        let mut handles = Vec::new();
        for i in 0..4 {
            let w = writer.clone();
            handles.push(std::thread::spawn(move || {
                for j in 0..50 {
                    w.write_line(&format!("thread-{}-line-{}", i, j)).unwrap();
                }
            }));
        }
        for h in handles {
            h.join().unwrap();
        }
        // 4 threads × 50 lines = 200 lines total.
        let content =
            std::fs::read_to_string(tmp.join("test-log.log")).unwrap();
        let line_count = content.lines().count();
        // Could be fewer if rotation happened mid-write (the current
        // file gets renamed to .log.1 and a fresh .log starts). Just
        // assert we wrote *something* and didn't panic.
        assert!(line_count > 0, "no lines in current log: {}", content);
        std::fs::remove_dir_all(&tmp).ok();
    }

    // ── CR-13: generate_token (ADR-0020 §3) ──────────────────────────

    #[test]
    fn test_generate_token_is_64_char_hex() {
        // ADR-0020 §3: 32 random bytes hex-encoded → 64 hex chars.
        let token = generate_token();
        assert_eq!(token.len(), 64, "token must be 64 hex chars (32 bytes * 2)");
        assert!(
            token.chars().all(|c| c.is_ascii_hexdigit()),
            "token must be valid hex, got: {}",
            token
        );
    }

    #[test]
    fn test_generate_token_is_unique_across_calls() {
        // Two consecutive tokens must differ (vanishingly unlikely with
        // thread_rng, but guards against a regression that e.g. seeds a
        // fixed value or reuses a buffer without clearing).
        let t1 = generate_token();
        let t2 = generate_token();
        let t3 = generate_token();
        assert_ne!(t1, t2, "tokens must be unique: t1={} t2={}", t1, t2);
        assert_ne!(t2, t3, "tokens must be unique: t2={} t3={}", t2, t3);
        assert_ne!(t1, t3, "tokens must be unique: t1={} t3={}", t1, t3);
    }

    // ── CR-13: bubble_level coalesce (ADR-0020 §9) ───────────────────

    #[test]
    fn test_bubble_coalesce_should_emit_first_event() {
        // First event (no prior emit) → always emit.
        let now = Instant::now();
        assert!(bubble_coalesce_should_emit(None, now, BUBBLE_LEVEL_COALESCE_HZ));
    }

    #[test]
    fn test_bubble_coalesce_should_emit_respects_min_interval() {
        // With hz=30, min_interval = 33ms. An event 32ms after the last
        // emit should be suppressed; an event 33ms after should pass.
        let start = Instant::now();
        let hz = BUBBLE_LEVEL_COALESCE_HZ;
        // 32ms gap → suppressed.
        let too_soon = start + Duration::from_millis(32);
        assert!(
            !bubble_coalesce_should_emit(Some(start), too_soon, hz),
            "event 32ms after last emit should be suppressed (min_interval=33ms)"
        );
        // 33ms gap → emitted (>= comparison).
        let just_enough = start + Duration::from_millis(33);
        assert!(
            bubble_coalesce_should_emit(Some(start), just_enough, hz),
            "event 33ms after last emit should pass (min_interval=33ms, >= comparison)"
        );
        // 100ms gap → emitted.
        let well_after = start + Duration::from_millis(100);
        assert!(
            bubble_coalesce_should_emit(Some(start), well_after, hz),
            "event 100ms after last emit should pass"
        );
    }

    #[test]
    fn test_bubble_level_coalesce_respects_30hz_cap() {
        // Simulate a 60 Hz event stream for ~1 second (60 events, ~16.67ms
        // apart). With BUBBLE_LEVEL_COALESCE_HZ=30 (min interval 33ms),
        // every other event passes the filter → exactly 30 emits per
        // simulated second, hitting the cap without exceeding it.
        let hz = BUBBLE_LEVEL_COALESCE_HZ;
        let start = Instant::now();
        let step_60hz = Duration::from_micros(16_667); // ~16.67ms = 1/60 s
        let mut last_emitted: Option<Instant> = None;
        let mut emitted = 0usize;
        for i in 0..60u32 {
            let now = start + step_60hz * i;
            if bubble_coalesce_should_emit(last_emitted, now, hz) {
                last_emitted = Some(now);
                emitted += 1;
            }
        }
        assert!(
            emitted <= 30,
            "emitted {} events in 1s, expected ≤30 (30 Hz cap)",
            emitted
        );
        // The 60 Hz stream downsampled to a 30 Hz cap should emit ~30
        // events per second (exactly 30 with 16.667ms spacing — every
        // other event). Allow a small ±2 tolerance in case integer
        // division edges shift the boundary by one.
        assert!(
            emitted >= 28,
            "emitted {} events in 1s, expected ~30 — coalesce is too aggressive",
            emitted
        );
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

    // ── CR-13: pending-dispatch map (ADR-0020 §7) ────────────────────

    #[tokio::test]
    async fn test_pending_dispatch_map_fulfill_by_id() {
        // ADR-0020 §7: the WS reader task fulfills pending dispatch
        // requests by id. Insert a pending request with id=42, then
        // fulfill it with a response carrying id=42 — the oneshot
        // receiver must resolve with that exact response, and the map
        // must be empty afterwards.
        let pending: PendingMap = Arc::new(AsyncMutex::new(HashMap::new()));
        let id = 42u64;

        // Insert the pending request.
        let (tx, rx) = oneshot::channel::<Value>();
        pending.lock().await.insert(id, tx);
        assert_eq!(pending.lock().await.len(), 1, "pending map should have 1 entry after insert");

        // Simulate the WS reader fulfilling the request by id.
        let response = json!({"id": 42, "type": "result", "data": {"ok": true}});
        {
            let mut map = pending.lock().await;
            if let Some(sender) = map.remove(&id) {
                let _ = sender.send(response.clone());
            }
        }

        // The oneshot must resolve with the response (within a generous
        // 1s timeout — should be near-instant since the send already
        // happened).
        let received = tokio::time::timeout(Duration::from_secs(1), rx)
            .await
            .expect("oneshot did not resolve within 1s — sender was never invoked")
            .expect("oneshot sender was dropped without sending");
        assert_eq!(received, response, "received response must match the sent payload");

        // Map must be empty after fulfillment.
        assert_eq!(
            pending.lock().await.len(),
            0,
            "pending map should be empty after fulfillment"
        );
    }

    #[tokio::test]
    async fn test_pending_dispatch_map_unfulfilled_id_leaves_entry() {
        // A response with the wrong id must NOT fulfill a pending request.
        // The dispatch caller will time out (DISPATCH_TIMEOUT_SECS) and
        // remove the entry itself (see `dispatch` command).
        let pending: PendingMap = Arc::new(AsyncMutex::new(HashMap::new()));
        let id = 99u64;
        let (tx, _rx) = oneshot::channel::<Value>();
        pending.lock().await.insert(id, tx);

        // Try to fulfill with the WRONG id (id=100, not 99).
        let wrong_response = json!({"id": 100, "type": "result"});
        {
            let mut map = pending.lock().await;
            // The reader task does `pending.remove(&id_from_frame)`, so
            // a mismatched id simply doesn't find an entry.
            if let Some(sender) = map.remove(&100u64) {
                let _ = sender.send(wrong_response.clone());
            }
        }

        // Entry for id=99 must still be present (the wrong-id fulfill
        // was a no-op from this map's perspective).
        assert_eq!(
            pending.lock().await.len(),
            1,
            "pending entry for id=99 must remain when a wrong-id response arrives"
        );
        assert!(
            pending.lock().await.contains_key(&id),
            "pending map must still contain id=99"
        );
    }

    // ── CR-13: FT-1 backoff constants (ADR-0020 §10) ─────────────────

    #[test]
    fn test_ft1_backoff_constants() {
        // ADR-0020 §10: FT-1 supervisor backoff schedule + retry cap.
        // The schedule doubles each step (500ms → 1s → 2s → 4s → 8s)
        // and the cap is 5 retries before full-app relaunch.
        assert_eq!(
            FT1_BACKOFF_MS,
            &[500, 1000, 2000, 4000, 8000],
            "FT1_BACKOFF_MS must be [500, 1000, 2000, 4000, 8000] (doubling schedule)"
        );
        assert_eq!(
            FT1_MAX_RETRIES, 5,
            "FT1_MAX_RETRIES must be 5 (then fall back to full-app relaunch)"
        );
        // The schedule length must match the retry cap so the loop in
        // `ft1_respawn_inner` actually iterates FT1_MAX_RETRIES times
        // (each iteration sleeps delay_ms[attempt] before retrying)
        // before falling back to `app.restart()`.
        assert_eq!(
            FT1_BACKOFF_MS.len() as u32,
            FT1_MAX_RETRIES,
            "FT1_BACKOFF_MS.len() must equal FT1_MAX_RETRIES so the loop iterates exactly N times"
        );
        // Verify the doubling property explicitly — guards against an
        // accidental edit that breaks the geometric progression.
        for i in 1..FT1_BACKOFF_MS.len() {
            assert_eq!(
                FT1_BACKOFF_MS[i],
                FT1_BACKOFF_MS[i - 1] * 2,
                "backoff step {} must be 2x step {} (got {} vs {})",
                i,
                i - 1,
                FT1_BACKOFF_MS[i],
                FT1_BACKOFF_MS[i - 1]
            );
        }
    }

    #[test]
    fn test_shutdown_ack_timeout_constant() {
        // ADR-0020 §10: cooperative shutdown hard timeout. The sidecar
        // must ack `{"type":"shutdown"}` and exit within this window;
        // if it doesn't, the host force-kills the process tree.
        // CR-2 polls `CommandEvent::Terminated` against this same
        // deadline via `tokio::time::timeout`.
        assert_eq!(
            SHUTDOWN_ACK_TIMEOUT_MS, 2000,
            "SHUTDOWN_ACK_TIMEOUT_MS must be 2000 (2s graceful window)"
        );
        // The poll interval is only used by the dev-mode fallback path
        // (the ShellPlugin path now uses tokio::time::timeout + rx.recv).
        assert_eq!(
            SHUTDOWN_POLL_INTERVAL_MS, 100,
            "SHUTDOWN_POLL_INTERVAL_MS must be 100ms (dev-mode fallback step)"
        );
    }
}
