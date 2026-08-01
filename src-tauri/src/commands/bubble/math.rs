//! Bubble geometry math helpers ( + ADR-0020 §9).
//!
//! Pure functions for clamping + rounding window coordinates. Split out
//! so the overflow-safety contracts can be unit-tested without a Tauri
//! runtime.
//!
//! - [`clamp_resize_width`] / [`clamp_resize_height`] enforce the
//!   Electron-parity pill bounds on `bubble_resize`.
//! - [`round_f64_to_u32_saturating`] / [`round_f64_to_i32_saturating`]
//!   convert renderer-supplied `f64` measurements/deltas to integers
//!   with fully-defined behavior on NaN / ±inf / out-of-range inputs.
//! - [`compute_move_by_new_pos`] is the `checked_add` core of
//!   `bubble_move_by` ( overflow safety).
//! - [`clamp_f64_to_i32`] is `#[cfg(test)]`-only — kept for the legacy
//!   `parse_position` test contract.

//bubble resize bounds — mirror Electron's
/// `MIN_BUBBLE_W` / `MAX_BUBBLE_W` / `MIN_BUBBLE_H` / `MAX_BUBBLE_H`
/// in `voice_typer/client/src/main/ipc/bubble-handlers.ts:45-48` so a
/// pill measurement (or a compromised sandboxed bubble) can't shrink the
/// bubble to invisible or grow it into a full-screen phishing overlay.
/// The pill content is typically 80–240px wide × 24–80px tall; these
/// bounds accommodate the transcribing text + mic button while keeping
/// the bubble pill-shaped.
///
/// Extracted into named constants + pure helpers so the bounds can be
/// unit-tested without a Tauri window AND so the same values are
/// visible at the call site (vs. being buried inside a closure).
pub(super) const MIN_BUBBLE_W: u32 = 40;
pub(super) const MIN_BUBBLE_H: u32 = 24;
pub(super) const MAX_BUBBLE_W: u32 = 400;
pub(super) const MAX_BUBBLE_H: u32 = 200;

//clamp a single resize width to
/// [`MIN_BUBBLE_W`]..=[`MAX_BUBBLE_W`]. Saturating — `u32::clamp`
/// returns `max(min, min(input, max))`, so any input in range passes
/// through unchanged and any input below / above is clamped to the
/// bound.
pub(super) fn clamp_resize_width(d: u32) -> u32 {
    d.clamp(MIN_BUBBLE_W, MAX_BUBBLE_W)
}

//clamp a single resize height to
/// [`MIN_BUBBLE_H`]..=[`MAX_BUBBLE_H`]. See [`clamp_resize_width`] for
/// the rationale.
pub(super) fn clamp_resize_height(d: u32) -> u32 {
    d.clamp(MIN_BUBBLE_H, MAX_BUBBLE_H)
}

//convert an `f64` dimension to `u32` with fully-defined
/// behavior on all possible `f64` values (NaN, negative, ±inf,
/// in-range, out-of-range). Rounds to nearest (half away from zero
/// per `f64::round`) before the cast.
///
/// - **NaN → 0**: a NaN measurement is a renderer bug; defaulting to 0
///   is no worse than the prior `as u32` behavior and the downstream
///   [`clamp_resize_width`] / [`clamp_resize_height`] will clamp 0 to
///   the MIN bound.
/// - **Negative → 0**: a negative measurement is nonsensical for a
///   window dimension; treat as 0 (clamped to MIN downstream).
/// - **+inf / huge finite → `u32::MAX`**: saturate (the downstream
///   clamp will reduce to `MAX_BUBBLE_W`/`MAX_BUBBLE_H` anyway).
/// - **In-range finite → `f.round() as u32`**: standard round-to-nearest
///   (half away from zero).
pub(super) fn round_f64_to_u32_saturating(f: f64) -> u32 {
    if f.is_nan() || f < 0.0 {
        return 0;
    }
    let rounded = f.round();
    // `u32::MAX as f64` is exactly 4294967295.0 (f64 mantissa is 53
    // bits, u32 is 32 bits — exact). Values above saturate to u32::MAX;
    // the downstream `clamp_resize_*` reduces to MAX_BUBBLE_W/H.
    if rounded > u32::MAX as f64 {
        return u32::MAX;
    }
    rounded as u32
}

//convert an `f64` delta to `i32` with fully-defined
/// behavior on all possible `f64` values (NaN, ±inf, in-range,
/// out-of-range). Rounds to nearest (half away from zero per
/// `f64::round`) before the cast.
///
/// - **NaN → 0**: a NaN delta is a renderer bug; defaulting to 0 is a
///   no-op move (no worse than the prior `as i32` behavior).
/// - **+inf → `i32::MAX`**: the renderer wants the rightmost pixel.
/// - **-inf → `i32::MIN`**: the renderer wants the leftmost pixel.
/// - **In-range finite → `f.round() as i32`**: standard round-to-nearest
///   (half away from zero).
/// - **Out-of-range finite (e.g. 1e30) → saturate to `i32::MAX`/`MIN`**
///   via the `f.clamp(i32::MIN as f64, i32::MAX as f64)` guard before
///   the `as i32` cast. (The downstream `compute_move_by_new_pos`
///   `checked_add` will then surface a descriptive error if the
///   saturated delta overflows `pos.x + dx` / `pos.y + dy`.)
pub(super) fn round_f64_to_i32_saturating(f: f64) -> i32 {
    if f.is_nan() {
        return 0;
    }
    if f.is_infinite() {
        return if f > 0.0 { i32::MAX } else { i32::MIN };
    }
    let rounded = f.round();
    // Clamp to the i32 representable range BEFORE the `as i32` cast.
    // `i32::MIN as f64` and `i32::MAX as f64` are exact (the i32 range
    // fits comfortably within f64's 53-bit mantissa), so the clamp
    // boundaries are not subject to rounding.
    let clamped = rounded.clamp(i32::MIN as f64, i32::MAX as f64);
    clamped as i32
}

//pure helper that computes the new `(x, y)` position for
/// `bubble_move_by` using `i32::checked_add` on each axis.
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
pub(super) fn compute_move_by_new_pos(
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

//convert an `f64` coordinate to `i32` with fully-defined
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
///
/// This helper is `#[cfg(test)]`-only (see `parse::parse_position` for
/// the rationale).
#[cfg(test)]
pub(super) fn clamp_f64_to_i32(f: f64) -> i32 {
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
