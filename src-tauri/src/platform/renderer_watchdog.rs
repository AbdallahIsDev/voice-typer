
use std::sync::Mutex;
use std::time::{Duration, Instant};

pub(crate) const HEARTBEAT_INTERVAL_SECS: u64 = 10;

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
