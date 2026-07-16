// src/renderer/src/lib/tauri-bridge.ts
//
// ADR-0020 §6.3 (Phase 3 UI port): Tauri ↔ React bridge.
//
// This module runs at renderer startup and detects whether the app is
// running inside Tauri (`window.__TAURI__` is present) or Electron. In
// Tauri mode it installs `window.python`, `window.bubble`, and
// `window.window_` using Tauri's `invoke()` + `event.listen()` APIs so
// the existing renderer code (including `usePython.ts` and its
// NEW-IPC-107 guards) works unchanged on both runtimes.
//
// In Electron mode this module is a no-op — the Electron preload
// (`src/preload/index.ts`) already installed the same namespaces via
// `contextBridge.exposeInMainWorld`.
//
// Contract preserved (identical on both paths):
//   • `window.python.call({type, data}) → Promise<data>` — dispatches
//     an IPC command to the Python sidecar. On Tauri this routes
//     through `invoke('dispatch', {cmd: type, data})`; the Rust host
//     forwards it over WS to the sidecar and returns `response.data`.
//     On Electron this routes through `ipcRenderer.invoke('python-call')`;
//     the main process forwards it over TCP and resolves with `msg.data`.
//     Both paths reject on `type:"error"` envelopes (Rust at main.rs:515,
//     Electron at index.ts:428).
//   • `window.python.onEvent(callback) → () => void` — subscribes to
//     all server-initiated events. On Tauri this listens to the
//     `python-event` Tauri event (emitted by main.rs:455 with
//     `{type, data}` envelope). On Electron this listens to the
//     `python-event` IPC channel.
//   • `window.bubble.onLevel(callback)` — bubble audio level stream.
//     On Tauri this listens to the `bubble_level` Tauri event (coalesced
//     to ≤30 Hz by main.rs:427-442). On Electron this listens to the
//     `bubble:level` IPC channel.
//   • `window.window_.minimize/maximize/close/isMaximized` — window
//     controls. On Tauri these use the core window API. On Electron
//     these route through `ipcRenderer.invoke`.
//
// The NEW-IPC-107 guard in `usePython.ts` (lines 36-68) works on both
// paths because:
//   - The `_error` field check catches Electron's not-connected /
//     send-exception envelopes (`{_error: "..."}`).
//   - The `type:"error"` check is a no-op on Tauri (Rust already
//     rejected the promise) but harmless.
//   - Both paths return `data` directly (not the full envelope), so
//     `result as T` has the same shape on both paths.

import type {
	MainRendererBubble,
	PythonBridge,
	PythonPushEvent,
	WindowBridge,
} from "@/types/ipc";

/* eslint-disable @typescript-eslint/no-explicit-any -- Tauri's global
 * API surface is untyped in our TS context (we deliberately avoid
 * pulling in @tauri-apps/api as a dep to keep the bundle lean). We
 * declare a minimal structural type below. */

// ─── Minimal Tauri v2 global API type ─────────────────────────────────
// Mirrors the shape injected by `tauri::Builder` when
// `app.withGlobalTauri = true` (see tauri.conf.json). Only the methods
// we actually use are declared.

interface TauriEvent<T> {
	event: string;
	payload: T;
	id: number;
}

interface TauriGlobal {
	core: {
		invoke<T = unknown>(
			cmd: string,
			args?: Record<string, unknown>,
		): Promise<T>;
	};
	event: {
		listen<T = unknown>(
			event: string,
			handler: (e: TauriEvent<T>) => void,
		): Promise<() => void>;
	};
	window: {
		getCurrentWindow: () => {
			minimize: () => Promise<void>;
			toggleMaximize: () => Promise<void>;
			close: () => Promise<void>;
			isMaximized: () => Promise<boolean>;
			onResized: (handler: () => void) => Promise<() => void>;
		};
	};
}

// ─── Detector + installer ─────────────────────────────────────────────

/**
 * Returns true if the renderer is running inside a Tauri WebView
 * (`window.__TAURI__` is present). When false, the Electron preload
 * has already installed the bridge namespaces.
 */
function isTauri(): boolean {
	return (
		typeof window !== "undefined" &&
		!!(window as unknown as { __TAURI__?: TauriGlobal }).__TAURI__?.core?.invoke
	);
}

/**
 * Install `window.python`, `window.bubble`, and `window.window_` using
 * Tauri's global API. Idempotent — safe to call multiple times.
 *
 * This is the Phase 3 UI port (ADR-0020 §6.3): the React renderer code
 * (including `usePython.ts`) is unchanged on both Electron and Tauri
 * paths because the bridge namespaces have identical shapes.
 */
export function installTauriBridge(): void {
	if (!isTauri()) {
		// Electron path — preload already installed the namespaces.
		return;
	}
	const tauri = (window as unknown as { __TAURI__: TauriGlobal }).__TAURI__;
	if (window.python && window.bubble && window.window_) {
		// Already installed (e.g. HMR re-import).
		return;
	}

	// ─── window.python ───────────────────────────────────────────
	// `call` → `invoke('dispatch', {cmd, data})`. The Rust `dispatch`
	// command (main.rs:484) forwards to the sidecar via WS, awaits the
	// per-id response, and returns `response.data` on success or
	// rejects with an error string on `type:"error"` (main.rs:515).
	// The shape matches Electron's `sendToPython` which resolves with
	// `msg.data` (index.ts:436).
	const python: PythonBridge = {
		call: (msg) =>
			tauri.core.invoke("dispatch", {
				cmd: msg.type,
				data: msg.data ?? {},
			}),
		onEvent: (callback) => {
			// The Rust host emits `python-event` with `{type, data}`
			// envelope for every server-initiated event (main.rs:455).
			// This matches Electron's `python-event` IPC channel.
			// Cast through unknown because the Tauri event payload is
			// structurally `{type: string, data: ...}` while
			// `PythonPushEvent` is a discriminated union with literal
			// `type` members — the runtime shape is identical.
			let unlisten: (() => void) | null = null;
			let cancelled = false;
			tauri.event
				.listen("python-event", (e) => {
					const payload = e.payload as unknown as PythonPushEvent;
					callback(payload);
				})
				.then((un) => {
					if (cancelled) {
						un();
					} else {
						unlisten = un;
					}
				});
			// CR-Finding 5: also listen for FT-1 host events
			// (`ft1_relaunching`, `ft1_reconnected`) and synthesize
			// `python-event` frames so `useConnection` updates the UI
			// during FT-1 respawn cycles. Without this, the renderer's
			// connection status stays "connected" while the sidecar is
			// dead, and the user sees a frozen UI with no feedback.
			const ft1Unlistens: Array<() => void> = [];
			const ft1Events: Array<[string, string]> = [
				["ft1_relaunching", "reconnecting"],
				["ft1_reconnected", "reconnected"],
			];
			for (const [tauriEvt, pythonEvt] of ft1Events) {
				tauri.event
					.listen(tauriEvt, () => {
						callback({
							type: pythonEvt,
							data: { reason: tauriEvt },
						} as unknown as PythonPushEvent);
					})
					.then((un) => {
						if (cancelled) {
							un();
						} else {
							ft1Unlistens.push(un);
						}
					});
			}
			return () => {
				cancelled = true;
				if (unlisten) {
					unlisten();
					unlisten = null;
				}
				for (const un of ft1Unlistens) {
					un();
				}
			};
		},
	};

	// ─── window.bubble ───────────────────────────────────────────
	// Only `onLevel` is wired (the core bubble function). The other
	// APIs are stubbed with no-ops; they require Rust-side
	// window-management commands that are out of scope for the MVP.
	const bubble: MainRendererBubble = {
		onLevel: (callback) => {
			let unlisten: (() => void) | null = null;
			let cancelled = false;
			tauri.event
				.listen<{ rms: number; peak: number }>("bubble_level", (e) => {
					callback(e.payload);
				})
				.then((un) => {
					if (cancelled) {
						un();
					} else {
						unlisten = un;
					}
				});
			return () => {
				cancelled = true;
				if (unlisten) {
					unlisten();
					unlisten = null;
				}
			};
		},
		show: () => {
			/* TODO: Tauri bubble window show command */
		},
		signalReady: () => {
			/* TODO: Tauri bubble signalReady command */
		},
		setPosition: () => {
			/* TODO: Tauri bubble setPosition command */
		},
		setDraggable: () => {
			/* TODO: Tauri bubble setDraggable command */
		},
		moveBy: () => {
			/* TODO */
		},
		onShow: (callback) => {
			let unlisten: (() => void) | null = null;
			let cancelled = false;
			tauri.event
				.listen("bubble:show", () => callback())
				.then((un) => {
					if (cancelled) un();
					else unlisten = un;
				});
			return () => {
				cancelled = true;
				if (unlisten) {
					unlisten();
					unlisten = null;
				}
			};
		},
		onHide: (callback) => {
			let unlisten: (() => void) | null = null;
			let cancelled = false;
			tauri.event
				.listen("bubble:hide", () => callback())
				.then((un) => {
					if (cancelled) un();
					else unlisten = un;
				});
			return () => {
				cancelled = true;
				if (unlisten) {
					unlisten();
					unlisten = null;
				}
			};
		},
		onDraggable: (callback) => {
			let unlisten: (() => void) | null = null;
			let cancelled = false;
			tauri.event
				.listen<boolean>("bubble:draggable", (e) =>
					callback(Boolean(e.payload)),
				)
				.then((un) => {
					if (cancelled) un();
					else unlisten = un;
				});
			return () => {
				cancelled = true;
				if (unlisten) {
					unlisten();
					unlisten = null;
				}
			};
		},
		hideComplete: () => {
			/* TODO */
		},
	};

	// ─── window.window_ ──────────────────────────────────────────
	// Basic window controls via Tauri's core window API. Export/dialog
	// APIs are stubbed (they require Electron's dialog module).
	const tauriWindow = tauri.window.getCurrentWindow();
	const window_: WindowBridge = {
		minimize: () => tauriWindow.minimize(),
		toggleMaximize: async () => {
			await tauriWindow.toggleMaximize();
			return tauriWindow.isMaximized();
		},
		close: () => tauriWindow.close(),
		isMaximized: () => tauriWindow.isMaximized(),
		onMaximizedChanged: (callback) => {
			let unlisten: (() => void) | null = null;
			let cancelled = false;
			// Tauri v2 doesn't have a direct "maximized-changed" event,
			// but `onResized` fires after any resize including maximize/
			// unmaximize. We query `isMaximized()` after each resize.
			tauriWindow
				.onResized(async () => {
					const maximized = await tauriWindow.isMaximized();
					callback(maximized);
				})
				.then((un) => {
					if (cancelled) un();
					else unlisten = un;
				});
			return () => {
				cancelled = true;
				if (unlisten) {
					unlisten();
					unlisten = null;
				}
			};
		},
		// Export/dialog APIs are Electron-specific — stub with rejections
		// so callers get a clear error instead of a silent no-op.
		exportHistory: async () => ({
			success: false,
			error: "Export not supported in Tauri mode yet",
		}),
		exportVocabulary: async () => ({
			success: false,
			error: "Export not supported in Tauri mode yet",
		}),
	};

	window.python = python;
	window.bubble = bubble;
	window.window_ = window_;
}

// Auto-install when this module is imported. Both `main.tsx` (main
// window) and `bubble-main.tsx` (bubble window) import this module at
// the top so the bridge is ready before the React app mounts.
installTauriBridge();
