/**
 * Regression guard for the page-level infinite render loop class of bug
 * that OOM'd a vitest worker during the axe-core scans (FATAL heap
 * OOM, whole suite died at ~356 files).
 *
 * Root cause (fixed in `pages/microphone/hooks/useMicrophoneData.ts` via
 * the `callRef` / `showSnackRef` mirrors + ref-identity deps): a test
 * mock (or future
 * code) that hands out a FRESH `call` identity on every render re-fires
 * any effect listing `call` in its deps — each run re-fetches
 * get_microphones + get_config and stores fresh state → render → new
 * `call` → … → unbounded render loop until the heap is exhausted.
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
	{ name: "get_microphones", response: [] },
	{ name: "get_config", response: makeConfig({}) },
];

renderLoopGuard({
	id: "microphone",
	page: () => import("@/pages/Microphone"),
	commands,
	// The page heading settles in place of the loading spinner once the
	// initial load lands.
	settle: (s) => s.getByText("Microphone") != null,
});
