/**
 *  vitest suite — covers , , ,
 * for the Sidebar component.
 *
 * - : active nav item uses a 2px left accent bar + soft accent
 *   background (replacing the weak full-border treatment).
 * - : nav items are grouped (Main / Power features / System)
 *   with visible section labels and <hr> dividers between groups.
 * - : aria-keyshortcuts is exposed on Home ("Control+h") and
 *   Settings ("Control+,") since App.tsx binds those shortcuts.
 *   Items without a shortcut omit the attribute entirely.
 * - : when collapsed, the Logo is wrapped in a <button> with
 *   aria-label={APP_NAME} so AT users still get the app name.
 *
 * The existing `components/__tests__/Sidebar.test.tsx` covers the
 * basic nav-label + aria-current behavior; this suite focuses on the
 * new fixes only and avoids duplicating those assertions.
 */
import {
	cleanup,
	fireEvent,
	render,
	screen,
	within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

// ThemeSwitch pulls in next-themes; stub it to avoid the dependency.
vi.mock("@/components/layout/ThemeSwitch", () => ({
	ThemeSwitch: () => <div data-testid="theme-switch" />,
}));

import { APP_NAME } from "@/branding";
import { Sidebar } from "@/components/layout/Sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";

// Sidebar renders real Radix Tooltips (via HotkeyTooltip on the nav
// items), which REQUIRE a TooltipProvider ancestor — the app shell
// provides one (App.tsx:475). Same props as App.tsx so tooltip timing
// in tests mirrors production.
function renderWithProviders(ui: React.ReactElement) {
	return render(
		<TooltipProvider delayDuration={200} skipDelayDuration={500}>
			{ui}
		</TooltipProvider>,
	);
}

// Helper: find a nav-item button by its visible label. The Sidebar sets
// `title` on each nav button (either the bare label for items without a
// shortcut, or `${label} (${shortcut})` for items with one). We match by
// a regex anchored at the start of the title so both shapes are found.
// `getByText` is unreliable here because the Button component renders
// the label inside a child <span> that may be collapsed/hidden, so the
// title attribute is the stable locator.
function findNavButton(label: string) {
	return screen.getByRole("button", {
		name: new RegExp(`^${label}(\\s|$)`),
	});
}

describe("Sidebar", () => {
	beforeEach(() => {
		cleanup();
	});

	afterEach(() => {
		cleanup();
	});

	const baseProps = {
		currentPage: "home" as const,
		onNavigate: vi.fn(),
		themeMode: "light" as const,
		onThemeChange: vi.fn(),
	};

	//active nav item visual hierarchy ───────────────────────

	it("UX-16: active nav item blends with the page (--bg background, no accent bar, no accent tint)", () => {
		renderWithProviders(<Sidebar {...baseProps} currentPage="settings" />);
		const activeButton = findNavButton("Settings");
		expect(activeButton).toBeTruthy();
		const cls = activeButton?.className ?? "";
		// Transparent 2px inline-start border stays for alignment only
		// (no layout shift when the active item changes).
		expect(cls).toContain("border-s-2");
		expect(cls).toContain("border-s-transparent");
		// The old accent dash (before:bg-accent) is gone.
		expect(cls).not.toContain("before:bg-accent");
		// Active background matches the page background (--bg), and the
		// old soft-accent tint is gone.
		expect(cls).toContain("bg-(--bg)");
		expect(cls).not.toContain("bg-(--accent-soft)");
		// Text color + weight bumped to primary/medium.
		expect(cls).toContain("text-(--text-primary)");
		expect(cls).toContain("font-medium");
	});

	it("UX-16: inactive nav items do NOT carry the accent border/background", () => {
		renderWithProviders(<Sidebar {...baseProps} currentPage="home" />);
		const inactiveButton = findNavButton("Settings");
		expect(inactiveButton).toBeTruthy();
		const cls = inactiveButton?.className ?? "";
		// Inactive uses a transparent border-s-2 so heights stay aligned
		// (no layout shift when the active item changes), but the bar
		// itself is transparent, NOT --accent.
		expect(cls).toContain("border-s-2");
		expect(cls).toContain("border-s-transparent");
		expect(cls).not.toContain("border-s-(--accent)");
		expect(cls).not.toContain("bg-(--accent-soft)");
		expect(cls).not.toContain("bg-(--bg)");
		expect(cls).not.toContain("before:bg-accent");
		expect(cls).not.toContain("font-medium");
	});

	//nav grouping ──────────────────────────────────────────

	it("PROD-7: renders three group labels (Main, Power features, System)", () => {
		renderWithProviders(<Sidebar {...baseProps} />);
		expect(screen.getByText("Main")).toBeTruthy();
		expect(screen.getByText("Power features")).toBeTruthy();
		expect(screen.getByText("System")).toBeTruthy();
	});

	it("PROD-7: groups are rendered as <section> elements with aria-label matching the group label", () => {
		renderWithProviders(<Sidebar {...baseProps} />);
		const mainSection = findNavButton("Home").closest("section");
		const powerSection = findNavButton("Templates").closest("section");
		const systemSection = findNavButton("Settings").closest("section");
		expect(mainSection?.getAttribute("aria-label")).toBe("Main");
		expect(powerSection?.getAttribute("aria-label")).toBe("Power features");
		expect(systemSection?.getAttribute("aria-label")).toBe("System");
	});

	it("PROD-7: Home/History/Analytics are in the Main group; Templates/Vocabulary/Models/Microphone in Power features; Settings/About in System", () => {
		renderWithProviders(<Sidebar {...baseProps} />);
		const groupOf = (label: string) =>
			findNavButton(label).closest("section")?.getAttribute("aria-label");

		expect(groupOf("Home")).toBe("Main");
		expect(groupOf("History")).toBe("Main");
		expect(groupOf("Analytics")).toBe("Main");

		expect(groupOf("Templates")).toBe("Power features");
		expect(groupOf("Vocabulary")).toBe("Power features");
		expect(groupOf("Models")).toBe("Power features");
		expect(groupOf("Microphone")).toBe("Power features");

		expect(groupOf("Settings")).toBe("System");
		expect(groupOf("About")).toBe("System");
	});

	it("PROD-7: renders exactly 2 <hr> dividers between the 3 groups", () => {
		renderWithProviders(<Sidebar {...baseProps} />);
		const nav = screen.getByRole("navigation", { name: "Main navigation" });
		const dividers = nav.querySelectorAll("hr");
		expect(dividers.length).toBe(2);
	});

	it("PROD-7: still renders all 9 nav item labels (grouping does not drop items)", () => {
		renderWithProviders(<Sidebar {...baseProps} />);
		const labels = [
			"Home",
			"History",
			"Analytics",
			"Templates",
			"Vocabulary",
			"Models",
			"Microphone",
			"Settings",
			"About",
		];
		for (const label of labels) {
			expect(findNavButton(label)).toBeTruthy();
		}
	});

	//aria-keyshortcuts on nav items ────────────────────────

	it("PROD-9: Home nav item exposes aria-keyshortcuts='Control+h' (App.tsx binds Ctrl+H)", () => {
		renderWithProviders(<Sidebar {...baseProps} />);
		const homeButton = findNavButton("Home");
		expect(homeButton.getAttribute("aria-keyshortcuts")).toBe("Control+h");
	});

	it("PROD-9: Settings nav item exposes aria-keyshortcuts='Control+,' (App.tsx binds Ctrl+,)", () => {
		renderWithProviders(<Sidebar {...baseProps} />);
		const settingsButton = findNavButton("Settings");
		expect(settingsButton.getAttribute("aria-keyshortcuts")).toBe("Control+,");
	});

	it("expanded: renders the shortcut INLINE in the nav item (no tooltip)", async () => {
		renderWithProviders(<Sidebar {...baseProps} currentPage="home" />);
		const home = findNavButton("Home");
		// No plain-text `title` tooltip attribute.
		expect(home.hasAttribute("title")).toBe(false);
		// The shortcut is real, visible content inside the button — one
		// <kbd> chip per key of the combo. formatHotkey("<ctrl>+<h>")
		// resolves to "Ctrl" + "H" chips on non-macOS (KbdGroup wraps the
		// combo in an outer <kbd>, so assert chip texts, not element count).
		const kbdTexts = Array.from(home.querySelectorAll("kbd")).map(
			(k) => k.textContent,
		);
		expect(kbdTexts).toContain("Ctrl");
		expect(kbdTexts).toContain("H");
		// The expanded label is visible, so NO tooltip is rendered —
		// focusing the item must not open one (there is no Tooltip
		// wrapper around the expanded nav items at all).
		home.focus();
		expect(screen.queryByRole("tooltip")).toBeNull();
	});

	it("collapsed: tooltip with label + shortcut chips still appears on hover/focus", async () => {
		renderWithProviders(
			<Sidebar {...baseProps} collapsed currentPage="home" />,
		);
		const home = findNavButton("Home");
		expect(home.hasAttribute("title")).toBe(false);
		// The label is hidden when collapsed — the right-side tooltip
		// carries the label + shortcut chips (Radix opens on focus).
		home.focus();
		const tooltip = await screen.findByRole("tooltip");
		expect(within(tooltip).getByText("Home")).toBeTruthy();
		const kbdTexts = Array.from(tooltip.querySelectorAll("kbd")).map(
			(k) => k.textContent,
		);
		expect(kbdTexts).toContain("Ctrl");
		expect(kbdTexts).toContain("H");
	});

	it("PROD-9: nav items without a keyboard shortcut omit aria-keyshortcuts entirely", () => {
		renderWithProviders(<Sidebar {...baseProps} />);
		// History, Templates, Vocabulary, Models, Microphone, About —
		// none have shortcuts bound in App.tsx.
		const noShortcutItems = [
			"History",
			"Templates",
			"Vocabulary",
			"Models",
			"Microphone",
			"About",
		];
		for (const label of noShortcutItems) {
			const btn = findNavButton(label);
			expect(btn?.hasAttribute("aria-keyshortcuts")).toBe(false);
		}
	});

	//collapsed-state logo accessibility ───────────────────

	it("PROD-14: when collapsed, the Logo is wrapped in a <button> with aria-label={APP_NAME}", () => {
		renderWithProviders(<Sidebar {...baseProps} collapsed />);
		const logoButton = screen.getByLabelText(APP_NAME);
		expect(logoButton.tagName).toBe("BUTTON");
	});

	it("PROD-14: when expanded, the Logo is NOT wrapped in a <button> (the visible <span> already carries the name)", () => {
		renderWithProviders(<Sidebar {...baseProps} collapsed={false} />);
		// APP_NAME appears once as the visible <span>{APP_NAME}</span>
		// (text content), but should NOT be the aria-label of a button.
		const buttons = document.querySelectorAll("button");
		const labeledButtons = Array.from(buttons).filter(
			(b) => b.getAttribute("aria-label") === APP_NAME,
		);
		expect(labeledButtons.length).toBe(0);
	});

	//collapsed-logo button has a real onClick (go home) ──────

	it("ZU-42: clicking the collapsed logo button calls onNavigate('home') (was a focusable non-action button)", () => {
		const onNavigate = vi.fn();
		renderWithProviders(
			<Sidebar
				{...baseProps}
				collapsed
				currentPage="settings"
				onNavigate={onNavigate}
			/>,
		);
		// The collapsed logo button is reachable via its aria-label
		// (APP_NAME). It must now navigate home when clicked.
		const logoButton = screen.getByLabelText(APP_NAME);
		expect(logoButton.tagName).toBe("BUTTON");
		// Use fireEvent so React's synthetic event system dispatches
		// the click handler (native .click() can miss React handlers
		// under some configurations).
		fireEvent.click(logoButton);
		expect(onNavigate).toHaveBeenCalledTimes(1);
		expect(onNavigate).toHaveBeenCalledWith("home");
	});

	it("ZU-42: collapsed logo button is keyboard-focusable and has cursor-pointer (signals interactivity)", () => {
		renderWithProviders(<Sidebar {...baseProps} collapsed />);
		const logoButton = screen.getByLabelText(APP_NAME);
		// The button is in the tab order (type="button", no tabIndex
		// override) so keyboard users can Tab to it.
		expect(logoButton.getAttribute("type")).toBe("button");
		expect(logoButton.className).toContain("cursor-pointer");
		expect(logoButton.className).not.toContain("cursor-default");
	});

	it("ZU-42: expanded sidebar still does NOT wrap the logo in a button (no regression)", () => {
		renderWithProviders(<Sidebar {...baseProps} collapsed={false} />);
		// The collapsed-logo button does NOT exist in expanded mode.
		const labeledButtons = Array.from(
			document.querySelectorAll("button"),
		).filter((b) => b.getAttribute("aria-label") === APP_NAME);
		expect(labeledButtons.length).toBe(0);
	});
});
