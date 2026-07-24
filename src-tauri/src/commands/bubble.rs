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

use crate::state::SidecarState;

// ─── Tauri commands: bubble window (MIG-1.2, ADR-0020 §9) ────────────

// ─── DE-71: main-window-origin guard for bubble control commands ──────
//
// `bubble_signal_ready` and `bubble_emit_state` emit events TO the
// bubble window that the bubble renderer interprets as authoritative
// sidecar state. A compromised sandboxed bubble (XSS in the waveform
// pill) MUST NOT be able to spoof these — e.g. emitting a fake
// `bubble:ready` to confuse the sidecar's level-pump startup, or
// emitting `bubble:set-state("recording")` to spoof the UI into
// showing "recording" while the sidecar is actually idle.
//
// The canonical `require_main_window` helper lives in
// `commands::sidecar_cmds` but is `fn`-private (not `pub(crate)`).
// Promoting it to `pub(crate)` would require editing `sidecar_cmds.rs`
// (out of scope for this `bubble.rs`-only fix wave), so we duplicate
// the helper here. The error envelope shape mirrors the canonical
// helper exactly so the renderer's reject path treats both identically.
// See `sidecar_cmds.rs:32-48` for the G4-H-01 rationale.

/// DE-71: pure helper that returns the error-envelope string used when
/// a non-main window attempts to invoke a main-only command. Extracted
/// from [`require_main_window`] so the envelope shape can be unit-
/// tested without a `tauri::Window` (which the in-process test harness
/// can't construct).
///
/// Returns:
/// - `Ok(())` if `label == "main"`.
/// - `Err(<json envelope string>)` otherwise. The envelope shape is
///   `{"type":"error","data":{"code":"disallowed_window","message":...}}`,
///   mirroring the sidecar WS error envelope so the renderer's existing
///   reject path handles it identically to a server-side rejection.
fn main_window_label_check(label: &str) -> Result<(), String> {
    if label != "main" {
        let err = json!({
            "type": "error",
            "data": {
                "code": "disallowed_window",
                "message": "command only allowed from main window"
            }
        });
        return Err(err.to_string());
    }
    Ok(())
}

/// DE-71: gate a `#[tauri::command]` on the calling window being the
/// main window. Mirrors `commands::sidecar_cmds::require_main_window`
/// (which is `fn`-private — see the section comment above for the
/// duplication rationale). Logs a G4-H-01 warning on rejection so the
/// security audit trail shows the rejected call attempt + the offending
/// window label.
fn require_main_window(window: &tauri::Window) -> Result<(), String> {
    let label = window.label();
    if let Err(e) = main_window_label_check(label) {
        log::warn!(
            "[G4-H-01] bubble command rejected from non-main window: {}",
            label
        );
        return Err(e);
    }
    Ok(())
}

/// Show the bubble window (ADR-0020 §9 + MIG-1.2).
///
/// **Window-origin policy (DE-71):** this command is intentionally NOT
/// gated by [`require_main_window`] — the bubble renderer is permitted
/// to self-manipulate its own visibility. The bubble's auto-show-on-
/// hover handler in `Bubble.tsx` invokes this when the cursor enters
/// the hot zone, so requiring the call to originate from the main
/// window would break that UX. The command's effect is confined to
/// the bubble window itself.
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
///
/// **DE-71 (main-window gate):** this command is now gated by
/// [`require_main_window`]. It signals sidecar-level readiness (the
/// Python sidecar's `bubble_level` pump starts on receipt), so a
/// compromised sandboxed bubble MUST NOT be able to spoof a readiness
/// signal and confuse the sidecar's startup handshake. The legitimate
/// caller is the MAIN renderer's `usePython.ts` boot sequence, which
/// fires this once after the Tauri host is ready. The bubble renderer
/// has no legitimate reason to invoke it.
#[tauri::command]
pub async fn bubble_signal_ready(
    app: tauri::AppHandle,
    window: tauri::Window,
) -> Result<(), String> {
    // DE-71: only the main window may signal bubble readiness — see
    // the doc comment above for the spoofing rationale.
    require_main_window(&window)?;
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
///
/// **Window-origin policy (DE-71):** this command is intentionally NOT
/// gated by [`require_main_window`] — the bubble renderer is permitted
/// to self-manipulate its own window position. The main renderer's
/// `usePython.ts` calls this on hotkey fire (to position the bubble
/// under the cursor), but the bubble renderer also calls it during
/// initialization to apply a saved last-position. The command's effect
/// is confined to the bubble window itself, so a compromised bubble
/// can at worst move itself off-screen (an annoyance, not a security
/// boundary — the bubble is sandboxed per SEC-026 / CR-5).
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
            // AC-33: x-axis "top"/"bottom" arms compute the centered-x
            // coordinate and clamp to ≥0 — mirrors the y-axis "bottom"
            // arm below (`.max(0)`). Without the clamp, when the bubble
            // window is wider than the primary monitor (e.g. 400px bubble
            // on a 320px-wide screen), `(screen_w - bubble_w) / 2`
            // evaluates to a NEGATIVE value, moving the bubble's top-left
            // off-screen left.
            "top" => ((screen_w - bubble_w) / 2).max(0),
            "bottom" => ((screen_w - bubble_w) / 2).max(0),
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

    // ───────────────────────────────────────────────────────────────────
    // DE-16: bubble_move_by overflow safety (checked_add)
    // ───────────────────────────────────────────────────────────────────
    //
    // The pre-fix code did `pos.x + dx` (plain `i32 + i32`), which
    // silently wraps on overflow. A renderer-supplied dx near i32::MAX
    // on top of a pos.x near i32::MAX would wrap to a negative pixel,
    // jerking the bubble off-screen with no diagnostic. The post-fix
    // `compute_move_by_new_pos` uses `i32::checked_add` and returns
    // a descriptive error naming both operands.

    #[test]
    fn test_compute_move_by_new_pos_typical_drag_delta() {
        // Small positive drag delta (typical mousemove during a drag).
        // Should pass through unchanged.
        let (nx, ny) = compute_move_by_new_pos(100, 5, 200, -3).unwrap();
        assert_eq!(nx, 105);
        assert_eq!(ny, 197);
    }

    #[test]
    fn test_compute_move_by_new_pos_zero_delta_is_noop() {
        // dx=dy=0 → position unchanged (no-op move).
        let (nx, ny) = compute_move_by_new_pos(500, 0, 600, 0).unwrap();
        assert_eq!(nx, 500);
        assert_eq!(ny, 600);
    }

    #[test]
    fn test_compute_move_by_new_pos_negative_delta_moves_left_up() {
        // Negative deltas should move the bubble left and up.
        let (nx, ny) = compute_move_by_new_pos(1000, -250, 800, -100).unwrap();
        assert_eq!(nx, 750);
        assert_eq!(ny, 700);
    }

    #[test]
    fn test_compute_move_by_new_pos_x_overflow_returns_err() {
        // DE-16: pos.x = i32::MAX, dx = 1 → overflow. Pre-fix: wraps to
        // i32::MIN (the bubble jumps to the leftmost pixel). Post-fix:
        // returns Err with a descriptive message.
        let result = compute_move_by_new_pos(i32::MAX, 1, 0, 0);
        assert!(result.is_err(), "i32::MAX + 1 should overflow");
        let err = result.unwrap_err();
        assert!(
            err.contains("move_by overflow"),
            "error should mention move_by overflow, got: {}",
            err
        );
        assert!(
            err.contains(&i32::MAX.to_string()),
            "error should include pos_x operand ({}), got: {}",
            i32::MAX,
            err
        );
        assert!(
            err.contains("1"),
            "error should include dx operand (1), got: {}",
            err
        );
    }

    #[test]
    fn test_compute_move_by_new_pos_y_overflow_returns_err() {
        // DE-16: pos.y = i32::MIN, dy = -1 → underflow.
        let result = compute_move_by_new_pos(0, 0, i32::MIN, -1);
        assert!(result.is_err(), "i32::MIN + (-1) should underflow");
        let err = result.unwrap_err();
        assert!(err.contains("move_by overflow"));
        assert!(err.contains(&i32::MIN.to_string()));
        assert!(err.contains("-1"));
    }

    #[test]
    fn test_compute_move_by_new_pos_x_at_i32_max_with_zero_delta_accepted() {
        // DE-16: at the boundary, dx=0 should NOT overflow (i32::MAX + 0
        // is well-defined). Pin this so a future "defensive" refactor
        // doesn't accidentally reject legitimate edge values.
        let (nx, _) = compute_move_by_new_pos(i32::MAX, 0, 0, 0).unwrap();
        assert_eq!(nx, i32::MAX);
    }

    #[test]
    fn test_compute_move_by_new_pos_both_axes_overflow_reports_x_first() {
        // DE-16: when both axes overflow, the x-axis error is reported
        // first (the helper checks x before y). Pin this so error
        // messages stay deterministic — a future refactor that swaps
        // the order would break renderer-side error parsing.
        let result = compute_move_by_new_pos(i32::MAX, 1, i32::MIN, -1);
        assert!(result.is_err());
        let err = result.unwrap_err();
        // Both operands are i32::MAX + 1, but the x check fires first
        // so the error mentions pos_x = i32::MAX (not pos_y = i32::MIN).
        assert!(err.contains(&i32::MAX.to_string()));
        assert!(!err.contains(&i32::MIN.to_string()));
    }

    // ───────────────────────────────────────────────────────────────────
    // DE-70: bubble_resize dimension cap (8K = 7680)
    // ───────────────────────────────────────────────────────────────────
    //
    // The pre-fix code passed width/height straight to set_size with
    // no upper bound — a renderer bug sending u32::MAX would ask the
    // OS window manager for a multi-gigapixel surface. The post-fix
    // `cap_resize_dim` saturates at 7680 (8K UHD), well above any
    // legitimate pill content measurement but well below the OS-
    // brokenness threshold.

    #[test]
    fn test_cap_resize_dim_typical_pill_size_passes_through() {
        // Typical pill content is 80–240px. Should pass unchanged.
        assert_eq!(cap_resize_dim(80), 80);
        assert_eq!(cap_resize_dim(240), 240);
        assert_eq!(cap_resize_dim(1), 1);
    }

    #[test]
    fn test_cap_resize_dim_at_boundary_7680_passes_through() {
        // The exact 8K cap (7680) should pass through unchanged
        // (`u32::min(7680, 7680) == 7680`).
        assert_eq!(cap_resize_dim(BUBBLE_RESIZE_MAX_DIM), BUBBLE_RESIZE_MAX_DIM);
        assert_eq!(cap_resize_dim(7680), 7680);
    }

    #[test]
    fn test_cap_resize_dim_just_over_boundary_clamped_to_7680() {
        // 7681 (one pixel over 8K) should clamp to 7680.
        assert_eq!(cap_resize_dim(7681), 7680);
    }

    #[test]
    fn test_cap_resize_dim_u32_max_clamped_to_7680() {
        // DE-70: the renderer-bug / compromised-bubble scenario —
        // u32::MAX (a 4-gigapixel dimension) must clamp to 7680, NOT
        // be passed to set_size where it would trigger a Wayland
        // xdg_surface protocol error or burn CPU on Windows.
        assert_eq!(cap_resize_dim(u32::MAX), 7680);
    }

    #[test]
    fn test_cap_resize_dim_zero_passes_through() {
        // 0 is a degenerate but not overflow value — `u32::min(0, 7680)
        // == 0`. We deliberately do NOT clamp 0 to a minimum because
        // the OS will reject a 0-size window with a clear error, and
        // the renderer should see that error to surface the bug
        // (rather than silently getting a 1×1 window). Pin this
        // contract so a future "defensive" refactor doesn't hide the
        // renderer bug behind a silent minimum.
        assert_eq!(cap_resize_dim(0), 0);
    }

    // ───────────────────────────────────────────────────────────────────
    // DE-71: main-window-origin guard (require_main_window)
    // ───────────────────────────────────────────────────────────────────
    //
    // The pure helper `main_window_label_check` is the testable surface
    // (it takes a `&str` instead of a `tauri::Window`, which the
    // in-process test harness can't construct). The full
    // `require_main_window` wrapper just adds a `log::warn!` and is
    // exercised end-to-end by the mig19 integration tests in
    // `tests/tauri/mig19/`.

    #[test]
    fn test_main_window_label_check_accepts_main() {
        // The "main" label is the canonical main-window label
        // (registered in main.rs::setup → WindowBuilder::new("main")).
        assert!(main_window_label_check("main").is_ok());
    }

    #[test]
    fn test_main_window_label_check_rejects_bubble_label() {
        // DE-71: a call originating from the "bubble" window (the
        // sandboxed pill renderer) MUST be rejected — this is the
        // core security boundary the gate enforces.
        let result = main_window_label_check("bubble");
        assert!(result.is_err(), "bubble label should be rejected");
        let err = result.unwrap_err();
        assert!(
            err.contains("disallowed_window"),
            "error should include the disallowed_window code, got: {}",
            err
        );
        assert!(
            err.contains("only allowed from main window"),
            "error should include the human-readable message, got: {}",
            err
        );
        // The envelope shape must mirror the sidecar WS error envelope
        // so the renderer's existing reject path handles it identically.
        assert!(
            err.contains("\"type\":\"error\""),
            "error should be a JSON envelope with type=error, got: {}",
            err
        );
        assert!(
            err.contains("\"data\":"),
            "error should be a JSON envelope with a data field, got: {}",
            err
        );
    }

    #[test]
    fn test_main_window_label_check_rejects_unknown_label() {
        // A future window label (e.g. a settings window) should also be
        // rejected — the gate is "main only", not "main + a few others".
        let result = main_window_label_check("settings");
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert!(err.contains("disallowed_window"));
    }

    #[test]
    fn test_main_window_label_check_rejects_empty_label() {
        // An empty window label (defensive — shouldn't happen in
        // practice, but Tauri doesn't enforce non-empty labels) must
        // be rejected, not silently accepted.
        assert!(main_window_label_check("").is_err());
    }

    #[test]
    fn test_main_window_label_check_error_envelope_is_valid_json() {
        // DE-71: the error envelope must be valid JSON so the renderer's
        // `JSON.parse(rejection_message)` path (in tauri-bridge's
        // rejection handler) doesn't throw a parse error on top of the
        // rejection. Pin this contract — a future refactor that
        // switches to a plain-string error would break the renderer.
        let err = main_window_label_check("bubble").unwrap_err();
        let parsed: serde_json::Value = serde_json::from_str(&err)
            .expect("error envelope should be valid JSON");
        assert_eq!(parsed["type"], "error");
        assert_eq!(parsed["data"]["code"], "disallowed_window");
        assert!(parsed["data"]["message"].is_string());
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
///
/// **Window-origin policy (DE-71):** this command is intentionally NOT
/// gated by [`require_main_window`] — the bubble renderer is permitted
/// to self-manipulate its own draggability. The main renderer's
/// `usePython.ts` toggles this on hotkey-down/up, but the bubble
/// renderer may also self-toggle in response to its own UI state
/// (e.g. disabling drag while the user is interacting with the mic
/// button so an accidental drag doesn't fire). The command's effect
/// is confined to the bubble window's drag listener.
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
///
/// **Window-origin policy (DE-71):** this command is intentionally NOT
/// gated by [`require_main_window`] — the bubble renderer is permitted
/// to self-manipulate its own window geometry. The drag handler in
/// `Bubble.tsx` invokes `bubble_move_by` on each mousemove while the
/// user drags the pill, so requiring the call to originate from the
/// main window would break drag entirely. The command's effect is
/// confined to the bubble window itself, so a compromised bubble can
/// at worst mess with its own position (an annoyance, not a security
/// boundary — the bubble is sandboxed per SEC-026 / CR-5).
///
/// **DE-16 (overflow safety):** the prior `pos.x + dx` / `pos.y + dy`
/// arithmetic was plain `i32 + i32`, which silently wraps on overflow
/// (Rust's default release-mode behavior). A renderer that sends a
/// huge `dx` (e.g. `i32::MAX`) on top of a `pos.x` near `i32::MAX`
/// would wrap to a negative coordinate, jerking the bubble off-screen
/// with no diagnostic. The fix uses [`compute_move_by_new_pos`],
/// which `checked_add`s each axis and returns a descriptive error
/// naming the offending operands so the renderer can surface it.
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
    // DE-16: use checked arithmetic so a renderer-supplied dx/dy that
    // would overflow i32::MAX surfaces a descriptive error instead of
    // silently wrapping the bubble to a wrapped-negative pixel.
    let (new_x, new_y) = compute_move_by_new_pos(pos.x, dx, pos.y, dy)?;
    window
        .set_position(PhysicalPosition::new(new_x, new_y))
        .map_err(|e| e.to_string())?;
    Ok(json!({"x": new_x, "y": new_y}))
}

/// DE-16: pure helper that computes the new `(x, y)` position for
/// [`bubble_move_by`] using `i32::checked_add` on each axis.
///
/// Extracted from the command body so the overflow safety can be unit-
/// tested without spinning up a Tauri `AppHandle` + webview window
/// (which the in-process `#[cfg(test)]` harness can't do).
///
/// # Errors
///
/// Returns `Err("move_by overflow: <pos> + <delta>")` if either axis
/// would overflow `i32`. The error message includes both operands so
/// the renderer (or a developer reading the log) can see exactly which
/// axis overflowed and by how much.
fn compute_move_by_new_pos(
    pos_x: i32,
    dx: i32,
    pos_y: i32,
    dy: i32,
) -> Result<(i32, i32), String> {
    let new_x = pos_x
        .checked_add(dx)
        .ok_or_else(|| format!("move_by overflow: {} + {}", pos_x, dx))?;
    let new_y = pos_y
        .checked_add(dy)
        .ok_or_else(|| format!("move_by overflow: {} + {}", pos_y, dy))?;
    Ok((new_x, new_y))
}

/// Hide the bubble window and emit `bubble:hide` so the renderer can
/// run cleanup (e.g., stop the level animation) BEFORE the window becomes
/// invisible (ADR-0020 §9 + MIG-1.2).
///
/// GT-50: previously this command (a) emitted `bubble:hide_complete` —
/// a name the renderer never listens for — and (b) hid the window FIRST,
/// so the renderer's cleanup ran AFTER the window was already torn down,
/// leaking the requestAnimationFrame loop for ~1 frame. The fix renames
/// the event to `bubble:hide` AND reorders the emit to fire BEFORE `.hide()`.
///
/// **Window-origin policy (DE-71):** this command is intentionally NOT
/// gated by [`require_main_window`] — the bubble renderer is permitted
/// to self-manipulate its own visibility lifecycle. The bubble's
/// "auto-hide after 3s idle" timer in `Bubble.tsx` invokes this when
/// the user stops interacting, so requiring the call to originate from
/// the main window would break the auto-hide UX. The command's effect
/// is confined to the bubble window itself.
#[tauri::command]
pub async fn bubble_hide_complete(app: tauri::AppHandle) -> Result<(), String> {
    // GT-50: emit FIRST so the renderer's cleanup runs while the
    // window is still visible.
    app.emit_to("bubble", "bubble:hide", ())
        .map_err(|e| e.to_string())?;
    let window = app
        .get_webview_window("bubble")
        .ok_or("bubble window not found")?;
    window.hide().map_err(|e| e.to_string())
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
///
/// **Window-origin policy (DE-71):** this command is intentionally NOT
/// gated by [`require_main_window`] — the bubble renderer is permitted
/// to self-manipulate its own window size. The bubble's content
/// measurement observer (`Bubble.tsx`'s `ResizeObserver`) invokes this
/// when the pill content changes (e.g. state label grows from
/// "listening" to "transcribing…"), so requiring the call to originate
/// from the main window would break the auto-fit behavior. The
/// command's effect is confined to the bubble window itself.
///
/// **DE-70 (size ceiling):** the prior code passed `width` / `height`
/// straight to `set_size` with no upper bound. A renderer bug (or a
/// compromised sandboxed bubble) that sent `width = u32::MAX` would
/// ask the window manager for a 4-gigapixel-wide window, which on
/// Linux triggers a Wayland `xdg_surface` protocol error (killing the
/// bubble) and on Windows silently clips to the monitor but burns CPU
/// compositing a huge surface. The fix caps both dimensions to
/// [`BUBBLE_RESIZE_MAX_DIM`] (7680 = 8K UHD) before calling
/// `set_size` — well above any legitimate pill content measurement but
/// well below the OS-brokenness threshold.
#[tauri::command]
pub async fn bubble_resize(
    width: u32,
    height: u32,
    app: tauri::AppHandle,
) -> Result<(), String> {
    let window = app
        .get_webview_window("bubble")
        .ok_or("bubble window not found")?;
    // DE-70: cap both dimensions to 8K (7680) before calling set_size
    // to avoid handing the OS window manager a multi-gigapixel surface
    // (see `cap_resize_dim` doc for the rationale).
    let capped_w = cap_resize_dim(width);
    let capped_h = cap_resize_dim(height);
    use tauri::PhysicalSize;
    window
        .set_size(PhysicalSize::new(capped_w, capped_h))
        .map_err(|e| e.to_string())
}

/// DE-70: hard ceiling on each `bubble_resize` dimension. 7680 = 8K
/// UHD width (the highest-resolution consumer display standard as of
/// 2024). A pill content measurement of 7680+px indicates a renderer
/// bug (the pill is typically 80–240px wide), so capping here is a
/// safety net, not a UX constraint.
///
/// Extracted into a named constant + pure helper so the cap can be
/// unit-tested without a Tauri window.
const BUBBLE_RESIZE_MAX_DIM: u32 = 7680;

/// DE-70: cap a single resize dimension to [`BUBBLE_RESIZE_MAX_DIM`].
/// Saturating — `u32::min` returns the smaller of the input and the
/// cap, so any input ≤ 7680 passes through unchanged and any input
/// above is clamped to 7680.
fn cap_resize_dim(d: u32) -> u32 {
    d.min(BUBBLE_RESIZE_MAX_DIM)
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
///
/// **DE-71 (main-window gate):** this command is now gated by
/// [`require_main_window`]. It emits an authoritative state label that
/// the bubble UI trusts (the bubble displays "recording" without any
/// independent verification), so a compromised sandboxed bubble MUST
/// NOT be able to spoof a state — e.g. emitting
/// `bubble:set-state("recording")` to fool the user into thinking the
/// mic is hot when the sidecar is actually idle (a privacy-relevant
/// spoof). The legitimate caller is the MAIN renderer's
/// `usePython.ts::onStatusChange` handler, which forwards sidecar
/// `status_change` events. The bubble renderer has no legitimate
/// reason to invoke it (it learns state via the emitted event, not by
/// emitting one itself).
#[tauri::command]
pub async fn bubble_emit_state(
    state: String,
    app: tauri::AppHandle,
    window: tauri::Window,
) -> Result<(), String> {
    // DE-71: only the main window may emit bubble state — see the doc
    // comment above for the state-spoofing rationale.
    require_main_window(&window)?;
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
    // EC-FIX-5 (EC-18 / PVT-25): the inline `json!` + `lock` +
    // `try_send` block that used to live here was the PVT-25 TODO. It
    // duplicated the send path in `dispatch_frame`. Replaced by a call
    // to `crate::commands::sidecar_cmds::dispatch_fire_and_forget`,
    // which constructs the id=0 frame, locks `ws_tx` via the poison-
    // safe `mutex_lock` helper, and `try_send`s the frame. The error
    // strings ("sidecar not connected" / "WS send failed: <e>")
    // mirror `dispatch_frame`'s shape so the renderer's reject path
    // handles them identically.
    crate::commands::sidecar_cmds::dispatch_fire_and_forget(
        state.inner(),
        "toggle_dictation",
        None,
    )
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
