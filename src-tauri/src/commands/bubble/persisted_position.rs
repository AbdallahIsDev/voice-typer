//! Durable bubble drag-position persistence on the Tauri host.
//!
//! The Python sidecar owns the authoritative `bubble_x` / `bubble_y`
//! config pair. This module gives the host three capabilities around it:
//!
//! 1. **Cache** — the WS reader task (`sidecar/ws.rs`) forwards every
//!    sidecar-published `bubble_config` frame here so the host always
//!    holds the most recent persisted pair (both coordinates, or `None`
//!    after the Settings edge-toggle clears them server-side).
//! 2. **Consume** — `bubble_show` (`commands/bubble/commands.rs`)
//!    restores the cached pair when it is still on-screen, mirroring
//!    Electron's in-session restore in
//!    `voice_typer/client/src/main/windows/bubble/positioning.ts`.
//! 3. **Persist** — the window-event hook in `main.rs` observes user
//!    drags of the bubble window and writes the pair back through the
//!    fire-and-forget dispatch path after a 500ms debounce. Programmatic
//!    placements arm a suppression window so they are never mistaken for
//!    drags (mirrors Electron's `suppressDurablePersistFor`).
//!
//! All state lives in process-global mutexes/atomics because the readers
//! and writers run on different threads (WS reader task, event-loop
//! callback, the long-lived debounce task). No `block_on` anywhere —
//! the debounce task parks on a `tokio::sync::Notify` and re-arms with
//! an async sleep (C-TOKIO-1).

use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

use serde_json::json;
use tokio::sync::Notify;

use crate::commands::sidecar_cmds::dispatch_fire_and_forget;
use crate::state::SidecarState;

/// Debounce window for persisting a dragged position (last move wins).
pub(crate) const PERSIST_DEBOUNCE_MS: u64 = 500;

/// How long programmatic placements suppress persistence. Must outlive
/// the debounce window: the placement's own `Moved` event schedules a
/// persist that fires `PERSIST_DEBOUNCE_MS` later and must still observe
/// the suppression at fire time.
const SUPPRESS_WINDOW_MS: u64 = PERSIST_DEBOUNCE_MS + 1000;

/// The most recent persisted pair seen in a `bubble_config` payload.
static PERSISTED_POS: Mutex<Option<(i32, i32)>> = Mutex::new(None);

/// Wall-clock instant until which persistence is suppressed.
static SUPPRESS_UNTIL: Mutex<Option<Instant>> = Mutex::new(None);

/// A drag move queued for the debounced persist: the coordinates plus
/// the instant they were scheduled, so the debounce task can sleep
/// until exactly `PERSIST_DEBOUNCE_MS` after the LAST move.
struct PendingMove {
    pos: (i32, i32),
    scheduled_at: Instant,
}

/// The newest drag move awaiting persistence (latest wins).
static PENDING_MOVE: Mutex<Option<PendingMove>> = Mutex::new(None);

/// Wakeup signal for the long-lived debounce task. `schedule_persist`
/// stores the move, then calls `notify_one`; the task parks on
/// `notified()` between drags. `notify_one` stores a permit when nobody
/// is parked, so a move landing while the task is mid-debounce is
/// never lost.
static PERSIST_NOTIFY: OnceLock<Notify> = OnceLock::new();

/// Ensures the debounce task is spawned exactly once per process (on
/// the first drag) instead of once per `WindowEvent::Moved` (~60/sec
/// during a drag).
static DEBOUNCE_TASK_SPAWNED: OnceLock<()> = OnceLock::new();

/// Extract the persisted pair from a decoded `bubble_config` payload.
/// Both keys must be present as finite numbers within `i32` range for
/// the cache to be set; anything else (nulls from an edge-toggle reset,
/// missing keys, junk) clears it. Returns the parsed pair so callers can
/// log what was applied.
fn parse_persisted_pos(data: &serde_json::Value) -> Option<(i32, i32)> {
    let obj = data.as_object()?;
    let x = obj.get("bubble_x")?;
    let y = obj.get("bubble_y")?;
    let (x, y) = match (x.as_f64(), y.as_f64()) {
        (Some(x), Some(y)) if x.is_finite() && y.is_finite() => (x, y),
        _ => return None,
    };
    // Coordinates outside i32 are junk (the server allowlist caps them
    // at ±100000); treat like any other invalid payload.
    let xi = i32::try_from(x as i64).ok()?;
    let yi = i32::try_from(y as i64).ok()?;
    Some((xi, yi))
}

/// Update the cache from a forwarded `bubble_config` payload. Called by
/// the WS reader task for every `bubble_config` frame.
pub(crate) fn update_persisted_pos_from_config(data: &serde_json::Value) {
    let parsed = parse_persisted_pos(data);
    if let Ok(mut slot) = PERSISTED_POS.lock() {
        *slot = parsed;
    }
    if let Some((x, y)) = parsed {
        log::info!("[BUBBLE] durable position updated from config: ({x}, {y})");
    }
}

/// Read the current persisted pair (if any).
pub(crate) fn persisted_pos() -> Option<(i32, i32)> {
    PERSISTED_POS.lock().ok()?.to_owned()
}

/// Loose sanity bound for the monitor-API-failure fallback. MUST stay
/// in lockstep with the server's `bubble_x`/`bubble_y` allowlist bounds
/// (`IPC_CONFIG_ALLOWLIST` in
/// `voice_typer/server/config_validators/allowlist.py`, pinned by
/// `tests/tauri/test_config_script_drift.py::test_persisted_position_
/// bound_matches_server_allowlist`) — if the server widens the range,
/// update this constant in the same change.
const PERSISTED_COORDINATE_LIMIT: i32 = 100_000;

/// True when `(x, y)` lies inside at least one attached monitor's work
/// area (physical pixels) — mirrors Electron's `isPositionOnAnyDisplay`.
/// Best-effort: if the monitor list can't be read, fall back to a loose
/// sanity range so a transient monitor-API failure doesn't strand the
/// restore (the position came from the validated server config).
fn position_on_any_monitor(app: &tauri::AppHandle, x: i32, y: i32) -> bool {
    if let Ok(monitors) = app.available_monitors() {
        for m in monitors {
            let wa = m.work_area();
            // Same predicate as the placement path (`math.rs::RectPx::
            // contains`) so restore and set-position can never disagree
            // about what counts as on-screen.
            if super::math::RectPx::new(wa.position.x, wa.position.y, wa.size.width, wa.size.height)
                .contains(x, y)
            {
                return true;
            }
        }
        return false;
    }
    (-PERSISTED_COORDINATE_LIMIT..=PERSISTED_COORDINATE_LIMIT).contains(&x)
        && (-PERSISTED_COORDINATE_LIMIT..=PERSISTED_COORDINATE_LIMIT).contains(&y)
}

/// Restore decision for `bubble_show`: the cached pair when it exists AND
/// lies on an attached monitor, else `None` (caller keeps default
/// centering).
pub(crate) fn restore_position(app: &tauri::AppHandle) -> Option<(i32, i32)> {
    let pos = persisted_pos()?;
    if position_on_any_monitor(app, pos.0, pos.1) {
        Some(pos)
    } else {
        log::info!(
            "[BUBBLE] durable position {:?} is off-screen — falling back to centering",
            pos
        );
        None
    }
}

/// Arm the suppression window used by programmatic placements
/// (`bubble_set_position`, show-time restores). Any drag move still
/// queued for the debounced persist fires inside this window (the
/// window strictly outlives the debounce window) and is skipped at
/// fire time — same net effect as the old generation invalidation.
pub(crate) fn suppress_persist_for_window() {
    if let Ok(mut slot) = SUPPRESS_UNTIL.lock() {
        *slot = Some(Instant::now() + Duration::from_millis(SUPPRESS_WINDOW_MS));
    }
}

fn currently_suppressed() -> bool {
    match SUPPRESS_UNTIL.lock() {
        Ok(slot) => slot.is_some_and(|until| Instant::now() < until),
        Err(_) => false,
    }
}

/// Record a drag move as the pending latest-wins candidate.
fn store_pending_move(x: i32, y: i32) {
    if let Ok(mut slot) = PENDING_MOVE.lock() {
        *slot = Some(PendingMove {
            pos: (x, y),
            scheduled_at: Instant::now(),
        });
    }
}

/// Schedule the debounced persist of a dragged bubble position. Called
/// from the event-loop's `WindowEvent::Moved` branch for the bubble
/// window — must never block the event loop, hence fire-and-forget all
/// the way down: record the move, then wake the ONE long-lived debounce
/// task (spawned on the first drag, parked on `PERSIST_NOTIFY` in
/// between). No per-event task spawn: at ~60 `Moved` events/sec during
/// a drag this used to spawn a fresh sleeper per event.
///
/// The WS frame is sent via `dispatch_fire_and_forget` (id 0, no pending
/// entry): a dropped or ignored write costs at most one stale restore,
/// never an error surfaced to the user.
pub(crate) fn schedule_persist(state: &std::sync::Arc<SidecarState>, x: i32, y: i32) {
    // Store BEFORE notifying: the task always reads the latest stored
    // pair, so this order can never lose a move.
    store_pending_move(x, y);
    if DEBOUNCE_TASK_SPAWNED.set(()).is_ok() {
        tauri::async_runtime::spawn(debounce_persist_loop(state.to_owned()));
    }
    PERSIST_NOTIFY.get_or_init(Notify::new).notify_one();
}

/// Park until a queued move has been quiet for the full debounce
/// window, then hand it back — a latest-wins trailing debounce whose
/// fire time is `PERSIST_DEBOUNCE_MS` after the LAST `schedule_persist`
/// call (identical timing to the previous per-event sleeper). `None`
/// means the wakeup had nothing queued (a surplus notify permit).
async fn wait_for_quiesced_move() -> Option<(i32, i32)> {
    enum Step {
        /// Quiet window elapsed — fire this move.
        Fire((i32, i32)),
        /// Move still inside its window — sleep this long, then re-check.
        Wait(Duration),
        /// Nothing queued (surplus wakeup permit).
        Empty,
    }

    let window = Duration::from_millis(PERSIST_DEBOUNCE_MS);
    PERSIST_NOTIFY.get_or_init(Notify::new).notified().await;
    loop {
        // Atomically decide under one lock: fire the queued move, or
        // sleep out the remainder of its quiet window. The lock guard
        // is confined to this block — never held across the sleep.
        let step = match PENDING_MOVE.lock() {
            Ok(mut slot) => match slot.take() {
                None => Step::Empty,
                Some(p) => {
                    let remaining = window.saturating_sub(p.scheduled_at.elapsed());
                    if remaining.is_zero() {
                        Step::Fire(p.pos)
                    } else {
                        // A move landed inside the window — put it back
                        // and re-arm (a newer schedule_persist may have
                        // replaced it by the time we wake).
                        *slot = Some(p);
                        Step::Wait(remaining)
                    }
                }
            },
            Err(_) => Step::Empty,
        };
        match step {
            Step::Fire(pos) => return Some(pos),
            Step::Wait(remaining) => tokio::time::sleep(remaining).await,
            Step::Empty => return None,
        }
    }
}

/// The long-lived debounce task: parks on `PERSIST_NOTIFY` between
/// drags and sends exactly one `set_config` frame per quiescence
/// window, carrying the newest queued move. Replaces the previous
/// spawn-per-`Moved`-event sleeper.
async fn debounce_persist_loop(state: std::sync::Arc<SidecarState>) {
    loop {
        let Some((x, y)) = wait_for_quiesced_move().await else {
            continue;
        };
        if currently_suppressed() {
            log::debug!("[BUBBLE] persist suppressed — skipping programmatic move");
            continue;
        }
        match dispatch_fire_and_forget(
            &state,
            "set_config",
            Some(json!({"bubble_x": x, "bubble_y": y})),
        ) {
            Ok(()) => log::info!("[BUBBLE] persisted durable position ({x}, {y})"),
            Err(e) => log::warn!("[BUBBLE] persisting durable position failed: {e}"),
        }
    }
}

#[cfg(test)]
#[path = "persisted_position_tests.rs"]
mod persisted_position_tests;
