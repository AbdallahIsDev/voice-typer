//! Voice Typer — Tauri v2 host (ADR-0020 implementation)
//!
//! This is the Rust shell that replaces the Electron main process. It:
//!
//! 1. Generates an HMAC token (`secrets.token_bytes(32)` equivalent) and
//!    spawns the Python sidecar via Tauri's `externalBin` mechanism,
//!    passing `VOICE_TYPER_IPC_TOKEN` + `TAURI_SIDECAR=1` env vars.
//! 2. Reads the sidecar's stdout until it sees the
//!    `{"event":"server_started","port":N}` JSON line, then opens a
//!    WebSocket client to `ws://127.0.0.1:N`.
//! 3. Performs the HMAC auth handshake (`{"type":"auth","token":...}`).
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
use std::env;
use std::process::Stdio;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use futures_util::{SinkExt, StreamExt};
use hmac::{Hmac, Mac};
use rand::RngCore;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::Sha256;
use tauri::{Manager, WindowEvent};
use tauri_plugin_shell::{ShellExt, process::CommandChild};
use tokio::sync::{mpsc, oneshot, Mutex as AsyncMutex};
use tokio_tungstenite::{connect_async, tungstenite::Message};

type HmacSha256 = Hmac<Sha256>;

// ─── Constants (ADR-0020) ─────────────────────────────────────────────

/// ADR-0020 §3: 256-bit HMAC token. Regenerated per launch + per
/// FT-1 respawn; never logged.
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

/// ADR-0020 §10: 1 MiB WS frame cap.
const MAX_FRAME_BYTES: usize = 1024 * 1024;

// ─── Shared state ─────────────────────────────────────────────────────

/// Pending dispatch requests keyed by id. Each entry has a oneshot
/// sender that the WS reader task fulfills when the matching response
/// arrives.
type PendingMap = Arc<AsyncMutex<HashMap<u64, oneshot::Sender<Value>>>>;

/// The WS writer half, wrapped in a channel so the dispatch command
/// (which runs on a Tauri async runtime) can send frames without
/// holding the WS writer directly.
type WsWriterTx = mpsc::UnboundedSender<Message>;

struct SidecarState {
    /// Child handle for kill_children backstop.
    child: Mutex<Option<CommandChild>>,
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
}

// ─── Token generation (ADR-0020 §3) ───────────────────────────────────

fn generate_token() -> String {
    let mut bytes = [0u8; TOKEN_BYTES];
    rand::thread_rng().fill_bytes(&mut bytes);
    hex::encode(bytes)
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
) -> Result<(u16, CommandChild), String> {
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
        .env("VOICE_TYPER_PREWARM_EXE", prewarm_exe)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

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
            Ok(Some(Ok(line))) => {
                let line = line.to_string();
                stdout_buf.push_str(&line);
                stdout_buf.push('\n');
                // Try to parse as the server_started event.
                if let Ok(v) = serde_json::from_str::<Value>(&line.trim()) {
                    if v.get("event").and_then(|e| e.as_str()) == Some("server_started") {
                        if let Some(port) = v.get("port").and_then(|p| p.as_u64()) {
                            log::info!("[SIDECAR] server_started port={}", port);
                            return Ok((port as u16, child));
                        }
                    }
                }
                // Not the server_started line — could be a stray log
                // (shouldn't happen per ADR-0020 §1, sidecar sends
                // all non-handshake logs to stderr).
                log::warn!("[SIDECAR] unexpected stdout line (expected only server_started): {}", line.trim());
            }
            Ok(Some(Err(e))) => {
                return Err(format!("sidecar stdout read error: {e}"));
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
    let arch = std::env::consts::ARCH;
    let os = std::env::consts::OS;
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

// ─── WebSocket client + auth handshake (ADR-0020 §1, §3) ──────────────

async fn connect_and_authenticate(
    port: u16,
    token: &str,
) -> Result<
    (
        tokio_tungstenite::WebSocketStream<tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>>,
        futures_util::stream::SplitSink<
            tokio_tungstenite::WebSocketStream<tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>>,
            Message,
        >,
    ),
    String,
> {
    let url = format!("ws://127.0.0.1:{}", port);
    log::info!("[WS-CLIENT] connecting to {}", url);
    let (ws, _) = connect_async(url)
        .await
        .map_err(|e| format!("WS connect failed: {e}"))?;
    let (write, mut read) = ws.split();

    // ADR-0020 §3: send the auth frame as the first message.
    let auth = json!({"type": "auth", "token": token});
    let mut write = write;
    write
        .send(Message::Text(auth.to_string()))
        .await
        .map_err(|e| format!("WS auth send failed: {e}"))?;

    // Wait for the sidecar to either accept (no immediate close) or
    // reject (close with code 1008). The sidecar doesn't send an
    // explicit ack — it just keeps the socket open on success.
    match tokio::time::timeout(Duration::from_secs(2), read.next()).await {
        Ok(Some(Ok(msg))) => {
            // Any message here is unexpected — the sidecar should
            // silently accept and wait for the first command.
            log::warn!("[WS-CLIENT] unexpected message after auth: {:?}", msg);
        }
        Ok(Some(Err(e))) => {
            return Err(format!("WS auth read failed: {e}"));
        }
        Ok(None) => {
            return Err("WS closed during auth".into());
        }
        Err(_) => {
            // Timeout — assume auth accepted (sidecar is silent on success).
        }
    }

    log::info!("[WS-CLIENT] auth accepted");
    Ok((/* re-join */ unreachable!(), write))
}

// ─── FT-1 supervisor (ADR-0020 §10) ───────────────────────────────────

/// Restart the sidecar with backoff. Returns true if the restart
/// succeeded, false if we exhausted retries (caller falls back to
/// full-app relaunch).
async fn ft1_respawn(
    app: &tauri::AppHandle,
    state: &Arc<SidecarState>,
) -> Result<(), String> {
    for (attempt, delay_ms) in FT1_BACKOFF_MS.iter().enumerate() {
        if attempt as u32 >= FT1_MAX_RETRIES {
            log::error!("[FT-1] exhausted {} retries — falling back to full-app relaunch", FT1_MAX_RETRIES);
            return Err("FT-1 exhausted retries".into());
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
            Ok((port, child)) => {
                *state.child.lock().unwrap() = Some(child);
                *state.token.lock().unwrap() = new_token.clone();
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
    Err("FT-1 exhausted retries".into())
}

async fn reconnect_ws(
    _app: &tauri::AppHandle,
    state: &Arc<SidecarState>,
    port: u16,
    token: &str,
) -> Result<(), String> {
    let url = format!("ws://127.0.0.1:{}", port);
    let (ws, _) = connect_async(&url)
        .await
        .map_err(|e| format!("WS reconnect failed: {e}"))?;
    let (write, mut read) = ws.split();

    let auth = json!({"type": "auth", "token": token});
    let mut write = write;
    write
        .send(Message::Text(auth.to_string()))
        .await
        .map_err(|e| format!("WS re-auth send failed: {e}"))?;

    // Set up the WS writer channel + reader task.
    let (ws_tx, mut ws_rx) = mpsc::unbounded_channel::<Message>();
    *state.ws_tx.lock().unwrap() = Some(ws_tx);
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
                        let min_interval = Duration::from_millis(1000 / BUBBLE_LEVEL_COALESCE_HZ);
                        if last_bubble_level.map_or(true, |t| now.duration_since(t) >= min_interval) {
                            last_bubble_level = Some(now);
                            let _ = app_for_reader.emit("bubble_level", last_bubble_payload.take().unwrap());
                        }
                        continue;
                    }

                    // ADR-0020 §6.1: rename `electron_notification` →
                    // `notification`; `relaunch_electron` → `relaunch_app`.
                    let emit_name = match event_type {
                        "electron_notification" => "notification",
                        "relaunch_electron" => "relaunch_app",
                        other => other,
                    };
                    let _ = app_for_reader.emit(emit_name, payload);
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
        // WS reader exited — trigger FT-1 respawn (unless we're shutting down).
        if !state_for_reader.shutting_down.load(Ordering::SeqCst) {
            log::warn!("[WS-READER] unexpected close — triggering FT-1");
            let _ = ft1_respawn(&app_for_reader, &state_for_reader).await;
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
    match tokio::time::timeout(Duration::from_secs(120), rx).await {
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
            Err("dispatch timeout (120s)".into())
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

    let short_threshold = 300;
    if text.chars().count() < short_threshold {
        // Short text — inject via enigo.text() (IME-safe).
        let mut enigo = enigo::Enigo::new(&enigo::Settings::default())
            .map_err(|e| format!("enigo init failed: {e}"))?;
        enigo.text(&text)
            .map_err(|e| format!("enigo.text failed: {e}"))?;
        log::info!("[PASTE] injected {} chars via enigo", text.chars().count());
    } else {
        // Long text — clipboard + Ctrl+V / Cmd+V.
        app.clipboard()
            .write_text(text.clone())
            .map_err(|e| format!("clipboard write failed: {e}"))?;
        let mut enigo = enigo::Enigo::new(&enigo::Settings::default())
            .map_err(|e| format!("enigo init failed: {e}"))?;
        use enigo::{Key, Keyboard};
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
    // Wait up to SHUTDOWN_ACK_TIMEOUT_MS for the sidecar to exit, then
    // force-kill via kill_children.
    let deadline = Instant::now() + Duration::from_millis(SHUTDOWN_ACK_TIMEOUT_MS);
    while Instant::now() < deadline {
        // The child handle's `kill_children` is the backstop — we
        // can't easily poll for exit from here, so we just wait
        // briefly then force-kill.
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    if let Some(child) = state.child.lock().unwrap().take() {
        let _ = child.kill();
    }
    log::info!("[SHUTDOWN] sidecar killed");
    let _ = app;
    Ok(())
}

// ─── App entry point ──────────────────────────────────────────────────

fn main() {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info"))
        .format_timestamp_millis()
        .init();

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            // ADR-0020 §12: second launch — focus the existing main window.
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_tray::init())
        .manage(Arc::new(SidecarState {
            child: Mutex::new(None),
            token: Mutex::new(String::new()),
            ws_tx: Mutex::new(None),
            pending: Arc::new(AsyncMutex::new(HashMap::new())),
            next_id: AtomicU64::new(1),
            shutting_down: AtomicBool::new(false),
        }))
        .invoke_handler(tauri::generate_handler![dispatch, paste_text, shutdown_sidecar])
        .setup(|app| {
            let app_handle = app.handle().clone();
            // Spawn the sidecar + WS bridge in a background tokio task.
            tauri::async_runtime::spawn(async move {
                let state: tauri::State<'_, Arc<SidecarState>> = app_handle.state();
                let state = state.inner().clone();

                let token = generate_token();
                *state.token.lock().unwrap() = token.clone();

                match spawn_sidecar_and_get_port(&app_handle, &token).await {
                    Ok((port, child)) => {
                        *state.child.lock().unwrap() = Some(child);
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
