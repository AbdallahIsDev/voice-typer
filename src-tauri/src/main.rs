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
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use futures_util::{SinkExt, StreamExt};
use rand::RngCore;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tauri::{Emitter, Manager, WindowEvent};
use tauri_plugin_shell::process::{CommandEvent, CommandChild};
use tauri_plugin_shell::ShellExt;
use tokio::sync::{mpsc, oneshot, Mutex as AsyncMutex};
use tokio_tungstenite::{connect_async_with_config, tungstenite::Message};

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

/// ADR-0020 §10: 1 MiB WS frame cap. Enforced at WS-connect time via
/// `connect_async_with_config(WebSocketConfig { max_message_size:
/// Some(MAX_FRAME_BYTES), .. })`. Guards against memory-exhaustion
/// attacks from a compromised sidecar.
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
                if let Ok(v) = serde_json::from_str::<Value>(line.trim()) {
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

// ─── FT-1 supervisor (ADR-0020 §10) ───────────────────────────────────

async fn ft1_respawn(
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
            tokio::time::sleep(Duration::from_millis(500)).await;
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
            Ok((port, child)) => {
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
    tokio::time::sleep(Duration::from_millis(500)).await;
    app.restart();
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
                        let min_interval = Duration::from_millis(1000 / BUBBLE_LEVEL_COALESCE_HZ);
                        if last_bubble_level.map_or(true, |t| now.duration_since(t) >= min_interval) {
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

                    // ADR-0020 §6.1: rename `electron_notification` →
                    // `notification`; `relaunch_electron` → `relaunch_app`.
                    let emit_name = match event_type {
                        "electron_notification" => "notification",
                        "relaunch_electron" => "relaunch_app",
                        other => other,
                    };
                    // ADR-0020 §6.3: emit BOTH the specific event (for
                    // direct listeners) AND the generic `python-event`
                    // (for the usePython hook's onEvent catch-all).
                    let _ = app_for_reader.emit(emit_name, payload.clone());
                    let _ = app_for_reader.emit("python-event", json!({"type": emit_name, "data": payload}));
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

    use enigo::{Enigo, Key, Keyboard, Settings};
    let short_threshold = 300;
    if text.chars().count() < short_threshold {
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
