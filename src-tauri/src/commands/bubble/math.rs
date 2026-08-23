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
//! - [`RectPx`] + the multi-monitor placement helpers
//!   ([`rect_contains_point`], [`edge_margin_physical`],
//!   [`centered_x_in_work_area`], [`keyword_edge_y_in_work_area`],
//!   [`bubble_position_in_work_area`]) implement the cursor-display
//!   work-area placement of `bubble_set_position`, mirroring Electron's
//!   `centerOnActiveDisplay`
//!   (`voice_typer/client/src/main/windows/bubble/positioning.ts`).
//!   All arithmetic stays in PHYSICAL pixels end-to-end:
//!   `AppHandle::cursor_position()` reports physical pixels,
//!   `Monitor::position()` / `Monitor::size()` / `Monitor::work_area()`
//!   are physical pixels, and the command applies the result via
//!   `PhysicalPosition` — so no logical↔physical conversion is needed
//!   anywhere except the one intentional one in
//!   [`edge_margin_physical`] (Electron expresses its edge margin in
//!   DIPs; we scale it per-monitor).
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
#[allow(clippy::cast_possible_truncation)] // saturating cast: guarded + documented above
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
#[allow(clippy::cast_possible_truncation)] // saturating cast: clamped + documented above
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
#[allow(clippy::cast_possible_truncation)] // saturating test helper: clamped + documented above
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

// ─── Multi-monitor work-area placement (physical pixels) ─────────────

/// Edge margin between the bubble and the work-area's top/bottom edge,
/// expressed in LOGICAL (DIP) pixels. Mirrors Electron's hardcoded
/// `+ 48` / `- 48` offsets in `centerOnActiveDisplay` /
/// `centerOnPrimaryDisplay`
/// (`voice_typer/client/src/main/windows/bubble/positioning.ts:196-221`)
/// so both hosts place the bubble at the same visual distance from the
/// screen edge.
const EDGE_MARGIN_LOGICAL_PX: f64 = 48.0;

/// Axis-aligned rectangle in PHYSICAL desktop coordinates. A plain
/// integer mirror of Tauri's monitor bounds / `Monitor::work_area()`
/// (`PhysicalRect<i32, u32>`) so the placement helpers below are pure
/// functions unit-testable without a live Tauri runtime + real
/// monitors.
///
/// All fields saturate on construction (`u32` dimensions → `i32`) so
/// no helper downstream can panic or wrap on absurd-but-representable
/// hardware reports (same saturating-cast pattern the command bodies
/// use for `monitor.size()`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct RectPx {
    pub x: i32,
    pub y: i32,
    pub width: i32,
    pub height: i32,
}

impl RectPx {
    /// Build from a physical-pixel top-left corner + physical-pixel
    /// dimensions (the exact shape of both `Monitor::position()` +
    /// `Monitor::size()` full bounds AND
    /// `Monitor::work_area().position` + `.size`).
    ///
    /// Dimensions use the same saturating `u32 → i32` conversion as
    /// the command body (`i32::try_from(..).unwrap_or(i32::MAX)`) so a
    /// hypothetical >2-Gpx dimension can't silently wrap negative.
    pub(super) fn new(x: i32, y: i32, width: u32, height: u32) -> Self {
        Self {
            x,
            y,
            width: i32::try_from(width).unwrap_or(i32::MAX),
            height: i32::try_from(height).unwrap_or(i32::MAX),
        }
    }

    /// Half-open containment test: `[x, x+width) × [y, y+height)`.
    ///
    /// Half-open so a cursor sitting EXACTLY on the shared vertical
    /// border of two side-by-side monitors resolves to exactly one
    /// monitor (the left one) instead of matching both — mirrors
    /// Electron's `getDisplayMatching` rect-intersection semantics
    /// where a zero-area overlap loses.
    ///
    /// Saturating adds: a degenerate rect at `x = i32::MAX` can't wrap
    /// the right edge negative (which would make `contains`
    /// spuriously true for everything left of it).
    pub(super) fn contains(&self, px: i32, py: i32) -> bool {
        px >= self.x
            && px < self.x.saturating_add(self.width)
            && py >= self.y
            && py < self.y.saturating_add(self.height)
    }
}

/// Pure hit-test used by `bubble_set_position`'s fallback monitor
/// resolution: is the cursor point (already rounded to physical `i32`)
/// inside this monitor's FULL bounds?
///
/// Full bounds — NOT the work area — because a cursor hovering over
/// the taskbar/dock strip still belongs to that monitor; Electron's
/// `getDisplayMatching` also matches against full display bounds.
pub(super) fn rect_contains_point(rect: &RectPx, px: i32, py: i32) -> bool {
    rect.contains(px, py)
}

/// Convert the [`EDGE_MARGIN_LOGICAL_PX`] DIP margin to PHYSICAL
/// pixels for a monitor with the given scale factor, so a 150%-scaled
/// display keeps the same visual gap as Electron's 48-DIP offset.
///
/// - `scale_factor ≤ 0` clamps to 0 margin (degenerate report; a 0
///   margin still places the bubble INSIDE the work area — the edge
///   clamp in [`keyword_edge_y_in_work_area`] guarantees that).
/// - `NaN` scale factor yields margin 0 via
///   [`round_f64_to_i32_saturating`]'s NaN contract (defined behavior
///   instead of an unspecified saturated cast).
pub(super) fn edge_margin_physical(scale_factor: f64) -> i32 {
    round_f64_to_i32_saturating(EDGE_MARGIN_LOGICAL_PX * scale_factor.max(0.0))
}

/// Centered top-left x for a bubble of `bubble_w` physical px inside
/// the work area `wa`, mirroring Electron's
/// `wa.x + (wa.width - BUBBLE_WIDTH) / 2`.
///
/// The result is clamped to ≥ `wa.x` (NOT to absolute ≥0): on a
/// left-of-primary secondary monitor whose work area starts at a
/// NEGATIVE x, clamping to 0 would shove the bubble onto the primary
/// display's territory — the whole bug this module fixes. When the
/// bubble is wider than the work area, the centered expression goes
/// negative relative to `wa.x`; clamping pins the bubble's left edge
/// to the work area's left edge instead of stranding it off-screen
/// left (mirrors the legacy `parse_keyword_position` ≥0 clamp, but in
/// work-area-relative terms).
pub(super) fn centered_x_in_work_area(wa: &RectPx, bubble_w: i32) -> i32 {
    wa.x + ((wa.width - bubble_w) / 2).max(0)
}

/// Top-left y for `"top"` / `"bottom"` edge placement inside the work
/// area `wa`, mirroring Electron's `centerOnActiveDisplay`:
/// - `"top"` → `wa.y + margin`
/// - `"bottom"` → `wa.y + wa.height - bubble_h - margin`, clamped to
///   ≥ `wa.y` (a bubble taller than the work area minus two margins
///   pins to the work-area top instead of poking above it)
/// - anything else → `Err` with the SAME message shape the deleted
///   `parse_keyword_position` produced, so renderer error surfacing is
///   unchanged.
///
/// Saturating arithmetic throughout: extreme work-area/bubble values
/// can't wrap i32 mid-formula.
///
/// # Errors
///
/// Returns `Err` when `position` is neither `"top"` nor `"bottom"`.
pub(super) fn keyword_edge_y_in_work_area(
    position: &str,
    wa: &RectPx,
    bubble_h: i32,
    margin: i32,
) -> Result<i32, String> {
    let y = match position {
        "top" => wa.y.saturating_add(margin),
        "bottom" => wa
            .y
            .saturating_add(wa.height)
            .saturating_sub(bubble_h)
            .saturating_sub(margin)
            .max(wa.y),
        other => {
            return Err(format!(
                "position must be \"top\" or \"bottom\", got {:?}",
                other
            ))
        }
    };
    Ok(y)
}

/// Compose the full bubble placement for one monitor's work area:
/// horizontally centered + top/bottom edge y, all physical pixels.
/// This is the single entry point `bubble_set_position` calls once per
/// invocation after resolving the cursor's monitor.
///
/// Mirrors Electron's `centerOnActiveDisplay`
/// (`voice_typer/client/src/main/windows/bubble/positioning.ts:211-222`)
/// except that the bubble dimensions come from the LIVE window's
/// measured `outer_size()` (physical px) rather than compile-time
/// constants — the Tauri bubble resizes itself to fit its pill content,
/// so the measured size is the correct input here.
///
/// # Errors
///
/// Returns `Err` when `position` is neither `"top"` nor `"bottom"`
/// (see [`keyword_edge_y_in_work_area`]).
pub(super) fn bubble_position_in_work_area(
    position: &str,
    wa: &RectPx,
    bubble_w: i32,
    bubble_h: i32,
    margin: i32,
) -> Result<(i32, i32), String> {
    let x = centered_x_in_work_area(wa, bubble_w);
    let y = keyword_edge_y_in_work_area(position, wa, bubble_h, margin)?;
    Ok((x, y))
}
