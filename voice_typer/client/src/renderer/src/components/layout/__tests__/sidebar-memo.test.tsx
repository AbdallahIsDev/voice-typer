/**
 *  vitest suite — Sidebar React.memo re-render gating.
 *
 * Sidebar receives only primitive props + stable `useCallback` refs
 * from App.tsx (`navigate`). Wrapping it in `React.memo` (matching the
 * TitleBar.tsx pattern) lets the default shallow-equal comparator
 * short-circuit re-renders when no prop has changed.
 *
 * The two tests below verify:
 *   1. Re-rendering the parent with the SAME prop references does
 *      NOT re-render Sidebar (render counter stays at 1).
 *   2. Re-rendering the parent with a CHANGED `currentPage` prop
 *      DOES re-render Sidebar (render counter increments to 2).
 *      This guards against an over-aggressive memo that would break
 *      navigation (NEVER DOWNGRADE behaviour).
 *
 * Render counting is done via a mocked `<Button>` child (the shared
 * design-system Button that every nav item/submenu parent renders
 * through). Sidebar always renders exactly 9 Buttons in the expanded
 * state (8 leaves + the Settings parent); Button itself is NOT memo'd,
 * so a Sidebar re-render propagates to every Button. Counting Button
 * renders is therefore a faithful proxy for counting Sidebar renders.
 */
import { act, cleanup, render } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: () => <span data-testid="hugeicon" />,
}));

vi.mock("@hugeicons/core-free-icons", async () => {
	const { createHugeiconsMock } = await import(
		"@/__tests__/helpers/hugeicons-mock"
	);
	return createHugeiconsMock();
});

//Button mock with a render counter. Every nav leaf + the Settings
// submenu parent render through the shared Button; counting Button
// renders counts Sidebar's renders (Button is not memo'd, so a
// Sidebar re-render propagates to each Button).
let buttonRenderCount = 0;
vi.mock("@/components/ui/button", () => ({
	Button: ({
		asChild: _asChild,
		children,
		...rest
	}: {
		asChild?: boolean;
		children?: React.ReactNode;
	} & React.ButtonHTMLAttributes<HTMLButtonElement>) => {
		buttonRenderCount++;
		return (
			<button type="button" {...rest}>
				{children}
			</button>
		);
	},
}));

import { Sidebar } from "@/components/layout/Sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { Page } from "@/types/ipc";

// Sidebar mounts real Radix Tooltips (HotkeyTooltip on the nav items),
// which REQUIRE a TooltipProvider ancestor — the app shell provides
// one (App.tsx:475). Same props as App.tsx so tooltip timing in tests
// mirrors production. The provider sits OUTSIDE the memoized Sidebar,
// so the re-render gating under test is unaffected.
function wrap(ui: React.ReactElement) {
	return (
		<TooltipProvider delayDuration={200} skipDelayDuration={500}>
			{ui}
		</TooltipProvider>
	);
}

interface SidebarProps {
	currentPage: Page;
	onNavigate: (page: Page) => void;
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

function makeProps(overrides: Partial<SidebarProps> = {}): SidebarProps {
	return {
		currentPage: "home",
		onNavigate: stableOnNavigate,
		collapsed: false,
		...overrides,
	};
}

describe("Sidebar — React.memo re-render gating", () => {
	beforeEach(() => {
		cleanup();
		buttonRenderCount = 0;
		stableOnNavigate.mockClear();
		forceRerender = () => {};
	});

	afterEach(() => {
		cleanup();
	});

	it("parent re-render with unchanged props does NOT re-render Sidebar", () => {
		const props = makeProps();
		render(wrap(<TestParent props={props} />));
		const initialButtons = buttonRenderCount;
		expect(initialButtons).toBeGreaterThan(0);

		// Force an unrelated parent re-render. Sidebar's props are the
		// SAME object references, so React.memo's shallow compare should
		// short-circuit and Sidebar (hence every Button) should NOT
		// re-render. Wrapped in `act()` so React flushes the state
		// update synchronously before the assertion runs.
		act(() => {
			forceRerender();
		});
		expect(buttonRenderCount).toBe(initialButtons);
	});

	it("NEVER-DOWNGRADE: changing `currentPage` re-renders Sidebar (navigation still works)", () => {
		// Use the same prop object references for stable callbacks; only
		// `currentPage` changes between renders.
		const { rerender } = render(
			wrap(<TestParent props={makeProps({ currentPage: "home" })} />),
		);
		const initialButtons = buttonRenderCount;

		// Re-render with a DIFFERENT currentPage. The shallow compare
		// detects the change and Sidebar re-renders (Button count
		// increments). This proves the memo doesn't break navigation.
		rerender(
			wrap(<TestParent props={makeProps({ currentPage: "settings" })} />),
		);
		expect(buttonRenderCount).toBeGreaterThan(initialButtons);
	});

	it("NEVER-DOWNGRADE: changing `collapsed` re-renders Sidebar (sidebar collapse still works)", () => {
		const { rerender } = render(
			wrap(<TestParent props={makeProps({ collapsed: false })} />),
		);
		const initialButtons = buttonRenderCount;

		rerender(wrap(<TestParent props={makeProps({ collapsed: true })} />));
		expect(buttonRenderCount).toBeGreaterThan(initialButtons);
	});
});
