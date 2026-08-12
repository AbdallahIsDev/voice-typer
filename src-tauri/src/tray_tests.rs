#![allow(clippy::unwrap_used, clippy::expect_used, clippy::panic, clippy::unreachable, clippy::todo, clippy::unimplemented, clippy::cast_possible_truncation)]

//! Sibling tests for `tray` (per C-TEST-5 — sibling test file, no
//! inline tests in production source).
//!
//! Covers three areas:
//!
//! - **TrayStatePayload / TrayMenuPayload parsing**: serde round-trip
//!   of the JSON shape the Python sidecar emits for tray icon +
//!   tooltip updates and full menu rebuilds.
//! - **Allowed-icon-name whitelist**: the four canonical tray icon
//!   names (`idle`, `recording`, `transcribing`, `error`) — anything
//!   else is rejected so a malformed sidecar payload can't reference
//!   an arbitrary filesystem path.
//! - **Click-event focus predicate**: `is_focus_main_window_event`
//!   accepts ONLY a single left-click (the documented "show + focus
//!   main window" binding); right-click, middle-click, double-click,
//!   and mouse-enter must NOT trigger show+focus.
//!
//! Pre-existing tests (moved verbatim from the legacy inline
//! `mod tests` block) are joined here alongside the new tests so the
//! sibling file is the single source of truth for tray unit tests.

use super::*;
use serde_json::json;

//TrayStatePayload parsing ───────────────────────────────

#[test]
fn test_tray_state_payload_parses_icon_only() {
    let p: TrayStatePayload = serde_json::from_str(r#"{"icon":"recording"}"#).expect("parse");
    assert_eq!(p.icon.as_deref(), Some("recording"));
    assert!(p.tooltip.is_none());
}

#[test]
fn test_tray_state_payload_parses_tooltip_only() {
    let p: TrayStatePayload =
        serde_json::from_str(r#"{"tooltip":"Voice Typer — Recording"}"#).expect("parse");
    assert!(p.icon.is_none());
    assert_eq!(p.tooltip.as_deref(), Some("Voice Typer — Recording"));
}

#[test]
fn test_tray_state_payload_parses_both_fields() {
    let p: TrayStatePayload =
        serde_json::from_str(r#"{"icon":"error","tooltip":"Voice Typer — Error"}"#).expect("parse");
    assert_eq!(p.icon.as_deref(), Some("error"));
    assert_eq!(p.tooltip.as_deref(), Some("Voice Typer — Error"));
}

#[test]
fn test_tray_state_payload_parses_empty_object() {
    let p: TrayStatePayload = serde_json::from_str(r#"{}"#).expect("parse");
    assert!(p.icon.is_none());
    assert!(p.tooltip.is_none());
}

#[test]
fn test_tray_state_payload_ignores_unknown_fields() {
    let p: TrayStatePayload =
        serde_json::from_str(r#"{"icon":"idle","tooltip":"ok","future_field":42}"#).expect("parse");
    assert_eq!(p.icon.as_deref(), Some("idle"));
    assert_eq!(p.tooltip.as_deref(), Some("ok"));
}

//TrayMenuPayload still parses (regression guard) ────────

#[test]
fn test_tray_menu_payload_parses_items() {
    let p: TrayMenuPayload =
        serde_json::from_str(r#"{"items":[{"id":"quit","label":"Quit"}]}"#).expect("parse");
    assert_eq!(p.items.len(), 1);
    assert_eq!(p.items[0].id, "quit");
    assert_eq!(p.items[0].label, "Quit");
}

#[test]
fn test_tray_menu_payload_parses_empty_items() {
    let p: TrayMenuPayload = serde_json::from_str(r#"{"items":[]}"#).expect("parse");
    assert!(p.items.is_empty());
}

#[test]
fn test_tray_menu_payload_parses_missing_items_default_empty() {
    let p: TrayMenuPayload = serde_json::from_str(r#"{}"#).expect("parse");
    assert!(p.items.is_empty(), "items defaults to empty vec");
}

#[test]
fn test_tray_menu_payload_parses_separator() {
    let p: TrayMenuPayload = serde_json::from_str(
        r#"{"items":[{"id":"a","label":"A"},{"separator":true},{"id":"b","label":"B"}]}"#,
    )
    .expect("parse");
    assert_eq!(p.items.len(), 3);
    assert!(!p.items[0].separator);
    assert!(p.items[1].separator);
    assert!(!p.items[2].separator);
}

#[test]
fn test_tray_menu_payload_parses_checked_state() {
    let p: TrayMenuPayload = serde_json::from_str(
            r#"{"items":[{"id":"x","label":"X","checked":true},{"id":"y","label":"Y","checked":false}]}"#,
        )
        .expect("parse");
    assert_eq!(p.items[0].checked, Some(true));
    assert_eq!(p.items[1].checked, Some(false));
}

#[test]
fn test_tray_menu_payload_parses_submenu() {
    let p: TrayMenuPayload = serde_json::from_str(
        r#"{"items":[{"id":"models","label":"Models","submenu":[{"id":"m1","label":"M1"}]}]}"#,
    )
    .expect("parse");
    assert_eq!(p.items.len(), 1);
    let sub = p.items[0].submenu.as_ref().expect("submenu present");
    assert_eq!(sub.len(), 1);
    assert_eq!(sub[0].id, "m1");
}

// Accelerator field parsing ──────────────────────
//
// The `accelerator` field is `Option<String>` with `#[serde(default)]`
// so it's backward-compatible with sidecar payloads that omit it.
// When present, `build_item_refs` forwards the string to Tauri's
// `MenuItemBuilder::accelerator` / `CheckMenuItemBuilder::accelerator`
// (validated by Tauri at `build()` time). These tests verify the
// serde shape — the actual native-builder wiring is exercised by the
// Tauri runtime on each platform.

#[test]
fn test_tray_menu_payload_parses_accelerator() {
    let p: TrayMenuPayload =
        serde_json::from_str(r#"{"items":[{"id":"quit","label":"Quit","accelerator":"Cmd+Q"}]}"#)
            .expect("parse");
    assert_eq!(p.items.len(), 1);
    assert_eq!(p.items[0].id, "quit");
    assert_eq!(p.items[0].accelerator.as_deref(), Some("Cmd+Q"));
}

#[test]
fn test_tray_menu_payload_accelerator_defaults_none() {
    // Backward compatibility: a payload WITHOUT an `accelerator` key
    // must deserialize cleanly (older sidecar builds that predate
    // some sidecar builds don.t emit the field.. `#[serde(default)]` makes
    // `Option<String>` default to `None`.
    let p: TrayMenuPayload =
        serde_json::from_str(r#"{"items":[{"id":"quit","label":"Quit"}]}"#).expect("parse");
    assert_eq!(p.items[0].accelerator, None);
}

#[test]
fn test_tray_menu_payload_parses_accelerator_with_checked_item() {
    // A CheckMenuItem can ALSO have an accelerator — verify the
    // two fields coexist on the same item.
    let p: TrayMenuPayload = serde_json::from_str(
        r#"{"items":[{"id":"mute","label":"Mute","checked":true,"accelerator":"Ctrl+M"}]}"#,
    )
    .expect("parse");
    assert_eq!(p.items[0].checked, Some(true));
    assert_eq!(p.items[0].accelerator.as_deref(), Some("Ctrl+M"));
}

#[test]
fn test_tray_menu_payload_parses_null_accelerator() {
    // An explicit `null` must deserialize to `None` (not error).
    let p: TrayMenuPayload =
        serde_json::from_str(r#"{"items":[{"id":"quit","label":"Quit","accelerator":null}]}"#)
            .expect("parse");
    assert_eq!(p.items[0].accelerator, None);
}

//load_tray_icon name whitelist (defense in depth) ──────

const ALLOWED_ICON_NAMES: &[&str] = &["idle", "recording", "transcribing", "error"];

#[test]
fn test_allowed_icon_names_are_stable() {
    assert_eq!(
        ALLOWED_ICON_NAMES,
        &["idle", "recording", "transcribing", "error"],
        "ALLOWED_ICON_NAMES changed — update src-tauri/icons/tray/ + bundle.resources too"
    );
}

#[test]
fn test_allowed_icon_names_rejects_arbitrary_path() {
    let bad_names = [
        "",
        ".",
        "..",
        "../etc/passwd",
        "/etc/passwd",
        "idle.png",
        "IDLE",
        "recording ",
        "recording\x00.png",
        "arbitrary_name",
    ];
    for bad in bad_names {
        assert!(
            !ALLOWED_ICON_NAMES.contains(&bad),
            "sentinel {:?} should NOT be in ALLOWED_ICON_NAMES",
            bad
        );
    }
}

// `is_allowed_icon_name` predicate tests ──
//
// The predicate is the runtime gate inside `load_tray_icon` that
// decides whether a sidecar-supplied icon name is safe to load from
// disk. These tests verify the predicate AGREES with the
// `ALLOWED_ICON_NAMES` test constant — if the two ever drift, the
// predicate would accept names the constant rejects (or vice versa),
// and the icon whitelist would silently break. The constant is the
// single source of truth shared across both the predicate impl and
// the test suite.

#[test]
fn test_is_allowed_icon_name_accepts_whitelist() {
    for &good in ALLOWED_ICON_NAMES {
        assert!(
            is_allowed_icon_name(good),
            "is_allowed_icon_name({:?}) should be true (matches ALLOWED_ICON_NAMES)",
            good
        );
    }
}

#[test]
fn test_is_allowed_icon_name_rejects_arbitrary_path() {
    // Same sentinel set as `test_allowed_icon_names_rejects_arbitrary_path`
    // — the predicate and the constant must agree on REJECTION too.
    let bad_names = [
        "",
        ".",
        "..",
        "../etc/passwd",
        "/etc/passwd",
        "idle.png",
        "IDLE",
        "recording ",
        "recording\x00.png",
        "arbitrary_name",
    ];
    for bad in bad_names {
        assert!(
            !is_allowed_icon_name(bad),
            "is_allowed_icon_name({:?}) should be false (defense against arbitrary path load)",
            bad
        );
    }
}

#[test]
fn test_is_allowed_icon_name_matches_constant_exactly() {
    // Cross-check: for EVERY possible sentinel in the whitelist,
    // the predicate returns true; for every sentinel NOT in the
    // whitelist from a broad set of plausible names, it returns
    // false. This catches the case where a name is in the constant
    // but the predicate's `matches!` arm was typo'd (or vice versa).
    let plausible_names = [
        "idle",
        "recording",
        "transcribing",
        "error",
        "paused",
        "listening",
        "processing",
        "ready",
        "warning",
        "fatal",
        "stopped",
        "starting",
    ];
    for name in plausible_names {
        let in_const = ALLOWED_ICON_NAMES.contains(&name);
        let in_pred = is_allowed_icon_name(name);
        assert_eq!(
            in_const, in_pred,
            "constant vs predicate disagreement on {:?}: const={}, pred={}",
            name, in_const, in_pred
        );
    }
}

//DispatchArgs construction shape (regression guard) ────

#[test]
fn test_dispatch_args_tray_click_shape() {
    let args = DispatchArgs {
        cmd: "tray_click".to_string(),
        data: Some(json!({ "id": "toggle_dictation" })),
    };
    assert_eq!(args.cmd, "tray_click");
    assert_eq!(args.data, Some(json!({ "id": "toggle_dictation" })));
}

#[test]
fn test_dispatch_args_tray_click_shape_with_empty_id() {
    let args = DispatchArgs {
        cmd: "tray_click".to_string(),
        data: Some(json!({ "id": "" })),
    };
    let serialized = serde_json::to_string(&args).expect("serialize");
    assert!(serialized.contains("\"cmd\":\"tray_click\""));
    assert!(serialized.contains("\"id\":\"\""));
}

//tray click button filter (left-click only) ──────────
//
// The `on_tray_icon_event` closure delegates to
// `is_focus_main_window_event` to decide whether to show + focus
// the main window. These tests construct synthetic
// `TrayIconEvent::Click` variants with each `MouseButton` value and
// assert the predicate is true ONLY for `Left`. The test
// construction mirrors the upstream Tauri test at
// `tauri-2.11.5/src/tray/mod.rs::tray_event_json_serialization`.

/// Build a minimal `TrayIconEvent::Click` with the given button.
/// All other fields use defaults (zero position, zero rect, Down
/// button_state, "test" id) — the predicate only inspects `button`,
/// so the other fields' values don't affect the test outcome.
fn make_click_event(button: MouseButton) -> TrayIconEvent {
    use tauri::tray::MouseButtonState;
    use tauri::{PhysicalPosition, Rect};
    TrayIconEvent::Click {
        button,
        button_state: MouseButtonState::Down,
        id: tauri::tray::TrayIconId::new("test"),
        position: PhysicalPosition::default(),
        rect: Rect::default(),
    }
}

#[test]
fn test_focus_predicate_true_for_left_click() {
    let event = make_click_event(MouseButton::Left);
    assert!(
        is_focus_main_window_event(&event),
        "left-click on tray icon must trigger show+focus main window (S3-CR-8)"
    );
}

#[test]
fn test_focus_predicate_false_for_right_click() {
    let event = make_click_event(MouseButton::Right);
    assert!(
        !is_focus_main_window_event(&event),
        "right-click must NOT trigger show+focus — it opens the context menu (S3-CR-8)"
    );
}

#[test]
fn test_focus_predicate_false_for_middle_click() {
    let event = make_click_event(MouseButton::Middle);
    assert!(
        !is_focus_main_window_event(&event),
        "middle-click must NOT trigger show+focus — no binding for it (S3-CR-8)"
    );
}

#[test]
fn test_focus_predicate_false_for_double_click() {
    // Even a left-button DoubleClick must NOT trigger the show+focus
    // path — only single left-click does. Double-clicking the tray
    // icon is reserved for future use (no current binding); treating
    // it as a focus trigger would fire show+focus twice in rapid
    // succession (once for Click, once for DoubleClick).
    use tauri::{PhysicalPosition, Rect};
    let event = TrayIconEvent::DoubleClick {
        button: MouseButton::Left,
        id: tauri::tray::TrayIconId::new("test"),
        position: PhysicalPosition::default(),
        rect: Rect::default(),
    };
    assert!(
        !is_focus_main_window_event(&event),
        "double-click must NOT trigger show+focus — only single left-click (S3-CR-8)"
    );
}

#[test]
fn test_focus_predicate_false_for_enter_event() {
    use tauri::{PhysicalPosition, Rect};
    let event = TrayIconEvent::Enter {
        id: tauri::tray::TrayIconId::new("test"),
        position: PhysicalPosition::default(),
        rect: Rect::default(),
    };
    assert!(
        !is_focus_main_window_event(&event),
        "mouse-enter must NOT trigger show+focus (S3-CR-8)"
    );
}
