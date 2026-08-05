/**
 * Shared timer / microtask helpers for the renderer vitest suite.
 *
 * Before this file existed, 10+ test files each inlined their own copy of
 *
 *   await new Promise<void>((resolve) => setTimeout(resolve, 0));
 *
 * to flush jsdom's `requestAnimationFrame` queue (jsdom schedules rAF
 * callbacks on the macrotask queue, so a single `setTimeout(0)` is enough
 * to drain them — see `bubble_rAF_pause.test.tsx`,
 * `bubble-raf-gating.test.tsx`, etc.). The duplication made it easy for
 * a contributor to drop the `await` (silently breaking the test) or to
 * swap in `await Promise.resolve()` (which only flushes microtasks, not
 * rAF callbacks, and silently misses the frame).
 *
 * `flushRaf()` collapses those copies into a single named helper whose
 * intent is obvious at the call site. Future migrations can replace the
 * 10 inline copies one at a time without behaviour drift.
 *
 * This file is intended to be imported directly by tests. It has NO
 * side effects on import.
 */

/**
 * Flush jsdom's `requestAnimationFrame` queue.
 *
 * jsdom (and the browser) schedule rAF callbacks as macrotasks, so a
 * single `setTimeout(resolve, 0)` is enough to drain every pending rAF
 * callback that was queued before this call. Use this after `act(...)`
 * blocks that trigger rAF-based effects (e.g. the Bubble's
 * `requestAnimationFrame` pause/resume gating) to make their side
 * effects visible to subsequent `expect` assertions.
 *
 * This is a NO-OP under Node's `node` test environment (no rAF), but is
 * safe to call there — `setTimeout` is always available.
 */
export function flushRaf(): Promise<void> {
	return new Promise<void>((resolve) => {
		setTimeout(resolve, 0);
	});
}
