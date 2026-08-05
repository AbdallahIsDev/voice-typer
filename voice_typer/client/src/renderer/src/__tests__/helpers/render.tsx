/**
 * Shared `render` wrappers for the renderer vitest suite.
 *
 * Before this file existed, 19 test files each rolled their own copy of
 *
 *   render(<TooltipProvider delayDuration={200}>{ui}</TooltipProvider>)
 *
 * (see `__tests__/a11y-rewrite/App-help-overlay.test.tsx`,
 * `__tests__/a11y-rewrite/PrivacySettings-consent.test.tsx`,
 * `__tests__/behavior-rewrite/hotkeys-behavior.test.tsx`, etc.). When a
 * test needed `rerender`, it had to re-wrap the node manually or the
 * TooltipProvider would be missing on the second render (triggering
 * "Tooltip must be used within TooltipProvider" errors — the same
 * symptom that motivated C-TEST-1 / `isolate: true` in
 * `vitest.config.ts`).
 *
 * `renderWithProviders(ui, options)` collapses those copies into a
 * single source of truth. It wraps `ui` in `<TooltipProvider
 * delayDuration={200}>` ONCE and preserves the wrapper across
 * `rerender` calls so the tooltip context never disappears mid-test.
 *
 * The `delayDuration={200}` value matches the production default the
 * existing 19 copies already use — keep it in sync with
 * `@/components/ui/tooltip` if the production default ever changes.
 *
 * This file is intended to be imported directly by tests. It has NO
 * side effects on import (no `vi.mock`, no `window.*` mutation).
 */
import {
	type RenderOptions,
	type RenderResult,
	render,
} from "@testing-library/react";
import type { ReactNode } from "react";

import { TooltipProvider } from "@/components/ui/tooltip";

/**
 * The `delayDuration` shared by every existing inline
 * `<TooltipProvider delayDuration={200}>` wrapper in the suite. Centralising
 * it here so a future change to the production default can be mirrored in
 * one place rather than across 19 files.
 */
export const TOOLTIP_DELAY_DURATION = 200;

/**
 * Render `ui` wrapped in `<TooltipProvider delayDuration={200}>`.
 *
 * Returns the standard `@testing-library/react` `RenderResult` with one
 * tweak: `rerender` re-applies the TooltipProvider wrapper so the tooltip
 * context survives re-renders. This matches the local `renderWithProviders`
 * pattern already in use in `audio-filter-chain-memo.test.tsx`.
 *
 * Pass `options` straight through to `render` if you need a custom
 * `container` / `baseElement` / `wrapper`. The `wrapper` option, if
 * provided, is composed INSIDE the TooltipProvider so both wrappers apply.
 *
 * `ui` is typed as `ReactNode` (not the narrower `ReactElement`) to match
 * the underlying `render` / `rerender` signatures from
 * `@testing-library/react` — that way a test passing a string, number,
 * or `null` doesn't need a cast.
 */
export function renderWithProviders(
	ui: ReactNode,
	options?: Omit<RenderOptions, "queries">,
): RenderResult {
	const wrap = (node: ReactNode) => (
		<TooltipProvider delayDuration={TOOLTIP_DELAY_DURATION}>
			{node}
		</TooltipProvider>
	);
	const utils = render(wrap(ui), options);
	return {
		...utils,
		rerender: (node: ReactNode) => utils.rerender(wrap(node)),
	};
}
