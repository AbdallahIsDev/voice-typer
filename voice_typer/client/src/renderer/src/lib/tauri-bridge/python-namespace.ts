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
// `cancelled` flag (the primary `python-event` channel + two relay
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
		// CR-Finding 5: also listen for host events
		// (`supervisor_relaunching`, `supervisor_reconnected`) and synthesize
		// `python-event` frames so `useConnection` updates the UI during
		// respawn cycles. Without this, the renderer's connection
		// status stays "connected" while the sidecar is dead, and the
		// user sees a frozen UI with no feedback.
		//
		//when `supervisor_relaunching` carries
		// `reason: "backoff_exhausted"` (emitted by `supervisor.rs:495`
		// right before the full-app `app.restart()`), synthesize an
		// `error` event instead of a `reconnecting` event — the
		// supervisor has given up retrying and the renderer must flip
		// to `"disconnected"` with a user-facing error message rather
		// than staying stuck on the transient `"restarting"` UI. The
		// `useConnection` `error` handler is enhanced to also call
		// `setConnectionStatus("disconnected")` when the message
		// matches the respawn-exhausted sentinel so the user sees the
		// "Lost connection" screen with the cause instead of an
		// indefinite "Restarting…" banner.
		//
		//the handlers below used to cast the synthesized
		// event via `as unknown as PythonPushEvent` because the
		// `PythonPushEvent` union previously lacked `ReconnectingEvent`
		//and `ReconnectedEvent` members.  has since added both
		// members, so the object literals now type-check directly —
		// the stale casts and TODOs were removed. The cast on the
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
			const supervisorRelaunching = makeListener<PythonPushEvent>(
				(handler) =>
					tauri.event.listen("supervisor_relaunching", (e) => {
						//inspect the reason payload to distinguish
						// a transient relaunch (status="restarting") from a
						// terminal exhaustion (status="disconnected" with a
						// user-facing error). The supervisor emits
						// `{"reason": "backoff_exhausted"}` from
						// `supervisor.rs:495` immediately before calling
						// `app.restart()`. Other reason values (e.g.
						// `"tcp_disconnected"`, `"ws_closed"`) indicate a
						// transient relaunch that should keep the UI in the
						// `"restarting"` state.
						const payload = (e.payload ?? {}) as { reason?: string };
						const reason =
							typeof payload?.reason === "string" ? payload.reason : "";
						if (reason === "backoff_exhausted") {
							const event: PythonPushEvent = {
								type: "error",
								data: {
									// Structured code: useConnection's `error`
									// handler checks for this code to flip
									// `connectionStatus` to `"disconnected"`
									// and surface a localized message via
									// `t("connection.respawnFailed")`.
									code: "respawn_exhausted" as never,
									message: "respawn exhausted",
								},
							};
							handler(event);
							return;
						}
						const event: PythonPushEvent = {
							type: "reconnecting",
							data: { reason: reason || "supervisor_relaunching" },
						};
						handler(event);
					}),
				callback,
			);
			const supervisorReconnected = makeListener<PythonPushEvent>(
				(handler) =>
					tauri.event.listen("supervisor_reconnected", () => {
						const event: PythonPushEvent = {
							type: "reconnected",
							data: { reason: "supervisor_reconnected" },
						};
						handler(event);
					}),
				callback,
			);
			//also listen for the circuit-breaker event
			// emitted by `supervisor.rs:207-221` when the disk-
			// persisted restart counter reaches
			// ``MAX_RESTART_ATTEMPTS``. The supervisor stops
			// retrying and emits ``supervisor_failed`` with
			// ``reason: "circuit_breaker_tripped"`` plus a
			// user-facing reinstall prompt in ``message``. The
			// renderer must transition to the terminal
			// ``"disconnected"`` state (with the supervisor's
			// message as ``lastError``) so the user sees the
			// "Lost connection" screen with the reinstall
			// prompt, instead of staying stuck on the
			// transient ``"restarting"`` banner. We synthesise
			// the same ``"respawn exhausted"`` sentinel
			// substring so ``useConnection``'s ``error`` handler
			// flips the connection status — the supervisor's
			// ``message`` field is appended so the user-facing
			// text in ``lastError`` carries the reinstall
			// instructions verbatim.
			const supervisorFailed = makeListener<PythonPushEvent>(
				(handler) =>
					tauri.event.listen("supervisor_failed", (e) => {
						const payload = (e.payload ?? {}) as {
							reason?: string;
							message?: string;
							restart_count?: number;
						};
						const supervisorMessage =
							typeof payload?.message === "string" ? payload.message : "";
						const event: PythonPushEvent = {
							type: "error",
							data: {
								// Structured code: useConnection's `error`
								// handler checks for this code to flip
								// ``connectionStatus`` to
								// ``"disconnected"`` and surface a
								// localized message via
								// ``t("connection.respawnFailed")``.
								// The supervisor's user-facing
								// ``message`` (reinstall prompt)
								// is carried in `message` so the
								// hook can log it for diagnostics.
								code: "respawn_exhausted" as never,
								message: supervisorMessage
									? `respawn exhausted: ${supervisorMessage}`
									: "respawn exhausted",
							},
						};
						handler(event);
					}),
				callback,
			);
			return () => {
				mainListener();
				supervisorRelaunching();
				supervisorReconnected();
				supervisorFailed();
			};
		},
	};
}
