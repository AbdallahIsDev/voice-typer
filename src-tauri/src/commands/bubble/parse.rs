
#[cfg(test)]
use serde_json::Value;

#[cfg(test)]
use super::math::clamp_f64_to_i32;

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
                i32::try_from(i).map_err(|e| format!("coordinate out of range ({}): {}", i, e))?
            } else if let Some(f) = n.as_f64() {
                clamp_f64_to_i32(f)
            } else {
                return Err(format!("x must be a number, got {:?}", x));
            }
        }
        Value::String(s) => match s.as_str() {
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
