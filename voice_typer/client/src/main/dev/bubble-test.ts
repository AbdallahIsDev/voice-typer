/**
 * : dev-only bubble diagnostic harness, gated by `VT_BUBBLE_TEST=1`.
 *
 * Extracted from `index.ts:101-116` so the production wiring entry point
 * stays wiring-only (the previous inlined block created 3 unbounded
 * timers —  flagged them as not cleared on shutdown).
 *
 * `runBubbleTestDiagnostics(state)` returns a `cleanup` function that
 * clears all three timers; the caller (index.ts) is responsible for
 * invoking `cleanup()` from the `before-quit` handler so the timers
 * don't outlive a normal app shutdown.
 *
 * Uses `console.warn` (NOT the structured `log`) intentionally — this
 * is a dev-only diagnostic, the structured logger's `electron-runtime.log`
 * file tee is overkill for a flag that's never set in production.
 */
import type { MainState } from "../state";
import { showBubbleWindow } from "../windows";

/**
 * Run the bubble-window diagnostic. Caller MUST register the returned
 * `cleanup` function with the `before-quit` handler so the timers don't
 * leak ().
 *
 * Behaviour (mirrors the original index.ts:101-116 block):
 *   1. After a 1.5s delay, show the bubble window.
 *   2. Every 100ms, send a synthetic `bubble:level` event with a
 *      sin-wave RMS so the bubble's level meter exercises.
 *   3. After 10s, stop the interval (clearInterval).
 *
 * @param state shared mutable state — used to access `state.bubbleWindow`
 *        for the synthetic level emit.
 * @returns `{ cleanup }` — `cleanup` clears the `setTimeout` (if still
 *          pending) and the `setInterval` (if still running). Safe to
 *          call multiple times.
 */
export function runBubbleTestDiagnostics(state: MainState): {
	cleanup: () => void;
} {
	let intervalId: ReturnType<typeof setInterval> | null = null;
	let stopIntervalTimer: ReturnType<typeof setTimeout> | null = null;

	const startTimer = setTimeout(() => {
		showBubbleWindow();
		intervalId = setInterval(() => {
			const rms = 0.05 + 0.4 * Math.abs(Math.sin(Date.now() / 200));
			state.bubbleWindow?.webContents.send("bubble:level", {
				rms,
				peak: rms * 1.5,
			});
		}, 100);
		stopIntervalTimer = setTimeout(() => {
			if (intervalId !== null) {
				clearInterval(intervalId);
				intervalId = null;
			}
		}, 10_000);
	}, 1500);

	const cleanup = (): void => {
		clearTimeout(startTimer);
		if (intervalId !== null) {
			clearInterval(intervalId);
			intervalId = null;
		}
		if (stopIntervalTimer !== null) {
			clearTimeout(stopIntervalTimer);
			stopIntervalTimer = null;
		}
	};

	return { cleanup };
}
