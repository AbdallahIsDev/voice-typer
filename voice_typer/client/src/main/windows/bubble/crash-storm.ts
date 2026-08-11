/**
 * Render-process-gone crash-storm tracker factory ( sub-finding
 * 1-B-11).
 *
 * : sliding-window crash storm detection. If a renderer crashes
 * more than `threshold` times in `windowMs`, the caller stops
 * reloading and shows a recovery dialog instead of looping forever.
 *
 * Previously the bubble and main windows each kept a private
 * `number[]` of crash timestamps plus a near-duplicate
 * `recordRenderCrash(timestamps, label)` helper in `main-window.ts`.
 * This module factors the helper into a factory so each window owns
 * an isolated tracker instance with the same shape:
 *
 *   const tracker = createCrashStormTracker("Bubble", 5, 60_000, "[BUBBLE]");
 *   if (tracker.record()) { …stop reloading… }
 *
 * `main-window.ts` migration is out of scope (different agent's
 * file); its private `recordRenderCrash` + `_bubbleWindowCrashTimestamps`
 * remain unchanged for now and continue to power the
 * `crash-storm-recovery.test.ts` source-level assertions. The
 * bubble window (`lifecycle.ts`) uses this new factory directly.
 */
import { log } from "../../logging";

export interface CrashStormTracker {
	/**
	 * Record a crash timestamp and return true if the sliding window
	 * has exceeded the threshold (i.e. the caller should stop
	 * reloading and surface a recovery dialog). Returns false
	 * otherwise (the caller should proceed with the normal
	 * reload-with-backoff path).
	 */
	record(): boolean;
	/**
	 * Clear the sliding window. Used by test helpers and any future
	 * "reset crash count after a successful reload" affordance.
	 */
	reset(): void;
}

/**
 * Create a sliding-window crash-storm tracker.
 *
 * @param label     Human-readable label inserted into the storm log
 *                  line (e.g. `"Bubble"`, `"Main"`).
 * @param threshold Crash count that, once exceeded, triggers a storm.
 *                  Legacy default was `5`.
 * @param windowMs  Sliding window length in milliseconds. Legacy
 *                  default was `60_000` (60s).
 * @param prefix    Log-level prefix for the storm line (e.g. `"[MAIN]"`
 *                  for the main window, `"[BUBBLE]"` for the bubble).
 *                  HU-29: this is a parameter — the legacy hardcoded
 *                  `[MAIN]` caused bubble crash storms to be
 *                  misattributed to the main window in the logs.
 */
export function createCrashStormTracker(
	label: string,
	threshold: number,
	windowMs: number,
	prefix: string,
): CrashStormTracker {
	const timestamps: number[] = [];
	return {
		record(): boolean {
			const now = Date.now();
			timestamps.push(now);
			// Evict timestamps older than the window. The `first !==
			// undefined` guard mirrors the original main-window.ts
			// implementation — TypeScript narrows `timestamps[0]` to
			// `number | undefined` under `noUncheckedIndexedAccess`-
			// style strictness even though we just checked `length >
			// 0`, so the explicit guard keeps the assertion honest.
			while (timestamps.length > 0) {
				const first = timestamps[0];
				if (first !== undefined && now - first > windowMs) {
					timestamps.shift();
				} else {
					break;
				}
			}
			if (timestamps.length > threshold) {
				log.error(
					`${prefix} ${label} render-process-gone storm: ${timestamps.length} crashes in ${windowMs / 1000}s - stopping reload`,
				);
				return true;
			}
			return false;
		},
		reset(): void {
			timestamps.length = 0;
		},
	};
}
