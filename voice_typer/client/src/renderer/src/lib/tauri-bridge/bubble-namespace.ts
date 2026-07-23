// src/renderer/src/lib/tauri-bridge/bubble-namespace.ts
//
// ADR-0020 §6.3 (Phase 3 UI port): `window.bubble` installer for the
// Tauri runtime.
//
// MIG-1.2: `onLevel` listens to the `bubble_level` Tauri event (coalesced
// to ≤30 Hz by main.rs:427-442). The 6 mutator methods (`show`,
// `signalReady`, `setPosition`, `setDraggable`, `moveBy`, `hideComplete`)
// invoke Rust window-management commands added in MIG-1.2. Each
// fires-and-forgets — the return type is `void` per the
// MainRendererBubble contract, matching the Electron preload (which uses
// `ipcRenderer.send`, also void). The Rust commands update the bubble
// BrowserWindow state asynchronously.
//
// CR-33: bubble-window-only methods (onConfig, onSetState, resizeTo,
// toggleDictation) — port of preload/bubble.ts:64-71 / 100-102 / 120-122.
// These are ONLY installed on the bubble window (see `windowLabel`
// parameter on `createBubbleNamespace` below — EC-FIX-6 / EC-13).
// The main renderer's `window.bubble` exposes only the 10 shared
// methods (the `MainRendererBubble` subset); the bubble renderer's
// `window.bubble` exposes the full `BubbleWindowBubble`. This mirrors
// the Electron preload's split (`preload/index.ts` vs `preload/bubble.ts`)
// and prevents a compromised main renderer from invoking
// `bubble_resize` / `bubble_toggle_dictation` directly (SEC-026).
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

import type { BubbleWindowBubble, MainRendererBubble } from "@/types/ipc";

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
 * (`"main" | "bubble"`) and returns ONLY the `MainRendererBubble` subset
 * on the main window (omitting `onSetState` / `onConfig` / `resizeTo` /
 * `toggleDictation`), mirroring the Electron preload's split
 * (`preload/index.ts` exposes only the 10 shared methods; `preload/bubble.ts`
 * adds the 4 bubble-only methods). The label is detected via
 * `tauri.window.getCurrentWindow().label` when no parameter is passed.
 *
 * `setPosition` accepts a single string `position` (XPLAT-6 fix). Both
 * production call sites — `useConnection.ts:117` (syncing the saved
 * `bubble_position` config) and `GeneralSettingsSection.tsx:151` (the
 * bubble-position dropdown) — pass one of `"top"` / `"bottom"`. The
 * Rust `bubble_set_position` command takes TWO args (`x: Value, y:
 * Value`) and parses `"top"`/`"bottom"` strings server-side, resolving
 * them to absolute physical coordinates based on the primary monitor's
 * bounds (see `src-tauri/src/commands/bubble.rs`). The bridge therefore
 * forwards the string as BOTH `x` and `y` — sending a single
 * `{ position }` arg made Tauri v2 reject the invoke (missing required
 * args), so the bubble never positioned. The call shape still matches
 * the `MainRendererBubble.setPosition?: (pos: string) => void` contract
 * exactly.
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
): MainRendererBubble | BubbleWindowBubble {
	const label = windowLabel ?? detectWindowLabel(tauri);

	// ─── Shared subset (both windows) ──────────────────────────────
	// SEC-026: the main renderer gets ONLY these 10 methods, matching
	// preload/index.ts. The bubble-only methods (onSetState / onConfig /
	// resizeTo / toggleDictation) are added below for the bubble window.
	const shared: MainRendererBubble = {
		onLevel: (callback) =>
			makeListener<{ rms: number; peak: number }>(
				(handler) =>
					tauri.event.listen<{ rms: number; peak: number }>(
						"bubble_level",
						(e) => handler(e.payload),
					),
				callback,
			),

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

		// MIG-1.2 + XPLAT-6: forward the `"top" | "bottom"` string as
		// BOTH `x` and `y` — the Rust `bubble_set_position(x: Value,
		// y: Value)` command requires both args (a single `{ position }`
		// payload is rejected by Tauri v2 arg deserialization) and
		// resolves the strings to absolute physical coordinates based on
		// the primary monitor's bounds (see src-tauri/src/commands/bubble.rs).
		setPosition: (position: string) => {
			tauri.core
				.invoke("bubble_set_position", {
					x: position,
					y: position,
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

		// MIG-1.2: notify the host that the bubble's exit animation has
		// finished and the window can be hidden. Matches Electron's
		// `ipcRenderer.send("bubble:hidden")`.
		hideComplete: () => {
			tauri.core
				.invoke("bubble_hide_complete")
				.catch((err) =>
					console.warn("[bubble IPC] bubble_hide_complete failed:", err),
				);
		},
	};

	if (label === "main") {
		// SEC-026: main renderer — return only the shared subset.
		// Omit onSetState / onConfig / resizeTo / toggleDictation
		// (bubble-window-only — matches preload/index.ts).
		return shared;
	}

	// ─── Bubble-window-only methods ────────────────────────────────
	const bubbleOnly = {
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

		// CR-33: bubble renderer listens for `bubble:set-state` events
		// pushed by the Rust `bubble_emit_state` command (invoked from
		// the main renderer when the sidecar emits `status_change`).
		// Matches Electron's `ipcRenderer.on("bubble:set-state", handler)`
		// in preload/bubble.ts:64-71.
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
	};

	// Bubble window — return the full BubbleWindowBubble shape.
	return { ...shared, ...bubbleOnly };
}
