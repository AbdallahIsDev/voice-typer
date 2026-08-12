//! Unit tests for `commands/sidecar_cmds/window_close.rs` (C-TEST-5:
//! sibling test file, wired via `#[cfg(test)] #[path = "..."]`).

use super::should_hide_to_tray;

#[test]
fn main_window_hides_to_tray_when_not_shutting_down() {
    assert!(
        should_hide_to_tray("main", false, true),
        "main window X click with no shutdown in flight and a tray present must hide to tray (Electron parity)"
    );
}

#[test]
fn main_window_does_not_hide_when_no_tray() {
    assert!(
        !should_hide_to_tray("main", false, false),
        "main window close must flow through to app exit when no tray exists (Linux Wayland without SNI — \
         hiding would strand the user; Electron's isLinuxWaylandWithoutSni() guard)"
    );
}

#[test]
fn main_window_close_is_allowed_during_shutdown() {
    assert!(
        !should_hide_to_tray("main", true, true),
        "main window close must flow through during a deliberate quit so the exit teardown completes"
    );
    assert!(
        !should_hide_to_tray("main", true, false),
        "main window close must flow through during a deliberate quit even when a tray exists"
    );
}

#[test]
fn bubble_window_never_hides_to_tray() {
    assert!(
        !should_hide_to_tray("bubble", false, true),
        "bubble window close is not intercepted"
    );
    assert!(
        !should_hide_to_tray("bubble", true, true),
        "bubble window close is not intercepted even during shutdown"
    );
}

#[test]
fn unknown_window_never_hides_to_tray() {
    assert!(
        !should_hide_to_tray("settings", false, true),
        "unexpected window labels fall through to the default (no interception)"
    );
}
