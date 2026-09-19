// `bubble_resize` / `bubble_toggle_dictation` directly (SEC-026).

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
 */
function detectWindowLabel(tauri: TauriGlobal): "main" | "bubble" {
	const win = tauri.window.getCurrentWindow() as unknown as { label?: string };
	return win?.label === "bubble" ? "bubble" : "main";
}

/**
 * Build the `window.bubble` namespace using Tauri's global API.
 *
 */
export function createBubbleNamespace(
	tauri: TauriGlobal,
	windowLabel?: "main" | "bubble",
): MainRendererBubbleMutators | BubbleWindowBubble {
	const label = windowLabel ?? detectWindowLabel(tauri);

	// SEC-026: the main renderer gets ONLY these 5 mutators,
	const mutators: MainRendererBubbleMutators = {
		show: () => {
			tauri.core
				.invoke("bubble_show")
				.catch((err) =>
					console.warn("[renderer:bubble-namespace] bubble_show failed:", err),
				);
		},

		signalReady: () => {
			tauri.core
				.invoke("bubble_signal_ready")
				.catch((err) =>
					console.warn(
						"[renderer:bubble-namespace] bubble_signal_ready failed:",
						err,
					),
				);
		},

		setPosition: (position: "top" | "bottom") => {
			tauri.core
				.invoke("bubble_set_position", {
					position,
				})
				.catch((err) =>
					console.warn(
						"[renderer:bubble-namespace] bubble_set_position failed:",
						err,
					),
				);
		},

		setDraggable: (draggable: boolean) => {
			tauri.core
				.invoke("bubble_set_draggable", { draggable })
				.catch((err) =>
					console.warn(
						"[renderer:bubble-namespace] bubble_set_draggable failed:",
						err,
					),
				);
		},

		moveBy: (deltaX: number, deltaY: number) => {
			tauri.core
				.invoke("bubble_move_by", {
					dx: deltaX,
					dy: deltaY,
				})
				.catch((err) =>
					console.warn(
						"[renderer:bubble-namespace] bubble_move_by failed:",
						err,
					),
				);
		},
	};

	if (label === "main") {
		// SEC-026: main renderer, return only the shared
		return mutators;
	}

	// These subscribe to Tauri events emitted by the Rust host /
	const subscriptions: BubbleEventSubscriptions = {
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

		// this listener is wired for parity: once the host emits
		onLocaleChanged: (callback) =>
			makeListener<string>(
				(handler) =>
					tauri.event.listen<string>("bubble:locale-changed", (e) =>
						handler(String(e.payload)),
					),
				callback,
			),
	};

	const bubbleOnly: BubbleWindowExtras = {
		// `bubble:config` Tauri event (emitted by the Rust host).
		onConfig: (callback: (payload: Record<string, unknown>) => void) =>
			makeListener<Record<string, unknown>>(
				(handler) =>
					tauri.event.listen<Record<string, unknown>>("bubble:config", (e) =>
						handler(e.payload as Record<string, unknown>),
					),
				callback,
			),

		onSetState: (callback: (payload: string) => void) =>
			makeListener<string>(
				(handler) =>
					tauri.event.listen<string>("bubble:set-state", (e) => {
						handler(String(e.payload));
					}),
				callback,
			),

		resizeTo: (width: number, height: number) => {
			tauri.core
				.invoke("bubble_resize", { width, height })
				.catch((err) =>
					console.warn(
						"[renderer:bubble-namespace] bubble_resize failed:",
						err,
					),
				);
		},

		//button. The bubble is sandboxed (SEC-026) with NO
		toggleDictation: () => {
			tauri.core
				.invoke("bubble_toggle_dictation")
				.catch((err) =>
					console.warn(
						"[renderer:bubble-namespace] bubble_toggle_dictation failed:",
						err,
					),
				);
		},

		hideComplete: () => {
			tauri.core
				.invoke("bubble_hide_complete")
				.catch((err) =>
					console.warn(
						"[renderer:bubble-namespace] bubble_hide_complete failed:",
						err,
					),
				);
		},

		// Rust side (SEC-016, only the bubble window may dismiss
		dismiss: () => {
			tauri.core
				.invoke("bubble_dismiss")
				.catch((err) =>
					console.warn(
						"[renderer:bubble-namespace] bubble_dismiss failed:",
						err,
					),
				);
		},
	};

	return { ...mutators, ...subscriptions, ...bubbleOnly };
}
