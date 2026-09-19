// `usePythonEvent`, typed subscription hook for Python push events.
// bridge modules live by concern under `lib/python-bridge/`. The
// public API is unchanged: consumers keep importing
// `{ usePythonEvent }` from `@/hooks/usePython`.

import { useEffect, useRef } from "react";
import type { PythonPushEvent } from "@/types/ipc";
import { useBridgeReady } from "./bridge-ready";
import { type EventHandler, subscribeToEventType } from "./event-dispatcher";
import { KNOWN_EVENT_TYPES } from "./known-event-types";

type ExtractEventData<K extends PythonPushEvent["type"]> =
	Extract<PythonPushEvent, { type: K }> extends { data: infer D }
		? D
		: undefined;

export function usePythonEvent<K extends PythonPushEvent["type"]>(
	type: K,
	handler: (data?: ExtractEventData<K>) => (() => void) | undefined,
): void;
export function usePythonEvent(
	type: string,
	handler: (data?: Record<string, unknown>) => (() => void) | undefined,
): void;
export function usePythonEvent<K extends PythonPushEvent["type"]>(
	type: K,
	// Implementation signature, identical to overload 1 (this is
	// the only non-`any` shape TypeScript's overload compatibility
	// check accepts for it: a plain `Record<string, unknown>` param
	// or a widened union both fail TS2394 against overload 1's
	// deferred conditional `ExtractEventData<K>`). Callers never
	// see this signature, they hit the public overloads above,
	// and unknown `string` types fall through to overload 2.
	handler: (data?: ExtractEventData<K>) => (() => void) | undefined,
) {
	const handlerRef = useRef(handler);
	handlerRef.current = handler;

	// Dev-time typo warning. Overload 2 (above) accepts any
	// `string` for forward-compat with backend-added events not yet
	// in `PythonPushEvent`. The cost is that a typo like
	// `usePythonEvent("past_failed", ...)` (intended
	// `"paste_failed"`) silently falls through to Overload 2 and
	// compiles, but the subscription never fires because the
	// backend never emits `past_failed`. The `KNOWN_EVENT_TYPES`
	// set below mirrors the `PythonPushEvent` union in
	// `types/ipc/push_events.ts` (kept in sync manually, TS
	// can't enumerate union members at runtime). When a `type`
	// argument isn't in the set, emit a `console.warn` so the
	// typo surfaces in the dev console (and the predecessor
	// main-process log via `webContents.on("console-message")`).
	// The warning is dev-only, production builds skip the check
	// (`import.meta.env.DEV` is `false` in production per Vite).
	if (import.meta.env.DEV && !KNOWN_EVENT_TYPES.has(type)) {
		console.warn(
			`[renderer:usePython] subscribing to unknown event "${type}", ` +
				`if this is a typo, fix it; if it's a new backend event, ` +
				`add it to PythonPushEvent in types/ipc/push_events.ts ` +
				`and to KNOWN_EVENT_TYPES in lib/python-bridge/known-event-types.ts`,
		);
	}

	// Track `window.python` presence so the effect re-runs when the
	// dependency was `[type]`, so if `window.python` was unset at mount
	// (e.g. slow preload / late Tauri bridge install), the subscription
	// was never re-attempted and events were silently dropped.
	const bridgeReady = useBridgeReady();

	useEffect(() => {
		// Short-circuit until the bridge is installed. Without this
		// guard the effect would call `api.onEvent` on a still-undefined
		// `window.python` and silently drop the subscription; including
		// `bridgeReady` in the dep array (below) is what makes React
		// re-run this effect once the bridge comes online.
		if (!bridgeReady) return;
		const api = window.python;
		if (!api) return; // defensive double-check (bridgeReady mirrors window.python presence)

		// Register with the module-level dispatcher instead
		// of subscribing to `api.onEvent` directly. The dispatcher
		// holds a SINGLE `api.onEvent` subscription shared across
		// all `usePythonEvent` callers and fan-outs to per-type
		// subscribers via a `Map<type, Set<entry>>`. This
		// callers created N subscriptions (4N Tauri event
		// listeners on Tauri), and every event triggered all N
		// callbacks only to be filtered by the
		// `if (event.type === type)` check. Now N callers share
		// 1 subscription and the Map lookup is O(1) per event.
		// The dispatcher preserves all existing semantics:
		// the cleanup returned by the previous
		//     handler invocation is run BEFORE the next matching
		//     event's handler (cancelling in-flight async work)
		//     and on unsubscribe (releasing resources). This is
		//     now stored in `entry.cleanupRef` rather than a
		//     local `currentCleanup` variable.
		// a throwing handler is caught and logged
		//     so it doesn't escape into the dispatch loop.
		//   - The handler identity is mirrored via `handlerRef`
		//     so callers can pass inline closures without
		//     re-subscribing on every render.
		// Type boundary: the dispatcher stores handlers in a
		// `Map<type, Set<entry>>` keyed by runtime strings, so
		// its `EventHandler` type cannot express the per-type
		// narrowing this hook's overload 1 gives callers. The
		// single `as` cast below is that boundary, it asserts
		// what the overloads already promise: the dispatcher
		// calls the handler with the event's `data` payload
		// (`Record<string, unknown> | undefined` on the wire),
		// and overload-1 callers trust the declared per-event
		// payload shape from `types/ipc/push_events.ts`.
		// Narrower than the previous `any` impl param: the cast
		// affects only this one expression, not the whole
		// signature.
		const unsubscribe = subscribeToEventType(
			type,
			() => handlerRef.current as EventHandler,
		);

		return () => {
			unsubscribe();
		};
		// `bridgeReady` is included so the effect re-subscribes when
		// `window.python` becomes available post-mount.
	}, [type, bridgeReady]);
}
