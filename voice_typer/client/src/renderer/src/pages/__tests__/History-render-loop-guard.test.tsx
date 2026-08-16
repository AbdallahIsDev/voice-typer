/**
 * Regression guard for the page-level infinite render loop class of bug
 * that OOM'd a vitest worker during the axe-core scans (FATAL heap
 * OOM, whole suite died at ~356 files).
 *
 * Root cause (fixed in `hooks/useHistoryCache.ts` via the `callRef`
 * mirror): a test mock (or future code) that hands out a FRESH `call`
 * identity on every render re-fires any effect listing `call` in its
 * deps — each run re-fetches history and stores a fresh array → render
 * → new `call` → … → unbounded render loop until the heap is exhausted.
 *
 * The harness below (shared `renderLoopGuard` helper) drives the page
 * with the SAME worst-case mock shape — a NEW `call` per render — and
 * asserts the page still settles: the mount load fires EXACTLY once per
 * command and the committed render count stays bounded. If future code
 * puts an unstable value in an effect dep (or re-introduces `call`
 * directly), the load re-fires and/or the render count explodes and
 * this test fails fast — instead of the worker OOMing.
 */

import {
	type GuardCommand,
	renderLoopGuard,
} from "@/__tests__/helpers/renderLoopGuard";

const commands: GuardCommand[] = [
	{ name: "get_history", response: [] },
	{
		name: "get_today_stats",
		response: { count: 0, chars: 0, word_count: 0, duration: 0 },
	},
];

renderLoopGuard({
	id: "history",
	page: () => import("@/pages/History"),
	commands,
	// The empty-state settles in place of the loading spinner once the
	// initial load lands.
	settle: (s) => s.getByText("No dictations yet") != null,
});
