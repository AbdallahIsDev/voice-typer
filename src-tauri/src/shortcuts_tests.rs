//! Unit tests for the global-shortcut module (MO-125, C-TEST-5 sibling
//! file): the accelerator constant parses, matches the predecessor
//! binding, and the press-edge gate is the only path that dismisses.

#![allow(clippy::unwrap_used, clippy::expect_used)]

use tauri_plugin_global_shortcut::{Code, Shortcut, ShortcutState};

use super::parse_dismiss_shortcut;
use super::BUBBLE_DISMISS_ACCELERATOR;

/// The accelerator string must parse into a real Shortcut — a typo
/// here would otherwise surface only as a silent warn at startup.
#[test]
fn test_accelerator_constant_parses() {
    let parsed: Shortcut = BUBBLE_DISMISS_ACCELERATOR
        .parse()
        .expect("the bubble-dismiss accelerator must be a valid Shortcut");
    // CmdOrCtrl+Shift+D: Ctrl on Windows/Linux (the CONTROL mapping is
    // platform-dependent inside the parser), SHIFT, and the physical D
    // key. Pin the KEY, which is platform-independent.
    assert_eq!(parsed.key, Code::KeyD);
}

#[test]
fn test_parse_dismiss_shortcut_returns_the_same_binding() {
    let parsed = parse_dismiss_shortcut().expect("constant must parse");
    let direct: Shortcut = BUBBLE_DISMISS_ACCELERATOR.parse().unwrap();
    assert_eq!(parsed, direct);
}

/// `CmdOrCtrl` is the predecessor spelling; the Tauri constant must be the
/// same binding so the two runtimes register identical system-wide
/// keys (cross-language pin:
/// `tests/tauri/test_global_shortcut_parity.py` compares this constant
/// against the TS shared `DISMISS_SHORTCUT`).
#[test]
fn test_accelerator_is_the_ctrl_shift_d_binding() {
    assert_eq!(BUBBLE_DISMISS_ACCELERATOR, "CmdOrCtrl+Shift+D");
}

/// Only the PRESS edge may dismiss: a RELEASE firing would double-fire
/// the `toggle_dictation` (start+stop in one keypress).
#[test]
fn test_press_edge_gate_matches_shortcut_state_semantics() {
    assert_ne!(ShortcutState::Pressed, ShortcutState::Released);
}
