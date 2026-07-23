// src/renderer/src/lib/tauri-bridge/python-namespace.ts
//
// ADR-0020 §6.3 (Phase 3 UI port): `window.python` installer for the
// Tauri runtime.
//
// Contract preserved (identical on both Tauri and Electron paths):
//   • `window.python.call({type, data}) → Promise<data>` — dispatches
//     an IPC command to the Python sidecar. On Tauri this routes
//     through `invoke('dispatch', {cmd: type, data})`; the Rust host
//     forwards it over WS to the sidecar and returns `response.data`.
//     Rejects on `type:"error"` envelopes (Rust at main.rs:515).
//   • `window.python.onEvent(callback) → () => void` — subscribes to
//     all server-initiated events. On Tauri this listens to the
//     `python-event` Tauri event (emitted by main.rs:455 with
//     `{type, data}` envelope).
//
// The previous version inlined a 60-line nested cancellation block
// here because `onEvent` subscribes to THREE Tauri events with a shared
// `cancelled` flag (the primary `python-event` channel + two FT-1 relay
// channels). Using `makeListener` per channel collapses each
// subscription to ~5 LOC and lets each listener own its own
// cancellation state — the shared flag is no longer needed because
// each listener's cleanup is independent.

import type { PythonBridge, PythonPushEvent } from "@/types/ipc";

import { makeListener, type TauriGlobal } from "./detect";

/**
 * Build the `window.python` namespace using Tauri's global API.
 *
 * Idempotent at the orchestrator level — `installTauriBridge()` checks
 * `window.python` before calling this. The returned object is a fresh
 * allocation each call (no shared state), so HMR re-imports are safe.
 */
export function createPythonNamespace(tauri: TauriGlobal): PythonBridge {
	return {
		// `call` → `invoke('dispatch', {cmd, data})`. The Rust `dispatch`
		// command (main.rs:484) forwards to the sidecar via WS, awaits
		// the per-id response, and returns `response.data` on success
		// or rejects with an error string on `type:"error"` (main.rs:515).
		// The shape matches Electron's `sendToPython` which resolves
		// with `msg.data` (index.ts:436).
		call: (msg) =>
			tauri.core.invoke("dispatch", {
				cmd: msg.type,
				data: msg.data ?? {},
			}),

		// The Rust host emits `python-event` with `{type, data}` envelope
		// for every server-initiated event (main.rs:455). This matches
		// Electron's `python-event` IPC channel.
		//
		// CR-Finding 5: also listen for FT-1 host events
		// (`ft1_relaunching`, `ft1_reconnected`) and synthesize
		// `python-event` frames so `useConnection` updates the UI during
		// FT-1 respawn cycles. Without this, the renderer's connection
		// status stays "connected" while the sidecar is dead, and the
		// user sees a frozen UI with no feedback.
		//
		// Cast through unknown because the Tauri event payload is
		// structurally `{type: string, data: ...}` while
		// `PythonPushEvent` is a discriminated union with literal `type`
		// members — the runtime shape is identical.
		//
		// EC-FIX-7 will add `ReconnectingEvent` and `ReconnectedEvent` to
		// the `PythonPushEvent` union (rule #26 — every IPC message must
		// have matching send/receive type definitions). Once those types
		// land, the FT-1 casts below can be removed (see TODOs on each
		// handler). The `as unknown as PythonPushEvent` cast on the
		// `python-event` channel itself stays — the Rust host forwards
		// arbitrary server events whose `type` field may not be in the
		// union (e.g. legacy / unknown events).
		onEvent: (callback) => {
			const mainListener = makeListener<PythonPushEvent>(
				(handler) =>
					tauri.event.listen("python-event", (e) => {
						handler(e.payload as unknown as PythonPushEvent);
					}),
				callback,
			);
			const ft1Relaunching = makeListener<PythonPushEvent>(
				(handler) =>
					tauri.event.listen("ft1_relaunching", () => {
						// TODO: remove cast once ReconnectingEvent / ReconnectedEvent
						// are added to the PythonPushEvent union (EC-FIX-7). EC-FIX-7
						// will widen the union to include
						// `{ type: "reconnecting"; data: { reason: string } }`, at
						// which point this object literal will type-check directly.
						// Until then, the runtime shape is correct (useConnection.ts
						// subscribes to "reconnecting" by string key) — only the
						// static type is too narrow. The `as unknown as` cast is
						// load-bearing for compilation; do NOT remove without EC-FIX-7.
						const event = {
							type: "reconnecting",
							data: { reason: "ft1_relaunching" },
						} as unknown as PythonPushEvent;
						handler(event);
					}),
				callback,
			);
			const ft1Reconnected = makeListener<PythonPushEvent>(
				(handler) =>
					tauri.event.listen("ft1_reconnected", () => {
						// TODO: remove cast once ReconnectingEvent / ReconnectedEvent
						// are added to the PythonPushEvent union (EC-FIX-7). See the
						// matching TODO on the `ft1_relaunching` handler above for
						// the full rationale.
						const event = {
							type: "reconnected",
							data: { reason: "ft1_reconnected" },
						} as unknown as PythonPushEvent;
						handler(event);
					}),
				callback,
			);
			return () => {
				mainListener();
				ft1Relaunching();
				ft1Reconnected();
			};
		},
	};
}
