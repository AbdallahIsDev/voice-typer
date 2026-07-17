/**
 * Route a decoded JSON message received from the Python backend.
 *
 * Extracted from `index.ts` (REF-2).
 *
 * Two kinds of messages:
 *   - Replies (carry `id`): resolve/reject the matching entry in
 *     `pendingRequests` (set by `sendToPython`).
 *   - Push events (no `id`): bubble show/hide/set-state/level, show_window,
 *     quit_app, relaunch_electron.  Each is routed to the appropriate
 *     BrowserWindow via `webContents.send("python-event", msg)` (with
 *     SEC-017 filtering so transcription/history never leak to the bubble).
 */
import { app } from "electron";
import { BUBBLE_CLR, RESET, ts } from "../logging";
import { state } from "../state";
import { hideBubbleWindow, showBubbleWindow, showMainWindow } from "../windows";
import { relaunchApp } from "./relaunch-app";
import { sendToPython } from "./send-to-python";

export function handleMessage(msg: Record<string, unknown>) {
	if (msg.id != null) {
		const entry = state.pendingRequests.get(msg.id as number);
		if (entry) {
			state.pendingRequests.delete(msg.id as number);
			if (msg.type === "error") {
				entry.reject(
					new Error(
						((msg.data as Record<string, unknown>)?.message as string) ??
							"Unknown error",
					),
				);
			} else {
				entry.resolve(msg.data);
			}
		}
	} else {
		// Route Python push events.  Bubble events go ONLY to the bubble
		// window (not the main app) so the floating overlay updates without
		// re-rendering the sidebar.
		if (msg.type === "bubble_show") {
			console.warn(
				`${ts()}  ${BUBBLE_CLR}[BUBBLE] received bubble_show from Python${RESET}`,
			);
			showBubbleWindow();
		} else if (msg.type === "bubble_hide") {
			console.warn(
				`${ts()}  ${BUBBLE_CLR}[BUBBLE] received bubble_hide from Python${RESET}`,
			);
			hideBubbleWindow();
		} else if (msg.type === "bubble_set_state") {
			const state_ = (msg.data as Record<string, unknown>)?.state as string;
			console.warn(
				`${ts()}  ${BUBBLE_CLR}[BUBBLE] received bubble_set_state: ${state_}${RESET}`,
			);
			state.bubbleWindow?.webContents.send("bubble:set-state", state_);
		} else if (msg.type === "bubble_level") {
			state.bubbleWindow?.webContents.send("bubble:level", msg.data);
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
		} else if (msg.type === "relaunch_electron") {
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
			console.warn(
				`[RESTART] received relaunch_electron from Python (${_relaunchDbg})`,
			);
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
			(msg as Record<string, unknown>)._session_nonce = state.sessionNonce;
		}
		// SEC-017: previously this broadcast every Python event to every
		// window.  Transcription text and history records were thus sent
		// to the bubble window too — a data leak (the bubble only needs
		// waveform level + show/hide events).  Filter to the main window
		// only; the bubble gets its own dedicated channel for waveform.
		if (state.mainWindow && !state.mainWindow.isDestroyed()) {
			state.mainWindow.webContents.send("python-event", msg);
		}
	}
}
