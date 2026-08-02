/**
 *  vitest suite — Sidebar React.memo re-render gating.
 *
 * Sidebar receives only primitive props + stable `useCallback` refs
 * from App.tsx (`navigate`, `handleThemeChange`). Wrapping it in
 * `React.memo` (matching the TitleBar.tsx:324 pattern) lets the
 * default shallow-equal comparator short-circuit re-renders when no
 * prop has changed.
 *
 * The two tests below verify:
 *   1. Re-rendering the parent with the SAME prop references does
 *      NOT re-render Sidebar (render counter stays at 1).
 *   2. Re-rendering the parent with a CHANGED `currentPage` prop
 *      DOES re-render Sidebar (render counter increments to 2).
 *      This guards against an over-aggressive memo that would break
 *      navigation (NEVER DOWNGRADE behaviour).
 *
 * Render counting is done via a mocked `<ThemeSwitch>` child —
 * Sidebar always renders exactly one `<ThemeSwitch>` instance, and
 * ThemeSwitch itself is NOT memo'd, so a Sidebar re-render
 * propagates to ThemeSwitch. Counting ThemeSwitch renders is
 * therefore a faithful proxy for counting Sidebar renders.
 */
import { act, cleanup, render } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: () => <span data-testid="hugeicon" />,
}));

vi.mock("@hugeicons/core-free-icons", () => {
	const make = (name: string) => ({ name });
	return {
		AiBrain03Icon: make("AiBrain03Icon"),
		Analytics01Icon: make("Analytics01Icon"),
		BookOpen02Icon: make("BookOpen02Icon"),
		File02Icon: make("File02Icon"),
		HistoryIcon: make("HistoryIcon"),
		Home04Icon: make("Home04Icon"),
		InformationCircleIcon: make("InformationCircleIcon"),
		Mic02Icon: make("Mic02Icon"),
		Settings03Icon: make("Settings03Icon"),
	};
});

//ThemeSwitch mock with a render counter. Sidebar renders exactly
// one ThemeSwitch; counting its renders counts Sidebar's renders.
let themeSwitchRenderCount = 0;
vi.mock("@/components/layout/ThemeSwitch", () => ({
	ThemeSwitch: () => {
		themeSwitchRenderCount++;
		return <div data-testid="theme-switch" />;
	},
}));

import { Sidebar } from "@/components/layout/Sidebar";
import type { VoiceTyperConfig } from "@/types/config";
import type { Page } from "@/types/ipc";

interface SidebarProps {
	currentPage: Page;
	onNavigate: (page: Page) => void;
	themeMode: VoiceTyperConfig["theme_mode"];
	onThemeChange: (mode: VoiceTyperConfig["theme_mode"]) => void;
	collapsed?: boolean;
}

// Test parent that exposes a `forceRerender` setter via a ref so the
// test can trigger an unrelated state update without changing the
// props passed to Sidebar.
let forceRerender: () => void;
function TestParent({ props }: { props: SidebarProps }) {
	const [, setTick] = useState(0);
	forceRerender = () => setTick((t) => t + 1);
	return <Sidebar {...props} />;
}

const stableOnNavigate = vi.fn();
const stableOnThemeChange = vi.fn();

function makeProps(overrides: Partial<SidebarProps> = {}): SidebarProps {
	return {
		currentPage: "home",
		onNavigate: stableOnNavigate,
		themeMode: "light",
		onThemeChange: stableOnThemeChange,
		collapsed: false,
		...overrides,
	};
}

describe("Sidebar — React.memo re-render gating", () => {
	beforeEach(() => {
		cleanup();
		themeSwitchRenderCount = 0;
		stableOnNavigate.mockClear();
		stableOnThemeChange.mockClear();
		forceRerender = () => {};
	});

	afterEach(() => {
		cleanup();
	});

	it("parent re-render with unchanged props does NOT re-render Sidebar", () => {
		const props = makeProps();
		render(<TestParent props={props} />);
		expect(themeSwitchRenderCount).toBe(1);

		// Force an unrelated parent re-render. Sidebar's props are the
		// SAME object references, so React.memo's shallow compare should
		// short-circuit and Sidebar (hence ThemeSwitch) should NOT
		// re-render. Wrapped in `act()` so React flushes the state
		// update synchronously before the assertion runs.
		act(() => {
			forceRerender();
		});
		expect(themeSwitchRenderCount).toBe(1);
	});

	it("NEVER-DOWNGRADE: changing `currentPage` re-renders Sidebar (navigation still works)", () => {
		// Use the same prop object references for stable callbacks; only
		// `currentPage` changes between renders.
		const { rerender } = render(
			<TestParent props={makeProps({ currentPage: "home" })} />,
		);
		expect(themeSwitchRenderCount).toBe(1);

		// Re-render with a DIFFERENT currentPage. The shallow compare
		// detects the change and Sidebar re-renders (ThemeSwitch count
		// increments). This proves the memo doesn't break navigation.
		rerender(<TestParent props={makeProps({ currentPage: "settings" })} />);
		expect(themeSwitchRenderCount).toBe(2);

		// And a third navigation flips it again.
		rerender(<TestParent props={makeProps({ currentPage: "history" })} />);
		expect(themeSwitchRenderCount).toBe(3);
	});

	it("NEVER-DOWNGRADE: changing `themeMode` re-renders Sidebar (theme toggle still works)", () => {
		const { rerender } = render(
			<TestParent props={makeProps({ themeMode: "light" })} />,
		);
		expect(themeSwitchRenderCount).toBe(1);

		rerender(<TestParent props={makeProps({ themeMode: "dark" })} />);
		expect(themeSwitchRenderCount).toBe(2);
	});
});
