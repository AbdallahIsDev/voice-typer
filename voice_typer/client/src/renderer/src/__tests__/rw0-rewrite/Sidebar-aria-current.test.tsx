/**
 * RW-0 vitest rewrite — behavioral test for `Sidebar.tsx` aria-current.
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
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: ({ icon }: { icon?: { name?: string } }) => (
		<span data-testid="hugeicon" data-name={icon?.name} />
	),
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

// ThemeSwitch pulls in next-themes; stub it to avoid the dependency.
vi.mock("@/components/layout/ThemeSwitch", () => ({
	ThemeSwitch: () => <div data-testid="theme-switch" />,
}));

import { Sidebar } from "@/components/layout/Sidebar";
import type { VoiceTyperConfig } from "@/types/config";

describe("Sidebar aria-current — RW-0 rewrite of test_sidebar_has_aria_current", () => {
	beforeEach(() => {
		cleanup();
	});

	afterEach(() => {
		cleanup();
	});

	it('marks the active nav button with aria-current="page"', () => {
		render(
			<Sidebar
				currentPage="settings"
				onNavigate={() => {}}
				themeMode="system"
				onThemeChange={() => {}}
			/>,
		);

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
		render(
			<Sidebar
				currentPage="home"
				onNavigate={() => {}}
				themeMode="system"
				onThemeChange={() => {}}
			/>,
		);

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
			<Sidebar
				currentPage="home"
				onNavigate={() => {}}
				themeMode="system"
				onThemeChange={() => {}}
			/>,
		);

		let activeButtons = document.querySelectorAll(
			'button[aria-current="page"]',
		);
		expect(activeButtons.length).toBe(1);
		expect(activeButtons[0]?.textContent).toContain("Home");

		rerender(
			<Sidebar
				currentPage="history"
				onNavigate={() => {}}
				themeMode="system"
				onThemeChange={() => {}}
			/>,
		);

		activeButtons = document.querySelectorAll('button[aria-current="page"]');
		expect(activeButtons.length).toBe(1);
		expect(activeButtons[0]?.textContent).toContain("History");
	});

	it("uses VoiceTyperConfig theme_mode type for themeMode prop (TypeScript safety)", () => {
		// The Python test only checked for the string
		// "aria-current" in Sidebar.tsx source.  As a bonus
		// invariant, we verify at compile time that the
		// themeMode prop accepts the same type as
		// VoiceTyperConfig["theme_mode"] (the union
		// "system" | "light" | "dark") — this catches
		// regressions where someone narrows the prop type.
		const themeMode: VoiceTyperConfig["theme_mode"] = "dark";
		render(
			<Sidebar
				currentPage="home"
				onNavigate={() => {}}
				themeMode={themeMode}
				onThemeChange={() => {}}
			/>,
		);
		expect(document.querySelector('button[aria-current="page"]')).toBeTruthy();
	});
});
