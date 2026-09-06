/**
 * Send an IPC message to the Python backend over the authenticated TCP
 * socket, returning a Promise that resolves with the reply data.
 *
 * Extracted from `index.ts` (REF-2).
 *
 * R6-F10 (session-1): the `ALLOWED_COMMANDS` set is now imported
 * directly from `../allowed-commands` (a dependency-free leaf module)
 * instead of `../index`. The previous `from "../index"` import created
 * a circular dependency (`index.ts` → `python/` → `send-to-python.ts`
 * → `index.ts`) that forced Node's CJS resolver to evaluate `index.ts`
 * partially before `sendToPython` was callable, producing
 * hard-to-trace load-order bugs. The canonical declaration lives in
 * `src/main/allowed-commands.ts` so that the Python
 * `test_allowlist_matches_server_commands` test and the vitest Section
 * 13 port can slice the literal `ALLOWED_COMMANDS = new Set([...])`
 * substring from that file.
 */
import { ALLOWED_COMMANDS } from "../allowed-commands";
import { IPC_TIMEOUT_LONG_MS, IPC_TIMEOUT_SHORT_MS } from "../constants";
import {
	MAX_PENDING_REQUESTS,
	RATE_LIMIT_MAX_CALLS,
	RATE_LIMIT_WINDOW_MS,
	state,
} from "../state";
import { PythonIpcError } from "./errors";

/**
 * Outbound replay queue for transient TCP disconnects.
 *
 * When ``state.tcpSocket`` is null AND the app has connected before
 * (``state._hadConnectedBefore === true`` — i.e. a transient blip, not
 * initial startup), idempotent commands are pushed here instead of
 * being rejected outright. On reconnect, ``flushPendingOutbound``
 * drains the queue in FIFO order, re-invoking ``sendToPython`` for
 * each entry and forwarding the new promise's resolution to the
 * original caller's ``resolve`` / ``reject``.
 *
 * Non-idempotent commands (e.g. ``toggle_dictation``) are still
 * rejected immediately when the socket is null — replaying them after
 * a disconnect risks double-execution (the Python side may have
 * already processed the original write before the socket dropped, so
 * replaying would start/stop a second recording).
 *
 * The queue is bounded to ``_MAX_PENDING_OUTBOUND`` entries. When the
 * bound is hit, the NEW idempotent request is rejected with the same
 * "Python backend is not connected" error rather than dropping a
 * queued entry — the oldest queued entries are the most likely to
 * still be relevant (they were queued first and have been waiting
 * longest), so we preserve them and shed load at the new edge.
 *
 * Design notes:
 *   - The queue is a module-level array (mirrors the existing
 *     ``_rendererCallTimestamps`` Map pattern). It is NOT stored on
 *     ``state`` because the field would have to be declared on
 *     ``MainState`` (in ``state.ts``, outside this module's
 *     ownership) and the queue has no readers outside this file +
 *     ``tcp-connect.ts``.
 *   - ``_hadConnectedBefore`` is the gate: during initial startup
 *     (before the first successful TCP connect) the queue is bypassed
 *     so the user still sees the "Python backend is not connected"
 *     error for premature clicks. After the first connect, transient
 *     disconnects queue idempotent commands so the user's click is
 *     not lost.
 */
interface PendingOutboundEntry {
	msg: Record<string, unknown>;
	resolve: (value: unknown) => void;
	reject: (reason: unknown) => void;
	// Capture the original caller's `senderId` at queue-push time so
	// the flush re-invokes `sendToPython` with the SAME sender
	// identity. This keeps replay accounting honest:
	//   (a) the per-renderer rate limit applies to replayed commands
	//       (a renderer that flooded idempotent commands during a
	//       disconnect cannot bypass its budget on flush), and
	//   (b) the renderer-vs-internal allowlist split keeps its
	//       renderer context — a queued `heartbeat` (idempotent AND
	//       internal-only) is rejected on replay for a renderer
	//       sender, exactly as it would have been rejected if the
	//       socket had been live. Main-process callers (senderId
	//       null) replay with null and keep internal-command access.
	// Stale sender ids (window closed while its command sat queued)
	// are safe: the `_rendererCallTimestamps` entry was already
	// removed by the window's `closed` handler, the replayed call
	// would only re-create a single bounded entry, and the whole Map
	// is cleared by `_resetIpcBackpressure()` on stopPython /
	// relaunchApp.
	senderId: number | null;
	ts: number;
}

const _IDEMPOTENT_COMMANDS: ReadonlySet<string> = new Set<string>([
	"get_config",
	"get_status",
	"heartbeat",
	"set_config",
]);

const _MAX_PENDING_OUTBOUND = 16;

const _pendingOutbound: PendingOutboundEntry[] = [];

/**
 * Drain the outbound replay queue in FIFO order, re-sending each
 * entry via ``sendToPython`` (which now has a non-null
 * ``state.tcpSocket``). The new promise's resolution is forwarded
 * to the original caller's ``resolve`` / ``reject`` so the queued
 * call behaves exactly as if it had been sent immediately.
 *
 * Called from ``tcp-connect.ts`` immediately after
 * ``state.tcpSocket = client`` is set on a successful reconnect.
 * Safe to call when the queue is empty (no-op).
 *
 * If a re-sent entry is rejected (e.g. allowlist drift, rate limit,
 * MAX_PENDING_REQUESTS cap), the original caller's ``reject`` is
 * invoked with the same error — the queue does not swallow failures.
 */
export function flushPendingOutbound(): void {
	if (_pendingOutbound.length === 0) {
		return;
	}
	// Drain into a local first so re-entrant sendToPython calls
	// (which themselves might queue if the socket drops again
	// mid-flush) append to a fresh queue rather than mutating the
	// array we're iterating.
	const drained = _pendingOutbound.splice(0, _pendingOutbound.length);
	for (const entry of drained) {
		// Forward the new promise's result to the original caller.
		// Use .then(fulfill, reject) so the original resolve/reject
		// is invoked exactly once. The entry's captured `senderId`
		// is passed through so replayed commands keep their original
		// sender identity — the per-renderer rate limit and the
		// renderer-vs-internal allowlist split both apply to the
		// replay, matching how the command would have been treated
		// if the socket had never dropped.
		sendToPython(entry.msg, entry.senderId).then(entry.resolve, entry.reject);
	}
}

/**
 * Reject every queued entry with the given reason. Called from
 * ``tcp/close-handler.ts``'s close handler when ``state._relaunching``
 * is true (the process is about to exit — queued calls would never be
 * flushed) and from tests for isolation.
 *
 * Exported because its callers live outside this module (the TCP close
 * handler plus test isolation); production teardown paths that close
 * the socket reach it via the close handler as well (stopPython
 * triggers a socket close).
 */
export function resetPendingOutbound(reason: string): void {
	while (_pendingOutbound.length > 0) {
		const entry = _pendingOutbound.shift();
		if (!entry) break;
		entry.reject(new Error(reason));
	}
}

/**
 * Test-only accessor for the queue length. Underscore-prefixed to
 * signal "internal/test-only" — mirrors the existing
 * ``_LONG_RUNNING_COMMANDS_FOR_TEST`` convention.
 */
export const _pendingOutboundLengthForTest = (): number =>
	_pendingOutbound.length;

/**
 * Per-renderer sliding-window rate limiter. Keyed by the
 * Electron `WebContents.id` so each renderer window gets its own
 * budget. A renderer that fires `python-call` faster than
 * `RATE_LIMIT_MAX_CALLS` per `RATE_LIMIT_WINDOW_MS` is rejected
 * with a structured error before the message ever reaches the TCP
 * socket — preventing both unbounded `pendingRequests` growth and
 * backend-side overload.
 *
 * `null` key (used by main-process-internal callers that don't have
 * a sender) skips the rate limit. The global `MAX_PENDING_REQUESTS`
 * cap still applies.
 */
const _rendererCallTimestamps: Map<number, number[]> = new Map();

function _rendererRateLimited(senderId: number | null): boolean {
	if (senderId === null) {
		return false;
	}
	const now = Date.now();
	const cutoff = now - RATE_LIMIT_WINDOW_MS;
	const prev = _rendererCallTimestamps.get(senderId) ?? [];
	// Drop timestamps outside the sliding window.
	const kept = prev.filter((t) => t >= cutoff);
	if (kept.length >= RATE_LIMIT_MAX_CALLS) {
		_rendererCallTimestamps.set(senderId, kept);
		return true;
	}
	kept.push(now);
	_rendererCallTimestamps.set(senderId, kept);
	return false;
}

/**
 * Reset the per-renderer rate-limit state. Called from `stopPython()`
 * and `relaunchApp()` (production callers — ) to give a freshly-
 * booted backend a clean slate, and from tests to isolate cases.
 *
 * : previously named `_resetIpcBackpressureForTests` (the `ForTests`
 * suffix was misleading — the docstring claimed production callers but
 * grep showed none). The Map was never cleared, so each destroyed
 * BrowserWindow leaked its `webContents.id` entry forever. Renaming to
 * `_resetIpcBackpressure` (still `_`-prefixed to signal "internal") and
 * wiring the production call sites honors the original intent and bounds
 * the Map's growth to "at most one entry per currently-live renderer".
 */
export function _resetIpcBackpressure(): void {
	_rendererCallTimestamps.clear();
}

/**
 * Remove the per-renderer rate-limit entry for a single
 * `webContents.id`. Called from `main-window.ts`'s `closed` handler
 * (BEFORE `state.mainWindow` is nulled) so the
 * `_rendererCallTimestamps` Map doesn't leak one entry per destroyed
 * BrowserWindow.
 *
 * Safe to call with an id that has no entry (no-op — `Map.delete` on
 * a missing key returns `false` and doesn't throw).
 *
 * Underscore-prefixed to signal "internal" — matches the existing
 * `_resetIpcBackpressure` convention. Exported because the call site
 * lives in `windows/main-window.ts`.
 */
export function _removeRendererFromBackpressure(webContentsId: number): void {
	_rendererCallTimestamps.delete(webContentsId);
}

/**
 * Commands whose handlers are documented to block for the
 * full duration of a long operation (model download / import, large
 * vocabulary save). These keep the legacy 120s timeout so legitimate
 * slow responses don't false-positive. Every other command gets the
 * shorter 15s timeout so a stuck handler releases its pending-map
 * entry (and the closure it pins) 8x sooner.
 *
 * Stale-entry cleanup: previously this Set contained 3 stale entries
 * that do NOT exist in `ALLOWED_COMMANDS` or the server's
 * `_COMMAND_REGISTRY`:
 *   - `"cancel_download"`  → renamed to `"cancel_model_download"`
 *   - `"pause_download"`   → renamed to `"pause_model_download"`
 *   - `"transcribe_audio"` → never existed; the renderer uses
 *     `toggle_dictation` for the recording lifecycle, and the actual
 *     transcription command on the server side is internal
 *     (the ASR engine is invoked synchronously inside the
 *     `recording_controller` pipeline, not as a separate IPC command).
 *
 * The 3 stale entries were dead — `_isLongRunningCommand(cmd)` always
 * returned `false` for them because the `ALLOWED_COMMANDS` gate at the
 * top of `sendToPython` rejected them BEFORE the timeout lookup ran
 * (`cmd` would never match a Set entry that doesn't exist in the
 * allowlist). The practical impact was that `cancel_model_download` /
 * `pause_model_download` / `resume_model_download` got the SHORTER 15s
 * timeout instead of the documented 120s "long-running" budget —
 * meaning a slow HuggingFace cancel/resume could false-positive as a
 * timeout, leaving the pending-request map in an inconsistent state.
 *
 * Fix: replace the 3 stale entries with the 3 real command names AND
 * add `resume_model_download` (it's a model-download control command
 * that may also block on the HuggingFace network round-trip). The
 * parity test at `src/main/__tests__/long-running-commands-parity.test.ts`
 * pins every entry against `ALLOWED_COMMANDS` so this drift cannot
 * recur silently.
 */
const _LONG_RUNNING_COMMANDS: ReadonlySet<string> = new Set([
	"download_model",
	"import_model",
	"delete_model",
	"cancel_model_download",
	"pause_model_download",
	"resume_model_download",
]);

/**
 * Test-only export of the long-running command set so the parity
 * test (`src/main/__tests__/long-running-commands-parity.test.ts`)
 * can assert every entry is also in `ALLOWED_COMMANDS`. Underscore-
 * prefixed to signal "internal/test-only" — matching the existing
 * `_resetIpcBackpressure` convention.
 */
export const _LONG_RUNNING_COMMANDS_FOR_TEST: ReadonlySet<string> =
	_LONG_RUNNING_COMMANDS;

function _isLongRunningCommand(cmd: string): boolean {
	return _LONG_RUNNING_COMMANDS.has(cmd);
}

/**
 * : per-command timeout overrides for commands that should
 * time out FASTER than the default 15s short timeout. These are
 * lifecycle / heartbeat commands whose handlers are documented to
 * return in well under 1s — a 15s wait for a ``heartbeat`` reply
 * means the backend has been unresponsive for ~15s before the
 * renderer learns about it, which is far too long for the
 * connection-health signal that heartbeat provides.
 *
 * Values are in milliseconds. The default short timeout (15s) and
 * long timeout (120s) are unchanged; this map only SHORTENS the
 * timeout for the specific commands listed.
 *
 * ``relaunch_ack`` is included because it's a fire-and-forget ack
 * whose reply (if any) is expected within milliseconds — the
 * backend sends the ack BEFORE calling ``sys.exit(0)``, so a 5s
 * timeout is generous (PERF-005).
 */
const _SHORT_TIMEOUT_COMMANDS: ReadonlyMap<string, number> = new Map([
	["heartbeat", 10_000], // 10s — backend heartbeat handler is <1s
	["quit_app", 5_000], // 5s — backend quit handler is <1s
	["relaunch_ack", 5_000], // 5s — fire-and-forget ack
]);

function _commandTimeoutMs(cmd: string): number {
	const override = _SHORT_TIMEOUT_COMMANDS.get(cmd);
	if (override !== undefined) {
		return override;
	}
	return _isLongRunningCommand(cmd)
		? IPC_TIMEOUT_LONG_MS
		: IPC_TIMEOUT_SHORT_MS;
}

/**
 * Internal-only IPC commands — invoked by the Electron main process
 * itself (never reachable from the renderer's `python-call` bridge).
 *
 *   - `quit_app`         — sent by `stop-python.ts` during shutdown.
 *   - `restart_app`      — sent by `relaunch-app.ts` to trigger a
 *                          backend restart (the main process then
 *                          relaunches itself).
 *   - `heartbeat`        — sent by `tcp-connect.ts` on the watchdog
 *                          tick to prove Electron is still alive.
 *   - `relaunch_ack`     — sent by `handle-message.ts` to ack a
 *                          `relaunch_app` request from the backend.
 *
 * All four are present in `ALLOWED_COMMANDS` because main-process
 * callers (which pass `senderId === null`) route through the same
 * `sendToPython` choke point. The renderer-vs-internal separation is
 * enforced below in `sendToPython`: when `senderId !== null` (a
 * renderer's `WebContents.id` from `python-call-handler.ts`), any
 * command in this Set is rejected with the same "Disallowed IPC
 * command" error used by the allowlist gate. A compromised renderer
 * that constructs `{type: "quit_app"}` and invokes `python-call` would
 * otherwise be able to kill the backend or starve the heartbeat
 * watchdog — both unacceptable.
 *
 * The Set is intentionally a private literal here (not imported from
 * `allowed-commands.ts`) so that the existing vitest mocks that stub
 * `../allowed-commands` with only `ALLOWED_COMMANDS` keep working
 * without each mock having to also stub `ALLOWED_COMMANDS_INTERNAL`.
 * The parity test in
 * `src/main/__tests__/renderer-internal-allowlist-split.test.ts` pins
 * that every entry here is also in the real `ALLOWED_COMMANDS` so
 * the two declarations cannot drift.
 */
const _INTERNAL_ONLY_COMMANDS: ReadonlySet<string> = new Set<string>([
	"quit_app",
	"restart_app",
	"heartbeat",
	"relaunch_ack",
]);

/**
 * Test-only export of the internal-only command set so the parity
 * test (`src/main/__tests__/renderer-internal-allowlist-split.test.ts`)
 * can assert every entry is also in `ALLOWED_COMMANDS`. Underscore-
 * prefixed to signal "internal/test-only" — matching the existing
 * `_LONG_RUNNING_COMMANDS_FOR_TEST` convention.
 */
export const _INTERNAL_ONLY_COMMANDS_FOR_TEST: ReadonlySet<string> =
	_INTERNAL_ONLY_COMMANDS;

export function sendToPython(
	msg: Record<string, unknown>,
	senderId: number | null = null,
): Promise<unknown> {
	return new Promise((resolve, reject) => {
		if (!state.tcpSocket) {
			// Transient-disconnect replay queue: if we've connected
			// before AND the command is idempotent AND the queue has
			// room, capture the request for flush-on-reconnect instead
			// of rejecting it. This eliminates the "flaky button" feel
			// where a brief TCP blip (sleep/resume, Wi-Fi flap, GC
			// pause on the Python side) causes a user click to fail
			// even though the reconnect happens milliseconds later.
			//
			// ``_hadConnectedBefore`` gates this so the initial-startup
			// UX is preserved: before the first successful TCP connect,
			// the user sees the "Python backend is not connected" error
			// for premature clicks (the dashboard is already showing a
			// "Connecting..." indicator).
			//
			// Non-idempotent commands (toggle_dictation, undo_last,
			// history mutations, etc.) are NEVER queued — the Python
			// side may have already processed the original write before
			// the socket dropped, so replaying would double-execute
			// (start/stop a second recording, undo twice, etc.).
			// Reject immediately so the caller sees a clear error and
			// can decide to retry manually.
			const cmd0 = String(msg?.type ?? "").trim();
			if (
				state._hadConnectedBefore === true &&
				_IDEMPOTENT_COMMANDS.has(cmd0) &&
				_pendingOutbound.length < _MAX_PENDING_OUTBOUND
			) {
				_pendingOutbound.push({
					msg,
					resolve,
					reject,
					ts: Date.now(),
					senderId,
				});
				return;
			}
			reject(
				new PythonIpcError(
					"backend_not_connected",
					"Python backend is not connected",
				),
			);
			return;
		}
		// SEC-019: validate the command against an allowlist before
		// forwarding to the Python backend. Combined with SEC-018
		// (unauth TCP), this prevents a compromised renderer from
		// calling arbitrary IPC commands like set_config / quit_app.
		// SEC-010: allowlist check MUST precede _relaunching check so a
		// _relaunching flag cannot be used to bypass the allowlist.
		//
		//(fix): previously missing `quit_app` and `restart_app`,
		// which broke tray Quit/Restart (stopPython sends `quit_app`).
		//(fix): removed 6 dead/mismatched entries (`quit`,
		// `restart`, `save_config`, `save_vocabulary_with_diff`,
		// `repaste_last`, `complete_onboarding`) — none exist as server
		// IPC commands. The list now matches the server's actual command
		// names exactly (cross-checked against ipc_server.py _dispatch).
		//
		// NOTE: the canonical ALLOWED_COMMANDS declaration lives in
		// `src/main/allowed-commands.ts` (not here, and no longer in
		// `src/main/index.ts` since R6-F10) so that the Python
		// `test_allowlist_matches_server_commands` test and the
		// vitest Section 13 port can slice the literal
		// `ALLOWED_COMMANDS = new Set([` ... `]);` substring from
		// the source. Do NOT move the declaration into this file.
		const cmd = String(msg?.type ?? "").trim();
		if (!ALLOWED_COMMANDS.has(cmd)) {
			reject(
				new PythonIpcError("command_failed", `Disallowed IPC command: ${cmd}`),
			);
			return;
		}
		// Renderer-vs-internal allowlist split:
		// `python-call-handler.ts` passes the renderer's
		// `WebContents.id` as `senderId`. When non-null, the
		// caller is a renderer (potentially compromised) and
		// MUST NOT be able to invoke internal-only lifecycle
		// commands (`quit_app`, `restart_app`, `heartbeat`,
		// `relaunch_ack`). Main-process callers pass
		// `senderId === null` and bypass this gate so the
		// heartbeat watchdog, shutdown sequence, and relaunch
		// ack still work.
		//
		// The error message intentionally matches the allowlist
		// gate's "Disallowed IPC command" wording so a
		// compromised renderer cannot distinguish "not in
		// allowlist" from "internal-only" — both look the same
		// to the attacker, so no information about the internal
		// command set leaks via the error string.
		//
		// This check MUST run AFTER the allowlist gate (so
		// unknown commands are still rejected with the same
		// "Disallowed" error) and BEFORE the `_relaunching`
		// check (so a renderer probing for the relaunching
		// state by sending internal commands is rejected here,
		// not later with the "Application is restarting" error
		// that would leak the relaunch state).
		if (senderId !== null && _INTERNAL_ONLY_COMMANDS.has(cmd)) {
			reject(
				new PythonIpcError("command_failed", `Disallowed IPC command: ${cmd}`),
			);
			return;
		}
		// If a full app relaunch is in flight, reject immediately so
		// pending IPC calls don't sit in pendingRequests until the
		// 5s timeout — the process is about to exit anyway.
		if (state._relaunching) {
			reject(new PythonIpcError("command_failed", "Application is restarting"));
			return;
		}
		// Per-renderer rate limit. Rejects a flood from
		// a single sender before it can pin `MAX_PENDING_REQUESTS`
		// entries.
		if (_rendererRateLimited(senderId)) {
			reject(
				new PythonIpcError(
					"command_failed",
					`Rate limit exceeded for command: ${cmd}`,
				),
			);
			return;
		}
		// Global cap on simultaneously-pending requests.
		// Each entry pins a `setTimeout` closure that captures
		// `msg` + `resolve` + `reject` + `timer`; without a cap a
		// compromised renderer could retain tens of MB of live
		// closures for 2 minutes. Reject fast with a structured
		// error rather than letting the Map grow.
		if (state.pendingRequests.size >= MAX_PENDING_REQUESTS) {
			reject(
				new PythonIpcError(
					"command_failed",
					`Pending IPC request limit reached (${MAX_PENDING_REQUESTS})`,
				),
			);
			return;
		}
		const id = state.nextId++;
		(msg as Record<string, unknown>).id = id;
		const line = `${JSON.stringify(msg)}\n`;
		state.tcpSocket.write(line);
		// Non-download commands use a shorter 15s timeout so
		// a stuck handler doesn't pin a 120s closure. Long-running
		// commands (model download, model import, transcription)
		// keep the 120s timeout because they're documented as
		// blocking. The Python-side heartbeat watchdog is at 120s
		// (ipc_server.py) for the same reason — both timeouts must
		// stay in sync for long-running commands.
		//use the per-command timeout map so heartbeat /
		// quit_app / relaunch_ack time out faster than the default
		// 15s short timeout.
		const timeoutMs = _commandTimeoutMs(cmd);
		//
		//capture the timer handle and clearTimeout in BOTH the
		// success and reject paths so the timer doesn't leak after
		// a prompt reply. Previously the timer held a strong reference
		// to `reject` (and the captured `msg`) for the full duration
		// even after the reply arrived — handle-message.ts deletes the
		// pendingRequests entry but never cleared the timer, so the
		// closure stayed in the Node.js timer wheel until it fired and
		// no-op'd the `has(id)` check. Wrapping resolve/reject here lets
		// handle-message.ts transparently clear the timer when it
		// resolves or rejects the entry, releasing the closure early.
		const timer = setTimeout(() => {
			if (state.pendingRequests.has(id)) {
				state.pendingRequests.delete(id);
				const cmd = String(msg?.type ?? "unknown").trim();
				//reject with a typed `PythonIpcError` so downstream
				// consumers (the `python-call-handler` IPC bridge) can
				// branch on `err instanceof PythonIpcError` + `err.code`
				// WITHOUT regex-matching the human-readable message. The
				// previous contract attached an ad-hoc `err.code = "timeout"`
				// string to a bare `Error`, which the handler classified
				// via a fragile `/timeout/i` regex on the message text
				// (silently broke if the message wording ever changed).
				reject(
					new PythonIpcError(
						"command_timeout",
						`Timeout after ${timeoutMs / 1000}s for command: ${cmd}`,
					),
				);
			}
		}, timeoutMs);
		state.pendingRequests.set(id, {
			resolve: (value: unknown) => {
				clearTimeout(timer);
				resolve(value);
			},
			reject: (reason: unknown) => {
				clearTimeout(timer);
				reject(reason);
			},
		});
	});
}
