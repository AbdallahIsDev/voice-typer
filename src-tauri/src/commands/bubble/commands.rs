#![allow(clippy::unreachable)] // tauri command macro expansion emits `unreachable!()` fallbacks

//! Bubble window Tauri commands (ADR-0020 §9).
//!
//! The 9 `#[tauri::command]` functions exposed to the renderer live
//! here. Pure helpers (parsing, geometry math, rate limiting, window
//! hide) live in the sibling `parse`, `math`, `rate_limit`, and
//! `window` modules.

use serde_json::{json, Value};
use std::sync::Arc;
use tauri::{Emitter, Manager, PhysicalPosition};

use crate::error::VoiceTyperError;
use crate::state::SidecarState;

use super::math::{
    bubble_position_in_work_area, clamp_resize_height, clamp_resize_width, compute_move_by_new_pos,
    edge_margin_physical, rect_contains_point, round_f64_to_i32_saturating,
    round_f64_to_u32_saturating, RectPx,
};
use super::rate_limit::toggle_rate_limiter_allows;
use super::window::hide_bubble_window;

// Tauri commands: bubble window (ADR-0020 §9) ────────────

// ─── Bubble-window-origin guard for `bubble_signal_ready` ─────────────
//
// `bubble_signal_ready` is invoked by the bubble renderer's boot
// sequence (`bubble-main.tsx:38` → `window.bubble.signalReady()`) to
// signal that the bubble page is mounted and ready to receive
// `bubble_level` events. Only the bubble window has a legitimate
// reason to invoke it — this mirrors the Electron main process's
// `assertFromBubble` gate (`bubble-handlers.ts:249-254`, SEC-016). A
// compromised main renderer (or any other window) MUST NOT be able to
// spoof a readiness signal. (The other bubble control commands in
// this file are intentionally NOT window-gated — see each command's
// doc comment for the rationale.)
//
// The `bubble_emit_state` command that used to live here was
// removed as dead code. The bubble's `bubble:set-state` event
// is now emitted directly by the WS reader task in `sidecar/ws/reader.rs`:
// the Python sidecar publishes `bubble_set_state` over the WebSocket,
// the WS reader's `translate_event_name`
// (`sidecar/ws/event_protocol.rs`) translates it to
// `bubble:set-state`, and `app.emit("bubble:set-state", ...)` fans it
// out to the bubble window. No main-renderer relay is involved.
//
// The canonical `require_main_window` + `main_window_label_check`
// helpers now live in `commands/mod.rs` (single source of truth). The
// previous local duplicates (with their `Result<(), String>`-returning
// `main_window_label_check`) are deleted. The canonical
// `main_window_label_check` returns `bool` — see `commands/mod.rs` for
// the rationale. See `commands/mod.rs::require_main_window` for the
// envelope shape contract.

/// Show the bubble window (ADR-0020 §9).
///
/// **Window-origin policy:** this command is intentionally NOT
/// gated by [`crate::commands::require_main_window`] — the bubble renderer is permitted
/// to self-manipulate its own visibility. The bubble's auto-show-on-
/// hover handler in `Bubble.tsx` invokes this when the cursor enters
/// the hot zone, so requiring the call to originate from the main
/// window would break that UX. The command's effect is confined to
/// the bubble window itself.
#[tauri::command]
pub async fn bubble_show(app: tauri::AppHandle) -> Result<(), VoiceTyperError> {
    let window = app
        .get_webview_window("bubble")
        .ok_or(VoiceTyperError::Host("bubble window not found".into()))?;
    // Durable drag-position restore (mirrors Electron's show-time
    // placement): when the sidecar's config carries a persisted
    // `bubble_x` / `bubble_y` pair that still lies on an attached
    // monitor, place the window there before showing. Without a cached
    // pair (never dragged / edge-toggle reset) the window keeps its
    // last keyword-centered position — default behavior unchanged.
    //
    // The restore itself is a PROGRAMMATIC placement — suppress the
    // debounced persist around it so its own `Moved` event doesn't
    // rewrite the config with the coordinates just read from it.
    if let Some((x, y)) = crate::commands::bubble::restore_position(&app) {
        crate::commands::bubble::suppress_persist_for_window();
        let _ = window.set_position(PhysicalPosition::new(x, y));
    }
    window
        .show()
        .map_err(|e| VoiceTyperError::Host(e.to_string()))
}

/// Emit `bubble:ready` to signal that the bubble page is mounted and
/// ready to receive `bubble_level` events (ADR-0020 §9).
///
/// **Bubble-window gate:** this command is gated on the calling window
/// being the bubble window. It mirrors the Electron main process's
/// `assertFromBubble` gate (`bubble-handlers.ts:249-254`, SEC-016):
/// the bubble renderer is the only legitimate caller. The boot
/// sequence in `bubble-main.tsx:38` invokes `window.bubble.signalReady()`
/// once after the bubble React app mounts. A compromised main renderer
/// (or any other window) MUST NOT be able to spoof a readiness signal.
#[tauri::command]
pub async fn bubble_signal_ready(
    app: tauri::AppHandle,
    window: tauri::Window,
) -> Result<(), VoiceTyperError> {
    // Only the bubble window may signal bubble readiness (mirrors
    // Electron's assertFromBubble in bubble-handlers.ts). Returns the
    // canonical JSON error envelope so the renderer's reject path
    // handles it identically to a server-side rejection.
    crate::commands::require_bubble_window(&window)?;
    app.emit_to("bubble", "bubble:ready", ())
        .map_err(|e| VoiceTyperError::Host(e.to_string()))
}

/// Move the bubble window to `(x, y)` in physical pixels (ADR-0020
/// §9). The TS bridge calls this with the cursor position
/// (offset by a small delta) so the bubble appears under the cursor.
///
/// The renderer's `setPosition("top" | "bottom")` call
/// shape is the ONLY production call shape — `useConnection.ts:117`
/// (syncing the saved `bubble_position` config) and
/// `GeneralSettingsSection.tsx:151` (the bubble-position dropdown) both
/// pass one of `"top"` / `"bottom"`. The previous `(x: Value, y: Value)`
/// signature was a leaky abstraction: the TS bridge had to forward the
/// single keyword as BOTH `x` and `y` (since Tauri v2 arg deserialization
/// rejected a single `{ position }` payload for a 2-arg command), and
/// the Rust `parse_position` helper then re-derived centered-x from
/// either axis. The new `(position: String)` signature accepts the
/// keyword directly and resolves it to absolute PHYSICAL coordinates
/// server-side: centered horizontally within the CURSOR monitor's work
/// area, y = work-area top + margin for `"top"`, y = work-area bottom -
/// bubble_h - margin for `"bottom"` (mirroring Electron's
/// `centerOnActiveDisplay`; see [`resolve_cursor_monitor`] for the
/// monitor-resolution order and the sibling `math` helpers for the
/// geometry contracts).
///
/// Electron's in-session saved-position validation
/// (`isPositionOnAnyDisplay` / `savedBubblePos` in `positioning.ts`) is
/// a RENDERER-side concern on this host — the bubble renderer applies
/// its last-position by invoking this command with a keyword, so the
/// Rust side only owns keyword → work-area placement.
///
/// The previous numeric `(x: Value, y: Value)` path was dead in
/// production (no caller passed numeric coords) — its parsing logic is
/// preserved as the test-only `parse_position` helper in `parse.rs`
/// so the existing unit tests for the numeric / NaN / inf edge cases
/// continue to pin the contract for any future caller that reintroduces
/// numeric coordinates.
///
/// **Window-origin policy:** this command is intentionally NOT
/// gated by [`crate::commands::require_main_window`] — the bubble renderer is permitted
/// to self-manipulate its own window position. The main renderer's
/// `usePython.ts` calls this on hotkey fire (to position the bubble
/// under the cursor), but the bubble renderer also calls it during
/// initialization to apply a saved last-position. The command's effect
/// is confined to the bubble window itself, so a compromised bubble
/// can at worst move itself off-screen (an annoyance, not a security
/// boundary — the bubble is sandboxed per SEC-026).
/// Resolve the monitor the bubble should appear on: the display the
/// CURSOR is currently on (multi-monitor aware — mirrors Electron's
/// `getActiveDisplay()` in
/// `voice_typer/client/src/main/windows/bubble/positioning.ts:171-186`),
/// falling back to the primary monitor when the cursor's display can't
/// be determined.
///
/// Resolution order:
/// 1. `AppHandle::cursor_position()` → physical-pixel desktop coords,
///    then `AppHandle::monitor_from_point(x, y)` (both take f64
///    physical pixels — no conversion needed between them).
/// 2. Manual hit-test over `AppHandle::available_monitors()` full
///    bounds via [`super::math::rect_contains_point`] — covers
///    platforms/runtime versions where `monitor_from_point` misses
///    stacked or negative-origin secondary layouts (historical tao
///    macOS axis bug, fixed upstream in tao 0.18 / PR #711; kept as a
///    cheap belt-and-suspenders fallback). Full bounds, NOT work area:
///    a cursor hovering the taskbar strip still belongs to that
///    monitor, matching Electron's `getDisplayMatching`.
/// 3. `primary_monitor()` → `Err("no primary monitor available")` if
///    the OS reports none (same error string as before).
///
/// All coordinates stay PHYSICAL end-to-end: `cursor_position` is
/// physical, monitor bounds/work areas are physical, and the result is
/// applied with `PhysicalPosition::set_position` — the only unit
/// conversion in the whole path is the Electron-parity edge margin
/// ([`super::math::edge_margin_physical`], DIP → per-monitor physical).
fn resolve_cursor_monitor(app: &tauri::AppHandle) -> Result<tauri::window::Monitor, String> {
    if let Ok(cursor) = app.cursor_position() {
        match app.monitor_from_point(cursor.x, cursor.y) {
            Ok(Some(monitor)) => return Ok(monitor),
            Ok(None) | Err(_) => {
                // Fallback hit-test: round the f64 cursor to i32 with
                // the shared saturating helper (NaN/±inf defined).
                let cx = round_f64_to_i32_saturating(cursor.x);
                let cy = round_f64_to_i32_saturating(cursor.y);
                let hit = app.available_monitors().ok().and_then(|monitors| {
                    monitors.into_iter().find(|m| {
                        let rect = RectPx::new(
                            m.position().x,
                            m.position().y,
                            m.size().width,
                            m.size().height,
                        );
                        rect_contains_point(&rect, cx, cy)
                    })
                });
                if let Some(monitor) = hit {
                    return Ok(monitor);
                }
                log::warn!(
                    "[BUBBLE] cursor ({cx},{cy}) matched no monitor — falling back to primary"
                );
            }
        }
    }
    app.primary_monitor()
        .map_err(|e| e.to_string())?
        .ok_or_else(|| "no primary monitor available".to_string())
}

#[tauri::command]
pub async fn bubble_set_position(
    position: String,
    app: tauri::AppHandle,
) -> Result<(), VoiceTyperError> {
    let window = app
        .get_webview_window("bubble")
        .ok_or(VoiceTyperError::Host("bubble window not found".into()))?;
    let monitor = resolve_cursor_monitor(&app)?;
    // This is a PROGRAMMATIC placement (Settings edge toggle / connect-
    // time sync): arm the suppression window BEFORE the move so the
    // `Moved` events it emits are never persisted as a user drag. The
    // Python side clears the durable pair server-side on the same
    // toggle; our own centered coordinates must not overwrite that
    // reset.
    crate::commands::bubble::suppress_persist_for_window();
    // Work area = monitor bounds minus taskbar/dock strips, in PHYSICAL
    // pixels (`Monitor::work_area()` returns a `PhysicalRect<i32, u32>`
    // — verified against the vendored tauri-runtime source). Placing
    // within the work area keeps the bubble clear of the taskbar on the
    // "bottom" edge and of top-docked bars on the "top" edge, matching
    // Electron's use of `display.workArea`.
    let wa = monitor.work_area();
    let wa_rect = RectPx::new(wa.position.x, wa.position.y, wa.size.width, wa.size.height);
    let bubble_size = window.outer_size().map_err(|e| e.to_string())?;
    // `bubble_size.width` / `.height` are `u32`; the saturating
    // `i32::try_from(..).unwrap_or(i32::MAX)` conversion (project-adopted
    // pattern) can't silently wrap negative on absurd dimensions.
    let bubble_w = i32::try_from(bubble_size.width).unwrap_or(i32::MAX);
    let bubble_h = i32::try_from(bubble_size.height).unwrap_or(i32::MAX);
    // Electron expresses its edge offset in DIPs (`wa.y + 48`); scale
    // it to this monitor's physical pixels so the visual gap matches
    // across scale factors.
    let margin = edge_margin_physical(monitor.scale_factor());
    let (px, py) = bubble_position_in_work_area(&position, &wa_rect, bubble_w, bubble_h, margin)?;
    window
        .set_position(PhysicalPosition::new(px, py))
        .map_err(|e| VoiceTyperError::Host(e.to_string()))
}

/// Toggle the bubble window's draggable state (ADR-0020 §9).
///
/// Tauri v2 does NOT expose a direct `set_draggable` on `WebviewWindow`.
/// Instead, we emit a `bubble:draggable` event to the bubble window
/// with the bool payload; the bubble renderer listens for this event
/// and calls `start_dragging()` on mouse-down (or unbinds the
/// listener when `false`). This keeps the drag logic in the renderer
/// where it can be throttled to the animation frame.
///
/// **Window-origin policy:** this command is intentionally NOT
/// gated by [`crate::commands::require_main_window`] — the bubble renderer is permitted
/// to self-manipulate its own draggability. The main renderer's
/// `usePython.ts` toggles this on hotkey-down/up, but the bubble
/// renderer may also self-toggle in response to its own UI state
/// (e.g. disabling drag while the user is interacting with the mic
/// button so an accidental drag doesn't fire). The command's effect
/// is confined to the bubble window's drag listener.
#[tauri::command]
pub async fn bubble_set_draggable(
    draggable: bool,
    app: tauri::AppHandle,
) -> Result<(), VoiceTyperError> {
    app.emit_to("bubble", "bubble:draggable", draggable)
        .map_err(|e| VoiceTyperError::Host(e.to_string()))
}

/// Move the bubble window by `(dx, dy)` physical pixels relative to
/// its current `outer_position` (ADR-0020 §9). Returns the
/// new `{x, y}` so the TS bridge can cache it without a round-trip.
///
/// **Window-origin policy:** this command is intentionally NOT
/// gated by [`crate::commands::require_main_window`] — the bubble renderer is permitted
/// to self-manipulate its own window geometry. The drag handler in
/// `Bubble.tsx` invokes `bubble_move_by` on each mousemove while the
/// user drags the pill, so requiring the call to originate from the
/// main window would break drag entirely. The command's effect is
/// confined to the bubble window itself, so a compromised bubble can
/// at worst mess with its own position (an annoyance, not a security
/// boundary — the bubble is sandboxed per SEC-026).
///
/// **Overflow safety:** the prior `pos.x + dx` / `pos.y + dy`
/// arithmetic was plain `i32 + i32`, which silently wraps on overflow
/// (Rust's default release-mode behavior). A renderer that sends a
/// huge `dx` (e.g. `i32::MAX`) on top of a `pos.x` near `i32::MAX`
/// would wrap to a negative coordinate, jerking the bubble off-screen
/// with no diagnostic. The fix uses [`super::math::compute_move_by_new_pos`],
/// which `checked_add`s each axis and returns a descriptive error
/// naming the offending operands so the renderer can surface it.
///
/// **Perf:** the body runs inside `tauri::async_runtime::spawn_blocking`
/// so the two OS-IPC calls per mousemove (`outer_position` + `set_position`)
/// run on the cached blocking-thread pool instead of holding an async
/// worker thread. The bubble drag handler fires `bubble_move_by` on every
/// `mousemove` event (~60 Hz during an active drag), and each OS-IPC
/// round-trip can take 1-10ms under a busy compositor — without
/// `spawn_blocking`, a sustained drag could pin a Tauri async worker
/// thread for the duration of the drag, starving other futures
/// (sidecar WS reader, status poll, etc.). The blocking pool absorbs
/// the IPC latency without contending with the async runtime.
#[tauri::command]
pub async fn bubble_move_by(
    dx: f64,
    dy: f64,
    app: tauri::AppHandle,
) -> Result<Value, VoiceTyperError> {
    // Wrap the OS-IPC body in `spawn_blocking` so the async runtime's
    // worker pool is not held for the duration of `outer_position` +
    // `set_position` (two blocking OS-IPC syscalls per mousemove). The
    // closure captures `app` (cheaply clonable — it's `Arc`-backed) and
    // returns the same `Result<Value, String>` shape the synchronous
    // body returned, so the only outer change is the `JoinError`-shaped
    // fallback (which surfaces as a descriptive Rust error string).
    let join_result = tauri::async_runtime::spawn_blocking(move || -> Result<Value, String> {
        let window = app
            .get_webview_window("bubble")
            .ok_or("bubble window not found")?;
        let pos = window.outer_position().map_err(|e| e.to_string())?;
        // The TS bridge forwards renderer-supplied numbers (the
        // `moveBy(deltaX: number, deltaY: number)` signature is `(number,
        // number)`). Accept `f64` at the FFI boundary and round to `i32` with
        // a saturating cast so a NaN / ±inf / out-of-range delta is defined
        // behavior instead of the silent wrap / UB-adjacent saturation of
        // `as i32` on `f64` (see `round_f64_to_i32_saturating` doc for the
        // per-input behavior).
        let dx_i32 = round_f64_to_i32_saturating(dx);
        let dy_i32 = round_f64_to_i32_saturating(dy);
        // Use checked arithmetic so a renderer-supplied dx/dy that
        // would overflow i32::MAX surfaces a descriptive error instead of
        // silently wrapping the bubble to a wrapped-negative pixel.
        let (new_x, new_y) = compute_move_by_new_pos(pos.x, dx_i32, pos.y, dy_i32)?;
        window
            .set_position(PhysicalPosition::new(new_x, new_y))
            .map_err(|e| e.to_string())?;
        Ok(json!({"x": new_x, "y": new_y}))
    })
    .await;
    match join_result {
        Ok(inner) => Ok(inner?),
        Err(join_err) => Err(VoiceTyperError::Host(format!(
            "bubble_move_by blocking task failed: {join_err}"
        ))),
    }
}

// A Tauri command that emitted bubble-state events (previously declared
// here with a full docstring) was REMOVED as dead code. It was never
// registered in `main.rs::tauri::generate_handler![...]` (the
// registration was removed earlier with the comment that the command
// was "dead in production"), so the function was unreachable from the
// renderer — the `#[tauri::command]` macro generated a handler that
// no `invoke(...)` call from the renderer could ever reach. Keeping
// the dead fn + docstring was misleading (the docstring claimed "the
// legitimate caller is the MAIN renderer's `usePython.ts::
// onStatusChange` handler", but no such caller exists in the
// renderer code) and created a maintenance hazard (a future
// contributor might re-register it without understanding why the
// earlier removal happened).
//
// The `bubble:set-state` Tauri event itself is still emitted by the
// WS reader task in `sidecar::ws` (which forwards sidecar
// `status_change` events directly to the bubble window) — that path
// does NOT go through a Tauri command, so deleting this command
// doesn't affect the bubble's state-update UX. The deleted command
// would have been a SECOND path (renderer → invoke → the deleted
// command → emit_to) that was never wired up.

/// Hide the bubble window and emit `bubble:hide` so the renderer can
/// run cleanup (e.g., stop the level animation) BEFORE the window becomes
/// invisible (ADR-0020 §9).
///
/// Previously this command (a) emitted `bubble:hide_complete` —
/// a name the renderer never listens for — and (b) hid the window FIRST,
/// so the renderer's cleanup ran AFTER the window was already torn down,
/// leaking the requestAnimationFrame loop for ~1 frame. The fix renames
/// the event to `bubble:hide` AND reorders the emit to fire BEFORE `.hide()`.
///
/// **SEC-016 gate:** this command IS now gated by the inverse check
/// — `require_bubble_window(&window)?` — so only the bubble window's
/// webview can invoke it. A compromised main renderer (or any other
/// non-bubble window) that sends `bubble:hidden` via the unrestricted
/// Tauri `invoke` channel would otherwise prematurely hide the bubble
/// overlay during its show/hide animation. The check mirrors the
/// renderer-side `assertFromBubble(event)` gate that
/// `bubble-window.ts:679` applies on the `bubble:hidden` IPC channel
/// (defense-in-depth — both gates must hold for the hide to take
/// effect). The `check_dispatch_window_label` helper does NOT apply
/// here (this command doesn't go through the `dispatch` path — it's a
/// dedicated `#[tauri::command]`), so a local `require_bubble_window`
/// helper is inlined below rather than reusing `commands::mod`.
#[tauri::command]
pub async fn bubble_hide_complete(
    app: tauri::AppHandle,
    window: tauri::Window,
) -> Result<(), VoiceTyperError> {
    // Only the bubble window may finalize its own hide. A
    // compromised main renderer invoking `bubble_hide_complete` would
    // otherwise skip the show/hide animation cycle and force the
    // overlay invisible mid-animation.
    crate::commands::require_bubble_window(&window)?;
    // Emit FIRST so the renderer's cleanup runs while the
    // window is still visible. The emit + hide sequence is shared with
    // `bubble_dismiss` via the `hide_bubble_window` helper so the two
    // commands have identical hide behavior (the distinction is purely
    // semantic — `bubble_hide_complete` is the renderer's
    // animation-complete signal; `bubble_dismiss` is the user's '×'
    // button affordance).
    hide_bubble_window(&app).map_err(VoiceTyperError::from)
}

/// Dismiss the bubble window from its own '×' button (ADR-0020 §9).
///
/// Mirror of [`bubble_hide_complete`] — emits `bubble:hide` so the
/// renderer can run cleanup BEFORE the window becomes invisible, then
/// hides the window unconditionally. The distinction from
/// `bubble_hide_complete` is purely semantic: `bubble_dismiss` is the
/// user-facing "close the bubble" affordance (the '×' button in
/// `always_visible` mode), while `bubble_hide_complete` is the
/// renderer's exit-animation-complete signal. Both route through the
/// same [`super::window::hide_bubble_window`] helper so the hide behavior is identical.
///
/// Mirrors the Electron `bubble:dismiss` IPC handler in
/// `voice_typer/client/src/main/ipc/bubble-handlers.ts:299-302` which
/// routes to `hideBubbleWindow()` — the same path used by every other
/// hide trigger (timeout fallback, set_config, etc.).
///
/// **SEC-016 gate:** gated by [`crate::commands::require_bubble_window`]
/// so only the bubble window's webview can dismiss itself. A compromised
/// main renderer invoking `bubble_dismiss` would otherwise be able to
/// prematurely hide the bubble overlay. The check mirrors the
/// renderer-side `assertFromBubble(event)` gate that
/// `bubble-handlers.ts:299-302` applies on the `bubble:dismiss` IPC
/// channel (defense-in-depth — both gates must hold for the dismiss to
/// take effect).
#[tauri::command]
pub async fn bubble_dismiss(
    app: tauri::AppHandle,
    window: tauri::Window,
) -> Result<(), VoiceTyperError> {
    // Only the bubble window may dismiss itself. A compromised
    // main renderer invoking `bubble_dismiss` would otherwise be able to
    // prematurely hide the bubble overlay (the dismiss button only shows
    // in `always_visible` mode, but the Rust gate is
    // mode-agnostic — defense-in-depth).
    crate::commands::require_bubble_window(&window)?;
    hide_bubble_window(&app).map_err(VoiceTyperError::from)
}

// Tauri commands: bubble window extensions ────────────────
//
// The Tauri bridge was missing 3 bubble-window methods that the
// Electron bubble preload (`voice_typer/client/src/preload/bubble.ts`)
// exposes — `resizeTo`, `onSetState`, `toggleDictation`. Without these,
// the bubble renderer's mic button (toggleDictation) is dead, the
// state label (onSetState) never updates, and the pill content has a
// transparent dead zone around it (resizeTo is never called to fit the
// window to the pill). These commands restore parity with the Electron
// preload surface so the same `Bubble.tsx` component works on both
// runtimes.

/// Resize the bubble window to exactly `(width, height)` physical
/// pixels (ADR-0020 §9). The TS bridge's `resizeTo(w, h)`
/// invokes this with the pill content's measured bounds so there is no
/// invisible dead zone around the bubble that would block clicks to the
/// windows underneath (the BrowserWindow is 240×80 initially; the pill
/// content is typically smaller).
///
/// Mirrors the Electron `bubble:resize` IPC handler in
/// `voice_typer/client/src/main/ipc/bubble-handlers.ts:183-197` which
/// calls `BrowserWindow.setSize(width, height)` after clamping to the
/// `MIN_BUBBLE_W`/`MAX_BUBBLE_W`/`MIN_BUBBLE_H`/`MAX_BUBBLE_H` bounds.
///
/// **Window-origin policy:** this command is intentionally NOT
/// gated by [`crate::commands::require_main_window`] — the bubble renderer is permitted
/// to self-manipulate its own window size. The bubble's content
/// measurement observer (`Bubble.tsx`'s `ResizeObserver`) invokes this
/// when the pill content changes (e.g. state label grows from
/// "listening" to "transcribing…"), so requiring the call to originate
/// from the main window would break the auto-fit behavior. The
/// command's effect is confined to the bubble window itself.
///
/// **f64 coercion + bound reconciliation:** the prior
/// signature was `(width: u32, height: u32)`. The TS bridge forwards
/// renderer-measured numbers (the `resizeTo(width: number, height:
/// number)` signature is `(number, number)`), so a non-integer
/// measurement (e.g. `240.7`) was silently truncated by Tauri's JSON
/// deserializer or rejected outright depending on the serde-u32 path.
/// The fix accepts `f64` at the FFI boundary and rounds to `u32` with
/// a saturating cast (see [`super::math::round_f64_to_u32_saturating`] for the
/// per-input behavior on NaN / negative / ±inf / out-of-range).
///
/// **Size bounds:** the prior Rust code capped both dimensions
/// to `BUBBLE_RESIZE_MAX_DIM` (7680 = 8K UHD) — well above any
/// legitimate pill content measurement but INCONSISTENT with
/// Electron's `MIN_BUBBLE_W=40` / `MAX_BUBBLE_W=400` /
/// `MIN_BUBBLE_H=24` / `MAX_BUBBLE_H=200` pill bounds in
/// `bubble-handlers.ts:45-48`. A Tauri-hosted bubble could be resized
/// to 1000×500 while an Electron-hosted one would clamp to 400×200 —
/// a cross-host UX divergence and a SEC-016 phishing-overlay-regression
/// (a compromised sandboxed bubble could grow itself to nearly
/// fullscreen on the Tauri host). The fix reconciles the bounds: Rust
/// now applies the SAME `MIN_BUBBLE_W`/`MAX_BUBBLE_W`/
/// `MIN_BUBBLE_H`/`MAX_BUBBLE_H` bounds as Electron (see the constants
/// in `math.rs`) so both hosts produce identical resize behavior.
#[tauri::command]
pub async fn bubble_resize(
    width: f64,
    height: f64,
    app: tauri::AppHandle,
) -> Result<(), VoiceTyperError> {
    let window = app
        .get_webview_window("bubble")
        .ok_or(VoiceTyperError::Host("bubble window not found".into()))?;
    // Round the f64 measurement to u32 with a saturating
    // cast (NaN/negative → 0, ±inf/huge → u32::MAX). See
    // `round_f64_to_u32_saturating` doc for the per-input behavior.
    let w = round_f64_to_u32_saturating(width);
    let h = round_f64_to_u32_saturating(height);
    // Clamp both dimensions to the same MIN/MAX
    // bounds Electron uses (`bubble-handlers.ts::clampBubbleSize`). The
    // downstream `clamp_resize_width` / `clamp_resize_height` reduce
    // any NaN/negative/0/+/u32::MAX to the pill range.
    let capped_w = clamp_resize_width(w);
    let capped_h = clamp_resize_height(h);
    use tauri::PhysicalSize;
    window
        .set_size(PhysicalSize::new(capped_w, capped_h))
        .map_err(|e| VoiceTyperError::Host(e.to_string()))
}

/// Toggle dictation from the bubble's own mic button (ADR-0020 §9).
/// The bubble is a sandboxed renderer (SEC-026)
/// with NO `dispatch` access — the main-window guard at
/// the top of `commands::sidecar_cmds::dispatch` (`require_main_window`)
/// rejects any
/// `dispatch` call from a non-main window, returning the
/// `disallowed_window` error envelope. So instead of calling
/// `dispatch` from JS, the bubble renderer invokes this dedicated
/// command which forwards the `toggle_dictation` envelope to the
/// sidecar via the WS bridge (mirroring how `dispatch` does it but
/// with a fixed command name and fire-and-forget semantics — the
/// bubble doesn't need the response because the sidecar's
/// `status_change` event will reach it via the WS-reader-translated
/// `bubble:set-state` route; see the section comment
/// at the top of this file for the full route description).
///
/// The Python sidecar's `toggle_dictation` handler responds with
/// `{type:"result", data:{recording: bool}}` — we ignore the response
/// here (no `pending` entry is registered) because the bubble renderer
/// doesn't need it (it learns the new state via the `bubble:set-state`
/// event emitted by the WS reader task — see
/// `sidecar/ws/event_protocol.rs::translate_event_name`). The main
/// renderer's `usePython.ts`
/// subscription to `status_change` is the source of truth for the
/// toggle's effect on the rest of the UI.
///
/// Mirrors the Electron `bubble:toggle-dictation` IPC handler in
/// `voice_typer/client/src/main/index.ts` which calls
/// `python.call({type: 'toggle_dictation'})`.
///
/// (sanctioned-bypass rationale + rate limiter):
///
/// This command is the **ONLY sanctioned bypass** of the
/// `dispatch`-allowlist (SEC-026). Every other cross-window
/// IPC MUST go through `dispatch` (which enforces the
/// `window.label() == "main"` guard). `bubble_toggle_dictation` is
/// allowed to bypass because:
///   1. It targets a SINGLE fixed, safe command (`toggle_dictation`)
///      — the user-visible effect is "start/stop recording", which is
///      already exposed via the tray icon + global hotkey. There is
///      NO privilege escalation (the bubble can't invoke arbitrary
///      sidecar commands).
///   2. The bubble renderer is sandboxed (no Node integration, no
///      filesystem, no shell access) — even if compromised, the worst
///      it can do is toggle dictation on/off (a denial-of-mic attack,
///      not a data-exfil attack).
///   3. The bypass is TYPE-FIXED at the Rust layer: the JSON envelope
///      is constructed HERE (not in the renderer), so a compromised
///      bubble can't send arbitrary `{type: "..."}` payloads.
///
/// To prevent abuse (e.g. a buggy renderer that spams toggle in a
/// tight loop, or a malicious compromise that tries to DoS the
/// sidecar's recording state machine), this command is rate-limited
/// to **1 toggle per 500ms** via a process-wide `AtomicU64` tracking
/// the last-toggle timestamp. Toggles that arrive within the 500ms
/// window are silently dropped (returning Ok(()) — the renderer
/// doesn't need to know it was rate-limited because the sidecar's
/// `status_change` event is translated by the WS reader task
/// (`sidecar/ws.rs::translate_event_name`) into the
/// `bubble:set-state` Tauri event, and the bubble UI reflects the
/// ACTUAL state, not the requested state).
#[tauri::command]
pub async fn bubble_toggle_dictation(
    state: tauri::State<'_, Arc<SidecarState>>,
) -> Result<(), VoiceTyperError> {
    // Rate limiter: max 1 toggle per 500ms. See the doc comment
    // above for the rationale (DoS protection against a buggy or
    // compromised bubble renderer spamming toggle_dictation).
    if !toggle_rate_limiter_allows() {
        log::warn!("[BUBBLE] toggle_dictation rate-limited (last toggle <500ms ago) — dropping");
        return Ok(());
    }
    // Fire-and-forget: send the toggle_dictation envelope with a
    // synthetic id of 0 (the sidecar's response is dropped — see the
    // doc comment above). We do NOT register a pending entry, so the
    // WS reader finds no match for id 0 and drops the response after
    // a single DEBUG-level `RX response id=0 had NO pending entry`
    // line — no warning fires (one debug line per toggle is
    // acceptable noise; the sidecar already logs every dispatch
    // round-trip).
    //
    // (extraction note): the inline `json!` + `lock` +
    // `try_send` block that used to live here duplicated the send
    // path in `dispatch_frame`. Replaced by a call
    // to `crate::commands::sidecar_cmds::dispatch_fire_and_forget`,
    // which constructs the id=0 frame, locks `ws_tx` via the poison-
    // safe `mutex_lock` helper, and `try_send`s the frame. The error
    // strings ("sidecar not connected" / "WS send failed: <e>")
    // mirror `dispatch_frame`'s shape so the renderer's reject path
    // handles them identically.
    crate::commands::sidecar_cmds::dispatch_fire_and_forget(state.inner(), "toggle_dictation", None)
}
