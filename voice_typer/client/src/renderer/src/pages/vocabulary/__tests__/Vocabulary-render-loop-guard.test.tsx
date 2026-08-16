/**
 * Regression guard for the page-level infinite render loop class of bug
 * that OOM'd a vitest worker during the axe-core scans (FATAL heap
 * OOM, whole suite died at ~356 files).
 *
 * Root cause (fixed in `pages/vocabulary/hooks/useVocabulary.ts` via
 * the `callRef` mirror): a test mock (or future code) that hands out a
 * FRESH `call` identity on every render re-fires any effect listing
 * `call` in its deps — each run re-fetches vocabulary and stores a
 * fresh array → render → new `call` → … → unbounded render loop until
 * the heap is exhausted.
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

/** Seed: 2 misspellings + 1 phrase correction (flat list, 3 rows). */
const seedData = {
	misspellings: { recieve: "receive", teh: "the" },
	phrase_corrections: [["i am going to", "I'm going to"]],
};

const commands: GuardCommand[] = [
	{ name: "get_vocabulary", response: seedData },
	// The usage snapshot is a single progressive-enhancement fetch.
	{ name: "get_correction_usage", response: { version: 1, entries: {} } },
];

renderLoopGuard({
	id: "vocabulary",
	page: () => import("@/pages/Vocabulary"),
	commands,
	// Initial load lands: the flat list renders.
	settle: (s) => s.getByText("recieve") != null,
	// This page settles at ~6 commits; pin tighter than the default.
	maxCommits: 15,
});
