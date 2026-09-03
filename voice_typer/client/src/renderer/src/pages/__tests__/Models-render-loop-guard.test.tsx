/**
 * Regression guard for the page-level infinite render loop class of bug
 * that OOM'd a vitest worker during the axe-core scans (FATAL heap
 * OOM, whole suite died at ~356 files).
 *
 * Root cause (fixed in `hooks/models/useModelConfig.ts` via the
 * `callRef` + `markUpdatedRef` mirrors): a test mock (or future code)
 * that hands out a FRESH `call` identity on every render re-fires any
 * effect listing `call` in its deps — each run re-fetches the full
 * models payload (get_config + get_model_status + get_model_catalog)
 * and stores fresh state → render → new `call` → … → unbounded render
 * loop until the heap is exhausted.
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
import { modelsConfigMock } from "@/__tests__/helpers/stableMocks";

// The canonical minimal models-page config shape (one source of truth
// in helpers/stableMocks.tsx — shared with ModelsPage.test.tsx,
// ModelsPage-cancel-download-reset and the data-pages live-region guards).
const MOCK_CONFIG = modelsConfigMock();

const commands: GuardCommand[] = [
	{ name: "get_config", response: MOCK_CONFIG },
	{ name: "get_model_status", response: {} },
	{ name: "get_model_catalog", response: { models: [] } },
];

renderLoopGuard({
	id: "models",
	page: () => import("@/pages/Models"),
	commands,
	// The page heading settles in place of the loading spinner once the
	// initial load lands.
	settle: (s) => s.getByRole("heading", { name: /Models/i }) != null,
});
