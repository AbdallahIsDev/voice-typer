/**
 * Regression guard for the page-level infinite render loop class of bug
 * that OOM'd a vitest worker during the axe-core scans (FATAL heap
 * OOM, whole suite died at ~356 files).
 *
 * Root cause (fixed in `pages/Home.tsx` via the `callRef` + mirror
 * effects): a test mock (or future code) that hands out a FRESH `call`
 * identity on every render re-fires any effect listing `call` in its
 * deps — each run re-fetches get_config/get_today_stats/get_history and
 * stores fresh state → render → new `call` → … → unbounded render loop
 * until the heap is exhausted.
 *
 * The harness below (shared `renderLoopGuard` helper) drives the page
 * with the SAME worst-case mock shape — a NEW `call` per render — and
 * asserts the page still settles: the mount load fires EXACTLY once per
 * command and the committed render count stays bounded. If future code
 * puts an unstable value in an effect dep (or re-introduces `call`
 * directly), the load re-fires and/or the render count explodes and
 * this test fails fast — instead of the worker OOMing.
 */

import { makeConfig } from "@/__tests__/helpers/fixtures";
import {
	type GuardCommand,
	renderLoopGuard,
} from "@/__tests__/helpers/renderLoopGuard";

const commands: GuardCommand[] = [
	{ name: "get_config", response: makeConfig({}) },
	{
		name: "get_today_stats",
		response: { count: 1, chars: 10, word_count: 2, duration: 1.5 },
	},
	{ name: "get_history", response: [] },
];

renderLoopGuard({
	id: "home",
	page: () => import("@/pages/Home"),
	commands,
	// The record button rendered in place of the loading spinner — the
	// page actually settled into its real UI. The button is icon-only:
	// its label lives in aria-label/title (no visible text), so query by
	// accessible name.
	settle: (s) => s.getByRole("button", { name: "Start dictation" }) != null,
});
