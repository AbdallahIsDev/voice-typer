//! Bubble window commands (MIG-1.2 + ADR-0020 §9).

use serde_json::{json, Value};
use tauri::{PhysicalPosition, Emitter, Manager};

// ─── Tauri commands: bubble window (MIG-1.2, ADR-0020 §9) ────────────

/// Show the bubble window (ADR-0020 §9 + MIG-1.2).
#[tauri::command]
pub async fn bubble_show(app: tauri::AppHandle) -> Result<(), String> {
    app.get_webview_window("bubble")
        .ok_or("bubble window not found")?
        .show()
        .map_err(|e| e.to_string())
}

/// Emit `bubble:ready` to the bubble window — the bubble renderer
/// listens for this and signals back to the Python sidecar that it's
/// ready to receive `bubble_level` events (ADR-0020 §9 + MIG-1.2).
#[tauri::command]
pub async fn bubble_signal_ready(app: tauri::AppHandle) -> Result<(), String> {
    app.emit_to("bubble", "bubble:ready", ())
        .map_err(|e| e.to_string())
}

/// Move the bubble window to `(x, y)` in physical pixels (ADR-0020 §9
/// + MIG-1.2). The TS bridge calls this with the cursor position
/// (offset by a small delta) so the bubble appears under the cursor.
///
/// XPLAT-6: the renderer's legacy `setPosition("top" | "bottom")` call
/// shape is supported by accepting `serde_json::Value` for `x` and `y`.
/// Numeric values are used directly; `"top"`/`"bottom"` strings compute
/// the appropriate y from the primary monitor size + bubble size
/// (centered horizontally). Any other shape returns an error.
#[tauri::command]
pub async fn bubble_set_position(
    x: Value,
    y: Value,
    app: tauri::AppHandle,
) -> Result<(), String> {
    let window = app
        .get_webview_window("bubble")
        .ok_or("bubble window not found")?;
    let monitor = app
        .primary_monitor()
        .map_err(|e| e.to_string())?
        .ok_or("no primary monitor available")?;
    let screen_size = monitor.size();
    let screen_w = screen_size.width as i32;
    let screen_h = screen_size.height as i32;
    let bubble_size = window
        .outer_size()
        .map_err(|e| e.to_string())?;
    let bubble_w = bubble_size.width as i32;
    let bubble_h = bubble_size.height as i32;
    let (px, py) = parse_position(x, y, screen_w, screen_h, bubble_w, bubble_h)?;
    window
        .set_position(PhysicalPosition::new(px, py))
        .map_err(|e| e.to_string())
}

/// Parse a position value into `(x, y)` physical pixels.
///
/// Accepts:
/// - numeric `x` (int or float) → used directly
/// - `"top"` → centered horizontally, y=0
/// - `"bottom"` → centered horizontally, y=screen_h - bubble_h
/// - `y` defaults to 0 if null/missing (legacy string-shape calls)
fn parse_position(
    x: Value,
    y: Value,
    screen_w: i32,
    screen_h: i32,
    bubble_w: i32,
    bubble_h: i32,
) -> Result<(i32, i32), String> {
    let px = match &x {
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                i as i32
            } else if let Some(f) = n.as_f64() {
                f as i32
            } else {
                return Err(format!("x must be a number, got {:?}", x));
            }
        }
        Value::String(s) => match s.as_str() {
            "top" => (screen_w - bubble_w) / 2,
            "bottom" => (screen_w - bubble_w) / 2,
            other => return Err(format!("x string must be \"top\" or \"bottom\", got {:?}", other)),
        },
        Value::Null => 0,
        _ => return Err(format!("x must be a number, string, or null, got {:?}", x)),
    };
    let py = match &y {
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                i as i32
            } else if let Some(f) = n.as_f64() {
                f as i32
            } else {
                return Err(format!("y must be a number, got {:?}", y));
            }
        }
        Value::String(s) => match s.as_str() {
            "top" => 0,
            "bottom" => (screen_h - bubble_h).max(0),
            other => return Err(format!("y string must be \"top\" or \"bottom\", got {:?}", other)),
        },
        Value::Null => 0,
        _ => return Err(format!("y must be a number, string, or null, got {:?}", y)),
    };
    Ok((px, py))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn test_parse_position_numeric_int() {
        let (x, y) = parse_position(json!(100), json!(200), 1920, 1080, 320, 80).unwrap();
        assert_eq!(x, 100);
        assert_eq!(y, 200);
    }

    #[test]
    fn test_parse_position_numeric_float() {
        let (x, y) = parse_position(json!(100.7), json!(200.3), 1920, 1080, 320, 80).unwrap();
        assert_eq!(x, 100);
        assert_eq!(y, 200);
    }

    #[test]
    fn test_parse_position_top_string() {
        let (x, y) = parse_position(json!("top"), json!("top"), 1920, 1080, 320, 80).unwrap();
        assert_eq!(x, (1920 - 320) / 2);
        assert_eq!(y, 0);
    }

    #[test]
    fn test_parse_position_bottom_string() {
        let (x, y) = parse_position(json!("bottom"), json!("bottom"), 1920, 1080, 320, 80).unwrap();
        assert_eq!(x, (1920 - 320) / 2);
        assert_eq!(y, 1080 - 80);
    }

    #[test]
    fn test_parse_position_null_y_defaults_to_zero() {
        let (x, y) = parse_position(json!(50), json!(null), 1920, 1080, 320, 80).unwrap();
        assert_eq!(x, 50);
        assert_eq!(y, 0);
    }

    #[test]
    fn test_parse_position_negative_coords() {
        let (x, y) = parse_position(json!(-10), json!(-20), 1920, 1080, 320, 80).unwrap();
        assert_eq!(x, -10);
        assert_eq!(y, -20);
    }

    #[test]
    fn test_parse_position_bottom_clamped_when_bubble_taller_than_screen() {
        let (x, y) = parse_position(json!("bottom"), json!("bottom"), 320, 80, 400, 200).unwrap();
        assert_eq!(x, (320 - 400) / 2);
        assert_eq!(y, 0); // (80 - 200).max(0) == 0
    }

    #[test]
    fn test_parse_position_invalid_x_string() {
        let result = parse_position(json!("middle"), json!(0), 1920, 1080, 320, 80);
        assert!(result.is_err());
    }

    #[test]
    fn test_parse_position_invalid_x_type() {
        let result = parse_position(json!([1, 2]), json!(0), 1920, 1080, 320, 80);
        assert!(result.is_err());
    }

    #[test]
    fn test_parse_position_invalid_y_string() {
        let result = parse_position(json!(0), json!("middle"), 1920, 1080, 320, 80);
        assert!(result.is_err());
    }

    #[test]
    fn test_parse_position_invalid_y_type() {
        let result = parse_position(json!(0), json!({"a": 1}), 1920, 1080, 320, 80);
        assert!(result.is_err());
    }

    #[test]
    fn test_parse_position_bool_rejected() {
        let result = parse_position(json!(true), json!(0), 1920, 1080, 320, 80);
        assert!(result.is_err());
    }

    #[test]
    fn test_parse_position_top_with_string_x_and_null_y() {
        let (x, y) = parse_position(json!("top"), json!(null), 1920, 1080, 320, 80).unwrap();
        assert_eq!(x, (1920 - 320) / 2);
        assert_eq!(y, 0);
    }
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
pub async fn bubble_set_draggable(
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
pub async fn bubble_move_by(
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
pub async fn bubble_hide_complete(app: tauri::AppHandle) -> Result<(), String> {
    let window = app
        .get_webview_window("bubble")
        .ok_or("bubble window not found")?;
    window.hide().map_err(|e| e.to_string())?;
    app.emit_to("bubble", "bubble:hide_complete", ())
        .map_err(|e| e.to_string())
}
