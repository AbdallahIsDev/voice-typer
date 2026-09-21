//! WS reader task (ADR-0020 §1 + §7 + §9).
//! Parses inbound frames, fulfills pending dispatches by id, fans out
//! server-initiated events. Catch_unwind + generation-gated cleanup.
//! NOTE: see docs/code-notes/tauri-host.md#ws-reader-cleanup

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

/// Spawn the WS reader task. Body is catch_unwind-wrapped so a panic still
/// runs cleanup (drain pending, clear ws_tx, trigger respawn).
/// Cleanup is generation-gated (C-WS-3): a stale reader must not wipe a
/// newer reconnect's pending map or arm a spurious respawn.
pub(super) fn spawn_reader_task(
    app: tauri::AppHandle,
    state: Arc<SidecarState>,
    mut read: SplitStream<WsStream>,
    // Generation captured at reconnect; cleanup no-ops if a newer one won.
    my_generation: u64,
) {
    let app_for_reader = app.clone();
    let state_for_reader = state.clone();
    // Clones for the cleanup block that runs OUTSIDE catch_unwind.
    let app_for_cleanup = app_for_reader.clone();
    let state_for_cleanup = state_for_reader.clone();
    tokio::spawn(async move {
        let result = AssertUnwindSafe(async move {
            let mut last_bubble_level: Option<Instant> = None;
            // Per-task flood counters: log first + every 100th only
            // (a misbehaving sidecar can spam at ~60 Hz).
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
                                    // Frame may carry PII — bounded prefix only.
                                    log::warn!(
                                        "[WS-READER] invalid JSON frame (count={}): {}",
                                        invalid_json_count,
                                        truncate_frame_text(&text)
                                    );
                                }
                                continue;
                            }
                        };
                        // Numeric `id` → dispatch response; fulfill pending oneshot.
                        // Remove under lock, send outside the lock (C-TOKIO-1 adjacent).
                        if let Some(id) = v.get("id").and_then(|i| i.as_u64()) {
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
                        // Non-numeric `id` is malformed: skip (do not emit as event).
                        else if v.get("id").is_some() {
                            non_numeric_id_count = non_numeric_id_count.saturating_add(1);
                            if non_numeric_id_count == 1 || non_numeric_id_count % 100 == 0 {
                                // Bounded log, never full frame.
                                log::warn!(
                                    "[WS-READER] frame has non-numeric id field, ignoring (count={}): {}",
                                    non_numeric_id_count,
                                    truncate_frame_text(&text)
                                );
                            }
                            continue;
                        }
                        // Server-initiated event.
                        let event_type =
                            v.get("type").and_then(|t| t.as_str()).unwrap_or("unknown");
                        let payload = v.get("data").cloned().unwrap_or(json!({}));

                        // Defense-in-depth: drop unknown types (compromised sidecar
                        // must not inject arbitrary renderer events). Allowlist is
                        // `ALLOWED_EVENT_TYPES` in event_protocol (pinned by tests).
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

                        // bubble_level: typed-only coalesced fast path (≤30 Hz).
                        if event_type == "bubble_level" {
                            let now = Instant::now();
                            if bubble_coalesce_should_emit(
                                last_bubble_level,
                                now,
                                BUBBLE_LEVEL_COALESCE_HZ,
                            ) {
                                last_bubble_level = Some(now);
                                // Typed-only: no generic python-event duplicate (PERF).
                                let _ = app_for_reader.emit("bubble_level", payload);
                            }
                            continue;
                        }

                        // snake_case → bubble:* kebab-case renames live in one place.
                        let emit_name = translate_event_name(event_type);

                        // Cache bubble_config before emit so later bubble_show
                        // restores the freshest persisted position.
                        if event_type == "bubble_config" {
                            crate::commands::bubble::update_persisted_pos_from_config(&payload);
                        }

                        // Recording start: show the (hidden) bubble OS window.
                        if crate::commands::bubble::wants_bubble_show(event_type) {
                            if let Err(e) = crate::commands::bubble::show_bubble_window(
                                &app_for_reader,
                            ) {
                                log::warn!("[WS-READER] bubble_show window show failed: {}", e);
                            }
                        }

                        // offline_pack_verified: stop-first then spawn ML worker.
                        if event_type == "offline_pack_verified" {
                            crate::sidecar::spawn::worker::on_pack_verified(
                                &app_for_reader,
                            );
                        }

                        // Default delivery: typed event + generic python-event envelope.
                        // Exception: bubble_level (above). mic_level takes THIS dual
                        // branch (consumer listens via usePythonEvent envelope).
                        if is_high_rate_event_type(event_type) {
                            let _ = app_for_reader.emit(emit_name, payload);
                        } else {
                            let _ = app_for_reader.emit(emit_name, payload.clone());
                            let _ = app_for_reader
                                .emit("python-event", python_event_envelope(emit_name, payload));
                        }
                    }
                    Ok(Message::Close(_)) => {
                        log::info!("[WS-READER] sidecar closed the WS");
                        break;
                    }
                    Ok(Message::Binary(_)) => {
                        // C-WS-2: sidecar→host MUST be TEXT frames. Binary = contract
                        // violation (historically dropped silently → command timeouts).
                        log::warn!(
                            "[WS-READER] ignoring BINARY frame: wire contract \
                             is UTF-8 TEXT frames (AGENTS.md C-WS-2)"
                        );
                    }
                    Ok(_) => {} // ping/pong/control: ignore
                    Err(e) => {
                        log::warn!("[WS-READER] error: {}", e);
                        break;
                    }
                }
            }
            // Confirms loop exit on the clean stream-end path.
            log::info!("[WS-READER] stream ended (read.next() returned None)");
        })
        .catch_unwind()
        .await;

        if let Err(_panic_payload) = &result {
            log::error!(
                "[WS-READER] reader task panicked during body: running \
                 cleanup (drain pending, clear ws_tx, emit supervisor_relaunching, \
                 trigger supervisor respawn)"
            );
        }

        // Unconditional cleanup (even after panic). Generation-gated:
        // C-WS-3 — a stale reader must not wipe a newer reconnect.
        let current_generation = state_for_cleanup.ws_generation.load(Ordering::SeqCst);
        if current_generation == my_generation {
            {
                // Clear ws_tx first so new dispatches fail fast.
                let mut ws_tx_guard = mutex_lock(&state_for_cleanup.ws_tx);
                *ws_tx_guard = None;
            }
            {
                // Reject in-flight dispatches immediately (don't wait 120s).
                let count = drain_pending_with_disconnect_error(&state_for_cleanup).await;
                if count > 0 {
                    log::warn!("[WS-READER] drained {} pending dispatch requests", count);
                }
            }
            if !state_for_cleanup.shutting_down.load(Ordering::SeqCst) {
                // UI banner immediately at disconnect start.
                let _ = app_for_cleanup
                    .emit("supervisor_relaunching", json!({"reason": "disconnected"}));
                log::warn!("[WS-READER] unexpected close, triggering supervisor");
                // C-WS-3: pass Some(my_generation); the supervisor's
                // std-thread bridge keeps respawns serialized.
                trigger_respawn_off_thread(
                    app_for_cleanup.clone(),
                    state_for_cleanup.clone(),
                    Some(my_generation),
                );
            }
        } else {
            log::info!(
                "[WS-READER] cleanup skipping drain + respawn trigger: generation mismatch \
                 (mine={}, current={}); a newer reconnect owns recovery",
                my_generation,
                current_generation
            );
        }
    });
}
