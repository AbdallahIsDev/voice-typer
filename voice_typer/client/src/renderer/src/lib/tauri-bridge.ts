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
// The NEW-IPC-107 guard in `usePython.ts` (d-review NEW-IPC-007) is
// Electron-path-only in practice:
//   - The `_error` field check catches Electron's not-connected /
//     send-exception envelopes (`{_error: "..."}` from index.ts:1908/
//     1911/1916). The Tauri Rust host never produces `{_error:...}`.
//   - The `type:"error"` check catches the Python server's unhandled-
//     dispatch envelope (`{type:"error", data:{code, message}}` from
//     ipc_server.py:1044-1050), which the Electron main process passes
//     through verbatim. On Tauri the Rust `dispatch` command
//     (main.rs:954-965) rejects the `invoke` promise on `type:"error"`
//     BEFORE the resolved value reaches JS, so this branch is dead
//     code on Tauri (errors surface via promise rejection instead).
//   - Both paths return `data` directly on success (Tauri unwraps
//     `response.data` in Rust; Electron resolves with the full envelope
//     but `usePython` returns `result as T` after the error checks pass),
//     so the success shape is consistent across runtimes.
//
// The previous "works on both paths" framing was false: on Tauri BOTH
// in-code checks are unreachable (the `await api.call(...)` throws
// first). They remain in the source because the same `usePython.ts`
// bundle ships under both hosts — they're harmless no-ops on Tauri and
// load-bearing on Electron.

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
	// MIG-1.2: `onLevel` listens to the `bubble_level` Tauri event
	// (coalesced to ≤30 Hz by main.rs:427-442). The 6 mutator methods
	// (`show`, `signalReady`, `setPosition`, `setDraggable`, `moveBy`,
	// `hideComplete`) invoke Rust window-management commands added in
	// MIG-1.2. Each fires-and-forgets — the return type is `void` per
	// the MainRendererBubble contract, matching the Electron preload
	// (which uses `ipcRenderer.send`, also void). The Rust commands
	// update the bubble BrowserWindow state asynchronously.
	//
	// `setPosition` accepts `(x, y)` numeric coordinates on the Tauri
	// path (the Rust `bubble_set_position` command takes `{x, y}` and
	// moves the window to absolute screen coords). The Electron preload
	// accepts `(pos: "top" | "bottom")` instead. The TS type signature
	// widens `x` to `number | string` so the same bridge assignment
	// satisfies the `MainRendererBubble` contract (which declares
	// `(pos: string) => void`) AND accepts the numeric shape used by
	// the Rust command. Parameter contravariance under
	// `strictFunctionTypes` makes `(x: number | string, y?: number)`
	// assignable to `(pos: string)` because `string ⊆ number | string`.
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
			// MIG-1.2: show the bubble BrowserWindow. The Rust
			// `bubble_show` command makes the bubble window visible +
			// moves it to the top of the z-order. Fire-and-forget —
			// matches Electron's `ipcRenderer.send("bubble:show-from-renderer")`.
			void tauri.core.invoke("bubble_show");
		},
		signalReady: () => {
			// MIG-1.2: signal that the bubble renderer has mounted and
			// is ready to receive `bubble_level` events. Matches
			// Electron's `ipcRenderer.send("bubble:ready")`.
			void tauri.core.invoke("bubble_signal_ready");
		},
		setPosition: (x: number | string, y?: number) => {
			// MIG-1.2: move the bubble window to absolute screen
			// coordinates. The Rust `bubble_set_position` command takes
			// `{x, y}` numerics. The `y ?? 0` default handles the
			// legacy `setPosition("top" | "bottom")` call shape from
			// `useConnection.ts:117` (which passes a string and no y);
			// on the Tauri path the renderer is expected to pass
			// numeric coords (the legacy string shape is a no-op on
			// Tauri — the Rust command will reject non-numeric x).
			void tauri.core.invoke("bubble_set_position", {
				x,
				y: y ?? 0,
			});
		},
		setDraggable: (draggable: boolean) => {
			// MIG-1.2: toggle whether the bubble window can be dragged.
			// Matches Electron's `ipcRenderer.send("bubble:draggable", draggable)`.
			void tauri.core.invoke("bubble_set_draggable", { draggable });
		},
		moveBy: (deltaX: number, deltaY: number) => {
			// MIG-1.2: nudge the bubble window by a relative delta
			// (keyboard-based move — NEW-A11Y-006). The Rust command
			// takes `{dx, dy}` (renamed from the renderer's `deltaX`/
			// `deltaY` to match the snake_case Rust convention).
			// Matches Electron's `ipcRenderer.send("bubble:move-by", {deltaX, deltaY})`.
			void tauri.core.invoke("bubble_move_by", {
				dx: deltaX,
				dy: deltaY,
			});
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
			// MIG-1.2: notify the host that the bubble's exit animation
			// has finished and the window can be hidden. Matches
			// Electron's `ipcRenderer.send("bubble:hidden")`.
			void tauri.core.invoke("bubble_hide_complete");
		},
	};

	// ─── window.window_ ──────────────────────────────────────────
	// Basic window controls via Tauri's core window API. Export/dialog
	// APIs (MIG-1.1) invoke the Rust `export_history` / `export_vocabulary`
	// commands which use `tauri-plugin-dialog`'s save dialog. The return
	// shape matches the Electron preload exactly:
	//   - success → `{success: true, path: string}`
	//   - user canceled → `{success: false}` (no path, no error)
	//   - error → `{success: false, error: string}`
	// The Rust command returns `{canceled: true}` on cancel (mapped to
	// `{success: false}` here) or throws on error (caught and mapped to
	// `{success: false, error}`). This keeps the renderer code (History.tsx
	// and Vocabulary.tsx export buttons) unchanged on both paths.
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
		// MIG-1.1: invoke the Rust `export_history` command, which opens
		// `tauri-plugin-dialog`'s save dialog and writes the file. The
		// renderer call sites (History.tsx export button) are unchanged
		// because the return shape matches Electron's `history:export`
		// IPC handler (`{success, path?, error?}`).
		exportHistory: async (data, format) => {
			try {
				const result = await tauri.core.invoke<{
					success?: boolean;
					path?: string;
					canceled?: boolean;
					error?: string;
				}>("export_history", { data, format });
				if (result?.canceled) {
					// User dismissed the save dialog — matches Electron's
					// `{success: false}` (no error, no path).
					return { success: false };
				}
				if (result?.error) {
					return { success: false, error: result.error };
				}
				return {
					success: Boolean(result?.success),
					path: result?.path,
				};
			} catch (e) {
				return {
					success: false,
					error: e instanceof Error ? e.message : String(e),
				};
			}
		},
		// MIG-1.1: invoke the Rust `export_vocabulary` command. Same
		// return-shape mapping as `exportHistory`. The renderer call
		// site (Vocabulary.tsx export button) is unchanged.
		exportVocabulary: async (data, format) => {
			try {
				const result = await tauri.core.invoke<{
					success?: boolean;
					path?: string;
					canceled?: boolean;
					error?: string;
				}>("export_vocabulary", { data, format });
				if (result?.canceled) {
					return { success: false };
				}
				if (result?.error) {
					return { success: false, error: result.error };
				}
				return {
					success: Boolean(result?.success),
					path: result?.path,
				};
			} catch (e) {
				return {
					success: false,
					error: e instanceof Error ? e.message : String(e),
				};
			}
		},
	};

	window.python = python;
	window.bubble = bubble;
	window.window_ = window_;
}

// Auto-install when this module is imported. Both `main.tsx` (main
// window) and `bubble-main.tsx` (bubble window) import this module at
// the top so the bridge is ready before the React app mounts.
installTauriBridge();
