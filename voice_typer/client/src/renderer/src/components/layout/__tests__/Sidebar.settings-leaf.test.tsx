/**
 * Sidebar — Settings as a SINGLE LEAF (hub-and-spoke model).
 *
 * The nested Settings submenu (parent trigger + Collapsible children +
 * collapsed Popover flyout + arrow glyph) was removed: the Settings
 * page is now a HUB whose rows open the focused section pages (see
 * settingsSections.ts), and the sidebar rail renders Settings as ONE
 * leaf button like every other destination. This suite pins the new
 * model's invariants:
 *
 *  - Single leaf: no disclosure semantics anywhere in the nav — no
 *    aria-expanded, no dialog/haspopup roles; exactly 9 leaf buttons.
 *  - Roving tabindex: the active page's leaf holds tabIndex=0 +
 *    aria-current="page"; every other leaf holds -1.
 *  - Settings-surface fallback: on ANY Settings surface (the hub or a
 *    section page — neither is a nav item) the SETTINGS leaf becomes
 *    the roving tab stop (tabIndex=0) via the `isSettingsSurface`
 *    fallback in SidebarInner, so keyboard focus follows the active
 *    section even though the section page itself is not a nav item.
 *  - Click semantics: the leaf navigates to the hub ("settings"),
 *    never directly to a section page.
 *  - Collapsed rail: the Settings leaf stays focusable under the
 *    fallback and keeps its aria-keyshortcuts contract.
 *
 * Labels are resolved through the real i18n module (en locale) — the
 * same `t()` calls Sidebar.tsx makes — so the accessible-name queries
 * can never drift from the production keys.
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: ({
		children,
		icon,
	}: {
		children?: React.ReactNode;
		icon?: { name?: string };
	}) => (
		<span data-testid="hugeicon" data-name={icon?.name}>
			{children}
		</span>
	),
}));

vi.mock("@hugeicons/core-free-icons", async () => {
	const { createHugeiconsMock } = await import(
		"@/__tests__/helpers/hugeicons-mock"
	);
	return createHugeiconsMock();
});

import { SHORTCUTS } from "@/components/hotkey/shortcuts";
import { Sidebar } from "@/components/layout/Sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";
import { t } from "@/i18n/i18n";

// Sidebar renders real Radix Tooltips (via HotkeyTooltip on the nav
// items), which REQUIRE a TooltipProvider ancestor — the app shell
// provides one (App.tsx). Same props as App.tsx so tooltip timing
// in tests mirrors production.
function renderWithProviders(ui: React.ReactElement) {
	return render(
		<TooltipProvider delayDuration={200} skipDelayDuration={500}>
			{ui}
		</TooltipProvider>,
	);
}

// Helper: find a nav-item button by its accessible name. The label
// span stays mounted in BOTH sidebar states (the animated text
// transition never unmounts it), so the name is stable collapsed and
// expanded.
function findNavButton(label: string) {
	return screen.getByRole("button", {
		name: new RegExp(`^${label}(\\s|$)`),
	});
}

function allNavButtons() {
	return Array.from(
		document.querySelectorAll<HTMLButtonElement>(
			"aside button[data-nav-item='true']",
		),
	);
}

describe("Sidebar — Settings is a single leaf (no submenu)", () => {
	afterEach(() => {
		cleanup();
	});

	const baseProps = {
		currentPage: "home" as const,
		onNavigate: vi.fn(),
	};

	it("the nav contains exactly 9 leaf buttons and Settings is one of them", () => {
		renderWithProviders(<Sidebar {...baseProps} />);
		const buttons = allNavButtons();
		expect(buttons.length).toBe(9);
		expect(findNavButton(t("nav.settings"))).toBeTruthy();
	});

	it("NO disclosure semantics anywhere in the nav: no aria-expanded, no dialog role, no aria-haspopup", () => {
		renderWithProviders(<Sidebar {...baseProps} />);
		const nav = document.querySelector("nav");
		expect(nav).toBeTruthy();
		// The former parent trigger carried aria-expanded — a leaf nav
		// has no expansion state at all.
		expect(nav?.querySelectorAll("[aria-expanded]").length).toBe(0);
		// The collapsed flyout used a Popover (dialog semantics) — gone.
		expect(document.querySelector('[role="dialog"]')).toBeNull();
		expect(document.querySelector("button[aria-haspopup]")).toBeNull();
	});

	it("active page leaf: aria-current='page' + tabIndex=0; every other leaf tabIndex=-1", () => {
		renderWithProviders(<Sidebar {...baseProps} currentPage="history" />);
		const active = findNavButton(t("nav.history"));
		expect(active.getAttribute("aria-current")).toBe("page");
		expect(active.tabIndex).toBe(0);
		for (const btn of allNavButtons()) {
			if (btn !== active) {
				expect(btn.tabIndex).toBe(-1);
				expect(btn.getAttribute("aria-current")).toBeNull();
			}
		}
	});

	it("settings-surface fallback (hub): currentPage='settings' → the Settings leaf is the roving tab stop with aria-current", () => {
		renderWithProviders(<Sidebar {...baseProps} currentPage="settings" />);
		const settings = findNavButton(t("nav.settings"));
		expect(settings.getAttribute("aria-current")).toBe("page");
		expect(settings.tabIndex).toBe(0);
		// Exactly ONE tab stop in the whole nav.
		expect(allNavButtons().filter((b) => b.tabIndex === 0).length).toBe(1);
		for (const btn of allNavButtons()) {
			if (btn !== settings) {
				expect(btn.tabIndex).toBe(-1);
				expect(btn.getAttribute("aria-current")).toBeNull();
			}
		}
	});

	it("settings-surface fallback (section page): currentPage='settingsPrivacy' → the Settings leaf carries tabIndex=0 while every other leaf is -1", () => {
		renderWithProviders(
			<Sidebar {...baseProps} currentPage="settingsPrivacy" />,
		);
		const settings = findNavButton(t("nav.settings"));
		// The section page itself is NOT a nav item — the isSettingsSurface
		// fallback hands the roving tab stop to the Settings leaf so
		// keyboard focus follows the active section.
		expect(settings.tabIndex).toBe(0);
		// Exactly ONE tab stop in the whole nav.
		expect(allNavButtons().filter((b) => b.tabIndex === 0).length).toBe(1);
		for (const btn of allNavButtons()) {
			if (btn !== settings) {
				expect(btn.tabIndex).toBe(-1);
				// No nav leaf can claim aria-current for a page that is not
				// its own destination.
				expect(btn.getAttribute("aria-current")).toBeNull();
			}
		}
	});

	it("clicking the Settings leaf navigates to the hub ('settings'), never a section page", () => {
		const onNavigate = vi.fn();
		renderWithProviders(
			<Sidebar {...baseProps} onNavigate={onNavigate} currentPage="home" />,
		);
		fireEvent.click(findNavButton(t("nav.settings")));
		expect(onNavigate).toHaveBeenCalledTimes(1);
		expect(onNavigate).toHaveBeenCalledWith("settings");
	});

	it("collapsed rail: the Settings leaf stays focusable under the fallback and keeps its aria-keyshortcuts contract", () => {
		renderWithProviders(
			<Sidebar {...baseProps} currentPage="settingsPrivacy" collapsed />,
		);
		const settings = findNavButton(t("nav.settings"));
		// Fallback applies in the collapsed rail too — the icon-only leaf
		// remains in the roving-tabindex composite.
		expect(settings.tabIndex).toBe(0);
		expect(allNavButtons().filter((b) => b.tabIndex === 0).length).toBe(1);
		// The Ctrl+, binding stays exposed to AT regardless of state.
		expect(settings.getAttribute("aria-keyshortcuts")).toBe(
			SHORTCUTS.openSettings.ariaKeyshortcuts,
		);
	});
});
