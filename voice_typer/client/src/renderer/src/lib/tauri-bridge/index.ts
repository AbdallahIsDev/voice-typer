// src/renderer/src/lib/tauri-bridge/index.ts
//
// ADR-0020 §6.3 (Phase 3 UI port): Tauri ↔ React bridge orchestrator.
//
// This module is the public entry point of the `@/lib/tauri-bridge`
// package. It exports `installTauriBridge()` and the namespace
// factories but DOES NOT auto-invoke `installTauriBridge()` at module
// load. The auto-install side effect was split out into a sibling
// `install.ts` module so callers that import `@/lib/tauri-bridge` for
// its named exports (e.g. `createPythonNamespace`, `isTauri`,
// `makeListener`) do not trigger a side effect that mutates
// `window.python`, `window.bubble`, `window.window_`.
//
// Production entrypoints that want the bridge installed must import
// the sibling module explicitly:
//   import "@/lib/tauri-bridge/install";
// Both `main.tsx` (main window) and `bubble-main.tsx` (bubble window)
// do this so the bridge is ready before the React app mounts.
//
//Internal layout ( split — see review.md):
//   • `detect.ts`           — `isTauri()` + `TauriGlobal` types + the
//                              `makeListener()` factory (eliminates the
//                              8× listener boilerplate previously
//                              duplicated across the namespace installers).
//   • `python-namespace.ts` — `createPythonNamespace(tauri): PythonBridge`
//                              (call dispatch + onEvent subscription with
//                              relay).
//   • `bubble-namespace.ts` — `createBubbleNamespace(tauri, windowLabel?):
//                              MainRendererBubbleMutators | BubbleWindowBubble`
//                              (audio level stream + 6 mutators + 5 event
//                              hooks; bubble-window-only methods gated by
//`windowLabel` —  / ).
//   • `window-namespace.ts` — `createWindowNamespace(tauri): WindowBridge`
//                              (window controls + 4 export commands via
//                              the `makeExportCommand(cmd)` factory +
//                              openLogs + openModelImportDialog).
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
//The  guard in `usePython.ts` (d-review ) is
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
//   - Both paths return `data` directly on success — Tauri unwraps
//     `response.data` in Rust; Electron unwraps `msg.data` at
//     `handle-message.ts:68` before resolving. `usePython` returns
//     `result as T` after the (Electron-only) error-envelope checks
//     pass, so the success shape is consistent across runtimes.
//
// The previous "works on both paths" framing was false: on Tauri BOTH
// in-code checks are unreachable (the `await api.call(...)` throws
// first). They remain in the source because the same `usePython.ts`
// bundle ships under both hosts — they're harmless no-ops on Tauri and
// load-bearing on Electron.

import { createBubbleNamespace } from "./bubble-namespace";
import { getTauri, isTauri } from "./detect";
import { createPythonNamespace } from "./python-namespace";
import { createWindowNamespace } from "./window-namespace";

/**
 * Install `window.python`, `window.bubble`, and `window.window_` using
 * Tauri's global API. Idempotent — safe to call multiple times.
 *
 * This is the Phase 3 UI port (ADR-0020 §6.3): the React renderer code
 * (including `usePython.ts`) is unchanged on both Electron and Tauri
 * paths because the bridge namespaces have identical shapes.
 *
 * Public API: preserved verbatim from the pre-split monolith. Callers
 * that want the namespaces installed at module load import the sibling
 * `install.ts` module (`import "@/lib/tauri-bridge/install"`), which
 * invokes this function as a side effect. This `index.ts` module no
 * longer auto-invokes `installTauriBridge()` so that importing the
 * named exports here is side-effect-free.
 */
export function installTauriBridge(): void {
	if (!isTauri()) {
		// Electron path — preload already installed the namespaces.
		return;
	}
	if (window.python && window.bubble && window.window_) {
		// Already installed (e.g. HMR re-import).
		return;
	}

	const tauri = getTauri();
	//(SEC-026 regression): pass the current Tauri
	// window label to `createBubbleNamespace` so the main renderer
	// (label "main") gets only the `MainRendererBubbleMutators` subset (no
	// `onSetState` / `onConfig` / `resizeTo` / `toggleDictation` —
	// matches `preload/index.ts`). The bubble window (label "bubble")
	// gets the full `BubbleWindowBubble`. The minimal `TauriGlobal`
	// type in `detect.ts` doesn't declare `label` on the
	// `getCurrentWindow()` return, so we cast here. Tauri v2's `Window`
	// object always exposes `label` as a public field set from
	// `tauri.conf.json`.
	const tauriWindow = tauri.window.getCurrentWindow() as unknown as {
		label?: string;
	};
	const windowLabel: "main" | "bubble" =
		tauriWindow.label === "bubble" ? "bubble" : "main";

	// SEC-026 (Tauri parity with preload/bubble.ts): the BUBBLE window is
	// a sandboxed renderer — Electron's preload never exposed
	// `window.python` / `window.window_` to it, and the Rust host's
	// window-guard rejects any `dispatch` from a non-main window anyway.
	// Installing the python namespace here made the bubble fire guaranteed
	// "rejected from non-main window" errors at startup (observed 7× on
	// 2026-08-30). The bubble gets ONLY `window.bubble`.
	if (windowLabel === "bubble") {
		window.bubble = createBubbleNamespace(tauri, windowLabel);
		return;
	}

	window.python = createPythonNamespace(tauri);
	window.bubble = createBubbleNamespace(tauri, windowLabel);
	window.window_ = createWindowNamespace(tauri);
}

export type { TauriEvent, TauriGlobal } from "./detect";
// Re-export the detector + factory so downstream tests / tools can
// introspect the bridge's runtime mode without duplicating the
// `window.__TAURI__` shape declaration. This also keeps the public
// symbol surface of `@/lib/tauri-bridge` stable for any external
// consumer that imports these names.
export { getTauri, isTauri, makeListener } from "./detect";

// NOTE: this module deliberately does NOT auto-invoke
// `installTauriBridge()` at module load. The auto-install side effect
// lives in the sibling `install.ts` module. Production entrypoints
// (`main.tsx`, `bubble-main.tsx`) import `./lib/tauri-bridge/install`
// explicitly to opt into the side effect; tests that exercise the
// auto-install behaviour do the same. Importing this `index.ts`
// module alone is side-effect-free.
