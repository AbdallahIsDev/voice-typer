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
import { app, Notification } from "electron";
// PythonIpcError is the typed error class the python-call-handler
// checks via `instanceof`. Constructing it here (instead of a bare
// `new Error(message)` with `code` attached ad-hoc) preserves the
// `err.code` field through the handler's `instanceof PythonIpcError`
// branch so the renderer receives the correct `_code` instead of the
// generic `"command_failed"` fallback.
import type { PythonCallErrorCode } from "../../shared/python-call-error-code";
import {
	BUBBLE_ONLY_TYPES,
	setLastKnownBubbleMode,
} from "../ipc/bubble-handlers";
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
import { setPersistedBubblePosition } from "../windows/bubble/positioning";
//broadcastToMainWindow imported directly from main-window
// (windows/index.ts is owned by another sub-agent and doesn't re-export it).
import { broadcastToMainWindow } from "../windows/main-window";
import { PythonIpcError } from "./errors";
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
		// Cache the bubble mode at the source — BEFORE the
		// `webContents.send`. The dismiss handler reads this
		// cached mode to decide whether to send
		// `toggle_dictation` before stopping the pipeline. The
		// previous design monkey-patched `webContents.send`
		// inside the `bubble:ready` handler to intercept outgoing
		// `bubble:set-state` sends; that patch accumulated on
		// every bubble reload. Updating at the source eliminates
		// the patch entirely.
		setLastKnownBubbleMode(state_);
		// Forward the FULL payload when the backend additionally
		// carries `message` (error reason) or `transcript`
		// (live partial text, XA-6-2) — falling back to the bare
		// state string for legacy state-only payloads so existing
		// renderers + tests keep receiving the minimal shape. The
		// renderer's `parseSetStatePayload` accepts both.
		const hasOptionalFields =
			rawData !== undefined &&
			("message" in rawData || "transcript" in rawData);
		const payload: unknown = hasOptionalFields ? rawData : state_;
		state.bubbleWindow?.webContents.send(BubbleChannels.setState, payload);
	},
	bubble_level: (msg) => {
		state.bubbleWindow?.webContents.send(BubbleChannels.level, msg.data);
	},
	bubble_config: (msg) => {
		// Cache the persisted drag position (Python config's optional
		// `bubble_x` / `bubble_y` pair) so bubble placement can restore
		// it across app restarts. Both fields must be finite numbers;
		// anything else (nulls after a Settings edge-toggle reset,
		// missing keys, non-numeric junk) clears the cache.
		const cfgData =
			typeof msg.data === "object" && msg.data !== null
				? (msg.data as Record<string, unknown>)
				: {};
		const px = cfgData.bubble_x;
		const py = cfgData.bubble_y;
		if (
			typeof px === "number" &&
			Number.isFinite(px) &&
			typeof py === "number" &&
			Number.isFinite(py)
		) {
			setPersistedBubblePosition({ x: px, y: py });
		} else {
			setPersistedBubblePosition(null);
		}
		state.bubbleWindow?.webContents.send(BubbleChannels.config, cfgData);
	},
	show_window: () => {
		log.info(
			`${ts()}  [TRAY] show_window received from Python — showing + raising dashboard window to front`,
		);
		showMainWindow();
	},
	// OS notification events from the Python backend (the
	// ``show_electron_notification`` IPC handler + the last-resort
	// unloaded-backend tray path). Shown as a NATIVE Electron toast so
	// the user gets a real OS notification (pystray Win32 balloons
	// cannot carry click handlers). When the payload carries a
	// ``click_path`` (e.g. ``"/models"``), clicking the toast opens the
	// main window and routes the renderer to that page via the same
	// ``navigate`` python-event the tray menu uses.
	notification: (msg) => {
		const data =
			typeof msg.data === "object" && msg.data !== null
				? (msg.data as Record<string, unknown>)
				: {};
		const title = typeof data.title === "string" ? data.title : "";
		const body = typeof data.message === "string" ? data.message : "";
		const clickPath =
			typeof data.click_path === "string" ? data.click_path : undefined;
		// ``click_consent_field`` — a consent-gate notification (e.g.
		// the voice-biometric dictation gate) deep-links to the EXACT
		// Settings consent row instead of a plain page: clicking the
		// toast broadcasts navigate {path:"/settings",
		// consent_field}, and App.tsx's navigate handler forwards the
		// field to Settings' ``pendingConsentField`` deep-link
		// (scroll-to + highlight). Takes precedence over ``click_path``
		// when both are present (a consent refusal is always about the
		// specific toggle).
		const clickConsentField =
			typeof data.click_consent_field === "string"
				? data.click_consent_field
				: undefined;
		const durationMs =
			typeof data.duration_ms === "number" ? data.duration_ms : 0;
		if (!title && !body) {
			log.debug(
				`${ts()}  [NOTIFY] notification event with empty title/body — skipping`,
			);
			return;
		}
		// ``Notification.isSupported()`` is the Electron-gated check
		// (false on Linux without a notification daemon, headless
		// sessions, etc.). Skip the native toast but keep the renderer
		// broadcast (the fall-through below) so consumers that surface
		// an in-app toast still get the event.
		if (!Notification.isSupported()) {
			log.debug(
				`${ts()}  [NOTIFY] native notifications unsupported — skipping toast (title=${title})`,
			);
			return;
		}
		const notif = new Notification({ title, body });
		if (clickPath || clickConsentField) {
			// Clicking the toast opens the main window and routes to the
			// target (a plain page via ``click_path``, or the exact
			// Settings consent row via ``click_consent_field``).
			// SEC-029: the synthetic ``navigate`` event must carry the
			// session nonce or the renderer drops it as a replayed frame.
			notif.on("click", () => {
				log.info(
					`${ts()}  [NOTIFY] notification clicked — opening ${
						clickConsentField
							? `settings consent field ${clickConsentField}`
							: (clickPath ?? "")
					}`,
				);
				// ``showMainWindow`` runs BEFORE the broadcast so the
				// window exists (``broadcastToMainWindow``'s guard drops
				// sends to a missing/destroyed window). In the Electron
				// flow the window is created on the first TCP connect and
				// the React tree (incl. the ``navigate`` listener in
				// App.tsx) mounts while hidden, so by the time a toast can
				// be clicked the listener is live — same convention as the
				// tray ``open_models_page`` path.
				showMainWindow();
				broadcastToMainWindow(PythonChannels.event, {
					type: "navigate",
					data:
						clickConsentField !== undefined
							? { path: "/settings", consent_field: clickConsentField }
							: { path: clickPath },
					_session_nonce: state.sessionNonce ?? undefined,
				});
			});
		}
		notif.show();
		// ``duration_ms`` from the payload drives an auto-close (the
		// ``show_electron_notification`` contract); 0 / absent = persist
		// until the OS or user dismisses.
		if (durationMs > 0) {
			setTimeout(() => {
				try {
					notif.close();
				} catch {
					// already closed/dismissed — safe to ignore
				}
			}, durationMs);
		}
	},
	quit_app: () => {
		app.quit();
	},
	relaunch_app: () => {
		const _relaunchDbg = state._relaunching
			? "already relaunching"
			: "triggering relaunch";
		log.info(`[RESTART] received relaunch_app from Python (${_relaunchDbg})`);
		// surface the ack-write failure at debug level
		// instead of silently swallowing. The ack is best-effort
		// (the backend proceeds to sys.exit(0) regardless), but a
		// failure here is worth logging for diagnostics — the
		// previous `.catch(() => {})` hid genuine socket-closed
		// errors during the teardown race.
		sendToPython({ type: "relaunch_ack" }).catch((e) =>
			log.debug("[IPC] relaunch_ack failed:", e),
		);
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
				// construct a typed `PythonIpcError` when
				// `errData.code` is a string so the
				// `python-call-handler`'s `instanceof PythonIpcError`
				// check passes and `err.code` propagates to the
				// renderer's `_code` field. Previously a bare
				// `new Error(message)` was constructed with `code`
				// attached ad-hoc, but `python-call-handler` checks
				// `instanceof PythonIpcError` — the ad-hoc `code` was
				// lost (the handler fell back to the generic
				// `"command_failed"` classification for EVERY
				// Python-side error, even timeouts).
				//
				// The Python backend emits structured `code` values
				// (`unknown_command`, `internal_error`, `rate_limited`,
				// `invalid_field`, `missing_field`, `unknown_tray_item`,
				// and potentially `command_timeout`); constructing a
				// `PythonIpcError` preserves whichever of these the
				// backend sent so downstream consumers branching on
				// `err.code` see the real value. The cast is necessary
				// because `PythonIpcError.code` is typed as the finite
				// `PythonCallErrorCode` union, but the Python side may
				// emit codes outside that union — at runtime the field
				// is just a string, so the cast is sound.
				//
				//sub-finding: previously only `message` was
				// surfaced on the rejected Error — `data.code` was discarded.
				// Attach the optional `field`/`command`/`id` context
				// fields too so consumers can do
				// `if ((err as any).code === "rate_limited") ...` instead of
				// pattern-matching the human-readable message string.
				//avoid the unsafe `as string | undefined` cast. Narrow
				// with `typeof` so only string codes are attached; any other shape
				// (number, object, array) is treated as undefined.
				const code =
					typeof errData.code === "string" ? errData.code : undefined;
				const err: Error & {
					code?: string;
					field?: unknown;
					command?: unknown;
					id?: unknown;
				} =
					code !== undefined
						? new PythonIpcError(code as PythonCallErrorCode, message)
						: new Error(message);
				// `PythonIpcError` already sets `.code` in its
				// constructor; the bare-`Error` branch leaves it
				// undefined. Re-assigning here is harmless and keeps
				// both branches uniform so the optional context
				// fields below attach to a consistent shape.
				if (code !== undefined) {
					err.code = code;
				}
				if (errData.field !== undefined) {
					err.field = errData.field;
				}
				if (errData.command !== undefined) {
					err.command = errData.command;
				}
				if (errData.id !== undefined) {
					err.id = errData.id;
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
