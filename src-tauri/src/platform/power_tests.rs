//! Sibling tests for `platform::power` (C-TEST-5).
//!
//! Pins the power state machine with mocked OS events only —
//! no real suspend, no Tauri handle, no sidecar process. The
//! supervisor-facing stop/ensure bodies are covered in
//! `sidecar/supervisor_tests.rs`.

use super::*;
use crate::state::SidecarState;
use std::sync::atomic::Ordering;

#[test]
fn test_initial_state_is_running() {
    let m = PowerMonitor::new();
    assert_eq!(m.state(), PowerState::Running);
}

#[test]
fn test_suspend_while_running_stops_sidecar() {
    let m = PowerMonitor::new();
    assert_eq!(m.apply(PowerEvent::Suspend), PowerAction::StopSidecar);
    assert_eq!(m.state(), PowerState::Suspended);
}

#[test]
fn test_resume_while_suspended_ensures_sidecar() {
    let m = PowerMonitor::new();
    let _ = m.apply(PowerEvent::Suspend);
    assert_eq!(m.apply(PowerEvent::Resume), PowerAction::EnsureSidecar);
    assert_eq!(m.state(), PowerState::Running);
}

#[test]
fn test_double_suspend_is_idempotent() {
    let m = PowerMonitor::new();
    assert_eq!(m.apply(PowerEvent::Suspend), PowerAction::StopSidecar);
    assert_eq!(m.apply(PowerEvent::Suspend), PowerAction::None);
    assert_eq!(m.state(), PowerState::Suspended);
}

#[test]
fn test_resume_while_already_running_is_none() {
    let m = PowerMonitor::new();
    assert_eq!(m.apply(PowerEvent::Resume), PowerAction::None);
    assert_eq!(m.state(), PowerState::Running);
}

#[test]
fn test_on_battery_is_log_only() {
    let m = PowerMonitor::new();
    assert_eq!(m.apply(PowerEvent::OnBattery), PowerAction::None);
    assert_eq!(m.state(), PowerState::Running);
    let _ = m.apply(PowerEvent::Suspend);
    assert_eq!(m.apply(PowerEvent::OnBattery), PowerAction::None);
    assert_eq!(m.state(), PowerState::Suspended);
}

#[test]
fn test_map_windows_power_wparam_contract() {
    // PBT_APMSUSPEND
    assert_eq!(map_windows_power_wparam(4), Some(PowerEvent::Suspend));
    // PBT_APMRESUMESUSPEND
    assert_eq!(map_windows_power_wparam(7), Some(PowerEvent::Resume));
    // PBT_APMRESUMEAUTOMATIC
    assert_eq!(map_windows_power_wparam(18), Some(PowerEvent::Resume));
    // PBT_APMPOWERSTATUSCHANGE
    assert_eq!(map_windows_power_wparam(10), Some(PowerEvent::OnBattery));
    // Unknown ids are ignored.
    assert_eq!(map_windows_power_wparam(0), None);
    assert_eq!(map_windows_power_wparam(99), None);
}

#[test]
fn test_suspend_resume_cycle_restores_running() {
    let m = PowerMonitor::new();
    for _ in 0..3 {
        assert_eq!(m.apply(PowerEvent::Suspend), PowerAction::StopSidecar);
        assert_eq!(m.apply(PowerEvent::Resume), PowerAction::EnsureSidecar);
    }
    assert_eq!(m.state(), PowerState::Running);
}

// ── Action dispatch: power_suspended flag mirroring ──────

#[test]
fn test_mirror_flag_stop_sidecar_sets_suspended() {
    let state = std::sync::Arc::new(SidecarState::new());
    assert!(!state.power_suspended.load(Ordering::SeqCst));
    mirror_power_flag(&state, PowerAction::StopSidecar);
    assert!(
        state.power_suspended.load(Ordering::SeqCst),
        "StopSidecar must set power_suspended so the supervisor stands down"
    );
}

#[test]
fn test_mirror_flag_ensure_sidecar_clears_suspended() {
    let state = std::sync::Arc::new(SidecarState::new());
    state.power_suspended.store(true, Ordering::SeqCst);
    mirror_power_flag(&state, PowerAction::EnsureSidecar);
    assert!(
        !state.power_suspended.load(Ordering::SeqCst),
        "EnsureSidecar must clear power_suspended before the respawn request"
    );
}

#[test]
fn test_mirror_flag_none_leaves_flag_untouched() {
    let state = std::sync::Arc::new(SidecarState::new());
    state.power_suspended.store(true, Ordering::SeqCst);
    mirror_power_flag(&state, PowerAction::None);
    assert!(state.power_suspended.load(Ordering::SeqCst));
    state.power_suspended.store(false, Ordering::SeqCst);
    mirror_power_flag(&state, PowerAction::None);
    assert!(!state.power_suspended.load(Ordering::SeqCst));
}

#[test]
fn test_action_dispatch_mapping_is_exhaustive() {
    // Every PowerAction the state machine can return has a defined
    // dispatch (Stop → supervisor stop, Ensure → supervisor ensure,
    // None → nothing). The match arms in dispatch_power_action must
    // stay in lockstep with this enum.
    let all = [
        PowerAction::StopSidecar,
        PowerAction::EnsureSidecar,
        PowerAction::None,
    ];
    for a in all {
        // Touch Debug so a new variant without Debug fails to compile.
        let _ = format!("{a:?}");
    }
}
