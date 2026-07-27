/**
 * Route a decoded JSON message received from the Python backend.
 *
 * Extracted from `index.ts` (REF-2).
 *
 * Two kinds of messages:
 *   - Replies (carry `id`): resolve/reject the matching entry in
 *     `pendingRequests` (set by `sendToPython`).
 *   - Push events (no `id`): bubble show/hide/set-state/level, show_window,
 *     quit_app, relaunch_app.  Each is routed to the appropriate
 *     BrowserWindow via `webContents.send("python-event", msg)` (with
 *     SEC-017 filtering so transcription/history never leak to the bubble).
 */
import { app } from "electron";
// DE-87 / S2-CR-75: route Python push-event lifecycle messages
// through the structured `log` logger so they persist to
// `electron-main.log` (with 5 MiB rotation) and
// `electron-lifecycle.log` (opt-in INFO persistence) instead of
// being lost in packaged builds where `console.warn` has no terminal
// attached.
import { BUBBLE_CLR, log, RESET, ts } from "../logging";
import { state } from "../state";
import { hideBubbleWindow, showBubbleWindow, showMainWindow } from "../windows";
// GT-A3-8: broadcastToMainWindow imported directly from main-window
// (windows/index.ts is owned by another sub-agent and doesn't re-export it).
import { broadcastToMainWindow } from "../windows/main-window";
import { relaunchApp } from "./relaunch-app";
import { sendToPython } from "./send-to-python";

// T2-005: explicit `void` return type — `handleMessage` always returns
// synchronously and callers (tcp-connect.ts, sidecar ws reader) ignore
// the return value. Pinning `void` prevents a future contributor from
// accidentally `return`-ing a Promise/value and expecting it to be
// awaited (it isn't).
export function handleMessage(msg: Record<string, unknown>): void {
	// Narrow `msg.id` with `typeof` so the `Map<number, …>.get()` lookup
	// receives a real `number` (no `as number` cast). Non-number ids are
	// treated as push events — matching the original `msg.id != null`
	// happy path (ids are always numeric on the wire) while refusing to
	// silently coerce stray shapes.
	if (typeof msg.id === "number") {
		const entry = state.pendingRequests.get(msg.id);
		if (entry) {
			state.pendingRequests.delete(msg.id);
			if (msg.type === "error") {
				const errData =
					typeof msg.data === "object" && msg.data !== null
						? (msg.data as Record<string, unknown>)
						: {};
				const message =
					typeof errData.message === "string"
						? errData.message
						: "Unknown error";
				const err = new Error(message);
				// PVT-G5-012 sub-finding: previously only `message` was
				// surfaced on the rejected Error — `data.code` was discarded.
				// The Python backend emits structured `code` values
				// (`unknown_command`, `internal_error`, `rate_limited`,
				// `invalid_field`, `missing_field`, `unknown_tray_item`)
				// precisely so renderer code can branch on them. Attach
				// `code` (and the optional `field`/`command`/`id` context
				// fields) to the Error so consumers can do
				// `if ((err as any).code === "rate_limited") ...` instead of
				// pattern-matching the human-readable message string.
				// GT-D2-6: avoid the unsafe `as string | undefined` cast. Narrow
				// with `typeof` so only string codes are attached; any other shape
				// (number, object, array) is treated as undefined.
				const code =
					typeof errData.code === "string" ? errData.code : undefined;
				if (code !== undefined) {
					(err as Error & { code?: string }).code = code;
				}
				if (errData.field !== undefined) {
					(err as Error & { field?: unknown }).field = errData.field;
				}
				if (errData.command !== undefined) {
					(err as Error & { command?: unknown }).command = errData.command;
				}
				if (errData.id !== undefined) {
					(err as Error & { id?: unknown }).id = errData.id;
				}
				entry.reject(err);
			} else {
				entry.resolve(msg.data);
			}
		}
	} else {
		// Route Python push events.  Bubble events go ONLY to the bubble
		// window (not the main app) so the floating overlay updates without
		// re-rendering the sidebar.
		if (msg.type === "bubble_show") {
			log.info(
				`${ts()}  ${BUBBLE_CLR}[BUBBLE] received bubble_show from Python${RESET}`,
			);
			showBubbleWindow();
		} else if (msg.type === "bubble_hide") {
			log.info(
				`${ts()}  ${BUBBLE_CLR}[BUBBLE] received bubble_hide from Python${RESET}`,
			);
			hideBubbleWindow();
		} else if (msg.type === "bubble_set_state") {
			// GT-D2-6: avoid the chained `as Record<string, unknown> as string`
			// cast chain. Coerce via `String(...)` after narrowing so the
			// renderer always gets a string on its `bubble:set-state` channel.
			const rawData =
				typeof msg.data === "object" && msg.data !== null
					? (msg.data as Record<string, unknown>)
					: undefined;
			const rawState = rawData?.state;
			const state_ = typeof rawState === "string" ? rawState : String(rawState);
			log.info(
				`${ts()}  ${BUBBLE_CLR}[BUBBLE] received bubble_set_state: ${state_}${RESET}`,
			);
			state.bubbleWindow?.webContents.send("bubble:set-state", state_);
		} else if (msg.type === "bubble_level") {
			state.bubbleWindow?.webContents.send("bubble:level", msg.data);
		} else if (msg.type === "bubble_config") {
			// UX-10: bubble-relevant config (bubble_behavior /
			// bubble_click_to_toggle / bubble_mic_button) pushed from the
			// Python backend so the sandboxed bubble renderer (which has
			// no get_config) knows whether to show its mic button.
			state.bubbleWindow?.webContents.send(
				"bubble:config",
				typeof msg.data === "object" && msg.data !== null
					? (msg.data as Record<string, unknown>)
					: {},
			);
		} else if (msg.type === "show_window") {
			// Tray "Open app": Python asks us to show + focus the dashboard.
			// Single hop over the always-up TCP channel; falls back to the
			// Win32 EnumWindows path in tray.open_electron_window() if this
			// never arrives (TCP momentarily down).
			showMainWindow();
		} else if (msg.type === "quit_app") {
			// Tray "Quit": Python is about to force-exit.  Close Electron too
			// so the user isn't left with a window that has no backend.
			app.quit();
		} else if (msg.type === "relaunch_app") {
			// Tray "Restart": Python's restart_app() pushes this event
			// BEFORE calling sys.exit(0).  It signals that a full
			// application restart is in flight.  We respond by
			// relaunching the entire Electron process (which in
			// turn spawns a fresh Python backend).
			//
			// RESTART-DEBUG: log the exact state when this event arrives
			// so we can trace the full restart flow in the terminal.
			const _relaunchDbg = state._relaunching
				? "already relaunching"
				: "triggering relaunch";
			log.info(`[RESTART] received relaunch_app from Python (${_relaunchDbg})`);
			// PERF-005: ack receipt BEFORE relaunchApp() tears down the
			// renderer/socket, so restart_app() can drop its fixed 300ms
			// sleep in favour of an event-driven wait.  Best-effort: if the
			// socket is already down or sendToPython rejects, the server
			// simply falls back to its 2s timeout — no behaviour change.
			sendToPython({ type: "relaunch_ack" }).catch(() => {});
			relaunchApp();
		}
		// SEC-029: tag each python-event with a per-session nonce so the
		// renderer can detect replayed frames from an unauthenticated TCP
		// attacker (SEC-018). The nonce is generated once per Electron
		// session and stored in this module-level variable. The renderer
		// compares the nonce on each event and drops any that don't match.
		if (!msg._session_nonce && state.sessionNonce) {
			msg._session_nonce = state.sessionNonce;
		}
		// SEC-017: previously this broadcast every Python event to every
		// window.  Transcription text and history records were thus sent
		// to the bubble window too — a data leak (the bubble only needs
		// waveform level + show/hide events).  Filter to the main window
		// only; the bubble gets its own dedicated channel for waveform.
		// GT-A3-8: route through broadcastToMainWindow instead of calling
		// webContents.send directly. Centralizes CR-28 pythonReady flip +
		// destroyed-window guard.
		broadcastToMainWindow("python-event", msg);
	}
}
