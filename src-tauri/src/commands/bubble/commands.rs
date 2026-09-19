#![allow(clippy::unreachable)] // tauri command macro expansion emits `unreachable!()` fallbacks

//! Bubble window Tauri commands (ADR-0020 §9).
//! Pure helpers live in sibling `parse` / `math` / `rate_limit` / `window`.
//! NOTE: see docs/code-notes/tauri-host.md#bubble-window-commands

use serde_json::{json, Value};
use std::sync::Arc;
use tauri::{Emitter, Manager, PhysicalPosition};

use crate::error::VoiceTyperError;
use crate::state::SidecarState;

use super::math::{
    bubble_position_in_work_area, clamp_resize_height, clamp_resize_width, compute_move_by_new_pos,
    edge_margin_physical, rect_contains_point, round_f64_to_i32_saturating,
    round_f64_to_u32_saturating, RectPx,
};
use super::rate_limit::toggle_rate_limiter_allows;
use super::window::{hide_bubble_window, show_bubble_window};

// Tauri commands: bubble window (ADR-0020 §9)
//
// Window-origin policy: most bubble commands are intentionally NOT
// require_main_window-gated — the bubble may self-manage visibility,
// position, drag, resize. SEC-016 gates apply only where spoofing
// readiness/hide from another window would break UX.

/// Show the bubble window. Not main-window-gated (hover UX + WS reader path).
#[tauri::command]
pub async fn bubble_show(app: tauri::AppHandle) -> Result<(), VoiceTyperError> {
    show_bubble_window(&app).map_err(VoiceTyperError::Host)
}

/// Signal bubble page mounted + ready for `bubble_level`.
/// SEC-016: only the bubble window may signal readiness.
#[tauri::command]
pub async fn bubble_signal_ready(
    app: tauri::AppHandle,
    window: tauri::Window,
) -> Result<(), VoiceTyperError> {
    crate::commands::require_bubble_window(&window)?;
    app.emit_to("bubble", "bubble:ready", ())
        .map_err(|e| VoiceTyperError::Host(e.to_string()))
}

/// Place bubble on the cursor's monitor from a `"top"|"bottom"` keyword.
/// Not main-window-gated (sandboxed bubble may only move itself).
/// NOTE: see docs/code-notes/tauri-host.md#bubble-window-commands
#[tauri::command]
pub async fn bubble_set_position(
    position: String,
    app: tauri::AppHandle,
) -> Result<(), VoiceTyperError> {
    let window = app
        .get_webview_window("bubble")
        .ok_or(VoiceTyperError::Host("bubble window not found".into()))?;
    let monitor = resolve_cursor_monitor(&app)?;
    // Programmatic placement: suppress Moved-event persistence before the move.
    crate::commands::bubble::suppress_persist_for_window();
    // Physical work area (bounds minus taskbar/dock).
    let wa = monitor.work_area();
    let wa_rect = RectPx::new(wa.position.x, wa.position.y, wa.size.width, wa.size.height);
    let bubble_size = window.outer_size().map_err(|e| e.to_string())?;
    // Saturating u32→i32 (project pattern; no silent wrap).
    let bubble_w = i32::try_from(bubble_size.width).unwrap_or(i32::MAX);
    let bubble_h = i32::try_from(bubble_size.height).unwrap_or(i32::MAX);
    // Edge offset is DIPs; scale to this monitor's physical pixels.
    let margin = edge_margin_physical(monitor.scale_factor());
    let (px, py) = bubble_position_in_work_area(&position, &wa_rect, bubble_w, bubble_h, margin)?;
    window
        .set_position(PhysicalPosition::new(px, py))
        .map_err(|e| VoiceTyperError::Host(e.to_string()))
}

/// Resolve monitor the bubble should appear on (cursor display, then primary).
/// Full bounds hit-test is belt-and-suspenders for stacked/negative layouts.
fn resolve_cursor_monitor(app: &tauri::AppHandle) -> Result<tauri::window::Monitor, String> {
    if let Ok(cursor) = app.cursor_position() {
        match app.monitor_from_point(cursor.x, cursor.y) {
            Ok(Some(monitor)) => return Ok(monitor),
            Ok(None) | Err(_) => {
                // Fallback hit-test: saturating f64→i32 for NaN/±inf.
                let cx = round_f64_to_i32_saturating(cursor.x);
                let cy = round_f64_to_i32_saturating(cursor.y);
                let hit = app.available_monitors().ok().and_then(|monitors| {
                    monitors.into_iter().find(|m| {
                        let rect = RectPx::new(
                            m.position().x,
                            m.position().y,
                            m.size().width,
                            m.size().height,
                        );
                        rect_contains_point(&rect, cx, cy)
                    })
                });
                if let Some(monitor) = hit {
                    return Ok(monitor);
                }
                log::warn!(
                    "[BUBBLE] cursor ({cx},{cy}) matched no monitor: falling back to primary"
                );
            }
        }
    }
    app.primary_monitor()
        .map_err(|e| e.to_string())?
        .ok_or_else(|| "no primary monitor available".to_string())
}

/// Toggle bubble draggable via `bubble:draggable` event (Tauri v2 has no set_draggable).
/// Not main-window-gated (bubble may self-toggle drag while interacting).
#[tauri::command]
pub async fn bubble_set_draggable(
    draggable: bool,
    app: tauri::AppHandle,
) -> Result<(), VoiceTyperError> {
    app.emit_to("bubble", "bubble:draggable", draggable)
        .map_err(|e| VoiceTyperError::Host(e.to_string()))
}

/// Move bubble by (dx, dy) physical pixels; returns new {x,y}.
/// Not main-window-gated (drag path lives in Bubble.tsx).
/// spawn_blocking: OS-IPC outer_position+set_position at ~60 Hz mousemove
/// must not pin an async worker (C-TOKIO-1 adjacent).
#[tauri::command]
pub async fn bubble_move_by(
    dx: f64,
    dy: f64,
    app: tauri::AppHandle,
) -> Result<Value, VoiceTyperError> {
    let join_result = tauri::async_runtime::spawn_blocking(move || -> Result<Value, String> {
        let window = app
            .get_webview_window("bubble")
            .ok_or("bubble window not found")?;
        let pos = window.outer_position().map_err(|e| e.to_string())?;
        // Saturating f64→i32 at the FFI boundary (NaN/±inf defined).
        let dx_i32 = round_f64_to_i32_saturating(dx);
        let dy_i32 = round_f64_to_i32_saturating(dy);
        // checked_add: huge dx/dy must error, not wrap the bubble off-screen.
        let (new_x, new_y) = compute_move_by_new_pos(pos.x, dx_i32, pos.y, dy_i32)?;
        window
            .set_position(PhysicalPosition::new(new_x, new_y))
            .map_err(|e| e.to_string())?;
        Ok(json!({"x": new_x, "y": new_y}))
    })
    .await;
    match join_result {
        Ok(inner) => Ok(inner?),
        Err(join_err) => Err(VoiceTyperError::Host(format!(
            "bubble_move_by blocking task failed: {join_err}"
        ))),
    }
}

/// Hide bubble after renderer cleanup. SEC-016: only the bubble may finalize hide.
#[tauri::command]
pub async fn bubble_hide_complete(
    app: tauri::AppHandle,
    window: tauri::Window,
) -> Result<(), VoiceTyperError> {
    crate::commands::require_bubble_window(&window)?;
    hide_bubble_window(&app).map_err(VoiceTyperError::from)
}

/// Dismiss bubble from its own '×' button. Same hide path as hide_complete.
/// SEC-016: only the bubble may dismiss itself.
#[tauri::command]
pub async fn bubble_dismiss(
    app: tauri::AppHandle,
    window: tauri::Window,
) -> Result<(), VoiceTyperError> {
    crate::commands::require_bubble_window(&window)?;
    hide_bubble_window(&app).map_err(VoiceTyperError::from)
}

/// Resize bubble to (width, height) physical pixels; clamped to pill MIN/MAX.
/// Not main-window-gated (ResizeObserver auto-fit). f64 at FFI → saturating u32.
#[tauri::command]
pub async fn bubble_resize(
    width: f64,
    height: f64,
    app: tauri::AppHandle,
) -> Result<(), VoiceTyperError> {
    let window = app
        .get_webview_window("bubble")
        .ok_or(VoiceTyperError::Host("bubble window not found".into()))?;
    // Saturating f64→u32, then clamp to predecessor pill bounds.
    let w = round_f64_to_u32_saturating(width);
    let h = round_f64_to_u32_saturating(height);
    let capped_w = clamp_resize_width(w);
    let capped_h = clamp_resize_height(h);
    use tauri::PhysicalSize;
    window
        .set_size(PhysicalSize::new(capped_w, capped_h))
        .map_err(|e| VoiceTyperError::Host(e.to_string()))
}

/// Bubble mic-button toggle. ONLY sanctioned dispatch-allowlist bypass (SEC-026):
/// fixed `toggle_dictation` envelope built in Rust, fire-and-forget, rate-limited
/// 1/500ms. Bubble cannot invoke arbitrary sidecar commands.
/// NOTE: see docs/code-notes/tauri-host.md#fire-and-forget-id0
#[tauri::command]
pub async fn bubble_toggle_dictation(
    state: tauri::State<'_, Arc<SidecarState>>,
) -> Result<(), VoiceTyperError> {
    // Rate limiter: drop rapid toggles silently (UI tracks actual status_change).
    if !toggle_rate_limiter_allows() {
        log::warn!("[BUBBLE] toggle_dictation rate-limited (last toggle <500ms ago), dropping");
        return Ok(());
    }
    // id=0, no pending entry; reader DEBUG-drops the response.
    crate::commands::sidecar_cmds::dispatch_fire_and_forget(state.inner(), "toggle_dictation", None)
}
