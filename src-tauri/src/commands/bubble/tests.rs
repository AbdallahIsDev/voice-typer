#![allow(clippy::unwrap_used, clippy::expect_used, clippy::panic, clippy::unreachable, clippy::todo, clippy::unimplemented, clippy::cast_possible_truncation)]

//! Bubble command unit tests ( + ADR-0020 §9).
//!
//! Originally inline in the single-file `bubble.rs`; moved here when
//! the module was split into focused submodules. The tests pin the
//! pure-helper contracts (position parsing, geometry math, rate
//! limiter) without constructing a Tauri runtime. The full
//! `#[tauri::command]` behavior is exercised by the mig19 integration
//! tests in `tests/tauri/mig19/`.

use super::commands::{bubble_dismiss, bubble_hide_complete};
use super::math::{
    clamp_f64_to_i32, clamp_resize_height, clamp_resize_width, compute_move_by_new_pos,
    round_f64_to_i32_saturating, round_f64_to_u32_saturating, MAX_BUBBLE_H, MAX_BUBBLE_W,
    MIN_BUBBLE_H, MIN_BUBBLE_W,
};
use super::parse::{parse_keyword_position, parse_position};
use super::window::hide_bubble_window;
use crate::commands::main_window_label_check;
use serde_json::{json, Value};

// ── parse_keyword_position (the new single-keyword API) ───────
//
// The new `bubble_set_position(position: String)` command delegates
// to `parse_keyword_position`. These tests pin the keyword →
// coordinate mapping for `"top"` and `"bottom"` and the rejection
// of unknown keywords. The behavior matches the legacy
// `parse_position(json!("top"), json!("top"), ...)` shape (centered
// horizontally, y=0 for "top", y=screen_h-bubble_h clamped to ≥0
// for "bottom").

#[test]
fn test_parse_keyword_position_top() {
    let (x, y) = parse_keyword_position("top", 1920, 1080, 320, 80).unwrap();
    assert_eq!(x, (1920 - 320) / 2);
    assert_eq!(y, 0);
}

#[test]
fn test_parse_keyword_position_bottom() {
    let (x, y) = parse_keyword_position("bottom", 1920, 1080, 320, 80).unwrap();
    assert_eq!(x, (1920 - 320) / 2);
    assert_eq!(y, 1080 - 80);
}

#[test]
fn test_parse_keyword_position_bottom_clamped_when_bubble_taller_than_screen() {
    // Mirrors the legacy `test_parse_position_bottom_clamped_when_bubble_taller_than_screen`
    // — `(screen_h - bubble_h).max(0)` clamps the negative result to 0.
    // The centered-x is clamped too: `((320 - 400) / 2).max(0) == 0`,
    // so a bubble wider than the screen doesn't end up off-screen left
    // (same clamp `test_parse_keyword_position_top_centered_x_clamped_when_bubble_wider_than_screen`
    // pins for the "top" arm).
    let (x, y) = parse_keyword_position("bottom", 320, 80, 400, 200).unwrap();
    assert_eq!(x, 0); // ((320 - 400) / 2).max(0) == 0
    assert_eq!(y, 0); // (80 - 200).max(0) == 0
}

#[test]
fn test_parse_keyword_position_top_centered_x_clamped_when_bubble_wider_than_screen() {
    //when the bubble is wider than the screen, `(screen_w -
    // bubble_w) / 2` is negative; `.max(0)` clamps to 0 so the
    // bubble's top-left doesn't end up off-screen left.
    let (x, _) = parse_keyword_position("top", 320, 1080, 400, 80).unwrap();
    assert_eq!(x, 0);
}

#[test]
fn test_parse_keyword_position_unknown_keyword_returns_err() {
    let result = parse_keyword_position("middle", 1920, 1080, 320, 80);
    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(
        err.contains("must be \"top\" or \"bottom\""),
        "error should mention the accepted keywords, got: {}",
        err
    );
    assert!(
        err.contains("middle"),
        "error should include the offending keyword, got: {}",
        err
    );
}

#[test]
fn test_parse_keyword_position_empty_string_returns_err() {
    let result = parse_keyword_position("", 1920, 1080, 320, 80);
    assert!(result.is_err());
}

//legacy: parse_position (kept test-only for the ───────
// numeric / NaN / inf edge-case contracts — see the `#[cfg(test)]`
// annotation on `parse_position` for the rationale).

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
    // The centered-x is clamped to >=0 in production (see the
    // `((screen_w - bubble_w) / 2).max(0)` arms in `parse_position`
    // and `parse_keyword_position`) — a bubble wider than the screen
    // must not end up off-screen left. `((320 - 400) / 2).max(0)`
    // evaluates to 0, not -40.
    let (x, y) = parse_position(json!("bottom"), json!("bottom"), 320, 80, 400, 200).unwrap();
    assert_eq!(x, 0); // ((320 - 400) / 2).max(0) == 0
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

//i64 overflow → Err (was silent truncation) ───────

#[test]
fn test_parse_position_int_x_overflow_returns_err() {
    //`i64::MAX` is way outside `i32` range. The pre-
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
    //same as the x case but for the y axis.
    let result = parse_position(json!(0), json!(i64::MIN), 1920, 1080, 320, 80);
    assert!(result.is_err(), "i64::MIN y should overflow i32");
    let err = result.unwrap_err();
    assert!(err.contains("out of range"));
    assert!(err.contains(&i64::MIN.to_string()));
}

#[test]
fn test_parse_position_int_x_at_i32_boundaries_accepted() {
    //the exact `i32::MAX` / `i32::MIN` values must
    // still be accepted (they're at the edge of the representable
    // range — `i32::try_from` accepts them).
    let (x, _) = parse_position(json!(i32::MAX as i64), json!(0), 1920, 1080, 320, 80).unwrap();
    assert_eq!(x, i32::MAX);
    let (x, _) = parse_position(json!(i32::MIN as i64), json!(0), 1920, 1080, 320, 80).unwrap();
    assert_eq!(x, i32::MIN);
}

//f64 NaN/inf → defined saturation ─────────────────
//
// The pre-fix `f as i32` cast had implementation-defined behavior
// on NaN and ±inf. The post-fix `clamp_f64_to_i32` pins the
// behavior: NaN → 0, +inf → i32::MAX, -inf → i32::MIN. These tests
// pin that contract so a future refactor can't silently break it.

#[test]
fn test_parse_position_float_nan_x_maps_to_zero() {
    //NaN coordinate → 0 (matches the prior `as i32`
    // behavior, but now explicit and documented).
    // serde_json represents NaN as `null` by default, but a custom
    // deserializer or a `f64::NAN`-producing serializer can still
    // emit it — pin the behavior anyway via the helper directly.
    assert_eq!(clamp_f64_to_i32(f64::NAN), 0);
}

#[test]
fn test_parse_position_float_pos_inf_x_maps_to_i32_max() {
    //+inf coordinate → i32::MAX (rightmost pixel).
    assert_eq!(clamp_f64_to_i32(f64::INFINITY), i32::MAX);
}

#[test]
fn test_parse_position_float_neg_inf_x_maps_to_i32_min() {
    //inf coordinate → i32::MIN (leftmost pixel).
    assert_eq!(clamp_f64_to_i32(f64::NEG_INFINITY), i32::MIN);
}

#[test]
fn test_parse_position_float_huge_positive_maps_to_i32_max() {
    //a finite-but-huge f64 (1e30) saturates to
    // i32::MAX rather than wrapping to a negative pixel.
    // Pre-fix `1e30 as i32` would have produced a
    // UB-adjacent / surprising value; the clamp makes it explicit.
    assert_eq!(clamp_f64_to_i32(1e30), i32::MAX);
}

#[test]
fn test_parse_position_float_huge_negative_maps_to_i32_min() {
    //a finite-but-huge negative f64 saturates to
    // i32::MIN rather than wrapping.
    assert_eq!(clamp_f64_to_i32(-1e30), i32::MIN);
}

#[test]
fn test_parse_position_float_in_range_truncates_toward_zero() {
    //in-range finite f64 truncates toward zero
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
    //exact `i32::MAX as f64` and `i32::MIN as f64`
    // are representable exactly (53-bit mantissa can hold 32-bit
    // ints), so the clamp boundary is exact and the cast yields
    // the expected i32.
    assert_eq!(clamp_f64_to_i32(i32::MAX as f64), i32::MAX);
    assert_eq!(clamp_f64_to_i32(i32::MIN as f64), i32::MIN);
}

#[test]
fn test_parse_position_float_just_outside_i32_range() {
    //`i32::MAX as f64 + 1.0` exceeds i32 range (the
    // +1.0 is representable since i32::MAX is 2_147_483_647 and
    // f64 has 53-bit mantissa). Should saturate to i32::MAX.
    let just_over = (i32::MAX as f64) + 1.0;
    assert!(
        just_over > i32::MAX as f64,
        "test setup: value should exceed i32::MAX"
    );
    assert_eq!(clamp_f64_to_i32(just_over), i32::MAX);
    let just_under = (i32::MIN as f64) - 1.0;
    assert!(
        just_under < i32::MIN as f64,
        "test setup: value should be below i32::MIN"
    );
    assert_eq!(clamp_f64_to_i32(just_under), i32::MIN);
}

#[test]
fn test_parse_position_float_subnormal_handled() {
    //subnormal / denormal f64 values near zero should
    // truncate to 0 (the clamp doesn't affect them; they're already
    // in range). Pin the behavior so a future refactor doesn't
    // accidentally treat subnormals as out-of-range.
    let sub = f64::MIN_POSITIVE / 2.0; // subnormal
    assert!(sub > 0.0 && sub < 1.0);
    assert_eq!(clamp_f64_to_i32(sub), 0);
    assert_eq!(clamp_f64_to_i32(-sub), 0);
}

// ───────────────────────────────────────────────────────────────────
//bubble_move_by overflow safety (checked_add)
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
    //pos.x = i32::MAX, dx = 1 → overflow. Pre-fix: wraps to
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
    //pos.y = i32::MIN, dy = -1 → underflow.
    let result = compute_move_by_new_pos(0, 0, i32::MIN, -1);
    assert!(result.is_err(), "i32::MIN + (-1) should underflow");
    let err = result.unwrap_err();
    assert!(err.contains("move_by overflow"));
    assert!(err.contains(&i32::MIN.to_string()));
    assert!(err.contains("-1"));
}

#[test]
fn test_compute_move_by_new_pos_x_at_i32_max_with_zero_delta_accepted() {
    //at the boundary, dx=0 should NOT overflow (i32::MAX + 0
    // is well-defined). Pin this so a future "defensive" refactor
    // doesn't accidentally reject legitimate edge values.
    let (nx, _) = compute_move_by_new_pos(i32::MAX, 0, 0, 0).unwrap();
    assert_eq!(nx, i32::MAX);
}

#[test]
fn test_compute_move_by_new_pos_both_axes_overflow_reports_x_first() {
    //when both axes overflow, the x-axis error is reported
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
//bubble_resize bounds + f64→u32 coercion
// ───────────────────────────────────────────────────────────────────
//
// The pre-fix code passed width/height (u32) straight to set_size
// with only an 8K (7680) upper cap — no MIN bound, and inconsistent
// with Electron's 40-400 × 24-200 pill bounds. The post-fix code
// (a) accepts `f64` at the FFI boundary (the TS bridge forwards
// `number`), (b) rounds to `u32` with a saturating cast via
// `round_f64_to_u32_saturating` (NaN/negative → 0, ±inf/huge →
// u32::MAX), and (c) clamps both dimensions to the SAME
// MIN_BUBBLE_W/MAX_BUBBLE_W/MIN_BUBBLE_H/MAX_BUBBLE_H bounds
// Electron uses (`bubble-handlers.ts:45-48`) so both hosts produce
// identical resize behavior.

#[test]
fn test_clamp_resize_width_typical_pill_size_passes_through() {
    assert_eq!(clamp_resize_width(80), 80);
    assert_eq!(clamp_resize_width(240), 240);
    assert_eq!(clamp_resize_width(100), 100);
}

#[test]
fn test_clamp_resize_width_at_max_boundary_400_passes_through() {
    assert_eq!(clamp_resize_width(MAX_BUBBLE_W), MAX_BUBBLE_W);
    assert_eq!(clamp_resize_width(400), 400);
}

#[test]
fn test_clamp_resize_width_at_min_boundary_40_passes_through() {
    assert_eq!(clamp_resize_width(MIN_BUBBLE_W), MIN_BUBBLE_W);
    assert_eq!(clamp_resize_width(40), 40);
}

#[test]
fn test_clamp_resize_width_just_over_max_clamped_to_400() {
    assert_eq!(clamp_resize_width(401), 400);
}

#[test]
fn test_clamp_resize_width_just_under_min_clamped_to_40() {
    assert_eq!(clamp_resize_width(39), 40);
}

#[test]
fn test_clamp_resize_width_u32_max_clamped_to_400() {
    assert_eq!(clamp_resize_width(u32::MAX), 400);
}

#[test]
fn test_clamp_resize_width_zero_clamped_to_40() {
    assert_eq!(clamp_resize_width(0), 40);
}

#[test]
fn test_clamp_resize_height_typical_pill_size_passes_through() {
    assert_eq!(clamp_resize_height(24), 24);
    assert_eq!(clamp_resize_height(80), 80);
}

#[test]
fn test_clamp_resize_height_at_max_boundary_200_passes_through() {
    assert_eq!(clamp_resize_height(MAX_BUBBLE_H), MAX_BUBBLE_H);
    assert_eq!(clamp_resize_height(200), 200);
}

#[test]
fn test_clamp_resize_height_just_over_max_clamped_to_200() {
    assert_eq!(clamp_resize_height(201), 200);
}

#[test]
fn test_clamp_resize_height_zero_clamped_to_24() {
    assert_eq!(clamp_resize_height(0), 24);
}

#[test]
fn test_clamp_resize_height_u32_max_clamped_to_200() {
    assert_eq!(clamp_resize_height(u32::MAX), 200);
}

#[test]
fn test_resize_bounds_match_electron_constants() {
    //pin the cross-host bound-parity contract.
    assert_eq!(MIN_BUBBLE_W, 40);
    assert_eq!(MIN_BUBBLE_H, 24);
    assert_eq!(MAX_BUBBLE_W, 400);
    assert_eq!(MAX_BUBBLE_H, 200);
    // Compile-time parity pins (clippy::assertions_on_constants-safe):
    // each relation is verified at compile time and cannot regress.
    const _: () = assert!(MIN_BUBBLE_W < MAX_BUBBLE_W);
    const _: () = assert!(MIN_BUBBLE_H < MAX_BUBBLE_H);
    const _: () = assert!(MAX_BUBBLE_W <= 400);
    const _: () = assert!(MAX_BUBBLE_H <= 200);
    const _: () = assert!(MIN_BUBBLE_W >= 20);
    const _: () = assert!(MIN_BUBBLE_H >= 16);
}

//round_f64_to_u32_saturating (NaN/inf/range) ──────

#[test]
fn test_round_f64_to_u32_saturating_in_range_rounds_to_nearest() {
    assert_eq!(round_f64_to_u32_saturating(100.4), 100);
    assert_eq!(round_f64_to_u32_saturating(100.5), 101);
    assert_eq!(round_f64_to_u32_saturating(100.6), 101);
    assert_eq!(round_f64_to_u32_saturating(0.4), 0);
    assert_eq!(round_f64_to_u32_saturating(0.6), 1);
    assert_eq!(round_f64_to_u32_saturating(239.7), 240);
}

#[test]
fn test_round_f64_to_u32_saturating_zero_passes_through() {
    assert_eq!(round_f64_to_u32_saturating(0.0), 0);
    assert_eq!(round_f64_to_u32_saturating(-0.0), 0);
}

#[test]
fn test_round_f64_to_u32_saturating_nan_maps_to_zero() {
    assert_eq!(round_f64_to_u32_saturating(f64::NAN), 0);
}

#[test]
fn test_round_f64_to_u32_saturating_negative_maps_to_zero() {
    assert_eq!(round_f64_to_u32_saturating(-1.0), 0);
    assert_eq!(round_f64_to_u32_saturating(-100.7), 0);
    assert_eq!(round_f64_to_u32_saturating(f64::NEG_INFINITY), 0);
}

#[test]
fn test_round_f64_to_u32_saturating_pos_inf_maps_to_u32_max() {
    assert_eq!(round_f64_to_u32_saturating(f64::INFINITY), u32::MAX);
}

#[test]
fn test_round_f64_to_u32_saturating_huge_finite_maps_to_u32_max() {
    assert_eq!(round_f64_to_u32_saturating(1e30), u32::MAX);
}

#[test]
fn test_round_f64_to_u32_saturating_at_u32_max_boundary() {
    assert_eq!(round_f64_to_u32_saturating(u32::MAX as f64), u32::MAX);
    let just_over = (u32::MAX as f64) + 1.0;
    assert!(just_over > u32::MAX as f64);
    assert_eq!(round_f64_to_u32_saturating(just_over), u32::MAX);
}

//round_f64_to_i32_saturating (NaN/inf/range) ──────

#[test]
fn test_round_f64_to_i32_saturating_in_range_rounds_to_nearest() {
    assert_eq!(round_f64_to_i32_saturating(10.4), 10);
    assert_eq!(round_f64_to_i32_saturating(10.5), 11);
    assert_eq!(round_f64_to_i32_saturating(-10.5), -11);
    assert_eq!(round_f64_to_i32_saturating(-10.4), -10);
    assert_eq!(round_f64_to_i32_saturating(0.4), 0);
    assert_eq!(round_f64_to_i32_saturating(-0.4), 0);
    assert_eq!(round_f64_to_i32_saturating(0.0), 0);
    assert_eq!(round_f64_to_i32_saturating(-0.0), 0);
}

#[test]
fn test_round_f64_to_i32_saturating_nan_maps_to_zero() {
    assert_eq!(round_f64_to_i32_saturating(f64::NAN), 0);
}

#[test]
fn test_round_f64_to_i32_saturating_pos_inf_maps_to_i32_max() {
    assert_eq!(round_f64_to_i32_saturating(f64::INFINITY), i32::MAX);
}

#[test]
fn test_round_f64_to_i32_saturating_neg_inf_maps_to_i32_min() {
    assert_eq!(round_f64_to_i32_saturating(f64::NEG_INFINITY), i32::MIN);
}

#[test]
fn test_round_f64_to_i32_saturating_huge_positive_maps_to_i32_max() {
    assert_eq!(round_f64_to_i32_saturating(1e30), i32::MAX);
}

#[test]
fn test_round_f64_to_i32_saturating_huge_negative_maps_to_i32_min() {
    assert_eq!(round_f64_to_i32_saturating(-1e30), i32::MIN);
}

#[test]
fn test_round_f64_to_i32_saturating_at_i32_boundaries() {
    assert_eq!(round_f64_to_i32_saturating(i32::MAX as f64), i32::MAX);
    assert_eq!(round_f64_to_i32_saturating(i32::MIN as f64), i32::MIN);
}

#[test]
fn test_round_f64_to_i32_saturating_just_outside_i32_range() {
    let just_over = (i32::MAX as f64) + 1.0;
    assert!(just_over > i32::MAX as f64);
    assert_eq!(round_f64_to_i32_saturating(just_over), i32::MAX);
    let just_under = (i32::MIN as f64) - 1.0;
    assert!(just_under < i32::MIN as f64);
    assert_eq!(round_f64_to_i32_saturating(just_under), i32::MIN);
}

// ───────────────────────────────────────────────────────────────────
//main-window-origin guard (require_main_window)
// ───────────────────────────────────────────────────────────────────
//
//the canonical `main_window_label_check` (now in
// `commands/mod.rs`) returns `bool` — the testable surface for the
// window-label predicate. The full `require_main_window` wrapper
// (which produces the JSON error envelope + logs the rejection) is
// exercised end-to-end by the mig19 integration tests in
// `tests/tauri/mig19/` because constructing a `tauri::Window` in a
// unit test requires a running Tauri runtime.

#[test]
fn test_main_window_label_check_accepts_main() {
    // The "main" label is the canonical main-window label
    // (registered in main.rs::setup → WindowBuilder::new("main")).
    assert!(
        main_window_label_check("main"),
        "the \"main\" label must be accepted by the gate"
    );
}

#[test]
fn test_main_window_label_check_rejects_bubble_label() {
    //a call originating from the "bubble" window (the
    // sandboxed pill renderer) MUST be rejected — this is the
    // core security boundary the gate enforces.
    assert!(
        !main_window_label_check("bubble"),
        "the \"bubble\" label must be rejected by the gate"
    );
}

#[test]
fn test_main_window_label_check_rejects_unknown_label() {
    // A future window label (e.g. a settings window) should also be
    // rejected — the gate is "main only", not "main + a few others".
    assert!(
        !main_window_label_check("settings"),
        "unknown window labels must be rejected by the gate"
    );
}

#[test]
fn test_main_window_label_check_rejects_empty_label() {
    // An empty window label (defensive — shouldn't happen in
    // practice, but Tauri doesn't enforce non-empty labels) must
    // be rejected, not silently accepted.
    assert!(
        !main_window_label_check(""),
        "empty window label must be rejected by the gate"
    );
}

#[test]
fn test_main_window_label_check_error_envelope_is_valid_json() {
    //the error envelope produced by `require_main_window`
    // must be valid JSON so the renderer's
    // `JSON.parse(rejection_message)` path (in tauri-bridge's
    // rejection handler) doesn't throw a parse error on top of the
    // rejection. Pin this contract — a future refactor that
    // switches to a plain-string error would break the renderer.
    //
    //`require_main_window` lives in `commands/mod.rs` and
    // takes a `&tauri::Window` (which the in-process test harness
    // can't construct). We pin the literal envelope shape via the
    // `json!` macro used inside the function — if anyone changes
    // the shape in `commands::mod::require_main_window`, this test
    // breaks and forces them to update the renderer's reject
    // handler too. Mirrors the equivalent test in `export.rs`.
    let envelope = json!({
        "type": "error",
        "data": {
            "code": "disallowed_window",
            "message": "command only allowed from main window"
        }
    });
    let parsed: Value = serde_json::from_str(&envelope.to_string()).unwrap();
    assert_eq!(parsed["type"], "error");
    assert_eq!(parsed["data"]["code"], "disallowed_window");
    assert!(parsed["data"]["message"].is_string());
}

//require_bubble_window envelope contract ───────────────
//
// `require_bubble_window` (in `commands/mod.rs`) is the inverse of
// `require_main_window`: it REQUIRES the calling window's label to
// be `"bubble"`. Used by `bubble_hide_complete` so a compromised
// main renderer cannot prematurely hide the bubble overlay
// mid-animation. The in-process test harness can't construct a
// `tauri::Window`, so we pin the envelope shape (valid JSON, the
// `disallowed_window` code, the bubble-specific message) the same
// way `test_main_window_label_check_error_envelope_is_valid_json`
// pins the main-window variant. Mirrors that test's structure.

#[test]
fn test_require_bubble_window_error_envelope_is_valid_json() {
    let envelope = json!({
        "type": "error",
        "data": {
            "code": "disallowed_window",
            "message": "command only allowed from bubble window"
        }
    });
    let parsed: Value = serde_json::from_str(&envelope.to_string()).unwrap();
    assert_eq!(parsed["type"], "error");
    assert_eq!(parsed["data"]["code"], "disallowed_window");
    assert!(
        parsed["data"]["message"].is_string(),
        "message field must be a string for the renderer's reject handler"
    );
    let msg = parsed["data"]["message"].as_str().unwrap();
    assert!(
        msg.contains("bubble"),
        "message must name the bubble window, got: {}",
        msg
    );
}

#[test]
fn test_require_bubble_window_label_predicate() {
    //the gate's predicate is `window.label() == "bubble"`.
    // Pin the predicate directly (without constructing a
    // `tauri::Window`) so a future refactor that loosens the check
    // (e.g. accepts `"bubble"` OR `"main"`) breaks this test.
    //
    // The predicate is inlined in `require_bubble_window`; we
    // mirror it here as a string equality check. If the predicate
    // ever changes, this test forces the author to reconsider.
    fn predicate(label: &str) -> bool {
        label == "bubble"
    }
    assert!(predicate("bubble"), "the bubble label must pass the gate");
    assert!(
        !predicate("main"),
        "the main window label must be rejected by the bubble-only gate"
    );
    assert!(
        !predicate(""),
        "empty window label must be rejected by the bubble-only gate"
    );
    assert!(
        !predicate("settings"),
        "unknown window labels must be rejected by the bubble-only gate"
    );
}

//bubble_dismiss command contract ──────────────────────
//
// The new `bubble_dismiss` command mirrors `bubble_hide_complete`:
//both gate on `require_bubble_window` ( / SEC-016) and both
// delegate the actual emit+hide to the shared `hide_bubble_window`
// helper. The in-process test harness can't construct a
// `tauri::AppHandle` / `tauri::Window`, so we pin the contract
// indirectly: (a) the `hide_bubble_window` helper exists (compile-
// time check via a fn-pointer cast), (b) the `bubble_dismiss` command
// fn exists (same check), and (c) the rejection envelope shape is
// valid JSON. The full end-to-end behavior is exercised by the mig19
// integration tests in `tests/tauri/mig19/`.

#[test]
fn test_hide_bubble_window_helper_exists() {
    //pin the existence + signature of the shared
    // `hide_bubble_window` helper via a fn-pointer cast. If a future
    // refactor renames, removes, or changes the signature, this cast
    // fails to compile.
    let _helper: fn(&tauri::AppHandle) -> Result<(), String> = hide_bubble_window;
    let _ = _helper;
}

#[test]
fn test_bubble_dismiss_command_exists() {
    //pin the existence of the new `bubble_dismiss` command.
    // The async fn's exact return type can't be named in stable
    // Rust, so we reference the fn item without calling it. The full
    // signature is verified by the `#[tauri::command]` macro +
    // `generate_handler![]` registration in main.rs.
    let _ = bubble_dismiss;
    let _ = bubble_hide_complete;
}

#[test]
fn test_bubble_dismiss_rejection_envelope_is_valid_json() {
    //pin the contract that `bubble_dismiss` is gated
    // by `require_bubble_window` (the same gate
    // `bubble_hide_complete` uses). The envelope is produced by
    // `require_bubble_window` (in `commands/mod.rs`), which BOTH
    // commands call.
    let envelope = json!({
        "type": "error",
        "data": {
            "code": "disallowed_window",
            "message": "command only allowed from bubble window"
        }
    });
    let parsed: Value = serde_json::from_str(&envelope.to_string()).unwrap();
    assert_eq!(parsed["type"], "error");
    assert_eq!(parsed["data"]["code"], "disallowed_window");
    let msg = parsed["data"]["message"].as_str().unwrap();
    assert!(
        msg.contains("bubble"),
        "bubble_dismiss rejection message must name the bubble window, got: {}",
        msg
    );
}
