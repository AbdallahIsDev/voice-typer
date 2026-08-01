//! Bubble window commands ( + ADR-0020 §9).
//!
//! The 9 `#[tauri::command]` functions exposed to the renderer live
//! in [`commands`]. Pure helpers are split by concern: position
//! parsing in [`parse`], geometry math in [`math`], the toggle
//! rate limiter in [`rate_limit`], and the shared bubble-hide helper
//! in [`window`]. Unit tests live in [`tests`].
//!
//! [`commands`]: commands
//! [`parse`]: parse
//! [`math`]: math
//! [`rate_limit`]: rate_limit
//! [`window`]: window
//! [`tests`]: tests

mod commands;
mod math;
mod parse;
mod rate_limit;
mod window;

pub(crate) use commands::{
    bubble_dismiss, bubble_hide_complete, bubble_move_by, bubble_resize, bubble_set_draggable,
    bubble_set_position, bubble_show, bubble_signal_ready, bubble_toggle_dictation,
};

#[cfg(test)]
mod tests;
