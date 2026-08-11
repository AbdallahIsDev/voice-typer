/**
 * Render-process-gone crash-storm tracking — sliding 60s window.
 *
 * Extracted from `main-window.ts` (split). Shared by both the
 * main window (`createMainWindow()`) and the bubble window
 * (`bubble/lifecycle.ts::createBubbleWindow()`). Lives in its own
 * module so the two window files don't have to import from each other
 * just to share crash-storm tracking state.
 *
 * If more than `RENDER_CRASH_THRESHOLD` crashes land in
 * `RENDER_CRASH_WINDOW_MS`, the caller should stop reloading and show a
 * dialog instead of entering a CPU-bound crash loop.
 *
 * Public API:
 *   - `recordRenderCrash(timestamps, label, prefix)` — internal helper;
 *     returns true when the sliding window is over the threshold. The
 *     `prefix` is the log-line window tag (e.g. `[MAIN]` / `[BUBBLE]`,
 *     HU-29) so storm lines are attributed to the right window.
 *   - `recordMainWindowRenderCrash()` / `recordBubbleRenderCrash()` —
 *     window-specific wrappers that pin the timestamp array + log label.
 *   - `_resetRenderCrashTrackingForTest()` — test seam that clears both
 *     window arrays; used by `__tests__/crash-storm-recovery.test.ts`.
 */
import { log } from "../logging";

const RENDER_CRASH_WINDOW_MS = 60_000;
const RENDER_CRASH_THRESHOLD = 5;
const _mainWindowCrashTimestamps: number[] = [];
const _bubbleWindowCrashTimestamps: number[] = [];

function recordRenderCrash(
	timestamps: number[],
	label: string,
	prefix: string,
): boolean {
	const now = Date.now();
	timestamps.push(now);
	while (timestamps.length > 0) {
		const first = timestamps[0];
		if (first !== undefined && now - first > RENDER_CRASH_WINDOW_MS) {
			timestamps.shift();
		} else {
			break;
		}
	}
	if (timestamps.length > RENDER_CRASH_THRESHOLD) {
		log.error(
			`${prefix} ${label} render-process-gone storm: ${timestamps.length} crashes in ${RENDER_CRASH_WINDOW_MS / 1000}s - stopping reload`,
		);
		return true;
	}
	return false;
}

export function _resetRenderCrashTrackingForTest(): void {
	_mainWindowCrashTimestamps.length = 0;
	_bubbleWindowCrashTimestamps.length = 0;
}

/** Render-process-gone: main-window-side wrapper. */
export function recordMainWindowRenderCrash(): boolean {
	return recordRenderCrash(_mainWindowCrashTimestamps, "Main", "[MAIN]");
}

/** Render-process-gone: bubble-window-side wrapper. */
export function recordBubbleRenderCrash(): boolean {
	return recordRenderCrash(_bubbleWindowCrashTimestamps, "Bubble", "[BUBBLE]");
}
