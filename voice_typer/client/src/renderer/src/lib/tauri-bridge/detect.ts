// src/renderer/src/lib/tauri-bridge/detect.ts
//
// ADR-0020 §6.3 (Phase 3 UI port): Tauri runtime detection + minimal
// typing for the `window.__TAURI__` global API surface.
//
// This module owns the three concerns shared by every namespace installer
// (python / bubble / window_):
//   1. `TauriGlobal` / `TauriEvent` — minimal structural types for the
//      shape injected by `tauri::Builder` when `app.withGlobalTauri = true`
//      (see tauri.conf.json). We deliberately avoid pulling in
//      `@tauri-apps/api` as a dep to keep the renderer bundle lean.
//   2. `isTauri()` — single source of truth for "are we inside a Tauri
//      WebView?". Returns false under Electron (where the preload script
//      already installed the three namespaces via `contextBridge`).
//   3. `makeListener()` — race-safe subscribe/unlisten factory that
//      eliminates the 8× listener boilerplate previously duplicated across
//      `bubble.onLevel` / `onShow` / `onHide` / `onDraggable` / `onConfig` /
//      `onSetState` / `python.onEvent` / `window_.onMaximizedChanged`.
//
// All three pieces are pure (no `window` mutation) so they can be unit
// tested without polluting the global jsdom `window`.

/* XS-88: the previous `eslint-disable @typescript-eslint/no-explicit-any`
 * directive was removed — the project uses biome (ESLint is not installed)
 * and the file uses `unknown` casts (no `any`), so the directive was stale.
 * Tauri's global API surface is untyped in our TS context (we deliberately
 * avoid pulling in @tauri-apps/api as a dep to keep the bundle lean). We
 * declare a minimal structural type below. */

// ─── Minimal Tauri v2 global API type ─────────────────────────────────
// Mirrors the shape injected by `tauri::Builder` when
// `app.withGlobalTauri = true` (see tauri.conf.json). Only the methods
// we actually use are declared.

export interface TauriEvent<T> {
	event: string;
	payload: T;
	id: number;
}

export interface TauriGlobal {
	core: {
		invoke<T = unknown>(
			cmd: string,
			args?: Record<string, unknown>,
		): Promise<T>;
	};
	event: {
		listen<T = unknown>(
			event: string,
			handler: (e: TauriEvent<T>) => void,
		): Promise<() => void>;
	};
	window: {
		getCurrentWindow: () => {
			minimize: () => Promise<void>;
			toggleMaximize: () => Promise<void>;
			close: () => Promise<void>;
			isMaximized: () => Promise<boolean>;
			onResized: (handler: () => void) => Promise<() => void>;
		};
	};
}

// ─── Detector ─────────────────────────────────────────────────────────

/**
 * Returns true if the renderer is running inside a Tauri WebView
 * (`window.__TAURI__` is present with `core.invoke`). When false, the
 * Electron preload has already installed the bridge namespaces.
 *
 * Defensive against partial / future Tauri globals that lack the invoke
 * method — those are treated as Electron (no-op), not crashed on. This
 * is the contract asserted by `tauri-bridge-detection.test.ts:196`.
 */
export function isTauri(): boolean {
	return (
		typeof window !== "undefined" &&
		!!(window as unknown as { __TAURI__?: TauriGlobal }).__TAURI__?.core?.invoke
	);
}

/**
 * Return the Tauri global. Caller must have already verified `isTauri()`
 * — this helper throws if `__TAURI__` is missing so a misuse surfaces
 * loudly instead of silently no-op-ing.
 */
export function getTauri(): TauriGlobal {
	const tauri = (window as unknown as { __TAURI__?: TauriGlobal }).__TAURI__;
	if (!tauri?.core?.invoke) {
		throw new Error(
			"getTauri() called outside Tauri runtime — guard with isTauri() first",
		);
	}
	return tauri;
}

// ─── makeListener factory ─────────────────────────────────────────────

/**
 * Race-safe subscribe/unlisten factory.
 *
 * Every Tauri event subscription follows the same pattern:
 *   1. Call `tauri.event.listen(name, handler)` which returns a Promise
 *      of an `unlisten` function (Tauri v2 registers listeners async).
 *   2. Track the unlisten in a closure variable, set a `cancelled` flag
 *      so the consumer can cancel before the subscribe promise resolves.
 *   3. On cleanup: set `cancelled`, then if `unlisten` has arrived, call
 *      it; otherwise the `.then` block will see `cancelled` and call the
 *      freshly-arrived unlisten.
 *
 * Previously this 12-line block was duplicated 8× across the bridge
 * (one per event subscription). `makeListener` collapses the boilerplate
 * to a single source of truth and makes the cancellation race testable
 * in isolation.
 *
 * @param subscribe Receives a `handler` that the caller wires to the
 *   underlying event source (e.g. `tauri.event.listen("bubble_level",
 *   (e) => handler(e.payload))`). Returns the unlisten promise.
 * @param handler   Called with the payload each time the event fires.
 *   This is the consumer's callback — for `onLevel(cb)` it's `cb`, for
 *   `onMaximizedChanged(cb)` it's also `cb` (after the subscribe wrapper
 *   queries `isMaximized()` and forwards the boolean).
 * @returns         A synchronous cleanup function. Idempotent — safe to
 *   call multiple times.
 */
export function makeListener<T>(
	subscribe: (handler: (payload: T) => void) => Promise<() => void>,
	handler: (payload: T) => void,
): () => void {
	let unlisten: (() => void) | null = null;
	let cancelled = false;
	// G4-M-70 (security/observability): if the underlying
	// ``tauri.event.listen(...)`` promise rejects (e.g. the event
	// channel closed mid-subscribe, the Tauri host is shutting
	// down, or a malformed event name slipped through), log the
	// rejection instead of letting it surface as an unhandled
	// promise rejection. Without this catch the rejection would
	// bubble up as a "Tauri error" in the renderer console with
	// no contextualising prefix; with it, the failure is tagged
	// with the bridge identity so the Electron main-process log
	// (forwarded via ``webContents.on("console-message")``)
	// surfaces a clear diagnostic. The cancellation logic in the
	// ``.then`` block is unaffected: a rejected subscribe never
	// resolves, so ``unlisten`` stays ``null`` and the cleanup
	// function is a no-op (which is correct — there's nothing to
	// unlisten).
	subscribe(handler)
		.then((un) => {
			if (cancelled) un();
			else unlisten = un;
		})
		.catch((err) =>
			console.warn("[tauri-bridge] listener subscribe failed:", err),
		);
	return () => {
		cancelled = true;
		if (unlisten) {
			unlisten();
			unlisten = null;
		}
	};
}
