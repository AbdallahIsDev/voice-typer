/**
 * Shared mutable state for the Electron main process.
 *
 * Extracted from `index.ts` (REF-2) so that the python/, windows/, ipc/,
 * and bootstrap/ modules can read and write the same process-level
 * variables without re-declaring them.
 *
 * All fields are intentionally `let`-style mutable properties on a single
 * exported object — this preserves the exact runtime semantics of the
 * original module-level `let` declarations (every reader sees the latest
 * value written by any writer).
 *
 * Dead-code cleanup: the `preMaximizeBounds`
 * field was removed. It used to live on `state` but is now a local
 * module-level variable inside `ipc/window-handlers.ts` (de-shadowed
 * in a prior refactor). No reader/writer of
 * `state.preMaximizeBounds` remained — only the local `preMaximizeBounds`
 * in window-handlers.ts is used. 4 test fixtures still include
 * `preMaximizeBounds: null` for backward-compat; they compile due to
 * `as MainState` casts (the extra property is silently ignored at
 * runtime). They should be cleaned up separately (P3).
 */
import type { ChildProcess } from "node:child_process";
import type { Socket } from "node:net";
import type { BrowserWindow } from "electron";

/**
 * Pending IPC request awaiting a response from the Python backend.
 * `sendToPython` stores one of these per outbound message id; the TCP
 * `data` handler resolves/rejects it when the matching reply arrives.
 */
export interface PendingRequest {
	resolve: (value: unknown) => void;
	reject: (reason: unknown) => void;
}

/**
 * Hard ceiling on the number of simultaneously-pending IPC
 * requests. Each pending entry pins a `setTimeout` closure that
 * captures the `msg`, `resolve`, `reject`, and `timer` — without a cap,
 * a buggy or compromised renderer polling `python-call` at 60 Hz for 2
 * minutes retains ~7,200 live entries × ~120s timers (tens of MB of
 * closures). The cap is generous enough for genuine concurrent IPC
 * (model downloads + history fetches + config writes rarely exceed a
 * few dozen in flight) and rejects flooders fast with a structured
 * error rather than letting the Map grow unbounded.
 */
export const MAX_PENDING_REQUESTS = 256;

/**
 * Per-renderer rate limit (calls per `RATE_LIMIT_WINDOW_MS`).
 * A single compromised renderer firing `python-call` in a tight loop
 * would otherwise exhaust the `MAX_PENDING_REQUESTS` budget before the
 * legitimate renderer's next call lands. The limit is per-`webContents`
 * (keyed by `webContents.id`) so two windows can each make their own
 * share of calls without one starving the other.
 */
export const RATE_LIMIT_MAX_CALLS = 120;
export const RATE_LIMIT_WINDOW_MS = 1000;

/**
 * Augment Electron's App interface with isQuitting so the close-to-tray
 * handler can distinguish a real quit (tray Quit → let the window close)
 * from the X button (→ hide instead).
 *
 * The electron module uses `export =`, so we must augment via the global
 * Electron namespace rather than `declare module "electron"`.
 */
declare global {
	namespace Electron {
		interface App {
			isQuitting?: boolean;
		}
	}
}

export interface MainState {
	/** Spawned Python backend process (null if VT_PYTHON_PORT adopted an existing one). */
	pythonProcess: ChildProcess | null;
	/** Authenticated TCP socket to the Python backend. Null until the auth line is written. */
	tcpSocket: Socket | null;
	/** Dashboard BrowserWindow (lazy-created on first TCP connect). */
	mainWindow: BrowserWindow | null;
	/** Bubble overlay BrowserWindow (lazy-created on first bubble_show). */
	bubbleWindow: BrowserWindow | null;
	/** Outbound IPC requests awaiting a Python reply, keyed by message id. */
	pendingRequests: Map<number, PendingRequest>;
	/** Monotonic message-id counter for sendToPython(). */
	nextId: number;
	/** Incomplete TCP line accumulator (Python sends newline-delimited JSON). */
	tcpBuffer: string;
	/** True once the first TCP connect succeeded (gates `pythonExitedEarly` handling). */
	pythonReady: boolean;
	/** True if Python exited before the first connect — surfaces a clear error to the user. */
	pythonExitedEarly: boolean;
	/** RW-10 heartbeat interval handle (5s tick). Cleared on TCP close / stopPython / relaunch. */
	heartbeatInterval: ReturnType<typeof setInterval> | null;
	/** SEC-029 per-session nonce tagged onto every python-event so the renderer can reject replays. */
	sessionNonce: string;
	/** Bubble screen position preference ("top" | "bottom"), synced from renderer via bubble:set-position. */
	bubblePosition: "top" | "bottom";
	/** Config-driven toggle for whether the bubble pill is draggable. Synced to the bubble renderer. */
	bubbleDraggable: boolean;
	/** Pending hide-animation timeout for the bubble (cancelled on rapid re-show). */
	_hideTimeout: ReturnType<typeof setTimeout> | null;
	/** tryConnect() retry counter (for log messaging + exponential backoff). */
	_tcpRetryCount: number;
	/** Monotonic generation counter — bumped by startPython() to invalidate stale retry loops. */
	_tcpRetryGeneration: number;
	/** R6-F6: pending TCP retry timer handle (cleared by stopPython/relaunchApp/startPython before bumping generation). */
	_tcpRetryTimer: ReturnType<typeof setTimeout> | null;
	/** True once the auth line has been written on the current socket. */
	_tcpAuthed: boolean;
	/** True once the renderer has ever seen a successful TCP connect (drives synthetic "reconnected"). */
	_hadConnectedBefore: boolean;
	/** True while a full app relaunch is in flight (suppresses noisy TCP errors + duplicate relaunch). */
	_relaunching: boolean;
	/** Persists after a restart trigger until the new backend connects (for "restart cycle complete" log). */
	_restartTriggered: boolean;
	/** True once the bubble renderer signals it has mounted and is ready for events. */
	_bubblePageReady: boolean;
	/** Idempotency guard for stopPython: true while a stop is in flight. */
	_stopPythonCalled: boolean;
}

export const state: MainState = {
	pythonProcess: null,
	tcpSocket: null,
	mainWindow: null,
	bubbleWindow: null,
	pendingRequests: new Map<number, PendingRequest>(),
	nextId: 1,
	tcpBuffer: "",
	pythonReady: false,
	pythonExitedEarly: false,
	heartbeatInterval: null,
	sessionNonce: "",
	bubblePosition: "bottom",
	bubbleDraggable: true,
	_hideTimeout: null,
	_tcpRetryCount: 0,
	_tcpRetryGeneration: 0,
	_tcpRetryTimer: null,
	_tcpAuthed: false,
	_hadConnectedBefore: false,
	_relaunching: false,
	_restartTriggered: false,
	_bubblePageReady: false,
	_stopPythonCalled: false,
};
