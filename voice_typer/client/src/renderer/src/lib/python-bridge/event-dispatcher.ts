// Shared event dispatcher for `usePythonEvent` subscribers.
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

export type EventHandler = (
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
				console.error(
					"[renderer:usePython] usePythonEvent cleanup threw:",
					err,
				);
			}
		}
		try {
			entry.cleanupRef.current = entry.getHandler()(event.data);
		} catch (err) {
			// A throwing handler must not escape
			// into the dispatch loop. Log and reset so the
			// next event starts from a clean slate.
			console.error("[renderer:usePython] usePythonEvent handler threw:", err);
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
			console.warn("[renderer:usePython] dispatcher teardown failed:", err);
		}
		dispatcherState = null;
	}
	const unsubscribe = api.onEvent((event) => {
		dispatchEvent(event as { type: string; data?: Record<string, unknown> });
	});
	dispatcherState = { api, unsubscribe };
}

export function subscribeToEventType(
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
				console.error(
					"[renderer:usePython] usePythonEvent cleanup threw:",
					err,
				);
			}
		}
		// If no subscribers remain, tear down the dispatcher
		// subscription so we don't hold a dangling listener
		// (e.g. after the last component unmounts).
		if (typeSubscribers.size === 0 && dispatcherState) {
			try {
				dispatcherState.unsubscribe();
			} catch (err) {
				console.warn("[renderer:usePython] dispatcher teardown failed:", err);
			}
			dispatcherState = null;
		}
	};
}
