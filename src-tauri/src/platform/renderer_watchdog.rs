//! Renderer / webview liveness watchdog (review.md MO-113).
//!
//! predecessor logged every child-process abnormal exit
//! (`child-process-gone`: GPU process crash, utility process crash,
//! renderer OOM) from `bootstrap/runtime.ts` into the host log, which is
//! the ONLY signal available when a window renders blank. After the
//! predecessor→Tauri cutover the Rust host had just a panic hook, so a
//! WebView2 renderer freeze / GPU crash left `voice-typer-rust.log`
//! completely clean while the user stared at a blank or frozen window.
//!
//! # Why a heartbeat instead of a process event
//!
//! Wry/Tauri v2 expose NO cross-platform renderer-crash event: WebView2's
//! `ProcessFailed` is not surfaced by wry, WKWebView has no crash
//! notification at all, and webkit2gtk's `web-process-crashed` signal is
//! likewise not surfaced. So the renderer process cannot be watched from
//! the host directly.
//!
//! What CAN be observed is whether the renderer's JavaScript is still
//! RUNNING: the renderer posts a lightweight heartbeat (a bare
//! `renderer_heartbeat` invoke) on an interval, and this watchdog
//! compares the newest heartbeat against a stall threshold. A frozen
//! webview stops executing timers, so the heartbeat stops with it, that
//! is the blank-window signal.
//!
//! # False-positive guards
//!
//! - The watchdog only evaluates while the MAIN window is visible AND not
//!   minimized: background/occluded windows legitimately have their
//!   timers throttled by every engine (WebView2 background throttling,
//!   macOS App Nap, `webkit2gtk` occlusion detection), so a hidden
//!   window must never be reported as stalled.
//! - The renderer only sends heartbeats while
//!   `document.visibilityState === "visible"` (same reason).
//! - One ERROR per stall episode (the `stalled` flag), plus a single INFO
//!   when heartbeats resume, so a long freeze cannot flood the log.
//!
//! This is telemetry only: nothing is killed, restarted, or spawned from
//! here. It mirrors the predecessor's shape (log the abnormal condition, let the
//! user decide) without inventing process control that the platform
//! cannot back.

use std::sync::Mutex;
use std::time::{Duration, Instant};

/// How often the renderer posts a heartbeat while its window is visible.
/// The renderer-side interval is the same value; the watchdog uses it to
/// derive the stall threshold below.
pub(crate) const HEARTBEAT_INTERVAL_SECS: u64 = 10;

/// A visible, un-minimized window that has not beaten for this long is
/// reported as stalled. 3x [`HEARTBEAT_INTERVAL_SECS`] tolerates a
/// dropped/queued frame (e.g. a long React render or a GC pause) without
/// crying wolf, while still surfacing a real freeze within ~30 s.
pub(crate) const STALL_THRESHOLD_SECS: u64 = 30;

/// How often the watchdog evaluates the heartbeat.
const WATCHDOG_TICK_SECS: u64 = 5;

/// Latest observed renderer state, updated by the `renderer_heartbeat`
/// command and read by the watchdog task.
#[derive(Debug, Default)]
pub(crate) struct HeartbeatState {
    /// Newest heartbeat timestamp, `None` until the renderer's first beat.
    last: Mutex<Option<Instant>>,
    /// Whether the current stall episode has already been logged (one
    /// ERROR per episode, cleared when heartbeats resume).
    stalled: Mutex<bool>,
}

impl HeartbeatState {
    pub(crate) fn new() -> Self {
        Self::default()
    }

    /// Record a heartbeat from the renderer.
    pub(crate) fn record(&self) {
        *crate::state::lock(&self.last) = Some(Instant::now());
    }

    /// Snapshot of (last heartbeat, already-stalled flag).
    fn snapshot(&self) -> (Option<Instant>, bool) {
        (*crate::state::lock(&self.last), *crate::state::lock(&self.stalled))
    }

    fn mark_stalled(&self) {
        *crate::state::lock(&self.stalled) = true;
    }

    fn clear_stalled(&self) -> bool {
        let mut flag = crate::state::lock(&self.stalled);
        let was = *flag;
        *flag = false;
        was
    }
}

/// What the watchdog should do on one tick. Pure so the policy is
/// unit-testable without a Tauri runtime or real timers.
#[derive(Debug, PartialEq, Eq)]
pub(crate) enum WatchdogAction {
    /// Nothing observable: no heartbeat yet, window not eligible, or the
    /// heartbeat is fresh.
    Idle,
    /// The window is visible/un-minimized and the heartbeat is stale:
    /// log the stall (once per episode).
    ReportStall {
        /// Seconds since the last heartbeat (for the log line).
        stale_secs: u64,
    },
    /// Heartbeats resumed after a reported stall: log the recovery.
    ReportRecovery,
}

/// Decide what to log on this tick (pure policy).
///
/// `eligible` is "the main window exists, is visible, and is not
/// minimized": the caller resolves it from the live window, and a
/// non-eligible window can never be reported (see the module docs on
/// engine throttling of background windows).
pub(crate) fn watchdog_decision(
    last: Option<Instant>,
    already_stalled: bool,
    eligible: bool,
    now: Instant,
    threshold: Duration,
) -> WatchdogAction {
    let Some(last) = last else {
        // No beat since host start (the window may still be booting, or
        // the renderer never installed the heartbeat): nothing to judge.
        return WatchdogAction::Idle;
    };
    let stale = now.duration_since(last);
    if !eligible {
        // Not visible / minimized: throttling is expected. Clear the
        // episode flag so a later eligible tick re-reports cleanly, but
        // do NOT log the transition (noise).
        return WatchdogAction::Idle;
    }
    if stale > threshold {
        if already_stalled {
            WatchdogAction::Idle
        } else {
            WatchdogAction::ReportStall {
                stale_secs: stale.as_secs(),
            }
        }
    } else if already_stalled {
        WatchdogAction::ReportRecovery
    } else {
        WatchdogAction::Idle
    }
}

/// Spawn the watchdog loop. Called once from `main.rs` setup.
///
/// Runs on the async runtime, off the event loop. Never blocks: each
/// tick is an atomic-ish state read plus a window-state query.
pub(crate) fn spawn_watchdog(app: &tauri::AppHandle) {
    use tauri::Manager;

    let state = app.state::<std::sync::Arc<HeartbeatState>>().inner().clone();
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        let threshold = Duration::from_secs(STALL_THRESHOLD_SECS);
        loop {
            tokio::time::sleep(Duration::from_secs(WATCHDOG_TICK_SECS)).await;
            // Eligibility: main window exists, is visible, not minimized.
            let eligible = match app.get_webview_window("main") {
                Some(window) => {
                    let visible = window.is_visible().unwrap_or(false);
                    let minimized = window.is_minimized().unwrap_or(false);
                    visible && !minimized
                }
                None => false,
            };
            let (last, stalled) = state.snapshot();
            match watchdog_decision(last, stalled, eligible, Instant::now(), threshold) {
                WatchdogAction::Idle => {}
                WatchdogAction::ReportStall { stale_secs } => {
                    state.mark_stalled();
                    // The redaction pass runs on the `log` sink itself (the
                    // host logger redacts every record), so the line carries
                    // no user content: only timings.
                    log::error!(
                        "[RENDERER-WATCHDOG] webview unresponsive: no heartbeat for {}s \
                         while the main window is visible (interval={}s, threshold={}s) \
                         -- stalled renderer / GPU failure, UI may be blank or frozen",
                        stale_secs,
                        HEARTBEAT_INTERVAL_SECS,
                        STALL_THRESHOLD_SECS
                    );
                }
                WatchdogAction::ReportRecovery => {
                    state.clear_stalled();
                    log::info!("[RENDERER-WATCHDOG] webview heartbeats resumed");
                }
            }
        }
    });
}

// Sibling test module (C-TEST-5: no inline `#[cfg(test)] mod tests` in
// production source).
#[cfg(test)]
#[path = "renderer_watchdog_tests.rs"]
mod renderer_watchdog_tests;
