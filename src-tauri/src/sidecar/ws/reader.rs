//! WS reader task (ADR-0020 §1 + §7 + §9).
//!
//! Extracted from the parent `ws.rs` module-split pipeline. Holds:
//! - `spawn_reader_task` — parses inbound WS frames, fulfills pending
//!   dispatch requests by id, and fans out server-initiated events
//!   (`bubble_level` typed-only coalesced fast path + the generic
//!   specific-event / `python-event` dual emit), wrapped in
//!   `AssertUnwindSafe(...).catch_unwind()` with the generation-gated
//!   cleanup block.
//!
//! Visibility contract:
//! - `spawn_reader_task` is `pub(super)` — visible to the parent
//!   `ws` module (single call site in `reconnect_ws`), mirroring
//!   `heartbeat::spawn_heartbeat_task`.
//! - Shared helpers stay in `ws.rs` (`WsStream` alias,
//!   `truncate_frame_text`, `drain_pending_with_disconnect_error`)
//!   and are reached via `super::`; the event gates/envelope live in
//!   the sibling `event_protocol` submodule and the respawn trigger
//!   in the sibling `respawn_scheduler` submodule.

use crate::sidecar::bubble_coalesce::bubble_coalesce_should_emit;
use crate::state::lock as mutex_lock;
use crate::state::SidecarState;
use crate::util::BUBBLE_LEVEL_COALESCE_HZ;
use futures_util::{stream::SplitStream, FutureExt, StreamExt};
use serde_json::{json, Value};
use std::panic::AssertUnwindSafe;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::Instant;
use tauri::Emitter;
use tokio_tungstenite::tungstenite::Message;

use super::respawn_scheduler::trigger_respawn_off_thread;
use super::{
    drain_pending_with_disconnect_error, is_allowed_event_type, is_high_rate_event_type,
    python_event_envelope, translate_event_name, truncate_frame_text, WsStream,
};

/// (was inline in `reconnect_ws`): spawn the WS reader task.
///
/// Parses incoming frames, fulfills pending dispatch requests by id,
/// emits Tauri events for server-initiated events. The body is wrapped
/// in `AssertUnwindSafe(...).catch_unwind()` so a panic
/// inside `read.next()` / `serde_json::from_str` / `app.emit()` /
/// `bubble_coalesce_should_emit()` doesn't tear down the task without
/// running cleanup. Without this wrapper, a panic would propagate to
/// the tokio runtime, which logs the panic and drops the task —
/// leaving `ws_tx` set (new dispatch calls would queue onto a dead
/// channel forever) and pending dispatch requests hanging until their
/// 120s timeout. With the wrapper, the panic is caught, logged at
/// ERROR, and the cleanup block below runs identically to the
/// normal-exit path (drain pending, clear ws_tx, emit
/// `supervisor_relaunching`, spawn supervisor respawn).
pub(super) fn spawn_reader_task(
    app: tauri::AppHandle,
    state: Arc<SidecarState>,
    mut read: SplitStream<WsStream>,
    // generation captured at reconnect time so the cleanup block
    // can skip clearing `ws_tx` if a newer reconnect has already stored
    // its own sender (race — see `SidecarState::ws_generation`).
    my_generation: u64,
) {
    let app_for_reader = app.clone();
    let state_for_reader = state.clone();
    // clone handles for the cleanup block, which runs OUTSIDE
    // the `catch_unwind` wrapper so it runs even if the reader body
    // panics. The originals are moved INTO the `AssertUnwindSafe`
    // body and consumed by the inner async block.
    let app_for_cleanup = app_for_reader.clone();
    let state_for_cleanup = state_for_reader.clone();
    tokio::spawn(async move {
        let result = AssertUnwindSafe(async move {
            let mut last_bubble_level: Option<Instant> = None;
            // `last_bubble_payload` was removed: it was always overwritten
            // before being read (the assignment on each `bubble_level`
            // frame precedes the `take()` inside the coalesce-emit block,
            // so the Option only ever held the current frame's payload).
            // The current `payload` local is used directly inside the
            // coalesce-emit block — same behavior, no per-frame Option
            // allocation, no `#[allow(unused_assignments)]` needed.
            // per-task counters for the flood-prone warning
            // sites (invalid JSON + dropped unknown events + non-numeric
            // id fields). A misbehaving sidecar (or an attacker who has
            // compromised it) could otherwise log-spam at the ~60 Hz
            // frame rate. Log the first occurrence and every 100th
            // thereafter; the counter is also emitted so operators can
            // see the true flood volume.
            let mut invalid_json_count: u32 = 0;
            let mut unknown_event_count: u32 = 0;
            let mut non_numeric_id_count: u32 = 0;
            while let Some(msg) = read.next().await {
                match msg {
                    Ok(Message::Text(text)) => {
                        let v: Value = match serde_json::from_str(&text) {
                            Ok(v) => v,
                            Err(_) => {
                                invalid_json_count = invalid_json_count.saturating_add(1);
                                if invalid_json_count == 1 || invalid_json_count % 100 == 0 {
                                    // HU-31: the malformed frame may still
                                    // contain partial transcription text
                                    // (PII) — log a bounded prefix, never
                                    // the full frame.
                                    log::warn!(
                                        "[WS-READER] invalid JSON frame (count={}): {}",
                                        invalid_json_count,
                                        truncate_frame_text(&text)
                                    );
                                }
                                continue;
                            }
                        };
                        // If the frame has an `id`, it's a dispatch
                        // response — fulfill the pending oneshot.
                        //
                        // take the sender out of the map under the
                        // lock, then send OUTSIDE the lock. `oneshot::send`
                        // is non-blocking (returns Err immediately if the
                        // receiver was already dropped), but holding the
                        // AsyncMutex across the send is an anti-pattern —
                        // a concurrent dispatch path's `pending.insert(...)`
                        // (or a concurrent reader-exit drain) would be
                        // stalled behind the send. Bounding the lock hold
                        // time to the HashMap lookup keeps the dispatch
                        // path's `lock().await` contention minimal.
                        if let Some(id) = v.get("id").and_then(|i| i.as_u64()) {
                            // DEBUG wire-trace: pairs with the Python-side
                            // "[SIDECAR-WS] RX/TX response" lines so frame
                            // loss can be bisected end-to-end.
                            log::debug!(
                                "[WS-READER] RX response id={} type={}",
                                id,
                                v.get("type").and_then(|t| t.as_str()).unwrap_or("?")
                            );
                            let tx_opt = {
                                let mut pending = state_for_reader.pending.lock().await;
                                pending.remove(&id)
                            };
                            if let Some(tx) = tx_opt {
                                let _ = tx.send(v);
                            } else {
                                log::debug!(
                                    "[WS-READER] RX response id={} had NO pending entry",
                                    id
                                );
                            }
                            continue;
                        }
                        // T3-06: a frame that HAS an `id` field but where
                        // `as_u64()` returns None (e.g. the field is a
                        // string, float, bool, array, or object) is
                        // malformed — previously it fell through to the
                        // server-event emit path below, where it would be
                        // emitted to the renderer as a bogus event with
                        // `type: "unknown"`. Log + skip instead so we
                        // neither fulfill a dispatch that never matched a
                        // pending id nor emit garbage to the UI.
                        else if v.get("id").is_some() {
                            non_numeric_id_count = non_numeric_id_count.saturating_add(1);
                            if non_numeric_id_count == 1 || non_numeric_id_count % 100 == 0 {
                                // HU-31: same bounded-logging contract as
                                // the invalid-JSON site — the frame may
                                // carry transcription text, so never log
                                // it verbatim.
                                log::warn!(
                                    "[WS-READER] frame has non-numeric id field, ignoring (count={}): {}",
                                    non_numeric_id_count,
                                    truncate_frame_text(&text)
                                );
                            }
                            continue;
                        }
                        // Otherwise it's a server-initiated event
                        // (channel 2). Emit it as a Tauri event.
                        let event_type =
                            v.get("type").and_then(|t| t.as_str()).unwrap_or("unknown");
                        let payload = v.get("data").cloned().unwrap_or(json!({}));

                        // drop unknown event types BEFORE
                        // emitting (defense-in-depth against a
                        // compromised sidecar process injecting
                        // arbitrary event names that the renderer's
                        // `usePythonEvent(type, ...)` listeners might
                        // be tricked into handling). The allowlist
                        // `ALLOWED_EVENT_TYPES` is defined at the top
                        // of this file and covers all event types the
                        // Python sidecar is known to publish today.
                        if !is_allowed_event_type(event_type) {
                            unknown_event_count = unknown_event_count.saturating_add(1);
                            if unknown_event_count == 1 || unknown_event_count % 100 == 0 {
                                log::warn!(
                                    "[WS-READER] dropping unknown event type (count={}): {}",
                                    unknown_event_count,
                                    event_type
                                );
                            }
                            continue;
                        }

                        // ADR-0020 §9: coalesce bubble_level from ~60 Hz
                        // to ≤30 Hz.
                        if event_type == "bubble_level" {
                            let now = Instant::now();
                            if bubble_coalesce_should_emit(
                                last_bubble_level,
                                now,
                                BUBBLE_LEVEL_COALESCE_HZ,
                            ) {
                                last_bubble_level = Some(now);
                                // ADR-0020 "Sidecar→UI Event Table" (channel 2):
                                // emit the typed event for direct
                                // listeners like the bubble window.
                                // Typed-only carve-out for `bubble_level`
                                // (ADR-0020 §9 + "Sidecar→UI Event Table"):
                                // this frame is emitted on the typed channel
                                // ONLY — NO generic `python-event` duplicate.
                                // Its bubble-window consumer listens typed
                                // (`onLevel` → tauri.event.listen("bubble_level"));
                                // the MAIN renderer's live recording indicator
                                // deliberately does NOT ride this 30 Hz channel —
                                // it consumes the server's separate ≤8 Hz
                                // `recording_level` event on the generic
                                // envelope instead (see ALLOWED_EVENT_TYPES),
                                // so this carve-out's PERF rationale holds
                                // while both windows stay fed.
                                // moved (not cloned) into the emit — this
                                // branch `continue`s right after, so the
                                // payload has no further readers and the
                                // clone was a per-frame allocation on the
                                // highest-rate host path.
                                let _ = app_for_reader.emit("bubble_level", payload);
                            }
                            continue;
                        }

                        // extracted the event-name translation
                        // into `translate_event_name` so it can be unit-
                        // tested without a Tauri runtime, and so additional
                        // bubble-related event renames can be added in one
                        // place (the Python sidecar still publishes some
                        // events under snake_case names that the renderer
                        // expects as `bubble:*` kebab-case).
                        let emit_name = translate_event_name(event_type);

                        // Durable bubble-position transport: the sidecar's
                        // `bubble_config` push carries the persisted
                        // `bubble_x` / `bubble_y` pair (both, or nulls after
                        // a Settings edge-toggle reset). Cache it BEFORE the
                        // emit so a `bubble_show` arriving later restores
                        // the freshest position.
                        if event_type == "bubble_config" {
                            crate::commands::bubble::update_persisted_pos_from_config(&payload);
                        }

                        // ADR-0020 "Sidecar→UI Event Table" (channel 2):
                        // DEFAULT delivery = the specific event (for direct
                        // listeners) AND the generic `python-event` envelope
                        // (for the usePython hook's onEvent catch-all,
                        // matching the Electron path's
                        // ipcRenderer.on("python-event")). The ONLY exception
                        // is `bubble_level`, handled above by its typed-only
                        // fast path; the `is_high_rate_event_type` guard stays
                        // here as a second-line gate so a future high-rate
                        // addition can never silently reintroduce the
                        // per-frame envelope. `mic_level` deliberately takes
                        // THIS dual branch: its sole consumer
                        // (useMicrophoneLevelMonitor) subscribes via
                        // `usePythonEvent("mic_level")` → api.onEvent → the
                        // generic envelope, not a typed listener.
                        if is_high_rate_event_type(event_type) {
                            // Typed-only branch: nothing reads `payload`
                            // after this emit, so MOVE it instead of
                            // cloning (≤30 Hz bubble_level-class frames).
                            let _ = app_for_reader.emit(emit_name, payload);
                        } else {
                            let _ = app_for_reader.emit(emit_name, payload.clone());
                            let _ = app_for_reader
                                .emit("python-event", python_event_envelope(emit_name, payload));
                        }

                        // the legacy `electron_notification` →
                        // `notification` alias block was REMOVED. The
                        // Python sidecar now publishes `notification`
                        // directly (and `electron_notification` is no
                        // longer in `ALLOWED_EVENT_TYPES`, so legacy
                        // frames are dropped earlier with a `[WS-READER]
                        // dropping unknown event type:` log line).
                    }
                    Ok(Message::Close(_)) => {
                        log::info!("[WS-READER] sidecar closed the WS");
                        break;
                    }
                    Ok(Message::Binary(_)) => {
                        // WIRE CONTRACT: sidecar→host JSON travels as
                        // UTF-8 TEXT frames only (see
                        // sidecar_ws.py::_safe_send). A binary frame here
                        // means a sender is violating the contract — log
                        // it loudly instead of silently swallowing it
                        // (pre-fix, binary dispatch responses were
                        // dropped HERE, so every renderer command timed
                        // out while heartbeat acks kept flowing).
                        log::warn!(
                            "[WS-READER] ignoring BINARY frame — wire contract \
                             is UTF-8 TEXT frames (AGENTS.md C-WS-2)"
                        );
                    }
                    Ok(_) => {} // ping/pong/control — ignore
                    Err(e) => {
                        log::warn!("[WS-READER] error: {}", e);
                        break;
                    }
                }
            }
            // log the silent stream-end explicitly. `read.next()`
            // returning `None` (vs an `Err`) means the WS stream ended
            // cleanly with no error frame — without this log line the
            // transition from "reader active" to "reader cleanup running"
            // was invisible in diagnostics. This line also fires on the
            // `break` paths above (Close frame, Err), but those arms log
            // their own specific reason first, so this serves as a
            // confirming "loop has exited" marker in all cases.
            log::info!("[WS-READER] stream ended (read.next() returned None)");
        })
        .catch_unwind()
        .await;

        if let Err(_panic_payload) = &result {
            log::error!(
                "[WS-READER] reader task panicked during body — running \
                 cleanup (drain pending, clear ws_tx, emit supervisor_relaunching, \
                 trigger supervisor respawn)"
            );
        }

        // WS reader exited (normally or via caught panic) — drain
        // pending dispatch requests + clear ws_tx so new dispatch
        // calls fail fast instead of queueing onto a dead channel
        // (findings 1 + 3). Then trigger supervisor respawn (unless we're
        // shutting down). This cleanup block runs UNCONDITIONALLY on
        // reader exit — even if the body panicked — because it uses the
        // cloned `state_for_cleanup` / `app_for_cleanup` handles (not
        // the originals, which were moved into the `AssertUnwindSafe`
        // body and may have been partially consumed before the panic).
        // The THREE side effects inside (ws_tx clear, drain pending,
        // respawn trigger) are individually gated on the generation
        // check below — see the next comment block for the race
        // rationale.
        //
        // only clear `ws_tx` / drain pending / trigger respawn if the
        // current generation matches `my_generation`. The race window is:
        // supervisor kills old sidecar → old reader's `read.next()`
        // returns None → meanwhile `reconnect_ws` runs and stores a
        // NEW `ws_tx` (bumping the generation) → old reader's cleanup
        // runs `*state.ws_tx = None` / drains `pending` / triggers a
        // respawn. Without the generation guard the old reader's cleanup
        // wipes the NEW connection's pending dispatches and arms a
        // spurious supervisor respawn on top of the new reconnect's
        // own recovery. The generation check makes the old reader's
        // cleanup a no-op across ALL three side effects — the new
        // reconnect owns recovery and handles its own drain/respawn if
        // it later fails.
        let current_generation = state_for_cleanup.ws_generation.load(Ordering::SeqCst);
        if current_generation == my_generation {
            {
                // Clear ws_tx first so new dispatch calls return
                // "sidecar not connected" immediately.
                // poison-safe lock helper.
                let mut ws_tx_guard = mutex_lock(&state_for_cleanup.ws_tx);
                *ws_tx_guard = None;
            }
            {
                // drain pending requests via the shared
                // `drain_pending_with_disconnect_error` helper (collect out
                // of the lock first, then send outside the lock). Reject
                // each with a `sidecar_disconnected` error so callers don't
                // wait the full 120s timeout.
                let count = drain_pending_with_disconnect_error(&state_for_cleanup).await;
                if count > 0 {
                    log::warn!("[WS-READER] drained {} pending dispatch requests", count);
                }
            }
            if !state_for_cleanup.shutting_down.load(Ordering::SeqCst) {
                // (ADR-0020 §10): emit `supervisor_relaunching` IMMEDIATELY
                // at disconnect start so the UI can show a "reconnecting…"
                // banner before the backoff schedule runs. The eventual
                // `supervisor_reconnected` (on success) or second `supervisor_relaunching`
                // (on exhaustion) supersedes this event.
                let _ = app_for_cleanup
                    .emit("supervisor_relaunching", json!({"reason": "disconnected"}));
                log::warn!("[WS-READER] unexpected close — triggering supervisor");
                // spawn supervisor respawn on a separate thread via the
                // shared `trigger_respawn_off_thread` helper. The thread
                // + `block_on` bridge is required because `respawn`
                // awaits `reconnect_ws`, whose future is `!Send`
                // (tokio-tungstenite holds a `!Send` across an await), and
                // `tokio::spawn` requires `Send` futures.
                // documents the failed attempt to use a direct
                // `tokio::spawn` here. See the helper's doc comment for
                // the full rationale.
                // pass `Some(my_generation)` — same asynchronous-landing
                // stale-request rationale as the writer cleanup site.
                trigger_respawn_off_thread(
                    app_for_cleanup.clone(),
                    state_for_cleanup.clone(),
                    Some(my_generation),
                );
            }
        } else {
            log::info!(
                "[WS-READER] cleanup skipping drain + respawn trigger — generation mismatch \
                 (mine={}, current={}); a newer reconnect owns recovery",
                my_generation,
                current_generation
            );
        }
    });
}
