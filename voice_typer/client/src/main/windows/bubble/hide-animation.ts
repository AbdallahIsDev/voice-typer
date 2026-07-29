/**
 * Single-slot callback for the renderer's "exit animation complete"
 * signal (DR-7 extract from `bubble-window.ts`).
 *
 * The `bubble:hidden` IPC handler (registered once in
 * bubble-handlers.ts) consumes this callback atomically. The previous
 * design used `ipcMain.once("bubble:hidden", onHidden)` per hide,
 * which mutated the global IPC bus — `showBubbleWindow` had to
 * defensively `ipcMain.removeAllListeners("bubble:hidden")` to avoid
 * stale callbacks. Concentrating the registration in a module-level
 * slot removes that global side effect: the show/hide paths now just
 * clear or replace the slot, and the persistent `bubble:hidden`
 * listener stays installed exactly once for the whole app lifetime.
 *
 * DR-7: the previous module-level `currentHideAnimationCallback`
 * variable + 4 accessor functions (`onHideAnimationComplete`,
 * `clearCurrentHideAnimationCallback`,
 * `consumeHideAnimationCallback`, and the unsubscribe closure
 * returned by `onHideAnimationComplete`) are replaced by the
 * `HideAnimationSlot` class below. The 3 named functions remain
 * exported as thin wrappers around a singleton instance so existing
 * consumers (`bubble-handlers.ts`, `show-hide.ts`, and the
 * `bubble-window-fallback.test.ts` runtime tests) continue to import
 * them from `bubble-window.ts` unchanged.
 */

/**
 * Single-slot callback holder for the renderer's exit-animation-
 * complete signal.
 *
 * Semantics (preserved exactly from the legacy module-level variable):
 *   - `set(cb)` replaces the slot with `cb` and returns an
 *     unsubscribe function. The unsubscribe is *defensive*: it only
 *     clears the slot if it still points at `cb`, so a stale
 *     unsubscriber firing after a newer hide cycle has already
 *     replaced the callback is a no-op (PVT-G5-081).
 *   - `consume()` atomically retrieves AND clears the slot. Used by
 *     the persistent `bubble:hidden` IPC listener in
 *     bubble-handlers.ts so a single `bubble:hidden` event fires the
 *     callback exactly once — even if the fallback timeout already
 *     ran, the slot is already null and the IPC event becomes a
 *     no-op (and vice versa: the timeout's `unsubscribe()` clears
 *     the slot before the IPC event arrives).
 *   - `clear()` clears the slot unconditionally. Used by
 *     `showBubbleWindow()`'s rapid-toggle guard to drop a stale
 *     pending callback from an in-flight hide that's being
 *     cancelled.
 *   - `clearIfMatches(cb)` clears the slot only if it still points
 *     at `cb`. Used by the unsubscribe closure returned from
 *     `set()`.
 */
export class HideAnimationSlot {
	private current: (() => void) | null = null;

	set(cb: () => void): () => void {
		this.current = cb;
		return () => this.clearIfMatches(cb);
	}

	consume(): (() => void) | null {
		const cb = this.current;
		this.current = null;
		return cb;
	}

	clear(): void {
		this.current = null;
	}

	clearIfMatches(cb: () => void): void {
		if (this.current === cb) {
			this.current = null;
		}
	}
}

/**
 * Module-level singleton instance used by `showBubbleWindow` /
 * `hideBubbleWindow` (in `show-hide.ts`) and by the persistent
 * `bubble:hidden` IPC listener (in `bubble-handlers.ts`). The 3
 * legacy accessor functions below delegate to this instance so
 * external callers don't need to know about the class.
 */
export const currentHideAnimationSlot = new HideAnimationSlot();

/**
 * Register the renderer's exit-animation-complete callback for the
 * current hide cycle. Returns an unsubscribe function that clears the
 * slot only if it still points at `cb` (defensive against a stale
 * unsubscriber firing after a newer hide cycle has already replaced
 * the callback). Called by `hideBubbleWindow()` once per hide.
 */
export function onHideAnimationComplete(cb: () => void): () => void {
	return currentHideAnimationSlot.set(cb);
}

/**
 * Clear the current hide-animation callback unconditionally. Called by
 * `showBubbleWindow()`'s rapid-toggle guard to drop a stale pending
 * callback from an in-flight hide that's being cancelled.
 */
export function clearCurrentHideAnimationCallback(): void {
	currentHideAnimationSlot.clear();
}

/**
 * Atomically retrieve AND clear the current hide-animation callback.
 * Called by the persistent `bubble:hidden` IPC handler in
 * bubble-handlers.ts so a single `bubble:hidden` event fires the
 * callback exactly once — even if the fallback timeout already ran,
 * the slot is already null and the IPC event becomes a no-op (and
 * vice versa: the timeout's `unsubscribe()` clears the slot before the
 * IPC event arrives).
 */
export function consumeHideAnimationCallback(): (() => void) | null {
	return currentHideAnimationSlot.consume();
}
