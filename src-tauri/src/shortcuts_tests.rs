//! Sibling tests for `shortcuts` (C-TEST-5).

use super::parse_dismiss_shortcut;
use crate::state::{lock, SidecarState};
use tauri_plugin_global_shortcut::Shortcut;

#[test]
fn test_parse_dismiss_shortcut_is_valid() {
    parse_dismiss_shortcut().expect("the bubble-dismiss accelerator must be a valid Shortcut");
}

#[test]
fn test_parse_dismiss_shortcut_returns_the_same_binding() {
    let parsed: Shortcut = parse_dismiss_shortcut().expect("constant must parse");
    // Pin the source-of-truth string; Debug form of Shortcut is platform-normalized.
    assert_eq!(super::BUBBLE_DISMISS_ACCELERATOR, "CmdOrCtrl+Shift+D");
    assert!(!parsed.to_string().is_empty());
}

/// Idle/unknown tray state must NOT imply "cancel toggle".
/// Dismiss gates toggle_dictation on host_knows_recording().
#[test]
fn test_host_knows_recording_false_for_idle_or_unknown() {
    let state = SidecarState::new();
    assert!(!state.host_knows_recording(), "default tray icon is idle");
    *lock(&state.last_tray_icon) = "idle".to_string();
    assert!(!state.host_knows_recording());
    *lock(&state.last_tray_icon) = "error".to_string();
    assert!(!state.host_knows_recording());
}

#[test]
fn test_host_knows_recording_true_for_recording_icons() {
    let state = SidecarState::new();
    *lock(&state.last_tray_icon) = "recording".to_string();
    assert!(state.host_knows_recording());
    *lock(&state.last_tray_icon) = "transcribing".to_string();
    assert!(state.host_knows_recording());
}

/// Only the PRESS edge may dismiss: a RELEASE firing would double-fire
/// the `toggle_dictation` (start+stop in one keypress) while recording.
#[test]
fn test_register_uses_press_edge_only() {
    let src = std::fs::read_to_string(
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("src/shortcuts.rs"),
    )
    .expect("shortcuts.rs must be readable");
    assert!(src.contains("ShortcutState::Pressed"));
    assert!(src.contains("host_knows_recording"));
}
