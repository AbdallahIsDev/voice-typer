/**
 *  vitest rewrite — behavioral test for `Sidebar.tsx` aria-current.
 *
 * Replaces the following string-pattern Python test from
 * `tests/test_ux_components.py`:
 *   - TestSidebarHasAriaCurrentPage::test_sidebar_has_aria_current
 *
 * The Python test asserted on substring presence inside `Sidebar.tsx`
 * (`"aria-current" in src`).  This passes even when the attribute is
 * set on the wrong element or with the wrong value.  The vitest
 * version below mounts the real Sidebar, passes a known `currentPage`,
 * and asserts the active nav button carries `aria-current="page"`
 * while the inactive buttons do not.
 *
 * The corresponding Python test is skipped via `@pytest.mark.skip`
 * with a pointer back to this file.  It is NOT deleted.
 */
import { cleanup, render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: ({ icon }: { icon?: { name?: string } }) => (
		<span data-testid="hugeicon" data-name={icon?.name} />
	),
}));

vi.mock("@hugeicons/core-free-icons", async () => {
	const { createHugeiconsMock } = await import(
		"@/__tests__/helpers/hugeicons-mock"
	);
	return createHugeiconsMock();
});

import { Sidebar } from "@/components/layout/Sidebar";

import { TooltipProvider } from "@/components/ui/tooltip";

// Sidebar mounts real Radix Tooltips (HotkeyTooltip on the nav items),
// which REQUIRE a TooltipProvider ancestor — the app shell provides
// one (App.tsx:475). Same props as App.tsx so tooltip timing in tests
// mirrors production.
function wrap(ui: React.ReactElement) {
	return (
		<TooltipProvider delayDuration={200} skipDelayDuration={500}>
			{ui}
		</TooltipProvider>
	);
}

import type { Page } from "@/types/ipc";

describe("Sidebar aria-current — RW-0 rewrite of test_sidebar_has_aria_current", () => {
	beforeEach(() => {
		cleanup();
	});

	afterEach(() => {
		cleanup();
	});

	it('marks the active nav button with aria-current="page"', () => {
		render(wrap(<Sidebar currentPage="settings" onNavigate={() => {}} />));

		// Find the active nav button by its aria-current attribute.
		const activeButton = document.querySelector('button[aria-current="page"]');
		expect(activeButton).toBeTruthy();

		// The active button must be the one for the
		// "settings" page (passed as currentPage).
		// Sidebar renders the t("nav.settings") label
		// ("Settings" in en.json).
		expect(activeButton?.textContent).toContain("Settings");
	});

	it("does NOT mark inactive nav buttons with aria-current", () => {
		render(wrap(<Sidebar currentPage="home" onNavigate={() => {}} />));

		// Exactly one button (the active one) should carry
		// aria-current="page" — every other nav item must
		// omit the attribute entirely (not set it to "false").
		const activeButtons = document.querySelectorAll(
			'button[aria-current="page"]',
		);
		expect(activeButtons.length).toBe(1);

		// And the active button is the Home button.
		expect(activeButtons[0]?.textContent).toContain("Home");

		// Every other nav button has no aria-current attr.
		const allNavButtons = document.querySelectorAll("nav button[aria-current]");
		expect(allNavButtons.length).toBe(1);
	});

	it("updates aria-current when currentPage changes", () => {
		const { rerender } = render(
			wrap(<Sidebar currentPage="home" onNavigate={() => {}} />),
		);

		let activeButtons = document.querySelectorAll(
			'button[aria-current="page"]',
		);
		expect(activeButtons.length).toBe(1);
		expect(activeButtons[0]?.textContent).toContain("Home");

		rerender(wrap(<Sidebar currentPage="history" onNavigate={() => {}} />));

		activeButtons = document.querySelectorAll('button[aria-current="page"]');
		expect(activeButtons.length).toBe(1);
		expect(activeButtons[0]?.textContent).toContain("History");
	});

	it("uses the Page union type for the currentPage prop (TypeScript safety)", () => {
		// The Python test only checked for the string
		// "aria-current" in Sidebar.tsx source.  As a bonus
		// invariant, we verify at compile time that the
		// currentPage prop accepts a member of the Page union
		// ("home" | "history" | ...) — this catches
		// regressions where someone narrows the prop type.
		const currentPage: Page = "home";
		render(wrap(<Sidebar currentPage={currentPage} onNavigate={() => {}} />));
		expect(document.querySelector('button[aria-current="page"]')).toBeTruthy();
	});
});

//finding 11: Sidebar keyboard navigation + aria-keyshortcuts ──
//
//The tests above only cover the aria-current visual state.
// finding 11 notes that NO test covers Sidebar's keyboard navigation
// behavior (roving tabindex: ArrowUp/Down/Home/End move focus between
// items; Tab enters/leaves the nav at the active item) or the
// aria-keyshortcuts attribute that exposes each item's keyboard
// shortcut to AT users.
//
// The Sidebar uses the standard ARIA "roving tabindex" composite
// widget pattern: only the active item (or the first item as a
// fallback) holds tabIndex=0; all other items hold tabIndex=-1.
// This means Tab from outside the nav focuses the active item, and
// the next Tab leaves the nav — ArrowUp/Down/Home/End move focus
// WITHIN the nav.  This is the correct pattern for a vertical menu;
// it lets keyboard users skip past the nav with one Tab press
// instead of having to Tab through every item.
//
// The tests below assert:
//   (1) Tab from before the nav focuses the active item (or first
//       item as fallback when currentPage isn't in the nav).
//   (2) ArrowDown cycles through all nav items in flat order,
//       crossing group boundaries (Main → Power → System).
//   (3) ArrowUp cycles in reverse.
//   (4) Home/End moves to the first/last item.
//   (5) Each nav item's aria-keyshortcuts matches the expected
//       value from NAV_KEYSHORTCUTS (Sidebar.tsx:97-100); items
//       without a shortcut omit the attribute entirely.
describe("BG-R19 #11: Sidebar keyboard navigation (roving tabindex) + aria-keyshortcuts", () => {
	// The expected aria-keyshortcuts values for items that have a
	// keyboard shortcut.  This mirrors `NAV_KEYSHORTCUTS` in
	// Sidebar.tsx:97-100 — duplicated here because the constant is
	// not exported (it's an internal implementation detail of the
	// Sidebar component).  If the production shortcuts change,
	// update BOTH Sidebar.tsx AND this map.
	const EXPECTED_KEYSHORTCUTS: Record<string, string> = {
		home: "Control+h",
		settings: "Control+,",
	};

	// The flat order of nav items across all groups (Main → Power →
	// System), matching ALL_NAV_ITEMS in Sidebar.tsx:77-81.  Used to
	// assert ArrowDown cycles through items in the right order.
	const EXPECTED_NAV_ORDER: string[] = [
		// Main
		"home",
		"history",
		"analytics",
		// Power
		"templates",
		"vocabulary",
		"models",
		"microphone",
		// System
		"settings",
		"about",
		"privacy",
	];

	beforeEach(() => {
		cleanup();
	});

	afterEach(() => {
		cleanup();
	});

	/** Get all nav-item buttons in DOM order. */
	function getNavButtons(): HTMLButtonElement[] {
		return Array.from(
			document.querySelectorAll<HTMLButtonElement>(
				'button[data-nav-item="true"]',
			),
		);
	}

	/** Get the accessible label of a button (used to map buttons to
	 *  their page-id by matching the localized `nav.<id>` text). */
	function buttonLabel(btn: HTMLButtonElement): string {
		return (btn.textContent || "").trim();
	}

	it("Tab into the nav focuses the active item (roving tabindex entry)", async () => {
		const user = userEvent.setup();
		render(wrap(<Sidebar currentPage="history" onNavigate={() => {}} />));

		// The active item ("history") should hold tabIndex=0;
		// all others should hold tabIndex=-1.
		const buttons = getNavButtons();
		expect(buttons.length).toBe(EXPECTED_NAV_ORDER.length);
		const tabindexes = buttons.map((b) => b.tabIndex);
		const zeroIndexCount = tabindexes.filter((t) => t === 0).length;
		expect(zeroIndexCount).toBe(1);

		// Tab from outside the nav.  Since the nav is the first
		// focusable element in this render (no skip-link / logo
		// button before it when not collapsed), Tab focuses the
		// single tabIndex=0 button — which must be "history" (the
		// active page).
		const activeButton = buttons.find((b) => b.tabIndex === 0);
		expect(activeButton).toBeTruthy();
		// biome-ignore lint/style/noNonNullAssertion: guarded by truthy expect above
		expect(buttonLabel(activeButton!)).toContain("History");

		await user.tab();
		expect(document.activeElement).toBe(activeButton);
	});

	it("ArrowDown cycles through all nav items in flat order (Main → Power → System)", async () => {
		const user = userEvent.setup();
		render(wrap(<Sidebar currentPage="home" onNavigate={() => {}} />));

		const buttons = getNavButtons();
		expect(buttons.length).toBe(EXPECTED_NAV_ORDER.length);

		// Start with focus on the first item (home — the active
		// page, so it has tabIndex=0).  ``getNavButtons()`` returns
		// ``Element[]`` whose items are ``Element | undefined`` under
		// ``noUncheckedIndexedAccess``; explicit locals narrow each
		// read once.
		const first = buttons[0];
		if (first === undefined)
			throw new Error("expected at least one nav button");
		first.focus();
		expect(document.activeElement).toBe(first);
		expect(buttonLabel(first)).toContain("Home");

		// ArrowDown through every item; assert focus moves to the
		// next button in DOM order, crossing group boundaries
		// (the <hr> dividers between Main/Power/System should
		// NOT block focus movement).
		for (let i = 1; i < buttons.length; i++) {
			await user.keyboard("{ArrowDown}");
			const next = buttons[i];
			if (next === undefined) continue;
			expect(document.activeElement).toBe(next);
		}

		// One more ArrowDown wraps to the first item (the nav is
		// a circular composite per Sidebar.tsx:158: `(currentIdx + 1) % buttons.length`).
		await user.keyboard("{ArrowDown}");
		expect(document.activeElement).toBe(first);
	});

	it("ArrowUp cycles in reverse, wrapping from first to last", async () => {
		const user = userEvent.setup();
		render(wrap(<Sidebar currentPage="home" onNavigate={() => {}} />));

		const buttons = getNavButtons();
		const first = buttons[0];
		if (first === undefined)
			throw new Error("expected at least one nav button");
		first.focus();
		expect(document.activeElement).toBe(first);

		// ArrowUp from the first item wraps to the last item.
		await user.keyboard("{ArrowUp}");
		const last = buttons[buttons.length - 1];
		expect(document.activeElement).toBe(last);

		// Continue ArrowUp through every item in reverse.
		for (let i = buttons.length - 2; i >= 0; i--) {
			await user.keyboard("{ArrowUp}");
			const next = buttons[i];
			if (next === undefined) continue;
			expect(document.activeElement).toBe(next);
		}
	});

	it("Home moves focus to the first nav item; End to the last", async () => {
		const user = userEvent.setup();
		render(wrap(<Sidebar currentPage="home" onNavigate={() => {}} />));

		const buttons = getNavButtons();
		// Start somewhere in the middle.
		const start = buttons[3];
		if (start === undefined) throw new Error("expected at least 4 nav buttons");
		start.focus();
		expect(document.activeElement).toBe(start);

		const first = buttons[0];
		const last = buttons[buttons.length - 1];
		if (first === undefined || last === undefined) {
			throw new Error("expected first and last nav buttons to exist");
		}

		await user.keyboard("{Home}");
		expect(document.activeElement).toBe(first);

		await user.keyboard("{End}");
		expect(document.activeElement).toBe(last);
	});

	it("each nav item's aria-keyshortcuts matches EXPECTED_KEYSHORTCUTS (omitted when no shortcut)", () => {
		render(wrap(<Sidebar currentPage="home" onNavigate={() => {}} />));

		const buttons = getNavButtons();
		expect(buttons.length).toBe(EXPECTED_NAV_ORDER.length);

		// For each button, find its page-id by matching the
		// accessible label against `nav.<id>` text.  The Sidebar
		// renders `t(\`nav.${item.id}\`)` as the button's text
		// content (see Sidebar.tsx:294, :362).  Since the i18n
		// mock returns English strings ("Home", "History", etc.),
		// we map the rendered text back to the page-id via the
		// EXPECTED_NAV_ORDER list.  The mapping is positional —
		// buttons are rendered in flat order matching
		// EXPECTED_NAV_ORDER.
		buttons.forEach((btn, idx) => {
			const pageId = EXPECTED_NAV_ORDER[idx];
			// noUncheckedIndexedAccess: both indexes return `T | undefined`;
			// explicit guards satisfy the strict checker without non-null.
			if (pageId === undefined) return;
			const expected = EXPECTED_KEYSHORTCUTS[pageId];
			if (expected) {
				expect(btn.getAttribute("aria-keyshortcuts")).toBe(expected);
			} else {
				// Items without a shortcut omit the attribute
				// entirely (per Sidebar.tsx:305-308: the
				// `aria-keyshortcuts={keyShortcut}` JSX prop
				// is undefined → React omits the attribute).
				expect(btn.hasAttribute("aria-keyshortcuts")).toBe(false);
			}
		});
	});

	it("clicking a nav item calls onNavigate with the item's page-id", async () => {
		const user = userEvent.setup();
		const onNavigate = vi.fn();
		render(wrap(<Sidebar currentPage="home" onNavigate={onNavigate} />));

		const buttons = getNavButtons();
		// Click the "Settings" button (index 7 in flat order).
		const settingsIdx = EXPECTED_NAV_ORDER.indexOf("settings");
		const settingsBtn = buttons[settingsIdx];
		if (settingsBtn === undefined) {
			throw new Error("settings nav button not found");
		}
		await user.click(settingsBtn);
		expect(onNavigate).toHaveBeenCalledWith("settings");
	});
});
