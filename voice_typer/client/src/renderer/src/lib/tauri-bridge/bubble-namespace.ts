// src/renderer/src/lib/tauri-bridge/bubble-namespace.ts
//
// ADR-0020 §6.3 (Phase 3 UI port): `window.bubble` installer for the
// Tauri runtime.
//
// MIG-1.2: `onLevel` listens to the `bubble_level` Tauri event (coalesced
// to ≤30 Hz by main.rs:427-442). The 5 shared mutator methods (`show`,
// `signalReady`, `setPosition`, `setDraggable`, `moveBy`) invoke Rust
// window-management commands added in MIG-1.2. Each fires-and-forgets —
// the return type is `void` per the `MainRendererBubbleMutators`
// contract, matching the Electron preload (which uses `ipcRenderer.send`,
// also void). The Rust commands update the bubble BrowserWindow state
// asynchronously.
//
// CR-33: bubble-window-only methods (onConfig, onSetState, resizeTo,
// toggleDictation, hideComplete) — port of preload/bubble.ts:64-71 /
// 100-102 / 120-122. These are ONLY installed on the bubble window
// (see `windowLabel` parameter on `createBubbleNamespace` below —
// EC-FIX-6 / EC-13). The main renderer's `window.bubble` exposes only
// the 5 shared mutators (the `MainRendererBubbleMutators` subset);
// the bubble renderer's `window.bubble` exposes the full
// `BubbleWindowBubble` (`MainRendererBubbleMutators &
// BubbleEventSubscriptions & BubbleWindowExtras`). This mirrors the
// Electron preload's split (`preload/index.ts` vs `preload/bubble.ts`)
// and prevents a compromised main renderer from invoking
// `bubble_resize` / `bubble_toggle_dictation` directly (SEC-026).
//
// The event-subscription methods (`onLevel` / `onShow` /
// `onHide` / `onDraggable`) are now ONLY installed on the bubble
// window. The main renderer has no reason to subscribe to bubble-
// window lifecycle events — the prior version installed dead
// listeners on main that silently no-op'd when the events never
// arrived. The type system enforces this: the main renderer's
// `window.bubble` is typed `MainRendererBubbleMutators` (no
// `onLevel` / `onShow` / `onHide` / `onDraggable` fields).
//
// `hideComplete` moved from the shared mutators to
// `BubbleWindowExtras` (bubble-only). Only the bubble renderer's
// exit-animation handler should invoke it. The Electron preload's
// exposure of `hideComplete` on main was removed in the same fix.
//
// The previous version inlined a 12-line race-safe subscribe block 6×
// (onLevel / onShow / onHide / onDraggable / onConfig / onSetState).
// Using `makeListener` per listener collapses each to a single call.
// `onShow` / `onHide` have no payload so they use `makeListener<void>`
// — the subscribe wrapper invokes `handler(undefined)` so the consumer
// callback (which takes no args) is called with the same effect.
//
// G4-M-70 (security/observability): every fire-and-forget `invoke(...)`
// call now ends with `.catch((err) => console.warn("[bubble IPC] ...",
// err))` instead of the previous `void invoke(...)` form which
// discarded rejections. A broken bubble host previously failed
// invisibly; failures now surface in the Electron main-process log
// (forwarded via webContents.on("console-message")). The corresponding
// observability fix for the `bubble_level` listener SUBSCRIBE promise
// (and every other listener in this namespace) lives in
// `makeListener()` itself — see `detect.ts` (it now logs subscribe
// rejections rather than letting them surface as unhandled promise
// rejections).

import type {
        BubbleEventSubscriptions,
        BubbleWindowBubble,
        BubbleWindowExtras,
        MainRendererBubbleMutators,
} from "@/types/ipc";

import { makeListener, type TauriGlobal } from "./detect";

/**
 * Detect the current Tauri window label. The main renderer is labeled
 * "main" (tauri.conf.json) and the bubble overlay is labeled "bubble".
 *
 * The minimal `TauriGlobal` type in `detect.ts` doesn't declare `label`
 * on the `getCurrentWindow()` return (the bridge previously didn't need
 * it), so we cast here. Tauri v2's `Window` object always exposes
 * `label` as a public field set from `tauri.conf.json`.
 *
 * Unknown labels default to "main" — the safer (smaller) subset, so a
 * misconfigured window label never accidentally exposes bubble-only
 * methods on the main renderer (SEC-026 regression — see EC-FIX-6 /
 * EC-13).
 */
function detectWindowLabel(tauri: TauriGlobal): "main" | "bubble" {
        const win = tauri.window.getCurrentWindow() as unknown as { label?: string };
        return win?.label === "bubble" ? "bubble" : "main";
}

/**
 * Build the `window.bubble` namespace using Tauri's global API.
 *
 * EC-FIX-6 / EC-13 (SEC-026 regression): the previous version installed
 * the FULL bubble API on BOTH windows — no window-label check. A
 * compromised main renderer could invoke `bubble_resize` /
 * `bubble_toggle_dictation` directly, bypassing the bubble-window
 * sandbox. The installer now takes an optional `windowLabel` parameter
 * (`"main" | "bubble"`) and returns ONLY the `MainRendererBubbleMutators`
 * subset on the main window (omitting `onSetState` / `onConfig` /
 * `resizeTo` / `toggleDictation` / `hideComplete` AND the event
 * subscriptions `onLevel` / `onShow` / `onHide` / `onDraggable`),
 * mirroring the Electron preload's split (`preload/index.ts` exposes
 * only the 5 shared mutators; `preload/bubble.ts` adds the bubble-only
 * methods + event subscriptions). The label is detected via
 * `tauri.window.getCurrentWindow().label` when no parameter is passed.
 *
 * Event subscriptions (`onLevel` / `onShow` / `onHide` /
 * `onDraggable`) are now bubble-only. The main renderer has no reason
 * to subscribe to bubble-window lifecycle events — the prior version
 * installed dead listeners on main that silently no-op'd.
 *
 * `hideComplete` is now bubble-only (was in the shared mutators).
 * Only the bubble renderer's exit-animation handler should invoke it.
 *
 * `setPosition` accepts a single string `position` (XPLAT-6
 * fix). Both production call sites — `useConnection.ts:117` (syncing
 * the saved `bubble_position` config) and `GeneralSettingsSection.tsx:151`
 * (the bubble-position dropdown) — pass one of `"top"` / `"bottom"`.
 * The Rust `bubble_set_position(position: String)` command parses the
 * keyword server-side and resolves it to absolute physical coordinates
 * based on the primary monitor's bounds (see
 * `src-tauri/src/commands/bubble.rs`). The call shape matches the
 * `MainRendererBubbleMutators.setPosition?: (pos: string) => void`
 * contract exactly.
 *
 * @param tauri        Tauri global API.
 * @param windowLabel  Optional label override. If omitted, detected
 *   via `tauri.window.getCurrentWindow().label`. Pass explicitly when
 *   the caller already knows the label (e.g. `installTauriBridge()` in
 *   `index.ts`) to make the intent obvious at the call site.
 */
export function createBubbleNamespace(
        tauri: TauriGlobal,
        windowLabel?: "main" | "bubble",
): MainRendererBubbleMutators | BubbleWindowBubble {
        const label = windowLabel ?? detectWindowLabel(tauri);

        // ─── Shared mutators (both windows) ─────────────────────────────
        // SEC-026: the main renderer gets ONLY these 5 mutators,
        // matching preload/index.ts. The bubble-only mutators (onSetState /
        // onConfig / resizeTo / toggleDictation / hideComplete) AND the
        // event subscriptions are added below for the bubble window.
        const mutators: MainRendererBubbleMutators = {
                // MIG-1.2: show the bubble BrowserWindow. Fire-and-forget —
                // matches Electron's `ipcRenderer.send("bubble:show-from-renderer")`.
                // G4-M-70: log failures instead of silent drop.
                show: () => {
                        tauri.core
                                .invoke("bubble_show")
                                .catch((err) => console.warn("[bubble IPC] bubble_show failed:", err));
                },

                // MIG-1.2: signal that the bubble renderer has mounted and is
                // ready to receive `bubble_level` events. Matches Electron's
                // `ipcRenderer.send("bubble:ready")`.
                signalReady: () => {
                        tauri.core
                                .invoke("bubble_signal_ready")
                                .catch((err) =>
                                        console.warn("[bubble IPC] bubble_signal_ready failed:", err),
                                );
                },

                // MIG-1.2 + XPLAT-6: forward the
                // `"top" | "bottom"` keyword as a single `position`
                // arg. The Rust `bubble_set_position(position: String)`
                // command parses the keyword server-side and resolves
                // it to absolute physical coordinates based on the
                // primary monitor's bounds (see
                // src-tauri/src/commands/bubble.rs). The previous
                // `(x: Value, y: Value)` signature was a leaky
                // abstraction — the bridge had to forward the keyword
                // as BOTH `x` and `y` because Tauri v2 arg
                // deserialization rejected a single `{ position }`
                // payload for a 2-arg command. The single-string `position` collapses the
                // command to a single-arg shape so the bridge is
                // straight-through.
                //
                // DT-52: narrow the param type to `"top" | "bottom"` to
                // match the shared `MainRendererBubbleMutators.setPosition`
                // contract (previously `string`, which let a typo like
                // `"left"` compile and reach the Rust runtime). The Rust
                // command still accepts `String` at the FFI boundary and
                // validates the value at runtime (defense-in-depth).
                setPosition: (position: "top" | "bottom") => {
                        tauri.core
                                .invoke("bubble_set_position", {
                                        position,
                                })
                                .catch((err) =>
                                        console.warn("[bubble IPC] bubble_set_position failed:", err),
                                );
                },

                // MIG-1.2: toggle whether the bubble window can be dragged.
                // Matches Electron's `ipcRenderer.send("bubble:draggable", draggable)`.
                setDraggable: (draggable: boolean) => {
                        tauri.core
                                .invoke("bubble_set_draggable", { draggable })
                                .catch((err) =>
                                        console.warn("[bubble IPC] bubble_set_draggable failed:", err),
                                );
                },

                // MIG-1.2: nudge the bubble window by a relative delta
                // (keyboard-based move — NEW-A11Y-006). The Rust command takes
                // `{dx, dy}` (renamed from the renderer's `deltaX`/`deltaY` to
                // match the snake_case Rust convention).
                moveBy: (deltaX: number, deltaY: number) => {
                        tauri.core
                                .invoke("bubble_move_by", {
                                        dx: deltaX,
                                        dy: deltaY,
                                })
                                .catch((err) =>
                                        console.warn("[bubble IPC] bubble_move_by failed:", err),
                                );
                },
        };

        if (label === "main") {
                // SEC-026: main renderer — return only the shared
                // mutators. Omit onSetState / onConfig / resizeTo /
                // toggleDictation / hideComplete (bubble-window-only — matches
                // preload/index.ts). ALSO omit the event subscriptions
                // (onLevel / onShow / onHide / onDraggable) — the main
                // renderer has no reason to subscribe to bubble-window
                // lifecycle events, and installing dead listeners was a
                // leaky abstraction.
                return mutators;
        }

        // ─── Bubble-window-only event subscriptions ─────────────
        // These subscribe to Tauri events emitted by the Rust host /
        // sidecar. The main renderer has no reason to listen to these
        // (it learns about bubble state via its own Python-side
        // `status_change` subscription, not via bubble-window events).
        const subscriptions: BubbleEventSubscriptions = {
                // MIG-1.2: `onLevel` listens to the `bubble_level` Tauri event
                // (coalesced to ≤30 Hz by main.rs:427-442).
                onLevel: (callback) =>
                        makeListener<{ rms: number; peak: number }>(
                                (handler) =>
                                        tauri.event.listen<{ rms: number; peak: number }>(
                                                "bubble_level",
                                                (e) => handler(e.payload),
                                        ),
                                callback,
                        ),

                onShow: (callback: () => void) =>
                        makeListener<void>(
                                (handler) =>
                                        tauri.event.listen("bubble:show", () => {
                                                handler();
                                        }),
                                () => callback(),
                        ),

                onHide: (callback: () => void) =>
                        makeListener<void>(
                                (handler) =>
                                        tauri.event.listen("bubble:hide", () => {
                                                handler();
                                        }),
                                () => callback(),
                        ),

                onDraggable: (callback) =>
                        makeListener<boolean>(
                                (handler) =>
                                        tauri.event.listen<boolean>("bubble:draggable", (e) =>
                                                handler(Boolean(e.payload)),
                                        ),
                                callback,
                        ),
        };

        // ─── Bubble-window-only mutators ─────────────────────────
        const bubbleOnly: BubbleWindowExtras = {
                // UX-10: bubble-relevant config pushed from the Python backend.
                // The sandboxed bubble renderer has no get_config, so this is how
                // it learns whether to show the mic button. Listens on the
                // `bubble:config` Tauri event (emitted by the Rust host).
                onConfig: (callback: (payload: Record<string, unknown>) => void) =>
                        makeListener<Record<string, unknown>>(
                                (handler) =>
                                        tauri.event.listen<Record<string, unknown>>("bubble:config", (e) =>
                                                handler(e.payload as Record<string, unknown>),
                                        ),
                                callback,
                        ),

                // CR-33: bubble renderer listens for `bubble:set-state`
                // events pushed by the Rust WS reader task (sidecar/ws.rs
                // `translate_event_name` translates the Python sidecar's
                // `bubble_set_state` event to the renderer's `bubble:set-state`
                // name). Matches Electron's `ipcRenderer.on("bubble:set-state",
                // handler)` in preload/bubble.ts:64-71.
                onSetState: (callback: (payload: string) => void) =>
                        makeListener<string>(
                                (handler) =>
                                        tauri.event.listen<string>("bubble:set-state", (e) => {
                                                handler(String(e.payload));
                                        }),
                                callback,
                        ),

                // CR-33: auto-resize the bubble BrowserWindow to exactly fit the
                // pill content (eliminates the transparent dead zone around the
                // bubble that blocks clicks to the windows underneath). Matches
                // Electron's `ipcRenderer.send("bubble:resize", {width, height})`
                // in preload/bubble.ts:100-102. Fire-and-forget — the Rust
                // `bubble_resize` command updates the window size asynchronously.
                resizeTo: (width: number, height: number) => {
                        tauri.core
                                .invoke("bubble_resize", { width, height })
                                .catch((err) =>
                                        console.warn("[bubble IPC] bubble_resize failed:", err),
                                );
                },

                // CR-33 + UX-10: toggle dictation from the bubble's own mic
                // button. The bubble is sandboxed (SEC-026 / CR-5) with NO
                // `dispatch` access — the Rust `check_dispatch_window_label`
                // guard rejects any `dispatch` call from a non-main window. So
                // instead of `invoke('dispatch', ...)`, the bubble invokes this
                // dedicated `bubble_toggle_dictation` command which forwards the
                // `toggle_dictation` envelope to the sidecar via the WS bridge
                // (fire-and-forget — the bubble learns the new state via the
                // `bubble:set-state` event). Matches Electron's
                // `ipcRenderer.send("bubble:toggle-dictation")` in
                // preload/bubble.ts:120-122.
                toggleDictation: () => {
                        tauri.core
                                .invoke("bubble_toggle_dictation")
                                .catch((err) =>
                                        console.warn("[bubble IPC] bubble_toggle_dictation failed:", err),
                                );
                },

                // notify the host that the bubble's exit animation has
                // finished and the window can be hidden. Only the bubble
                // renderer's exit-animation handler should invoke this — the
                // main renderer has no equivalent lifecycle. Matches Electron's
                // `ipcRenderer.send("bubble:hidden")` in preload/bubble.ts.
                hideComplete: () => {
                        tauri.core
                                .invoke("bubble_hide_complete")
                                .catch((err) =>
                                        console.warn("[bubble IPC] bubble_hide_complete failed:", err),
                                );
                },
        };

        // Bubble window — return the full BubbleWindowBubble shape:
        // MainRendererBubbleMutators & BubbleEventSubscriptions & BubbleWindowExtras.
        return { ...mutators, ...subscriptions, ...bubbleOnly };
}
