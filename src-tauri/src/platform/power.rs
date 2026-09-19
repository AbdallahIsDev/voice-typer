//! OS power-event handling for the Tauri host (MO-126).
//!
//! predecessor bridged `powerMonitor` suspend/resume/on-battery
//! (`client/src/main/power.ts`). Tauri/wry expose no equivalent
//! (`RunEvent::Resumed` is the winit Android/desktop event-loop resume,
//! NOT OS sleep/wake — verified against docs.rs tauri 2.11.5).
//!
//! This module owns the platform-specific subscription and reduces
//! every OS callback to a small, testable state machine. The **action**
//! is handed to the sidecar supervisor via
//! `tauri::async_runtime::spawn` (the platform thread is NOT a tokio
//! worker and must never `block_on`, C-TOKIO-1):
//!
//! - **suspend** → [`crate::sidecar::supervisor::stop_sidecar_for_suspend`]
//!   (cooperative shutdown frame + short wait + kill_tree backstop;
//!   does NOT mark the host as `shutting_down`).
//! - **resume**  → [`crate::sidecar::supervisor::ensure_sidecar_after_resume`]
//!   (no-op if the WS survived; otherwise one supervisor respawn).
//! - **on-battery** → log only. The Python backend's prewarm scheduler
//!   already backs off on battery; host-side heartbeat retuning needs
//!   a config-schema change owned by another lane. Documented as a
//!   deliberate drop (see `PowerEvent::OnBattery`).
//!
//! The supervisor also checks `SidecarState::power_suspended` and
//! stands down while the OS is suspending so a mid-sleep crash does
//! not spawn a replacement into a frozen process.
//!
//! Platform coverage:
//! - **Windows**: a dedicated thread owns a message-only window and
//!   receives `WM_POWERBROADCAST` (`PBT_APMSUSPEND` /
//!   `PBT_APMRESUMEAUTOMATIC` / `PBT_APMPOWERSTATUSCHANGE`).
//! - **macOS / Linux**: the state machine is fully wired and tested;
//!   the OS subscription is a no-op stub (VALIDATE-ON-HOST / future
//!   NSWorkspace / logind work). The supervisor already recovers a
//!   dead sidecar via respawn, so the worst case without a platform
//!   subscription is a delayed reconnect, not a stuck mic.
//!
//! Every failure degrades to today's behavior (supervisor respawn),
//! never worse. Power callbacks must never panic or block shutdown.

use crate::state::SidecarState;
use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::Arc;

/// Coarse power state, encoded for lock-free observation.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub(crate) enum PowerState {
    Running = 0,
    Suspended = 1,
}

impl PowerState {
    fn from_u8(v: u8) -> Self {
        match v {
            1 => PowerState::Suspended,
            _ => PowerState::Running,
        }
    }
}

/// The OS events this module cares about.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum PowerEvent {
    Suspend,
    Resume,
    /// Power-source change (AC ↔ battery). Log-only by design.
    OnBattery,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum PowerAction {
    /// Suspend while running: stop/finalize the sidecar.
    StopSidecar,
    /// Resume while suspended: ensure the sidecar is running.
    EnsureSidecar,
    /// Redundant event (already in the target state) or log-only.
    None,
}

/// Shared, lock-free power state for the host.
pub(crate) struct PowerMonitor {
    state: AtomicU8,
}

impl PowerMonitor {
    pub(crate) fn new() -> Self {
        Self {
            state: AtomicU8::new(PowerState::Running as u8),
        }
    }

    pub(crate) fn state(&self) -> PowerState {
        PowerState::from_u8(self.state.load(Ordering::SeqCst))
    }

    pub(crate) fn apply(&self, event: PowerEvent) -> PowerAction {
        match event {
            PowerEvent::Suspend => {
                if self.state.swap(PowerState::Suspended as u8, Ordering::SeqCst)
                    == PowerState::Running as u8
                {
                    PowerAction::StopSidecar
                } else {
                    PowerAction::None
                }
            }
            PowerEvent::Resume => {
                if self.state.swap(PowerState::Running as u8, Ordering::SeqCst)
                    == PowerState::Suspended as u8
                {
                    PowerAction::EnsureSidecar
                } else {
                    PowerAction::None
                }
            }
            PowerEvent::OnBattery => PowerAction::None,
        }
    }
}

impl Default for PowerMonitor {
    fn default() -> Self {
        Self::new()
    }
}

/// Spawn the platform power subscription. Returns the shared monitor
/// so the host can also inspect state.
///
/// Best-effort: a failure to subscribe must never abort startup.
/// The OS thread hands sidecar actions to the supervisor via
/// `tauri::async_runtime::spawn` (never `block_on` from this thread,
/// C-TOKIO-1).
pub(crate) fn spawn_power_monitor(
    app: tauri::AppHandle,
    state: Arc<SidecarState>,
) -> Arc<PowerMonitor> {
    let monitor = Arc::new(PowerMonitor::new());
    spawn_platform_watch(app, state, Arc::clone(&monitor));
    monitor
}

#[cfg(target_os = "windows")]
fn spawn_platform_watch(
    app: tauri::AppHandle,
    state: Arc<SidecarState>,
    monitor: Arc<PowerMonitor>,
) {
    match std::thread::Builder::new()
        .name("vt-power-monitor".into())
        .spawn(move || windows_power_loop(app, state, monitor))
    {
        Ok(_handle) => {
            log::info!("[POWER] platform power-monitor thread spawned");
        }
        Err(e) => {
            log::warn!(
                "[POWER] failed to spawn power-monitor thread: {}; \
                 suspend/resume sidecar actions unavailable (supervisor respawn still covers wake)",
                e
            );
        }
    }
}

#[cfg(not(target_os = "windows"))]
fn spawn_platform_watch(
    _app: tauri::AppHandle,
    _state: Arc<SidecarState>,
    _monitor: Arc<PowerMonitor>,
) {
    log::info!(
        "[POWER] platform subscription not implemented on this OS; \
         relying on supervisor respawn after wake (MO-126 stub)"
    );
}

pub(crate) fn map_windows_power_wparam(wparam: u32) -> Option<PowerEvent> {
    match wparam {
        4 => Some(PowerEvent::Suspend),
        7 | 18 => Some(PowerEvent::Resume),
        10 => Some(PowerEvent::OnBattery),
        _ => None,
    }
}

pub(crate) fn mirror_power_flag(state: &Arc<SidecarState>, action: PowerAction) {
    match action {
        PowerAction::StopSidecar => {
            state.power_suspended.store(true, Ordering::SeqCst);
        }
        PowerAction::EnsureSidecar => {
            state.power_suspended.store(false, Ordering::SeqCst);
        }
        PowerAction::None => {}
    }
}

/// Hand a [`PowerAction`] to the sidecar supervisor without blocking
/// the caller (the platform power thread is not a tokio worker,
/// C-TOKIO-1). Spawns a fire-and-forget task on the host runtime;
/// every failure inside those tasks is already best-effort.
pub(crate) fn dispatch_power_action(
    app: &tauri::AppHandle,
    state: &Arc<SidecarState>,
    action: PowerAction,
) {
    mirror_power_flag(state, action);
    match action {
        PowerAction::StopSidecar => {
            let st = Arc::clone(state);
            tauri::async_runtime::spawn(async move {
                crate::sidecar::supervisor::stop_sidecar_for_suspend(&st).await;
            });
        }
        PowerAction::EnsureSidecar => {
            let app = app.clone();
            let st = Arc::clone(state);
            tauri::async_runtime::spawn(async move {
                crate::sidecar::supervisor::ensure_sidecar_after_resume(&app, &st).await;
            });
        }
        PowerAction::None => {}
    }
}

pub(crate) fn handle_power_event(
    app: &tauri::AppHandle,
    state: &Arc<SidecarState>,
    monitor: &PowerMonitor,
    event: PowerEvent,
) -> PowerAction {
    let action = monitor.apply(event);
    match event {
        PowerEvent::Suspend => log::info!(
            "[POWER] suspend received; action={action:?} state={:?}",
            monitor.state()
        ),
        PowerEvent::Resume => log::info!(
            "[POWER] resume received; action={action:?} state={:?}",
            monitor.state()
        ),
        PowerEvent::OnBattery => {
            log::info!(
                "[POWER] power-status change (on-battery/AC); log-only (MO-126 documented drop)"
            )
        }
    }
    dispatch_power_action(app, state, action);
    action
}

#[cfg(target_os = "windows")]
fn windows_power_loop(
    app: tauri::AppHandle,
    state: Arc<SidecarState>,
    monitor: Arc<PowerMonitor>,
) {
    use windows::Win32::Foundation::{HWND, LPARAM, LRESULT, WPARAM};
    use windows::Win32::UI::WindowsAndMessaging::{
        CreateWindowExW, DefWindowProcW, DestroyWindow, DispatchMessageW, GetMessageW,
        RegisterClassW, CW_USEDEFAULT, HWND_MESSAGE, MSG, WINDOW_EX_STYLE, WM_POWERBROADCAST,
        WNDCLASSW, WS_POPUP,
    };

    // A message-only window receives WM_POWERBROADCAST without any
    // visible surface. Failure is non-fatal: the supervisor recovers.
    unsafe extern "system" fn wnd_proc(
        hwnd: HWND,
        msg: u32,
        wparam: WPARAM,
        lparam: LPARAM,
    ) -> LRESULT {
        if msg == WM_POWERBROADCAST {
            let _ = lparam;
            let _ = wparam;
            return LRESULT(1);
        }
        DefWindowProcW(hwnd, msg, wparam, lparam)
    }

    let class_name: Vec<u16> = "VoiceTyperPowerMonitor\0".encode_utf16().collect();
    let wnd_class = WNDCLASSW {
        lpfnWndProc: Some(wnd_proc),
        lpszClassName: windows::core::PCWSTR(class_name.as_ptr()),
        ..Default::default()
    };
    unsafe {
        if RegisterClassW(&wnd_class) == 0 {
            log::warn!("[POWER] RegisterClassW failed; power events unavailable");
            return;
        }
        let hwnd = CreateWindowExW(
            WINDOW_EX_STYLE::default(),
            windows::core::PCWSTR(class_name.as_ptr()),
            windows::core::PCWSTR::null(),
            WS_POPUP,
            CW_USEDEFAULT,
            CW_USEDEFAULT,
            CW_USEDEFAULT,
            CW_USEDEFAULT,
            Some(HWND_MESSAGE),
            None,
            None,
            None,
        );
        let Ok(hwnd) = hwnd else {
            log::warn!("[POWER] CreateWindowExW failed; power events unavailable");
            return;
        };

        let mut msg = MSG::default();
        while GetMessageW(&mut msg, Some(hwnd), 0, 0).as_bool() {
            if msg.message == WM_POWERBROADCAST {
                let wparam = msg.wParam.0 as u32;
                if let Some(event) = map_windows_power_wparam(wparam) {
                    handle_power_event(&app, &state, &monitor, event);
                }
            }
            let _ = DispatchMessageW(&msg);
        }
        let _ = DestroyWindow(hwnd);
    }
}

#[cfg(test)]
#[path = "power_tests.rs"]
mod power_tests;
