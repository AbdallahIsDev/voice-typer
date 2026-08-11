// src/renderer/src/hooks/usePython.ts

import { useCallback, useEffect, useRef, useSyncExternalStore } from "react";

//import `installTauriBridge` so `subscribeBridgeReady` can
// re-trigger the installer when the Tauri runtime appears AFTER the
// initial module-import-time auto-install ran (which no-op'd because
// the Tauri global wasn't yet present — a rare timing edge under
// Tauri v2 with `withGlobalTauri: true`). The installer itself is
// idempotent (no-ops when not in Tauri mode or when the namespaces
// are already installed), so the hook stays transport-agnostic:
// it never touches Tauri or Electron APIs directly, only
// `window.python` + the idempotent installer.
import { installTauriBridge } from "@/lib/tauri-bridge";
import type { PythonPushEvent } from "@/types/ipc";
// Import the `PythonCallErrorCode` union so the renderer can narrow
// `result._code` against the typed union. The canonical declaration
// lives in the Electron main process's `python-call-handler.ts`
// (outside the web tsconfig's `include` scope — a cross-boundary import
// would fail `tsc --noEmit` with `TS6307`). The renderer-side mirror
// lives in `types/ipc/enums.ts` (this file's import below); the two
// declarations MUST stay in sync — both files carry a comment pointing
// to the other.
import type { PythonCallErrorCode } from "@/types/ipc/enums";
import type { PythonRequest } from "@/types/ipc/requests";

/**
 * : extracts the per-event ``data`` payload shape for a given
 * PythonPushEvent ``type`` literal. For events with NO ``data`` field
 * (e.g. ``RecordingStartedEvent``), this resolves to ``undefined`` —
 * the handler is then typed as ``(data?: undefined) => ...`` so callers
 * that ignore ``data`` still compile.
 *
 * The conditional ``extends { data: infer D }`` is necessary because
 * ``Extract<PythonPushEvent, { type: K }>["data"]`` would be a
 * compile-time error for events that have no ``data`` field at all
 * (TS4.x's ``["data"]`` index access requires the key to exist on
 * every member of the extracted union).
 */
type ExtractEventData<K extends PythonPushEvent["type"]> =
	Extract<PythonPushEvent, { type: K }> extends { data: infer D }
		? D
		: undefined;

//per-command timeout table ────────────────────────────────
//
// A blanket 120s `setTimeout` is applied to every IPC call by the
// Electron main process's `sendToPython` (client/src/main/index.ts:
// 507-644) and by the Rust `dispatch` command (src-tauri/src/commands/
// sidecar_cmds.rs:67-73 `dispatch_timeout_for` + util.rs:53
// `DISPATCH_TIMEOUT_SECS = 120`). A `get_status` call that hangs takes
// 120s to surface an error; the 120s timer is created even for trivial
// commands.
//
// The renderer's `call` function (below) wraps the underlying bridge
// call in a `Promise.race` against a per-command timeout, so:
//   - `get_status` / `get_config` surface a hang in 5s instead of 120s.
//   - `download_model` is allowed a generous budget (but see the Rust
//     hard-cap note below).
//   - Unknown commands default to 30s (a reasonable middle ground).
//
// The underlying bridge promise may still resolve later (the Electron
// main / Rust host's timer is still active on their side), but the
// caller sees the renderer-side timeout rejection first.
//
//Rust hard cap on `download_model` ─────────────
//
// The Rust `dispatch` command enforces a hard timeout of 120s for the
// 6 model-lifecycle commands (`download_model`, `import_model`,
// `delete_model`, `cancel_model_download`, `pause_model_download`,
// `resume_model_download`) and 15s for everything else — see
// `src-tauri/src/commands/sidecar_cmds.rs:50-73` and
// `src-tauri/src/util.rs:53`.  The previous `download_model: 600_000`
// (10 min) entry in this table was effectively DEAD CODE: the Rust
// host always rejected first at 120s with the generic
// `"dispatch timeout (120s)"` error, so the renderer's 10-minute
// budget was never the binding constraint.
//
// The entry is now capped at 115_000ms (5s BELOW the Rust 120s hard
// cap) so the renderer surfaces a clearer, command-specific timeout
// error (`IPC command "download_model" timed out after 115000ms`)
// BEFORE the Rust side rejects with its generic message. This gives
// the user an actionable, contextual error instead of a host-side
// reject that doesn't identify which command timed out.
//
// Durable fix (out of scope for this file): extend the Rust
// `DispatchArgs` struct with a `timeout_secs` field so the renderer
// can request a longer budget for legitimate large downloads. Until
// then, downloads that exceed 120s will fail — users on slow links
// should use the `import_model` flow (downloads via browser/curl and
// imports the local file, bypassing the dispatch timeout entirely).
const COMMAND_TIMEOUTS: Record<string, number> = {
	get_status: 5_000,
	get_config: 5_000,
	get_history: 10_000,
	//capped at 115s — 5s below the Rust host's
	// 120s `DISPATCH_TIMEOUT_SECS` hard cap so the renderer surfaces
	// the timeout first with a command-specific error message
	// instead of letting the Rust side reject with the generic
	// "dispatch timeout (120s)" string. The previous 600_000ms
	// (10 min) value was dead code: the Rust dispatch always fired
	// first at 120s.
	download_model: 115_000,
	// `transcribe` was previously listed here at 120s but `transcribe`
	// is NOT a real IPC command (the actual control RPC is
	// `toggle_dictation`, which is a short control call that returns
	// immediately; the recording/transcription itself runs async on
	// the backend and pushes results via `transcription_final`
	// events). The dead `transcribe` entry was leftover from a
	// pre-rename era. Replaced with `toggle_dictation` at 30s so a
	// hung toggle call surfaces an error in 30s instead of falling
	// through to DEFAULT_COMMAND_TIMEOUT_MS (also 30s — explicit is
	// better than implicit so future contributors don't accidentally
	// remove the entry thinking it's the default).
	toggle_dictation: 30_000,
};

const DEFAULT_COMMAND_TIMEOUT_MS = 30_000;

//runtime mirror of the `PythonPushEvent["type"]` union
// declared in `types/ipc/push_events.ts`. TS can't enumerate union
// members at runtime, so we maintain this set by hand. The dev-time
// warning in `usePythonEvent` (below) consults this set to surface
// typos like `usePythonEvent("past_failed", ...)` (intended
// `"paste_failed"`) in the dev console.
//
// KEEP IN SYNC with the `PythonPushEvent` union in
// `types/ipc/push_events.ts`. When a new event is added there, add
// its `type` literal here too. The dev-time warning will surface
// forgetfulness the first time a renderer subscribes to the new
// event (the warning fires for unknown types — including ones added
// to the TS union but not yet to this set).
//exported so the parity test
// (`__tests__/usePython-known-event-types-parity.test.ts`) can assert
// the runtime set matches the compile-time `PythonPushEvent["type"]`
// union. Not part of the public hook API — only consumed by tests.
export const KNOWN_EVENT_TYPES: ReadonlySet<string> = new Set([
	"status_change",
	"error",
	"transcription_final",
	"recording_started",
	"recording_stopped",
	"config_changed",
	"hotkey_capture_cancel",
	"history_changed",
	"state_changed",
	"paste_failed",
	"download_progress",
	"notification",
	"vocabulary_suggestion",
	"microphones_changed",
	"microphone_test_complete",
	"audio_clip",
	"tray_menu",
	"navigate",
	"ready",
	"bubble_show",
	"bubble_hide",
	"bubble_set_state",
	"bubble_level",
	"bubble_config",
	"show_window",
	"quit_app",
	"relaunch_app",
	"tray_state",
	"consent_required",
	"parakeet_cpu_fallback",
	"asr_backend_disabled",
	"asr_last_resort_unloaded",
	"llm_polish_failed",
	"reconnecting",
	"reconnected",
	// the new mic_level push event (coalesced at
	// ≤30 Hz by the same level_monitor worker that publishes
	// `bubble_level`). Subscribed to by
	// `pages/microphone/hooks/useMicrophoneTest.ts` instead of
	// the legacy 10 Hz `microphone_test_get_level` IPC poll.
	"mic_level",
]);

/**
 * Returns the per-command timeout (ms) for the given IPC command name.
 * Falls back to {@link DEFAULT_COMMAND_TIMEOUT_MS} for unknown commands.
 *
 * Exported for unit testing (see `__tests__/command-timeouts.test.ts`).
 */
export function getTimeout(cmd: string): number {
	return COMMAND_TIMEOUTS[cmd] ?? DEFAULT_COMMAND_TIMEOUT_MS;
}

/**
 * Wraps a promise with a per-command timeout. If the promise does not
 * settle within `getTimeout(cmd)` ms, the returned promise rejects with
 * an `Error` of the form `IPC command "<cmd>" timed out after <ms>ms`.
 *
 * The timeout timer is cleared when the underlying promise settles first
 * (so we don't leak a `setTimeout` reference).
 */
function withCommandTimeout<T>(promise: Promise<T>, cmd: string): Promise<T> {
	const timeoutMs = getTimeout(cmd);
	let timer: ReturnType<typeof setTimeout> | undefined;
	const timeoutPromise = new Promise<never>((_, reject) => {
		timer = setTimeout(() => {
			reject(new Error(`IPC command "${cmd}" timed out after ${timeoutMs}ms`));
		}, timeoutMs);
	});
	return Promise.race([promise, timeoutPromise]).finally(() => {
		if (timer) clearTimeout(timer);
	});
}

/**
 * VP-6: Parse a Tauri ``invoke`` rejection string into a real ``Error``.
 *
 * On Tauri v2, when the Rust ``dispatch`` command returns ``Err``, the
 * ``invoke`` promise rejects with the raw ``e.to_string()`` — which for
 * structured errors is the JSON-serialized envelope
 * ``{"type":"error","data":{"code":"...","message":"..."}}``
 * (see ``src-tauri/src/commands/sidecar_cmds/dispatch.rs``). The
 * Electron path resolves the SAME envelope shape as a successful value,
 * which the ``type === "error"`` check in ``call`` turns into an
 * ``Error`` with ``err.code`` stamped. This helper makes the Tauri path
 * behave identically, so callers branching on ``err.code`` (e.g.
 * ``command_timeout`` vs ``backend_not_connected``) work on BOTH
 * runtimes instead of silently falling through to a generic error on
 * Tauri (VP-6).
 *
 * Returns ``null`` when the string is NOT a structured error envelope
 * (e.g. the Rust ``dispatch timeout (120s)`` plain-string rejection) —
 * the caller falls back to ``new Error(raw)``.
 */
export function parseTauriErrorEnvelope(raw: string): Error | null {
	let parsed: unknown;
	try {
		parsed = JSON.parse(raw);
	} catch {
		// Not JSON — a plain-string rejection (e.g. "dispatch timeout (120s)").
		return null;
	}
	if (typeof parsed !== "object" || parsed === null) return null;
	const envelope = parsed as {
		type?: unknown;
		data?: { code?: unknown; message?: unknown };
	};
	if (envelope.type !== "error" || !envelope.data) return null;
	const msg =
		typeof envelope.data.message === "string" ? envelope.data.message : raw;
	const err = new Error(msg);
	const code = envelope.data.code;
	if (typeof code === "string" && code.length > 0) {
		(err as { code?: string }).code = code;
	}
	return err;
}

//bridge-ready subscription via useSyncExternalStore ────────
//
// `usePythonEvent` previously returned early from its `useEffect` when
// `window.python` was undefined at mount, and the effect's only
// dependency was `[type]` — so if `window.python` was installed later
// (e.g. by the Tauri bridge's auto-install on first import, or by the
// Electron preload under slow HMR), the subscription was never
// re-attempted and events were silently dropped.
//
// `useBridgeReady` polls `window.python` presence every 100ms until it
// appears, then notifies React via the `useSyncExternalStore` callback.
// Including `bridgeReady` in the effect's dependency array causes the
// effect to re-run when `window.python` becomes available, so the
// subscription is created lazily on first bridge availability.
function subscribeBridgeReady(callback: () => void): () => void {
	//short-circuit when the bridge is already installed at
	// subscribe time. `useSyncExternalStore` calls `subscribe` once per
	// component instance, so without this guard each mounted consumer
	// (`usePythonEvent` callers) would spin up its own 100ms polling
	// interval even though `window.python` is already present. In the
	// normal production path the preload/bridge install runs before
	// React mounts, so this short-circuit eliminates all 12+ polling
	// intervals that would otherwise tick forever (the snapshot never
	// flips back to `false`, so `clearInterval` only fires on unmount).
	// Returning a no-op cleanup matches the contract: subscribers must
	// return an unsubscribe function.
	if (getBridgeReadySnapshot()) return () => {};
	// Poll every 100ms until window.python is available, then stop.
	// The interval self-clears on first detection to avoid leaking a
	// timer once the bridge is installed.
	//
	// If `window.python` is already set at subscribe time, the first
	// tick (≤100ms later) detects it and calls `callback()`. React
	// re-renders, `getSnapshot()` returns the same `true`, and the
	// effect (which already ran with `bridgeReady=true` on the
	// initial render) does not re-run — so the no-op re-render is
	// harmless.
	//also detect the Tauri runtime appearing AFTER the
	// initial module-import-time auto-install. The auto-install in
	// the tauri-bridge installer runs once at module load — if
	// the Tauri global isn't yet present (rare timing edge under
	// Tauri v2 with `withGlobalTauri: true`), the auto-install
	// no-ops and `window.python` is never installed. The previous
	// code polled `window.python` forever with NO mechanism to
	// re-trigger the installer. We now re-invoke
	// `installTauriBridge()` (idempotent — no-ops if not in Tauri
	// mode or if already installed) on every tick, which installs
	// the three namespaces, and the next tick's
	// `window.python` check then succeeds and notifies React.
	const interval = setInterval(() => {
		if (typeof window.python !== "undefined") {
			callback();
			clearInterval(interval);
			return;
		}
		//The Tauri global appeared after the auto-install
		// no-op'd — re-trigger the installer. The installer is
		// idempotent: it no-ops again if the runtime isn't fully
		// ready yet (e.g. the global is partial) and the next
		// tick retries.
		try {
			installTauriBridge();
		} catch (err) {
			// Defensive: a partially-mocked global
			// (e.g. in tests) could throw inside a
			// namespace installer. Surface the error
			// so it's debuggable instead of silently
			// looping forever.
			console.warn("[usePython] installTauriBridge retry failed:", err);
		}
	}, 100);
	return () => clearInterval(interval);
}

function getBridgeReadySnapshot(): boolean {
	return typeof window.python !== "undefined";
}

function getBridgeReadyServerSnapshot(): boolean {
	// During SSR (no `window`), the bridge is never ready. Vitest's
	// jsdom env always has `window`, so this only fires in true SSR.
	return false;
}

/**
 * Returns `true` once `window.python` is installed (by the Electron
 * preload script or by `installTauriBridge()`). Re-render-safe via
 * `useSyncExternalStore`: the snapshot is a stable boolean.
 *
 * Used by {@link usePythonEvent} to re-attempt the event subscription
 * when the bridge becomes available after mount ().
 */
export function useBridgeReady(): boolean {
	return useSyncExternalStore(
		subscribeBridgeReady,
		getBridgeReadySnapshot,
		getBridgeReadyServerSnapshot,
	);
}

//Shared event dispatcher () ────────────────────────────────
//
// Previously each `usePythonEvent` call subscribed to `api.onEvent`
// directly, creating N subscriptions for N callers. On Tauri, each
// subscription registers 4 Tauri event listeners (the main
// `python-event` channel + 3 supervisor relay channels in
// `python-namespace.ts`), so N callers created 4N Tauri listeners —
// and every event triggered all 4N callbacks only to be filtered
// down to the (typically 1) matching caller by the
// `if (event.type === type)` check. On Electron each subscription
// adds one `python-event` IPC listener, so
// N callers created N IPC listeners with the same fan-out waste.
//
// The dispatcher subscribes to `api.onEvent` exactly ONCE per
// `window.python` instance and fan-outs to per-type subscribers
// stored in a `Map<type, Set<entry>>`. This collapses the
// N-listener multiplication: N callers share 1 subscription (4
// Tauri listeners / 1 Electron IPC listener).
//
// The dispatcher is module-level (singleton). It is lazily set up
// when the first subscriber registers (after the bridge is ready)
// and torn down when the last subscriber unsubscribes — so a test
// that mounts + unmounts a single hook leaves no dangling
// subscription for the next test. If `window.python` is replaced
// (e.g. test `afterEach` deletes and re-sets it), `ensureDispatcher`
// detects the instance change and re-subscribes.

type EventHandler = (
	data?: Record<string, unknown>,
) => (() => void) | undefined;

interface DispatcherEntry {
	// () => handlerRef.current — indirection so the dispatcher
	// always invokes the latest handler identity without
	// re-subscribing on every render.
	getHandler: () => EventHandler;
	// Per-entry cleanup slot. Holds the cleanup function returned
	// by the most recent handler invocation. The dispatcher
	// invokes it before the NEXT matching event's handler runs
	// (cancelling in-flight async work) and `unsubscribe` invokes
	// it on teardown (releasing resources).
	cleanupRef: { current: (() => void) | undefined };
}

const typeSubscribers: Map<string, Set<DispatcherEntry>> = new Map();
let dispatcherState: {
	api: NonNullable<typeof window.python>;
	unsubscribe: () => void;
} | null = null;

function dispatchEvent(event: {
	type: string;
	data?: Record<string, unknown>;
}): void {
	const set = typeSubscribers.get(event.type);
	if (!set || set.size === 0) return;
	// Snapshot the set so a handler that unsubscribes itself (or
	// subscribes a new entry for the same type) during iteration
	// doesn't corrupt the iteration.
	const entries = Array.from(set);
	for (const entry of entries) {
		// Invoke the previous cleanup BEFORE the next handler so
		// concurrent invocations compose correctly (e.g. stale
		// `reloadHotkey` chains are cancelled before a new one
		// starts).
		if (typeof entry.cleanupRef.current === "function") {
			const fn = entry.cleanupRef.current;
			entry.cleanupRef.current = undefined;
			try {
				fn();
			} catch (err) {
				console.error("usePythonEvent cleanup threw:", err);
			}
		}
		try {
			entry.cleanupRef.current = entry.getHandler()(event.data);
		} catch (err) {
			//a throwing handler must not escape
			// into the dispatch loop. Log and reset so the
			// next event starts from a clean slate.
			console.error("usePythonEvent handler threw:", err);
			entry.cleanupRef.current = undefined;
		}
	}
}

function ensureDispatcher(): void {
	const api = window.python;
	if (!api) return;
	// Same instance → already subscribed.
	if (dispatcherState && dispatcherState.api === api) return;
	// Different instance (e.g. test `afterEach` deleted and re-set
	// `window.python`) → tear down the stale subscription and
	// re-subscribe to the new one.
	if (dispatcherState) {
		try {
			dispatcherState.unsubscribe();
		} catch (err) {
			console.warn("[usePython] dispatcher teardown failed:", err);
		}
		dispatcherState = null;
	}
	const unsubscribe = api.onEvent((event) => {
		dispatchEvent(event as { type: string; data?: Record<string, unknown> });
	});
	dispatcherState = { api, unsubscribe };
}

function subscribeToEventType(
	type: string,
	getHandler: () => EventHandler,
): () => void {
	let set = typeSubscribers.get(type);
	if (!set) {
		set = new Set();
		typeSubscribers.set(type, set);
	}
	const entry: DispatcherEntry = {
		getHandler,
		cleanupRef: { current: undefined },
	};
	set.add(entry);
	ensureDispatcher();
	return () => {
		const currentSet = typeSubscribers.get(type);
		if (currentSet) {
			currentSet.delete(entry);
			if (currentSet.size === 0) {
				typeSubscribers.delete(type);
			}
		}
		// Invoke the most recent cleanup so the handler can
		// release resources on unsubscribe (unmount / type
		// change / bridge going away).
		if (typeof entry.cleanupRef.current === "function") {
			const fn = entry.cleanupRef.current;
			entry.cleanupRef.current = undefined;
			try {
				fn();
			} catch (err) {
				console.error("usePythonEvent cleanup threw:", err);
			}
		}
		// If no subscribers remain, tear down the dispatcher
		// subscription so we don't hold a dangling listener
		// (e.g. after the last component unmounts).
		if (typeSubscribers.size === 0 && dispatcherState) {
			try {
				dispatcherState.unsubscribe();
			} catch (err) {
				console.warn("[usePython] dispatcher teardown failed:", err);
			}
			dispatcherState = null;
		}
	};
}

/**
 * Type of the ``call`` function returned by {@link usePython}.
 *
 * Two overloads — a strict one that narrows the ``data`` parameter
 * against the {@link PythonRequest} discriminated union (so a typo in
 * the command name or a wrong data shape surfaces at compile time for
 * known commands), and a loose one that accepts any string +
 * ``Record<string, unknown>`` for forward-compat with backend-added
 * commands that haven't made it into the union yet. TypeScript picks
 * the first matching overload, so known commands hit the strict
 * overload and unknown commands fall through to the loose one.
 *
 * The strict overload's ``data`` parameter uses a conditional type so
 * requests without a ``data`` field (e.g. ``GetConfigRequest``,
 * ``GetStatusRequest``) resolve to ``undefined`` instead of erroring
 * on the ``["data"]`` index — TypeScript can't index a union where
 * some members lack the key.
 */
export type PythonCall = {
	<T = unknown, K extends PythonRequest["type"] = PythonRequest["type"]>(
		type: K,
		data?: "data" extends keyof Extract<PythonRequest, { type: K }>
			? Extract<PythonRequest, { type: K }>["data"]
			: undefined,
	): Promise<T>;
	<T = unknown>(type: string, data?: Record<string, unknown>): Promise<T>;
};

export function usePython() {
	const call = useCallback(
		async <T = unknown>(
			type: string,
			data?: Record<string, unknown>,
		): Promise<T> => {
			const api = window.python;
			if (!api) throw new Error("Python bridge not available");
			//race the underlying bridge call against a per-command
			// timeout so a hung trivial command (e.g. `get_status`) surfaces
			// an error in seconds instead of the prior blanket 120s timeout
			// imposed by the Electron main / Rust host. The underlying
			// promise may still resolve later; the caller sees the timeout
			// rejection first.
			//
			//Tauri/Electron error-envelope normalization. On
			// Tauri v2, `invoke` rejects with a RAW STRING (not an Error)
			// when the Rust `dispatch` command returns an Err — the host's
			// `e.to_string()` becomes the rejection value verbatim. Callers
			// that guard with `err instanceof Error ? err.message : String(err)`
			// work, but callers that do `err.message` directly
			// (e.g. `Microphone.tsx:278`, `lib/utils/models.ts:252`) read
			// `undefined` and lose the server error message. We wrap the
			// `await withCommandTimeout` call in try/catch and re-throw:
			//   - Error instances propagate unchanged (no double-wrapping);
			//   - string rejections are normalized into `new Error(string)`;
			//   - other shapes (numbers, objects) become `new Error("unknown IPC error")`.
			// The catch ALSO swallows the post-rejection envelope checks
			// below — on Tauri the await throws before we ever inspect the
			// resolved value (the in-code `_error`/`type:"error"` checks
			// are Electron-path-only, see the comment below).
			let result: Record<string, unknown>;
			try {
				result = (await withCommandTimeout(
					api.call({ type, data }),
					type,
				)) as Record<string, unknown>;
			} catch (err) {
				if (err instanceof Error) throw err;
				// VP-6: on Tauri the Rust `dispatch` command rejects the
				// invoke promise with a raw STRING — for structured errors
				// it's the JSON-serialized `{type:"error", data:{code,
				// message}}` envelope (sidecar_cmds/dispatch.rs). Parse it
				// so `err.code` is stamped and callers that branch on the
				// failure class work on Tauri exactly as they do on
				// Electron (previously the whole JSON string became the
				// message and `code` was dropped, so
				// `err.code === "command_timeout"` checks silently fell
				// through on Tauri).
				if (typeof err === "string") {
					const parsed = parseTauriErrorEnvelope(err);
					if (parsed) throw parsed;
					throw new Error(err);
				}
				throw new Error("unknown IPC error");
			}
			//(d-review ): handle BOTH error
			// envelope shapes that can flow back over the Electron
			// path, surfacing each as a real JS Error so callers
			// using `try { await python.call(...) } catch (e) {}`
			// see failures instead of silently treating the error
			// envelope as a successful result (which previously left
			// callers reading `undefined` from data fields).
			//
			//   1. `{_error: "..."}` — Electron main-process synthetic
			//      errors (index.ts:1908/1911/1916): backend-not-
			//      connected and sendToPython exceptions. `_error` is
			//      a STRING in the actual Electron code; we also
			//      accept `{message: "..."}` defensively.
			//   2. `{type:"error", data:{code, message}}` — Python
			//      server unhandled-dispatch exceptions
			//      (ipc_server.py:1044-1050). The Electron main
			//      process resolves the pending request with this
			//      object verbatim (it does NOT translate it into
			//      `{_error: ...}`).
			//
			// On Tauri, NEITHER in-code check is reachable: the Rust
			// `dispatch` command (main.rs:954-965) rejects the
			// `invoke` promise on `type:"error"` (and never produces
			// `{_error:...}`), so `await api.call(...)` throws before
			// we ever inspect the resolved value. The checks below
			// are therefore Electron-path-only — DEAD CODE on Tauri,
			// but harmless (and the unified error shape keeps
			// caller-facing behavior consistent across both runtimes).
			// Errors on Tauri propagate as-is from the Rust rejection
			// (no double-wrapping) — the `await` throws and we never
			// reach the envelope inspection.
			if (result && typeof result === "object" && "_error" in result) {
				const e = (result as { _error?: unknown })._error;
				const msg =
					typeof e === "string"
						? e
						: ((e as { message?: string } | null)?.message ?? "unknown error");
				//surface the structured ``_code`` field
				// (e.g. ``command_timeout``,
				// ``backend_not_connected``,
				// ``backend_exited_early``) so callers can branch
				// on retry / surface-toast / escalate. Pre-fix,
				// the envelope's ``_code`` was dropped on the
				// floor and every error became a plain
				// ``new Error(msg)`` — consumers could not
				// distinguish transient timeouts from fatal
				// backend-exited errors.
				const code = (result as { _code?: PythonCallErrorCode })._code;
				const err = new Error(msg);
				if (typeof code === "string" && code.length > 0) {
					(err as { code?: string }).code = code;
				}
				throw err;
			}
			if (
				result &&
				typeof result === "object" &&
				(result as { type?: unknown }).type === "error"
			) {
				// Surface the FULL ``data.errors`` list when
				// present so multi-field validation failures (e.g.
				// batched Settings → Audio saves with 3 invalid
				// fields) don't require 3 fix-and-resubmit cycles.
				// ``data.message`` is kept as ``errors[0]`` for
				// backward compat with older renderers; new
				// renderers (useSettingsConfig) prefer
				// ``err.errors`` (joined) when present.
				const data = (
					result as {
						data?: {
							message?: string;
							errors?: string[];
							code?: string;
							// Structured consent fields carried by
							// ``client.consent_required`` envelopes (see
							// HandlerBase._respond_with_error +
							// ConsentRequiredError.to_dict). Preserved onto
							// the thrown Error so the renderer can
							// deep-link to the EXACT Settings toggle.
							consent_field?: unknown;
							engine_name?: unknown;
							model_id?: unknown;
						};
					}
				).data;
				const msg = data?.message ?? "unknown error";
				const errs = Array.isArray(data?.errors)
					? (data?.errors as string[])
					: undefined;
				// Preserve the structured ``code`` (e.g.
				// ``client.consent_required``) onto the thrown Error
				// so callers can branch on the failure class instead
				// of substring-matching the message. Mirrors the
				// ``_error``/``_code`` handling above — without this,
				// the ``client.consent_required`` envelope from the
				// level-monitor / mic-test handlers is indistinguishable
				// from a generic ``internal_error`` and the renderer
				// shows a misleading generic toast.
				const code = data?.code;
				const err = new Error(msg);
				if (typeof code === "string" && code.length > 0) {
					(err as { code?: string }).code = code;
				}
				if (errs && errs.length > 0) {
					(err as { errors?: string[] }).errors = errs;
				}
				// Preserve the structured consent fields (consent_field /
				// engine_name / model_id) the backend attaches to
				// ``client.consent_required`` envelopes so callers can
				// deep-link to the exact Settings toggle (e.g. the
				// level-monitor / mic-test handlers raise
				// ``ConsentRequiredError`` with
				// ``consent_field="voice_biometric_consent"``). Only
				// ``consent_field`` is consumed by the deep-link, but the
				// siblings ride along for completeness / diagnostics.
				const consentField = data?.consent_field;
				const engineName = data?.engine_name;
				const modelId = data?.model_id;
				if (typeof consentField === "string" && consentField.length > 0) {
					(err as { consent_field?: string }).consent_field = consentField;
				}
				if (typeof engineName === "string" && engineName.length > 0) {
					(err as { engine_name?: string }).engine_name = engineName;
				}
				if (typeof modelId === "string" && modelId.length > 0) {
					(err as { model_id?: string }).model_id = modelId;
				}
				throw err;
			}
			return result as T;
		},
		[],
	) as PythonCall;

	//previously this hook also returned ``isReady: !!api``.
	// That flag was always ``true`` in production because the preload
	// script installs ``window.python`` before the React app mounts, so
	// every consumer's ``if (!isReady) return`` guard was dead code.
	// Worse, the name suggested "Python backend is ready" when it
	// actually meant "Python bridge exists" — callers that wanted real
	// readiness should track ``connectionStatus === 'connected'`` in
	// App.tsx (which probes the backend via ``get_config``).
	//
	// If a future caller needs to distinguish "bridge installed" from
	// "bridge missing" (e.g. running outside Electron), they can do
	// ``const api = window.python`` and
	// check ``!!api`` directly.  We don't expose a misleading flag.
	return { call };
}

/**
 * Subscribe to a Python push event of the given ``type``.
 *
 * The ``handler`` is called with the event ``data`` (if any) each time a
 * matching event arrives.  It may optionally return a cleanup function
 * () which is invoked:
 *
 *   1. Before the **next** matching event's handler runs — so rapid
 *      successive events don't accumulate stale async work (e.g. the
 *      ``reloadHotkey`` chain in ``Home.tsx`` can cancel its in-flight
 *      ``get_config`` via a per-invocation ``cancelled`` flag).
 *   2. When the subscription is torn down (unmount, ``type`` change, or
 *      bridge going away) — so resources acquired by the most recent
 *      invocation are released.
 *
 * Handlers that return ``void`` (the common case) keep working unchanged:
 * ``typeof cleanup === "function"`` guards every call site, so a missing
 * return value is a no-op.
 *
 * The handler identity is mirrored into a ref so callers can pass an inline
 * closure without re-subscribing on every render (only ``type`` and
 * ``bridgeReady`` are effect deps).
 *
 * : the first overload is now generic AND narrows ``data`` to the
 * per-event payload shape declared in ``types/ipc/push_events.ts`` (e.g.
 * ``TranscriptionFinalEvent.data: { text: string }``).
 * For events with NO ``data`` field (e.g. ``RecordingStartedEvent``),
 * ``ExtractEventData<K>`` resolves to ``undefined``, so the handler is
 * typed as ``(data?: undefined) => ...`` — callers that ignore ``data``
 * still compile. Existing callers that pass an explicit
 * ``(data?: Record<string, unknown>) => ...`` closure still compile
 * because every per-event ``data`` shape in ``types/ipc/push_events.ts``
 * is assignable to ``Record<string, unknown>`` (they're all object
 * literals), and function-param contravariance (strictFunctionTypes)
 * makes a wider-accepting function assignable to a narrower-accepting
 * one. New callers can opt into the narrowed shape by writing the
 * handler param as ``(data) => ...`` with no explicit type annotation —
 * TS infers ``ExtractEventData<K>`` from the ``type`` argument.
 */
export function usePythonEvent<K extends PythonPushEvent["type"]>(
	type: K,
	handler: (data?: ExtractEventData<K>) => (() => void) | undefined,
): void;
/**
 * : overload accepting an arbitrary ``string`` for forward-compat
 * with backend-added events not yet in the ``PythonPushEvent`` union.
 *
 * The narrow first overload catches typos at compile time for the
 * events we know about (e.g. ``usePythonEvent("transcription_final",
 * ...)`` — ``"transcription_final"`` is in the union, so a typo like
 * ``"past_failed"`` would fail). This second overload accepts any
 * string so the renderer can subscribe to events the backend ships
 * before the renderer's type definitions catch up — at the cost of
 * losing compile-time typo detection for those new events. Callers
 * that pass a string literal matching the union hit the first overload
 * (TS picks the first matching overload); only unknown literals fall
 * through to this one.
 */
export function usePythonEvent(
	type: string,
	handler: (data?: Record<string, unknown>) => (() => void) | undefined,
): void;
export function usePythonEvent(
	type: string,
	// Implementation signature — must use `any` for the handler's data
	// param because TypeScript's overload compatibility check requires
	// the impl to accept ALL overload handler shapes. Overload 1 narrows
	// to `ExtractEventData<K>` (which can be `undefined` for events with
	// no data); overload 2 widens to `Record<string, unknown>`. No single
	// non-`any` type satisfies both under strictFunctionTypes contravariance
	// (a function accepting `ExtractEventData<K>` is not assignable to a
	// parameter expecting a function accepting `Record<string, unknown>`,
	// and vice versa). The `any` here is type-safe at the CALL SITE —
	// callers hit the public overloads, not this impl signature — and at
	// runtime `event.data` is `Record<string, unknown> | undefined` which
	// every handler accepts. biome-ignore lint/noExplicitAny: required for
	// TypeScript overload compatibility (see comment above).
	// biome-ignore lint/suspicious/noExplicitAny: required for TS overload impl
	handler: (data?: any) => (() => void) | undefined,
) {
	const handlerRef = useRef(handler);
	handlerRef.current = handler;

	//dev-time typo warning. Overload 2 (above) accepts any
	// `string` for forward-compat with backend-added events not yet
	// in `PythonPushEvent`. The cost is that a typo like
	// `usePythonEvent("past_failed", ...)` (intended
	// `"paste_failed"`) silently falls through to Overload 2 and
	// compiles — but the subscription never fires because the
	// backend never emits `past_failed`. The `KNOWN_EVENT_TYPES`
	// set below mirrors the `PythonPushEvent` union in
	// `types/ipc/push_events.ts` (kept in sync manually — TS
	// can't enumerate union members at runtime). When a `type`
	// argument isn't in the set, emit a `console.warn` so the
	// typo surfaces in the dev console (and the Electron
	// main-process log via `webContents.on("console-message")`).
	// The warning is dev-only — production builds skip the check
	// (`import.meta.env.DEV` is `false` in production per Vite).
	if (import.meta.env.DEV && !KNOWN_EVENT_TYPES.has(type)) {
		console.warn(
			`[usePythonEvent] subscribing to unknown event "${type}" — ` +
				`if this is a typo, fix it; if it's a new backend event, ` +
				`add it to PythonPushEvent in types/ipc/push_events.ts ` +
				`and to KNOWN_EVENT_TYPES in hooks/usePython.ts`,
		);
	}

	//track `window.python` presence so the effect re-runs when the
	// bridge becomes available after mount. Previously the effect's only
	// dependency was `[type]`, so if `window.python` was unset at mount
	// (e.g. slow preload / late Tauri bridge install), the subscription
	// was never re-attempted and events were silently dropped.
	const bridgeReady = useBridgeReady();

	useEffect(() => {
		//short-circuit until the bridge is installed. Without this
		// guard the effect would call `api.onEvent` on a still-undefined
		// `window.python` and silently drop the subscription; including
		// `bridgeReady` in the dep array (below) is what makes React
		// re-run this effect once the bridge comes online.
		if (!bridgeReady) return;
		const api = window.python;
		if (!api) return; // defensive double-check (bridgeReady mirrors window.python presence)

		//register with the module-level dispatcher instead
		// of subscribing to `api.onEvent` directly. The dispatcher
		// holds a SINGLE `api.onEvent` subscription shared across
		// all `usePythonEvent` callers and fan-outs to per-type
		// subscribers via a `Map<type, Set<entry>>`. This
		// eliminates the N-listener multiplication: previously N
		// callers created N subscriptions (4N Tauri event
		// listeners on Tauri), and every event triggered all N
		// callbacks only to be filtered by the
		// `if (event.type === type)` check. Now N callers share
		// 1 subscription and the Map lookup is O(1) per event.
		//
		// The dispatcher preserves all existing semantics:
		//the cleanup returned by the previous
		//     handler invocation is run BEFORE the next matching
		//     event's handler (cancelling in-flight async work)
		//     and on unsubscribe (releasing resources). This is
		//     now stored in `entry.cleanupRef` rather than a
		//     local `currentCleanup` variable.
		//a throwing handler is caught and logged
		//     so it doesn't escape into the dispatch loop.
		//   - The handler identity is mirrored via `handlerRef`
		//     so callers can pass inline closures without
		//     re-subscribing on every render.
		const unsubscribe = subscribeToEventType(type, () => handlerRef.current);

		return () => {
			unsubscribe();
		};
		// `bridgeReady` is included so the effect re-subscribes when
		// `window.python` becomes available post-mount.
	}, [type, bridgeReady]);
}
