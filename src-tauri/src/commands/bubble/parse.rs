//! Bubble position parsing helpers ( + ADR-0020 §9).
//!
//! [`parse_position`] is kept `#[cfg(test)]`-only so the existing unit
//! tests for the legacy numeric / NaN / inf edge cases continue to pin
//! the contract for any future caller that reintroduces numeric
//! coordinates.
//!
//! The former production keyword path (`parse_keyword_position`) was
//! removed when `bubble_set_position` moved to cursor-monitor
//! work-area placement: its full-screen-bounds geometry (y=0 for
//! "top", screen_h - bubble_h for "bottom", primary monitor only) no
//! longer matched the Electron-parity behavior. The keyword →
//! coordinate mapping now lives in `math::bubble_position_in_work_area`
//! (with the identical `"position must be \"top\" or \"bottom\""`
//! error contract), and the multi-monitor resolution lives in
//! `commands::resolve_cursor_monitor`.

#[cfg(test)]
use serde_json::Value;

#[cfg(test)]
use super::math::clamp_f64_to_i32;

/// Parse a position value into `(x, y)` physical pixels.
///
/// Accepts:
/// - numeric `x` (int or float) → used directly
/// - `"top"` → centered horizontally, y=0
/// - `"bottom"` → centered horizontally, y=screen_h - bubble_h
/// - `y` defaults to 0 if null/missing (legacy string-shape calls)
///
//# : integer-coord range safety
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
/// - `f64 → i32` uses [`super::math::clamp_f64_to_i32`] (NaN → 0, ±inf → i32::MAX /
///   i32::MIN, in-range → `f as i32`). This avoids the silent NaN→0
///   footgun while still saturating +inf/-inf to the i32 bounds (which
///   matches the JSON `bubble_set_position` contract — a +inf x lands
///   the bubble at the rightmost representable pixel).
///
/// This helper is no longer called by `bubble_set_position` (the
/// command takes a single `position: String` keyword and delegates the
/// keyword → work-area mapping to `math::bubble_position_in_work_area`).
/// It's kept as a `#[cfg(test)]`-only
/// helper so the existing unit tests for the numeric / NaN / inf edge
/// cases continue to pin the contract for any future caller that
/// reintroduces numeric coordinates.
#[cfg(test)]
pub(super) fn parse_position(
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
                //was `i as i32` (silent truncation on
                // overflow). `i32::try_from` returns Err for out-of-
                // range values so we can surface a diagnostic to the
                // caller (the React TS bridge) rather than silently
                // moving the bubble to a wrapped-negative pixel.
                i32::try_from(i).map_err(|e| format!("coordinate out of range ({}): {}", i, e))?
            } else if let Some(f) = n.as_f64() {
                //was `f as i32` (NaN → 0, ±inf → UB-
                // adjacent saturation). Use `clamp_f64_to_i32` for
                // defined behavior on all finite + non-finite inputs.
                clamp_f64_to_i32(f)
            } else {
                return Err(format!("x must be a number, got {:?}", x));
            }
        }
        Value::String(s) => match s.as_str() {
            //x-axis "top"/"bottom" arms compute the centered-x
            // coordinate and clamp to ≥0 — mirrors the y-axis "bottom"
            // arm below (`.max(0)`). Without the clamp, when the bubble
            // window is wider than the primary monitor (e.g. 400px bubble
            // on a 320px-wide screen), `(screen_w - bubble_w) / 2`
            // evaluates to a NEGATIVE value, moving the bubble's top-left
            // off-screen left.
            "top" => ((screen_w - bubble_w) / 2).max(0),
            "bottom" => ((screen_w - bubble_w) / 2).max(0),
            other => {
                return Err(format!(
                    "x string must be \"top\" or \"bottom\", got {:?}",
                    other
                ))
            }
        },
        Value::Null => 0,
        _ => return Err(format!("x must be a number, string, or null, got {:?}", x)),
    };
    let py = match &y {
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                //see the `px` arm above for rationale.
                i32::try_from(i).map_err(|e| format!("coordinate out of range ({}): {}", i, e))?
            } else if let Some(f) = n.as_f64() {
                //see the `px` arm above for rationale.
                clamp_f64_to_i32(f)
            } else {
                return Err(format!("y must be a number, got {:?}", y));
            }
        }
        Value::String(s) => match s.as_str() {
            "top" => 0,
            "bottom" => (screen_h - bubble_h).max(0),
            other => {
                return Err(format!(
                    "y string must be \"top\" or \"bottom\", got {:?}",
                    other
                ))
            }
        },
        Value::Null => 0,
        _ => return Err(format!("y must be a number, string, or null, got {:?}", y)),
    };
    Ok((px, py))
}
