/**
 *  vitest suite — StatsShareImage React.memo re-render gating.
 *
 * StatsShareImage is rendered off-screen and captured as a PNG only
 * when the user clicks "Share Stats". Re-rendering it on every
 * parent re-render is wasted work. Wrapping it in `React.memo`
 * (matching the TitleBar.tsx:324 pattern) lets the default
 * shallow-equal comparator short-circuit re-renders when the `stats`
 * prop reference is unchanged.
 *
 * The two tests below verify:
 *   1. Re-rendering the parent with the SAME `stats` reference does
 *      NOT re-render StatsShareImage (render counter stays at 1).
 *   2. Re-rendering the parent with a DIFFERENT `stats` reference
 *      (e.g. new computeShareStats return value) DOES re-render
 *      StatsShareImage (render counter increments to 2). This guards
 *      against an over-aggressive memo that would break the share
 *      image refresh (NEVER DOWNGRADE behaviour).
 *
 * Render counting is done via a render counter inside a wrapped
 * StatsShareImage — but since StatsShareImage is the component under
 * test, we instead count via the i18n `t()` calls it makes on each
 * render (StatsShareImage calls `t()` multiple times per render).
 * Mocking `t` with a counter gives a faithful render-count proxy.
 */
import { act, cleanup, render } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

//Mock `t()` with a render-call counter. StatsShareImage calls `t()`
// at least once per render (e.g. `t("stats.shareImage.title")`), so
// counting `t` invocations is a stable proxy for render count.
let tCallCount = 0;
vi.mock("@/i18n/i18n", async (importOriginal) => {
	const actual = await importOriginal<typeof import("@/i18n/i18n")>();
	return {
		...actual,
		t: (key: string, params?: Record<string, string>) => {
			tCallCount++;
			return actual.t(key, params);
		},
	};
});

import { StatsShareImage } from "@/components/dashboard/StatsShareImage";
import type { ShareStats } from "@/types/stats";

const stats: ShareStats = {
	wpm: 92,
	wpmDisplay: "92",
	minutesSaved: 18,
	minutesSavedDisplay: "18",
	modeDisplay: "Cloud",
	modeDetail: "Cloud ASR (OpenAI)",
	fasterThanAvg: "100% faster than avg typer",
};

// A second stats object — same field values but a DIFFERENT reference,
// simulating a fresh `computeShareStats()` return value.
const statsV2: ShareStats = {
	...stats,
	wpm: 100,
	wpmDisplay: "100",
};

// Test parent that exposes a `forceRerender` setter so the test can
// trigger an unrelated parent state update without changing the
// `stats` prop reference.
let forceRerender: () => void;
function TestParent({ statsProp }: { statsProp: ShareStats }) {
	const [, setTick] = useState(0);
	forceRerender = () => setTick((t) => t + 1);
	return <StatsShareImage stats={statsProp} />;
}

describe("StatsShareImage — React.memo re-render gating", () => {
	beforeEach(() => {
		cleanup();
		tCallCount = 0;
		forceRerender = () => {};
	});

	afterEach(() => {
		cleanup();
	});

	it("parent re-render with same `stats` reference does NOT re-render StatsShareImage", () => {
		render(<TestParent statsProp={stats} />);
		const firstRenderCount = tCallCount;
		expect(firstRenderCount).toBeGreaterThan(0);

		// Force an unrelated parent re-render. The `stats` prop is the
		// SAME object reference, so React.memo's shallow compare should
		// short-circuit and StatsShareImage should NOT re-render (no
		// additional `t()` calls). Wrapped in `act()` so React flushes
		// the state update synchronously before the assertion runs.
		act(() => {
			forceRerender();
		});
		expect(tCallCount).toBe(firstRenderCount);
	});

	it("NEVER-DOWNGRADE: changing `stats` reference re-renders StatsShareImage (share image refreshes)", () => {
		const { rerender } = render(<TestParent statsProp={stats} />);
		const firstRenderCount = tCallCount;
		expect(firstRenderCount).toBeGreaterThan(0);

		// Re-render with a DIFFERENT stats reference. The shallow
		// compare detects the change and StatsShareImage re-renders
		// (additional `t()` calls). This proves the memo doesn't break
		// the share image refresh when stats actually change.
		rerender(<TestParent statsProp={statsV2} />);
		expect(tCallCount).toBeGreaterThan(firstRenderCount);
	});
});
