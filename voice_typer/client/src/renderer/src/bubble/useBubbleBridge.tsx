/**
 * Bubble overlay package — `useBubbleBridge` hook.
 *
 * Centralises the bubble renderer's IPC subscriptions so each
 * `window.bubble` event (`onShow` / `onHide` / `onSetState` /
 * `onConfig` / `onDraggable` / `onLevel`) is subscribed EXACTLY ONCE
 * per `<Bubble>` mount. Consumers (`useBubbleLifecycle`,
 * `useBubbleStateMachine`, `useAudioLevels`, `useThemeSync`, and
 * `Bubble.tsx` itself) register event handlers via the bridge's
 * `on(event, handler)` API instead of each calling
 * `api.onShow(...)` / `api.onSetState(...)` / etc. directly.
 *
 * Pre-refactor, the bubble package registered 11 separate IPC
 * listeners across 3 hooks + 1 component (`useBubbleLifecycle` × 2,
 * `useBubbleStateMachine` × 3, `useAudioLevels` × 3,
 * `useThemeSync` × 1, `Bubble.tsx` × 2). Each subscription was a
 * separate Electron IPC listener on the BrowserWindow's
 * `webContents`; every event the main process emitted was
 * marshalled to N listeners even when only one of them cared about
 * that event. The bridge consolidates to 1 listener per event
 * channel; the per-consumer fan-out happens in-process (cheap Set
 * iteration).
 *
 * The bridge is shared across all consumers via React Context. A
 * single `<BubbleBridgeProvider>` wraps the bubble tree (installed
 * by `Bubble.tsx`); descendants call `useBubbleBridge()` to obtain
 * the bridge instance. The bridge instance is created lazily on
 * first mount and torn down (all IPC listeners unsubscribed) when
 * the provider unmounts.
 *
 * `onLevel` is special: the Python backend pushes audio-peak events
 * at ~50-60 Hz while the recorder is running, which is pure waste
 * when the bubble is in `transcribing` / `idle` / `error` / `fading`
 * mode (the visualizer doesn't render those peaks). The bridge
 * exposes `setLevelActive(boolean)` so `useAudioLevels` can
 * dynamically toggle the underlying `api.onLevel` subscription on
 * entry to / exit from recording mode — preserving the dynamic-gate
 * optimisation from the pre-refactor implementation.
 */

import type { ReactNode } from "react";
import { createContext, useContext, useEffect, useMemo, useRef } from "react";
import type { BubbleWindowBubble } from "@/types/ipc";
import {
	type BubbleMode,
	nextBubbleMode,
	parseSetStatePayload,
} from "./constants";

// ── Event map ───────────────────────────────────────────────────────

/**
 * Mapping of bridge event name → payload type. `undefined` payload means
 * the event carries no data (the handler takes no args).
 */
export interface BubbleBridgeEventMap {
	show: undefined;
	hide: undefined;
	setState: unknown;
	config: Record<string, unknown>;
	level: { rms: number; peak: number };
	draggable: boolean;
}

type BubbleBridgeEventName = keyof BubbleBridgeEventMap;

type BubbleBridgeHandler<K extends BubbleBridgeEventName> = (
	payload: BubbleBridgeEventMap[K],
) => void;

/** Unsubscribe function returned by `bridge.on(...)`. */
export type BubbleBridgeOff = () => void;

/**
 * Public surface of the bubble bridge. Consumers obtain this via
 * `useBubbleBridge()` and register handlers via `on(event, handler)`.
 */ export interface BubbleBridge {
	/**
	 * Register a handler for a bubble event. Returns an unsubscribe
	 * function — call it on cleanup to remove the handler.
	 *
	 * Handlers are stored in a `Set` and invoked in insertion order
	 * on emit. Exceptions in one handler do NOT block subsequent
	 * handlers (each is wrapped in try/catch with a console.warn).
	 */
	on<K extends BubbleBridgeEventName>(
		event: K,
		handler: BubbleBridgeHandler<K>,
	): BubbleBridgeOff;

	/**
	 * Read the CURRENT authoritative bubble mode (IN-62).
	 *
	 * The bridge owns the single source-of-truth mode ref, updated by
	 * `nextBubbleMode` BEFORE handlers fan out — so a handler invoked
	 * by an event observes that event's resulting mode synchronously,
	 * regardless of handler registration order. Consumers that need to
	 * gate behaviour on the mode (e.g. `useAudioLevels`'s rAF loop +
	 * dynamic `onLevel` subscription) read this instead of maintaining
	 * their own duplicate tracker.
	 */
	getMode(): BubbleMode;

	/**
	 * Toggle the underlying `api.onLevel` IPC subscription. When
	 * `active` is true (and the bridge has an `api.onLevel`), the
	 * bridge subscribes; when false, it unsubscribes. Idempotent —
	 * calling with the same value twice is a no-op.
	 *
	 * This is the dynamic-gate hook `useAudioLevels` uses to avoid
	 * receiving ~50-60 Hz audio-peak IPC events while the bubble is
	 * NOT in recording mode.
	 */
	setLevelActive(active: boolean): void;
}

// ── Internal implementation ─────────────────────────────────────────

/**
 * Concrete bridge implementation. Owns:
 *   - The per-event handler `Set`s (populated by `on()`).
 *   - The single `api.onX` subscriptions (installed in `attach()`).
 *   - The dynamic `api.onLevel` subscription (toggled by
 *     `setLevelActive()`).
 *
 * Created lazily by `BubbleBridgeProvider` and torn down on unmount.
 */
class BubbleBridgeImpl implements BubbleBridge {
	private readonly handlers: {
		[K in BubbleBridgeEventName]?: Set<BubbleBridgeHandler<K>>;
	} = {};

	private api: BubbleWindowBubble | undefined;
	private apiOffs: BubbleBridgeOff[] = [];
	private levelOff: BubbleBridgeOff | null = null;
	private levelActive = false;
	// Authoritative bubble mode (IN-62 single source of truth).
	// Defaults to `recording` — the bubble's initial mode — and tracks
	// the show / hide / setState event stream via `nextBubbleMode`,
	// updated in `emit()` BEFORE handlers fan out. Persists across
	// attach/detach cycles (mirroring `useBubbleStateMachine`'s React
	// state, which also survives re-attach).
	private mode: BubbleMode = "recording";

	constructor(api: BubbleWindowBubble | undefined) {
		this.api = api;
	}

	on<K extends BubbleBridgeEventName>(
		event: K,
		handler: BubbleBridgeHandler<K>,
	): BubbleBridgeOff {
		let set = this.handlers[event] as Set<BubbleBridgeHandler<K>> | undefined;
		if (!set) {
			set = new Set<BubbleBridgeHandler<K>>();
			// Re-cast to satisfy the mapped-type write; the per-key
			// Set is homogeneously typed at runtime.
			(this.handlers as Record<string, unknown>)[event] = set;
		}
		set.add(handler);
		return () => {
			set?.delete(handler);
		};
	}

	/**
	 * Fan out an event to every registered handler. Called from the
	 * single IPC listener installed in `attach()`. Handler exceptions
	 * are swallowed + warned so one buggy consumer can't starve the
	 * others.
	 */
	private emit<K extends BubbleBridgeEventName>(
		event: K,
		payload: BubbleBridgeEventMap[K],
	): void {
		// IN-62: keep the authoritative mode ref in lockstep with the
		// event stream BEFORE any handler runs, so a consumer handler
		// always observes the current event's resulting mode — no
		// registration-order dependence. The reducer is the same
		// function `useBubbleStateMachine` uses for its React state, so
		// the two cannot drift. Unknown / non-normalizable setState
		// payloads map to `prev` (no-op) inside the reducer.
		if (event === "show") {
			this.mode = nextBubbleMode(this.mode, { type: "show" });
		} else if (event === "hide") {
			this.mode = nextBubbleMode(this.mode, { type: "hide" });
		} else if (event === "setState") {
			this.mode = nextBubbleMode(this.mode, {
				type: "setState",
				state: parseSetStatePayload(payload).state,
			});
		}

		const set = this.handlers[event] as Set<BubbleBridgeHandler<K>> | undefined;
		if (!set || set.size === 0) return;
		for (const h of set) {
			try {
				h(payload);
			} catch (e) {
				console.warn(`[bubble-bridge] ${event} handler threw:`, e);
			}
		}
	}

	getMode(): BubbleMode {
		return this.mode;
	}

	/**
	 * Install the single per-event IPC subscriptions on the given
	 * `window.bubble` API. Returns the unsubscribe functions so the
	 * caller can tear them down on unmount. The `onLevel`
	 * subscription is NOT installed here — it's gated by
	 * `setLevelActive()`.
	 *
	 * Safe to call multiple times (e.g. if `window.bubble` changes
	 * identity across renders, which it shouldn't in practice but
	 * the test suite re-assigns `window.bubble` between cases). Each
	 * call tears down the previous subscriptions before installing
	 * new ones.
	 */
	attach(api: BubbleWindowBubble): void {
		if (this.api === api && this.apiOffs.length > 0) return;
		this.detach();
		this.api = api;
		const offs: BubbleBridgeOff[] = [];
		// Defensive `?.` on every method — the bubble preload
		// guarantees these, but the cast through `unknown` + a
		// missing preload in some test contexts means the
		// optional chaining is the cheaper guard.
		if (api.onShow) {
			offs.push(api.onShow(() => this.emit("show", undefined)));
		}
		if (api.onHide) {
			offs.push(api.onHide(() => this.emit("hide", undefined)));
		}
		if (api.onSetState) {
			offs.push(api.onSetState((s) => this.emit("setState", s)));
		}
		if (api.onConfig) {
			offs.push(api.onConfig((c) => this.emit("config", c)));
		}
		if (api.onDraggable) {
			offs.push(api.onDraggable((d) => this.emit("draggable", d)));
		}
		this.apiOffs = offs;
		// Re-arm the dynamic onLevel subscription if it was active
		// before detach (e.g. mid-recording re-attach). The
		// `setLevelActive` impl is idempotent when the active flag
		// already matches, so we flip to false first to force a
		// re-subscribe under the new api.
		if (this.levelActive) {
			this.levelActive = false;
			this.setLevelActive(true);
		}
	}

	/**
	 * Tear down all IPC subscriptions installed by `attach()` EXCEPT
	 * the dynamic `onLevel` subscription (which is owned by
	 * `setLevelActive()`). Called on unmount + before re-attach.
	 */
	detach(): void {
		for (const off of this.apiOffs) {
			try {
				off();
			} catch {
				// The preload's unsubscribe is defensive
				// but warn-only — swallow late-dispatch
				// races so a stale call during cleanup
				// doesn't crash the bridge.
			}
		}
		this.apiOffs = [];
	}

	setLevelActive(active: boolean): void {
		if (active === this.levelActive) return;
		this.levelActive = active;
		const api = this.api;
		if (!api?.onLevel) return;
		if (active) {
			if (this.levelOff !== null) return;
			this.levelOff = api.onLevel((data) => this.emit("level", data));
		} else {
			if (this.levelOff === null) return;
			try {
				this.levelOff();
			} catch {
				// Same defensive swallow as detach().
			}
			this.levelOff = null;
		}
	}

	/**
	 * Full teardown — detach + drop the dynamic onLevel subscription.
	 * Called by `BubbleBridgeProvider`'s cleanup effect.
	 */
	dispose(): void {
		this.detach();
		if (this.levelOff !== null) {
			try {
				this.levelOff();
			} catch {
				// swallow
			}
			this.levelOff = null;
		}
		this.levelActive = false;
		// Clear handler sets so a stale `off()` returned by `on()`
		// (held by a long-lived consumer) is a no-op instead of a
		// use-after-dispose.
		for (const key of Object.keys(this.handlers) as BubbleBridgeEventName[]) {
			this.handlers[key]?.clear();
		}
	}
}

// ── React Context ───────────────────────────────────────────────────

const BubbleBridgeContext = createContext<BubbleBridge | null>(null);

/**
 * Provider that owns the bridge instance. Wraps the bubble tree
 * (installed by `Bubble.tsx`) so all descendant hooks can obtain the
 * bridge via `useBubbleBridge()`.
 *
 * The bridge subscribes to `window.bubble` events in a `useEffect`
 * (after mount) — child effects register handlers via `bridge.on(...)`
 * BEFORE the parent effect attaches to the API (React runs child
 * effects first), so no events are missed at startup.
 */
export function BubbleBridgeProvider({ children }: { children: ReactNode }) {
	const bridgeRef = useRef<BubbleBridgeImpl | null>(null);
	if (bridgeRef.current === null) {
		const api = window.bubble as BubbleWindowBubble | undefined;
		bridgeRef.current = new BubbleBridgeImpl(api);
	}
	const bridge = bridgeRef.current;

	useEffect(() => {
		const api = window.bubble as BubbleWindowBubble | undefined;
		if (!api) return;
		bridge.attach(api);
		return () => {
			bridge.dispose();
		};
	}, [bridge]);

	const value = useMemo<BubbleBridge>(() => bridge, [bridge]);

	return (
		<BubbleBridgeContext.Provider value={value}>
			{children}
		</BubbleBridgeContext.Provider>
	);
}

/**
 * Obtain the bubble bridge from Context. Returns `null` if called
 * outside a `<BubbleBridgeProvider>` (e.g. in a unit test that
 * renders a hook in isolation without the provider). Consumers
 * should guard against `null` and skip subscribing.
 */
export function useBubbleBridge(): BubbleBridge | null {
	return useContext(BubbleBridgeContext);
}
