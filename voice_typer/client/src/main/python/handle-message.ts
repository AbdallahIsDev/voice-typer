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
import { BUBBLE_ONLY_TYPES } from "../ipc/bubble-handlers";
import { BubbleChannels, PythonChannels } from "../ipc/channels";
//route Python push-event lifecycle messages
// through the structured `log` logger so they persist to
// `electron-main.log` (with 5 MiB rotation) and
// `electron-lifecycle.log` (opt-in INFO persistence) instead of
// being lost in packaged builds where `console.warn` has no terminal
// attached.
import { BUBBLE_CLR, log, RESET, ts } from "../logging";
import { state } from "../state";
import { hideBubbleWindow, showBubbleWindow, showMainWindow } from "../windows";
//broadcastToMainWindow imported directly from main-window
// (windows/index.ts is owned by another sub-agent and doesn't re-export it).
import { broadcastToMainWindow } from "../windows/main-window";
import { relaunchApp } from "./relaunch-app";
import { sendToPython } from "./send-to-python";

//dispatch table for the 8 push-event types. Replaces the prior
// 79-line if-else chain so a 9th event type is a one-line addition to
// `PUSH_HANDLERS` instead of a new `else if` branch with all of the
// behavioural copy/pasted. Each handler receives the already-narrowed
// `msg` (a `Record<string, unknown>`); handlers are responsible for
// their own `msg.data` validation (matching the prior per-branch
// behaviour).
//
//handlers are keyed on the exact `msg.type` string. Unknown
// string-typed event types fall through past the dispatch to the
// broadcast below — preserving back-compat with renderer code that
// listens on `python-event` for types not explicitly handled here (e.g.
// future event types added on the Python side before a corresponding
// handler is wired up here). Non-string `msg.type` values are dropped
// with a warning before dispatch (see `handleMessage`).
type PushHandler = (msg: Record<string, unknown>) => void;

const PUSH_HANDLERS: Record<string, PushHandler> = {
	bubble_show: () => {
		log.info(
			`${ts()}  ${BUBBLE_CLR}[BUBBLE] received bubble_show from Python${RESET}`,
		);
		showBubbleWindow();
	},
	bubble_hide: () => {
		log.info(
			`${ts()}  ${BUBBLE_CLR}[BUBBLE] received bubble_hide from Python${RESET}`,
		);
		hideBubbleWindow();
	},
	bubble_set_state: (msg) => {
		const rawData =
			typeof msg.data === "object" && msg.data !== null
				? (msg.data as Record<string, unknown>)
				: undefined;
		const rawState = rawData?.state;
		const state_ = typeof rawState === "string" ? rawState : String(rawState);
		log.info(
			`${ts()}  ${BUBBLE_CLR}[BUBBLE] received bubble_set_state: ${state_}${RESET}`,
		);
		state.bubbleWindow?.webContents.send(BubbleChannels.setState, state_);
	},
	bubble_level: (msg) => {
		state.bubbleWindow?.webContents.send(BubbleChannels.level, msg.data);
	},
	bubble_config: (msg) => {
		state.bubbleWindow?.webContents.send(
			BubbleChannels.config,
			typeof msg.data === "object" && msg.data !== null
				? (msg.data as Record<string, unknown>)
				: {},
		);
	},
	show_window: () => {
		showMainWindow();
	},
	quit_app: () => {
		app.quit();
	},
	relaunch_app: () => {
		const _relaunchDbg = state._relaunching
			? "already relaunching"
			: "triggering relaunch";
		log.info(`[RESTART] received relaunch_app from Python (${_relaunchDbg})`);
		sendToPython({ type: "relaunch_ack" }).catch(() => {});
		relaunchApp();
	},
};

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
				//sub-finding: previously only `message` was
				// surfaced on the rejected Error — `data.code` was discarded.
				// The Python backend emits structured `code` values
				// (`unknown_command`, `internal_error`, `rate_limited`,
				// `invalid_field`, `missing_field`, `unknown_tray_item`)
				// precisely so renderer code can branch on them. Attach
				// `code` (and the optional `field`/`command`/`id` context
				// fields) to the Error so consumers can do
				// `if ((err as any).code === "rate_limited") ...` instead of
				// pattern-matching the human-readable message string.
				//avoid the unsafe `as string | undefined` cast. Narrow
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
		// ── Push events (no `msg.id`) ───────────────────────────────────
		// Route Python push events.  Bubble events go ONLY to the bubble
		// window (not the main app) so the floating overlay updates without
		// re-rendering the sidebar.

		//type guard. If `msg.type` is not a string (e.g. a
		// malformed message with `type: null` or `type: 42`), drop it with
		// a warning instead of letting it fall through to the renderer
		// broadcast — a non-string type can never match a handler and the
		// renderer's `python-event` listeners key on `msg.type` as a string.
		if (typeof msg.type !== "string") {
			log.warn(
				`${ts()}  [TCP] push event missing type string, dropping: ${JSON.stringify(msg.type)}`,
			);
			return;
		}

		//dispatch via the lookup table. Unknown (but string-typed)
		// event types intentionally fall through to the broadcast below —
		// preserves back-compat with renderer code that listens on
		// `python-event` for types not explicitly handled here.
		const handler = PUSH_HANDLERS[msg.type];
		if (handler !== undefined) {
			handler(msg);
		}

		//bubble-only events (bubble_show / bubble_hide /
		// bubble_set_state / bubble_level / bubble_config) are consumed
		// entirely by the bubble window (the dispatch handler above already
		// routed them). They MUST NOT be broadcast to the main window
		// renderer — doing so causes 30-60 Hz IPC churn (one `bubble_level`
		// per audio frame while recording) and contradicts the SEC-017
		// comment at the top of this block. The filter set is sourced from
		// `bubble-handlers.ts` so the bubble-IPC module remains the single
		// source of truth for "which event types belong to the bubble".
		if (BUBBLE_ONLY_TYPES.has(msg.type)) {
			return;
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
		//route through broadcastToMainWindow instead of calling
		//webContents.send directly. Centralizes  pythonReady flip +
		// destroyed-window guard.
		broadcastToMainWindow(PythonChannels.event, msg);
	}
}
