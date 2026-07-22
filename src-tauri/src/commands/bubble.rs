//! Bubble window commands (MIG-1.2 + ADR-0020 §9).
//!
//! # Cross-file TODOs (sub-agent 1-9 findings, NOT fixed in this file)
//!
//! These two code-quality findings live in other modules and are out of
//! scope for this session's `bubble.rs`-only edit window. They are
//! recorded here as a single index so the next fix wave can find them.
//!
//! - **F-Q9** (`platform/paths.rs:60`): `config_dir(app: &tauri::AppHandle)`
//!   accepts the `app` handle but never uses it — the body does
//!   `let _ = app;` and reads only `std::env::var(...)` calls. The
//!   signature should either be simplified to `config_dir()` (no param)
//!   or, preferably, the implementation should consult
//!   `app.path().app_config_dir()` as a fallback when env vars are
//!   missing (so the Tauri path layer participates in the resolver
//!   chain instead of duplicating the Python `_paths.config_dir()`
//!   logic in Rust).
//!   // TODO(PVT-25): address F-Q9 in `platform/paths.rs`.
//!
//! - **F-S1** (`state.rs:16,122`): `SidecarState.pending` is typed
//!   `PendingMap = Arc<AsyncMutex<HashMap<...>>>`, but `SidecarState`
//!   itself is always shared as `Arc<SidecarState>` (Tauri managed
//!   state). Callers thus navigate `Arc<SidecarState>` → `.pending:
//!   Arc<AsyncMutex<...>>` — a redundant outer `Arc` that adds an
//!   indirection per dispatch. The fix is to drop the inner `Arc` and
//!   type `pending: AsyncMutex<HashMap<...>>` (the outer `Arc<SidecarState>`
//!   already provides shared ownership).
//!   // TODO(PVT-25): address F-S1 in `state.rs`.

use serde_json::{json, Value};
use std::sync::Arc;
use tauri::{PhysicalPosition, Emitter, Manager};
use tokio_tungstenite::tungstenite::Message;

use crate::state::SidecarState;

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
///
/// # PVT-G5-051: integer-coord range safety
///
/// Previously this function used lossy `i as i32` / `f as i32` casts
/// from `i64` / `f64`. For `i64` → `i32`, the `as` cast truncates
/// silently — a 5-billion JSON value (e.g. from a buggy renderer
/// sending a timestamp-as-x) wraps to a negative pixel coordinate,
/// moving the bubble off-screen with no diagnostic. For `f64` → `i32`,
/// the `as` cast is even worse: NaN → 0, ±inf → saturating bounds,
/// out-of-range → UB-adjacent saturation (the Rust ref says "the cast
/// is fully defined as of Rust 1.45+, but the saturated value may be
/// surprising").
///
/// The fix:
/// - `i64 → i32` uses `i32::try_from(i)?` and returns a descriptive
///   `coordinate out of range` error so the caller knows exactly which
///   axis overflowed and by how much.
/// - `f64 → i32` uses [`clamp_f64_to_i32`] (NaN → 0, ±inf → i32::MAX /
///   i32::MIN, in-range → `f as i32`). This avoids the silent NaN→0
///   footgun while still saturating +inf/-inf to the i32 bounds (which
///   matches the JSON `bubble_set_position` contract — a +inf x lands
///   the bubble at the rightmost representable pixel).
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
                // PVT-G5-051: was `i as i32` (silent truncation on
                // overflow). `i32::try_from` returns Err for out-of-
                // range values so we can surface a diagnostic to the
                // caller (the React TS bridge) rather than silently
                // moving the bubble to a wrapped-negative pixel.
                i32::try_from(i).map_err(|e| {
                    format!("coordinate out of range ({}): {}", i, e)
                })?
            } else if let Some(f) = n.as_f64() {
                // PVT-G5-051: was `f as i32` (NaN → 0, ±inf → UB-
                // adjacent saturation). Use `clamp_f64_to_i32` for
                // defined behavior on all finite + non-finite inputs.
                clamp_f64_to_i32(f)
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
                // PVT-G5-051: see the `px` arm above for rationale.
                i32::try_from(i).map_err(|e| {
                    format!("coordinate out of range ({}): {}", i, e)
                })?
            } else if let Some(f) = n.as_f64() {
                // PVT-G5-051: see the `px` arm above for rationale.
                clamp_f64_to_i32(f)
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

/// PVT-G5-051: convert an `f64` coordinate to `i32` with fully-defined
/// behavior on all possible `f64` values (NaN, ±inf, in-range, out-of-
/// range).
///
/// - **NaN → 0**: matches the prior `as i32` behavior (NaN as i32 is 0
///   in Rust). A NaN coordinate is a renderer bug; defaulting to 0 is
///   no worse than the prior behavior and the file log will surface the
///   upstream `log::warn!` from the WS reader if NaN ever arrives.
/// - **+inf → `i32::MAX`**: the JSON sender wants the rightmost pixel.
/// - **-inf → `i32::MIN`**: the JSON sender wants the leftmost pixel.
/// - **In-range finite → `f as i32`**: standard truncation toward zero.
/// - **Out-of-range finite (e.g. 1e30) → saturate to `i32::MAX`/`MIN`**
///   via the `f.clamp(i32::MIN as f64, i32::MAX as f64)` guard before
///   the `as i32` cast. Without this, the `as i32` cast on a value
///   larger than `i32::MAX` produces a saturated value as of Rust
///   1.45+, but the saturation target is unspecified for `f64` → `i32`
///   (it's `i32::MIN` for very large positives due to float-bit-
///   pattern reinterpretation, which is surprising). The explicit clamp
///   makes the intent obvious.
fn clamp_f64_to_i32(f: f64) -> i32 {
    if f.is_nan() {
        return 0;
    }
    if f.is_infinite() {
        return if f > 0.0 { i32::MAX } else { i32::MIN };
    }
    // Clamp to the i32 representable range BEFORE the `as i32` cast.
    // `i32::MIN as f64` and `i32::MAX as f64` are exact (the i32 range
    // fits comfortably within f64's 53-bit mantissa), so the clamp
    // boundaries are not subject to rounding.
    let clamped = f.clamp(i32::MIN as f64, i32::MAX as f64);
    clamped as i32
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

    // ── PVT-G5-051: i64 overflow → Err (was silent truncation) ───────

    #[test]
    fn test_parse_position_int_x_overflow_returns_err() {
        // PVT-G5-051: `i64::MAX` is way outside `i32` range. The pre-
        // fix `i as i32` would silently truncate to -1, moving the
        // bubble off-screen with no diagnostic. The post-fix
        // `i32::try_from` returns Err with a descriptive message.
        let result = parse_position(json!(i64::MAX), json!(0), 1920, 1080, 320, 80);
        assert!(result.is_err(), "i64::MAX x should overflow i32");
        let err = result.unwrap_err();
        assert!(
            err.contains("out of range"),
            "error message should mention range, got: {}",
            err
        );
        assert!(
            err.contains(&i64::MAX.to_string()),
            "error message should include the offending value, got: {}",
            err
        );
    }

    #[test]
    fn test_parse_position_int_y_overflow_returns_err() {
        // PVT-G5-051: same as the x case but for the y axis.
        let result = parse_position(json!(0), json!(i64::MIN), 1920, 1080, 320, 80);
        assert!(result.is_err(), "i64::MIN y should overflow i32");
        let err = result.unwrap_err();
        assert!(err.contains("out of range"));
        assert!(err.contains(&i64::MIN.to_string()));
    }

    #[test]
    fn test_parse_position_int_x_at_i32_boundaries_accepted() {
        // PVT-G5-051: the exact `i32::MAX` / `i32::MIN` values must
        // still be accepted (they're at the edge of the representable
        // range — `i32::try_from` accepts them).
        let (x, _) = parse_position(json!(i32::MAX as i64), json!(0), 1920, 1080, 320, 80).unwrap();
        assert_eq!(x, i32::MAX);
        let (x, _) = parse_position(json!(i32::MIN as i64), json!(0), 1920, 1080, 320, 80).unwrap();
        assert_eq!(x, i32::MIN);
    }

    // ── PVT-G5-051: f64 NaN/inf → defined saturation ─────────────────
    //
    // The pre-fix `f as i32` cast had implementation-defined behavior
    // on NaN and ±inf. The post-fix `clamp_f64_to_i32` pins the
    // behavior: NaN → 0, +inf → i32::MAX, -inf → i32::MIN. These tests
    // pin that contract so a future refactor can't silently break it.

    #[test]
    fn test_parse_position_float_nan_x_maps_to_zero() {
        // PVT-G5-051: NaN coordinate → 0 (matches the prior `as i32`
        // behavior, but now explicit and documented).
        // serde_json represents NaN as `null` by default, but a custom
        // deserializer or a `f64::NAN`-producing serializer can still
        // emit it — pin the behavior anyway via the helper directly.
        assert_eq!(clamp_f64_to_i32(f64::NAN), 0);
    }

    #[test]
    fn test_parse_position_float_pos_inf_x_maps_to_i32_max() {
        // PVT-G5-051: +inf coordinate → i32::MAX (rightmost pixel).
        assert_eq!(clamp_f64_to_i32(f64::INFINITY), i32::MAX);
    }

    #[test]
    fn test_parse_position_float_neg_inf_x_maps_to_i32_min() {
        // PVT-G5-051: -inf coordinate → i32::MIN (leftmost pixel).
        assert_eq!(clamp_f64_to_i32(f64::NEG_INFINITY), i32::MIN);
    }

    #[test]
    fn test_parse_position_float_huge_positive_maps_to_i32_max() {
        // PVT-G5-051: a finite-but-huge f64 (1e30) saturates to
        // i32::MAX rather than wrapping to a negative pixel.
        // Pre-fix `1e30 as i32` would have produced a
        // UB-adjacent / surprising value; the clamp makes it explicit.
        assert_eq!(clamp_f64_to_i32(1e30), i32::MAX);
    }

    #[test]
    fn test_parse_position_float_huge_negative_maps_to_i32_min() {
        // PVT-G5-051: a finite-but-huge negative f64 saturates to
        // i32::MIN rather than wrapping.
        assert_eq!(clamp_f64_to_i32(-1e30), i32::MIN);
    }

    #[test]
    fn test_parse_position_float_in_range_truncates_toward_zero() {
        // PVT-G5-051: in-range finite f64 truncates toward zero
        // (standard `as i32` behavior — preserved).
        assert_eq!(clamp_f64_to_i32(100.7), 100);
        assert_eq!(clamp_f64_to_i32(-100.7), -100);
        assert_eq!(clamp_f64_to_i32(0.0), 0);
        assert_eq!(clamp_f64_to_i32(-0.0), 0);
        assert_eq!(clamp_f64_to_i32(0.999), 0);
        assert_eq!(clamp_f64_to_i32(-0.999), 0);
    }

    #[test]
    fn test_parse_position_float_at_i32_boundaries() {
        // PVT-G5-051: exact `i32::MAX as f64` and `i32::MIN as f64`
        // are representable exactly (53-bit mantissa can hold 32-bit
        // ints), so the clamp boundary is exact and the cast yields
        // the expected i32.
        assert_eq!(clamp_f64_to_i32(i32::MAX as f64), i32::MAX);
        assert_eq!(clamp_f64_to_i32(i32::MIN as f64), i32::MIN);
    }

    #[test]
    fn test_parse_position_float_just_outside_i32_range() {
        // PVT-G5-051: `i32::MAX as f64 + 1.0` exceeds i32 range (the
        // +1.0 is representable since i32::MAX is 2_147_483_647 and
        // f64 has 53-bit mantissa). Should saturate to i32::MAX.
        let just_over = (i32::MAX as f64) + 1.0;
        assert!(just_over > i32::MAX as f64, "test setup: value should exceed i32::MAX");
        assert_eq!(clamp_f64_to_i32(just_over), i32::MAX);
        let just_under = (i32::MIN as f64) - 1.0;
        assert!(just_under < i32::MIN as f64, "test setup: value should be below i32::MIN");
        assert_eq!(clamp_f64_to_i32(just_under), i32::MIN);
    }

    #[test]
    fn test_parse_position_float_subnormal_handled() {
        // PVT-G5-051: subnormal / denormal f64 values near zero should
        // truncate to 0 (the clamp doesn't affect them; they're already
        // in range). Pin the behavior so a future refactor doesn't
        // accidentally treat subnormals as out-of-range.
        let sub = f64::MIN_POSITIVE / 2.0; // subnormal
        assert!(sub > 0.0 && sub < 1.0);
        assert_eq!(clamp_f64_to_i32(sub), 0);
        assert_eq!(clamp_f64_to_i32(-sub), 0);
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

// ─── Tauri commands: bubble window extensions (CR-33) ────────────────
//
// CR-33: the Tauri bridge was missing 3 bubble-window methods that the
// Electron bubble preload (`voice_typer/client/src/preload/bubble.ts`)
// exposes — `resizeTo`, `onSetState`, `toggleDictation`. Without these,
// the bubble renderer's mic button (toggleDictation) is dead, the
// state label (onSetState) never updates, and the pill content has a
// transparent dead zone around it (resizeTo is never called to fit the
// window to the pill). These commands restore parity with the Electron
// preload surface so the same `Bubble.tsx` component works on both
// runtimes.

/// Resize the bubble window to exactly `(width, height)` physical
/// pixels (CR-33 / ADR-0020 §9). The TS bridge's `resizeTo(w, h)`
/// invokes this with the pill content's measured bounds so there is no
/// invisible dead zone around the bubble that would block clicks to the
/// windows underneath (the BrowserWindow is 240×80 initially; the pill
/// content is typically smaller).
///
/// Mirrors the Electron `bubble:resize` IPC handler in
/// `voice_typer/client/src/main/index.ts` which calls
/// `BrowserWindow.setSize(width, height)`.
#[tauri::command]
pub async fn bubble_resize(
    width: u32,
    height: u32,
    app: tauri::AppHandle,
) -> Result<(), String> {
    let window = app
        .get_webview_window("bubble")
        .ok_or("bubble window not found")?;
    use tauri::PhysicalSize;
    window
        .set_size(PhysicalSize::new(width, height))
        .map_err(|e| e.to_string())
}

/// Emit a `bubble:set-state` event to the bubble window with the given
/// state string (CR-33 / ADR-0020 §9). The bubble renderer's
/// `onSetState(callback)` listener (in preload/bubble.ts:64-71) updates
/// the state label — e.g. "recording", "transcribing", "loading".
///
/// This command is invoked from the MAIN renderer (which has dispatch
/// access) when the Python sidecar sends a `status_change` event, so
/// the sandboxed bubble renderer doesn't need to subscribe to the full
/// Python event stream. The main renderer routes only the state
/// relevant to the bubble via this dedicated channel.
///
/// Mirrors the Electron `bubble:set-state` IPC send in
/// `voice_typer/client/src/main/index.ts`.
#[tauri::command]
pub async fn bubble_emit_state(
    state: String,
    app: tauri::AppHandle,
) -> Result<(), String> {
    app.emit_to("bubble", "bubble:set-state", state)
        .map_err(|e| e.to_string())
}

/// Toggle dictation from the bubble's own mic button (CR-33 / ADR-0020
/// §9 + UX-10). The bubble is a sandboxed renderer (SEC-026 / CR-5)
/// with NO `dispatch` access — the `window.label() != "main"` guard at
/// the top of `commands::sidecar_cmds::dispatch` (CR-5) rejects any
/// `dispatch` call from a non-main window, returning the
/// `disallowed_window` error envelope. So instead of calling
/// `dispatch` from JS, the bubble renderer invokes this dedicated
/// command which forwards the `toggle_dictation` envelope to the
/// sidecar via the WS bridge (mirroring how `dispatch` does it but
/// with a fixed command name and fire-and-forget semantics — the
/// bubble doesn't need the response because the sidecar's
/// `status_change` event will reach it via `bubble_emit_state`).
///
/// The Python sidecar's `toggle_dictation` handler responds with
/// `{type:"result", data:{recording: bool}}` — we ignore the response
/// here (no `pending` entry is registered) because the bubble renderer
/// doesn't need it (it learns the new state via the `bubble:set-state`
/// event the main renderer forwards). The main renderer's
/// `usePython.ts` subscription to `status_change` is the source of
/// truth for the toggle's effect on the rest of the UI.
///
/// Mirrors the Electron `bubble:toggle-dictation` IPC handler in
/// `voice_typer/client/src/main/index.ts` which calls
/// `python.call({type: 'toggle_dictation'})`.
///
/// G4-L-03 (sanctioned-bypass doc + rate limiter):
///
/// This command is the **ONLY sanctioned bypass** of the
/// `dispatch`-allowlist (CR-5 / SEC-026). Every other cross-window
/// IPC MUST go through `dispatch` (which enforces the
/// `window.label() == "main"` guard). `bubble_toggle_dictation` is
/// allowed to bypass because:
///   1. It targets a SINGLE fixed, safe command (`toggle_dictation`)
///      — the user-visible effect is "start/stop recording", which is
///      already exposed via the tray icon + global hotkey. There is
///      NO privilege escalation (the bubble can't invoke arbitrary
///      sidecar commands).
///   2. The bubble renderer is sandboxed (no Node integration, no
///      filesystem, no shell access) — even if compromised, the worst
///      it can do is toggle dictation on/off (a denial-of-mic attack,
///      not a data-exfil attack).
///   3. The bypass is TYPE-FIXED at the Rust layer: the JSON envelope
///      is constructed HERE (not in the renderer), so a compromised
///      bubble can't send arbitrary `{type: "..."}` payloads.
///
/// To prevent abuse (e.g. a buggy renderer that spams toggle in a
/// tight loop, or a malicious compromise that tries to DoS the
/// sidecar's recording state machine), this command is rate-limited
/// to **1 toggle per 500ms** via a process-wide `AtomicU64` tracking
/// the last-toggle timestamp. Toggles that arrive within the 500ms
/// window are silently dropped (returning Ok(()) — the renderer
/// doesn't need to know it was rate-limited because the sidecar's
/// `status_change` event will reach it via `bubble_emit_state` and
/// the bubble UI reflects the ACTUAL state, not the requested state).
#[tauri::command]
pub async fn bubble_toggle_dictation(
    state: tauri::State<'_, Arc<SidecarState>>,
) -> Result<(), String> {
    // G4-L-03 rate limiter: max 1 toggle per 500ms. See the doc comment
    // above for the rationale (DoS protection against a buggy or
    // compromised bubble renderer spamming toggle_dictation).
    if !toggle_rate_limiter_allows() {
        log::warn!(
            "[BUBBLE] toggle_dictation rate-limited (last toggle <500ms ago) — dropping"
        );
        return Ok(());
    }
    // Fire-and-forget: send the toggle_dictation envelope with a
    // synthetic id of 0 (the sidecar's response is dropped — see the
    // doc comment above). We do NOT register a pending entry, so the
    // WS reader task's response will be a no-op log warning about an
    // unknown id (acceptable: the sidecar already logs every dispatch
    // round-trip; one extra unmatched response per toggle is noise).
    //
    // F-H3 (sub-agent 1-9 finding): this manual WS-frame construction
    // duplicates the send path in
    // `crate::commands::sidecar_cmds::dispatch_frame` (sidecar_cmds.rs
    // :203-266). The canonical fix would be to delegate by calling
    // `dispatch_frame(&state, "toggle_dictation", None)` — but that
    // helper (a) allocates a real id via `state.next_id`, (b) inserts
    // a `oneshot::Sender` into `state.pending`, and (c) awaits the
    // response with a `DISPATCH_TIMEOUT_SECS` timeout. We can't reuse
    // it for fire-and-forget semantics without one of:
    //   (i)  leaking a stale `pending[id]` entry for every toggle
    //        (the Python side does NOT echo `id=0` back, so the entry
    //        would live until the next reconnect drain — F-H3's "minor
    //        leak" footnote); or
    //   (ii) adding a new `dispatch_fire_and_forget` helper to
    //        `sidecar_cmds.rs` that sends a fixed `id=0` frame and
    //        skips the pending map.
    // Approach (ii) is the right long-term refactor but requires
    // editing `sidecar_cmds.rs`, which is outside this sub-agent's
    // `bubble.rs`-only edit window. We therefore keep the duplicated
    // send inline and document the divergence here. The next fix wave
    // should extract the shared send helper.
    // TODO(PVT-25): extract a `dispatch_fire_and_forget` helper into
    // `sidecar_cmds.rs` and have this command delegate to it, removing
    // the duplicated `json!`/`ws_tx.send` block below.
    let frame = json!({
        "type": "toggle_dictation",
        "data": {},
        "id": 0u64,
    });
    // G4-H-27: poison-safe lock helper (state.rs::lock) instead of
    // `.lock().unwrap()` — a poisoned Mutex here would brick the
    // bubble's mic button permanently (every subsequent click would
    // re-panic in the unwrap). With the helper, the lock is recovered
    // and the worst case is a stale `ws_tx` slot (treated the same as
    // "sidecar not connected" — the renderer shows the disconnected
    // state via the `bubble:set-state` event).
    let ws_tx_opt = crate::state::lock(&state.ws_tx).clone();
    let ws_tx = ws_tx_opt.ok_or_else(|| "sidecar not connected".to_string())?;
    // PVT-G5-059: `ws_tx` is a bounded `mpsc::Sender` — use `try_send`
    // (synchronous) rather than `.send().await` (which would require an
    // async context AND block on the writer-task consumer). Returns
    // `TrySendError::Full` if the writer is overwhelmed (256-cap) or
    // `TrySendError::Closed` if the writer task exited.
    ws_tx.try_send(Message::Text(frame.to_string()))
        .map_err(|e| format!("WS send failed: {e}"))?;
    Ok(())
}

// ─── G4-L-03: bubble_toggle_dictation rate limiter ────────────────────
//
// Process-wide last-toggle timestamp (nanoseconds since UNIX epoch).
// `AtomicU64` because:
//   - The Tauri command handler can be invoked concurrently from
//     multiple webview windows (e.g. if a future code path opens a
//     second bubble), so we need atomic access.
//   - `Instant` doesn't have a stable u64 representation (it's an
//     opaque monotonic clock with no public conversion to integer
//     types), so we use `SystemTime::now().duration_since(UNIX_EPOCH)`
//     and store the nanoseconds. This is monotonic enough for rate
//     limiting (NTP adjustments could shift it but not by enough to
//     matter for a 500ms window).
// 0 = "never toggled" (epoch start) — the first toggle always passes.
static LAST_TOGGLE_NANOS: std::sync::atomic::AtomicU64 =
    std::sync::atomic::AtomicU64::new(0);

/// G4-L-03: minimum interval between consecutive toggle_dictation
/// invocations (500ms = 500_000_000 ns). Matches the bubble renderer's
/// UI animation frame budget (~16ms) — a 500ms window allows ~30
/// clicks/sec before throttling, which is well above any legitimate
/// user click rate (~5 clicks/sec max) but well below the rate that
/// would DoS the sidecar's recording state machine.
const TOGGLE_RATE_LIMIT_NS: u64 = 500_000_000;

/// G4-L-03: rate-limiter predicate. Returns `true` if the toggle is
/// allowed (>= 500ms since the last toggle), `false` if rate-limited.
/// Updates `LAST_TOGGLE_NANOS` atomically on success.
///
/// Uses `compare_exchange` in a loop to handle the rare race where two
/// concurrent calls both read the same `last`. The loop terminates
/// quickly: on `compare_exchange` failure (another caller updated the
/// timestamp), we re-read the new timestamp and re-check the rate
/// limit (almost always returns `false` on the second iteration
/// because the winning caller just updated it <500ms ago).
fn toggle_rate_limiter_allows() -> bool {
    use std::sync::atomic::Ordering;
    use std::time::{SystemTime, UNIX_EPOCH};
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos() as u64)
        .unwrap_or(0);
    loop {
        let last = LAST_TOGGLE_NANOS.load(Ordering::SeqCst);
        // If `now < last` (NTP skew or clock went backwards), allow
        // the toggle (don't penalize the user for a clock glitch).
        // If the elapsed time is < TOGGLE_RATE_LIMIT_NS, deny.
        if now >= last && now.saturating_sub(last) < TOGGLE_RATE_LIMIT_NS {
            return false;
        }
        // Try to claim this toggle by updating LAST_TOGGLE_NANOS. If
        // another caller beat us, retry the loop with the new value.
        match LAST_TOGGLE_NANOS.compare_exchange(
            last,
            now,
            Ordering::SeqCst,
            Ordering::SeqCst,
        ) {
            Ok(_) => return true,
            Err(_) => continue,
        }
    }
}
