
pub(super) const MIN_BUBBLE_W: u32 = 40;
pub(super) const MIN_BUBBLE_H: u32 = 24;
pub(super) const MAX_BUBBLE_W: u32 = 400;
pub(super) const MAX_BUBBLE_H: u32 = 200;

pub(super) fn clamp_resize_width(d: u32) -> u32 {
    d.clamp(MIN_BUBBLE_W, MAX_BUBBLE_W)
}

pub(super) fn clamp_resize_height(d: u32) -> u32 {
    d.clamp(MIN_BUBBLE_H, MAX_BUBBLE_H)
}

#[allow(clippy::cast_possible_truncation)] // saturating cast: guarded + documented above
pub(super) fn round_f64_to_u32_saturating(f: f64) -> u32 {
    if f.is_nan() || f < 0.0 {
        return 0;
    }
    let rounded = f.round();
    if rounded > u32::MAX as f64 {
        return u32::MAX;
    }
    rounded as u32
}

#[allow(clippy::cast_possible_truncation)] // saturating cast: clamped + documented above
pub(super) fn round_f64_to_i32_saturating(f: f64) -> i32 {
    if f.is_nan() {
        return 0;
    }
    if f.is_infinite() {
        return if f > 0.0 { i32::MAX } else { i32::MIN };
    }
    let rounded = f.round();
    let clamped = rounded.clamp(i32::MIN as f64, i32::MAX as f64);
    clamped as i32
}

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

#[cfg(test)]
#[allow(clippy::cast_possible_truncation)] // saturating test helper: clamped + documented above
pub(super) fn clamp_f64_to_i32(f: f64) -> i32 {
    if f.is_nan() {
        return 0;
    }
    if f.is_infinite() {
        return if f > 0.0 { i32::MAX } else { i32::MIN };
    }
    let clamped = f.clamp(i32::MIN as f64, i32::MAX as f64);
    clamped as i32
}

// ─── Multi-monitor work-area placement (physical pixels) ─────────────

const EDGE_MARGIN_LOGICAL_PX: f64 = 48.0;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct RectPx {
    pub x: i32,
    pub y: i32,
    pub width: i32,
    pub height: i32,
}

impl RectPx {
    pub(super) fn new(x: i32, y: i32, width: u32, height: u32) -> Self {
        Self {
            x,
            y,
            width: i32::try_from(width).unwrap_or(i32::MAX),
            height: i32::try_from(height).unwrap_or(i32::MAX),
        }
    }

    pub(super) fn contains(&self, px: i32, py: i32) -> bool {
        px >= self.x
            && px < self.x.saturating_add(self.width)
            && py >= self.y
            && py < self.y.saturating_add(self.height)
    }
}

pub(super) fn rect_contains_point(rect: &RectPx, px: i32, py: i32) -> bool {
    rect.contains(px, py)
}

pub(super) fn edge_margin_physical(scale_factor: f64) -> i32 {
    round_f64_to_i32_saturating(EDGE_MARGIN_LOGICAL_PX * scale_factor.max(0.0))
}

pub(super) fn centered_x_in_work_area(wa: &RectPx, bubble_w: i32) -> i32 {
    wa.x + ((wa.width - bubble_w) / 2).max(0)
}

pub(super) fn keyword_edge_y_in_work_area(
    position: &str,
    wa: &RectPx,
    bubble_h: i32,
    margin: i32,
) -> Result<i32, String> {
    let y = match position {
        "top" => wa.y.saturating_add(margin),
        "bottom" => {
            wa.y.saturating_add(wa.height)
                .saturating_sub(bubble_h)
                .saturating_sub(margin)
                .max(wa.y)
        }
        other => {
            return Err(format!(
                "position must be \"top\" or \"bottom\", got {:?}",
                other
            ))
        }
    };
    Ok(y)
}

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
