/**
 *  vitest suite — covers , , ,
 * for the Sidebar component.
 *
 * - : active nav item uses a 2px left accent bar + soft accent
 *   background (replacing the weak full-border treatment).
 * - : nav items are grouped (Main / Power features / System)
 *   with visible section labels.
 * - : aria-keyshortcuts is exposed on Home ("Control+h") and
 *   Settings ("Control+,") since App.tsx binds those shortcuts.
 *   Items without a shortcut omit the attribute entirely.
 * - : the sidebar branding header (logo + app-name) was REMOVED —
 *   the nav is the sidebar's first content, in both collapsed and
 *   expanded states, and no theme switch renders inside the sidebar
 *   (the theme control moved to the TitleBar).
 *
 * The existing `components/__tests__/Sidebar.test.tsx` covers the
 * basic nav-label + aria-current behavior; this suite focuses on the
 * new fixes only and avoids duplicating those assertions.
 */
import { cleanup, render, screen, within } from "@testing-library/react";
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
	};

	//active nav item visual hierarchy ───────────────────────

	it("UX-16: active leaf nav item blends with the page (card border, --bg background, primary text)", () => {
		renderWithProviders(<Sidebar {...baseProps} currentPage="home" />);
		const activeButton = findNavButton("Home");
		expect(activeButton).toBeTruthy();
		const cls = activeButton?.className ?? "";
		// Active leaf = the standard card treatment: the shared card
		// border token at ~10% opacity (the border every card uses).
		expect(cls).toContain("border-border/10");
		// The legacy left-accent-bar borders are gone from ALL nav buttons.
		expect(cls).not.toContain("border-s-2");
		expect(cls).not.toContain("border-s-transparent");
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

	it("UX-16: Settings parent does NOT take the leaf card border/background when its submenu is active (calm foreground only)", () => {
		renderWithProviders(
			<Sidebar {...baseProps} currentPage="settingsGeneral" />,
		);
		const settingsButton = findNavButton("Settings");
		expect(settingsButton).toBeTruthy();
		const cls = settingsButton?.className ?? "";
		// Parent groups (Settings) stay visually calm: the active-section
		// signal is the stronger text/icon foreground ONLY — never the
		// leaf `bg-(--bg)` highlighted background or the leaf card border
		// (no third active style competing with the active child).
		expect(cls).toContain("text-(--text-primary)");
		expect(cls).toContain("font-medium");
		expect(cls).toContain("hover:bg-foreground/5");
		expect(cls).not.toContain("bg-(--bg)");
		expect(cls).not.toContain("bg-(--accent-soft)");
		expect(cls).not.toContain("border-border/10");
	});

	it("UX-16: inactive nav items do NOT carry the card border or any active background", () => {
		renderWithProviders(<Sidebar {...baseProps} currentPage="home" />);
		const inactiveButton = findNavButton("History");
		expect(inactiveButton).toBeTruthy();
		const cls = inactiveButton?.className ?? "";
		// Inactive leaves carry NO border token beyond the Button base's
		// transparent border — the legacy border-s-2 alignment bar is gone.
		expect(cls).not.toContain("border-s-2");
		expect(cls).not.toContain("border-s-transparent");
		expect(cls).not.toContain("border-border/10");
		expect(cls).not.toContain("border-s-(--accent)");
		expect(cls).not.toContain("bg-(--accent-soft)");
		expect(cls).not.toContain("bg-(--bg)");
		expect(cls).not.toContain("before:bg-accent");
		expect(cls).not.toContain("font-medium");
	});

	//nav grouping ──────────────────────────────────────────

	it("PROD-7: renders two group labels (Power features, System) — 'Main' is hidden", () => {
		renderWithProviders(<Sidebar {...baseProps} />);
		expect(screen.getByText("Power features")).toBeTruthy();
		expect(screen.getByText("System")).toBeTruthy();
		// "Main" is no longer rendered as visible text (the group
		// heading is hidden; the section's aria-label is preserved for
		// screen-reader navigation).
		expect(screen.queryByText("Main")).toBeNull();
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

	it("PROD-7: Home/History/Analytics are in the Main group; Templates/Vocabulary/Models/Microphone in Power features; Settings + About & Privacy in System", () => {
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
		expect(groupOf("About & Privacy")).toBe("System");
	});

	it("PROD-7: renders NO <hr> dividers between the groups", () => {
		renderWithProviders(<Sidebar {...baseProps} />);
		const nav = screen.getByRole("navigation", { name: "Main navigation" });
		expect(nav.querySelectorAll("hr").length).toBe(0);
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
			"About & Privacy",
		];
		for (const label of labels) {
			expect(findNavButton(label)).toBeTruthy();
		}
	});

	it("About & Privacy is ONE destination: exactly one merged nav button, no separate About or Privacy buttons", () => {
		renderWithProviders(<Sidebar {...baseProps} />);
		// Exactly one button matches the merged label.
		const merged = screen.getAllByRole("button", {
			name: /^About & Privacy/,
		});
		expect(merged.length).toBe(1);
		// No button carries the former standalone "About" or "Privacy"
		// label as its full accessible name.
		const plainLabels = screen
			.getAllByRole("button")
			.map((b) => b.textContent?.trim())
			.filter((name) => name === "About" || name === "Privacy");
		expect(plainLabels.length).toBe(0);
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

	it("expanded: NO visible shortcut keycaps render inside the nav item (aria-keyshortcuts stays)", async () => {
		renderWithProviders(<Sidebar {...baseProps} currentPage="home" />);
		const home = findNavButton("Home");
		// No plain-text `title` tooltip attribute.
		expect(home.hasAttribute("title")).toBe(false);
		// The shortcut is NOT rendered as visible chips inside the
		// expanded nav item — the sidebar stays clean (the shortcut
		// remains functional via aria-keyshortcuts + the global
		// shortcut handler).
		const kbdTexts = Array.from(home.querySelectorAll("kbd")).map(
			(k) => k.textContent,
		);
		expect(kbdTexts).not.toContain("Ctrl");
		expect(kbdTexts).not.toContain("H");
		// aria-keyshortcuts still exposes the binding to AT.
		expect(home.getAttribute("aria-keyshortcuts")).toBe("Control+h");
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
		// History, Templates, Vocabulary, Models, Microphone,
		// About & Privacy — none have shortcuts bound in App.tsx.
		const noShortcutItems = [
			"History",
			"Templates",
			"Vocabulary",
			"Models",
			"Microphone",
			"About & Privacy",
		];
		for (const label of noShortcutItems) {
			const btn = findNavButton(label);
			expect(btn?.hasAttribute("aria-keyshortcuts")).toBe(false);
		}
	});

	it("PROD-7: the System group is pinned to the sidebar bottom (mt-auto); Main and Power features are not", () => {
		renderWithProviders(<Sidebar {...baseProps} />);
		const sectionClassOf = (label: string) =>
			findNavButton(label).closest("section")?.className ?? "";
		expect(sectionClassOf("Settings")).toContain("mt-auto");
		expect(sectionClassOf("About & Privacy")).toContain("mt-auto");
		// The top groups flow normally — no auto margin competing with
		// the System group's bottom anchor.
		expect(sectionClassOf("Home")).not.toContain("mt-auto");
		expect(sectionClassOf("Templates")).not.toContain("mt-auto");
	});

	//sidebar branding removed ──────────────────────────

	it("SIDEBAR-BRANDING: no logo button (collapsed or expanded) — the branding header was removed entirely", () => {
		const { rerender } = renderWithProviders(
			<Sidebar {...baseProps} collapsed />,
		);
		// The collapsed logo <button aria-label={APP_NAME}> no longer exists.
		expect(screen.queryByLabelText(APP_NAME)).toBeNull();

		rerender(
			<TooltipProvider delayDuration={200} skipDelayDuration={500}>
				<Sidebar {...baseProps} collapsed={false} />
			</TooltipProvider>,
		);
		expect(screen.queryByLabelText(APP_NAME)).toBeNull();
	});

	it("SIDEBAR-BRANDING: the nav is the sidebar's first content (nothing sits above it)", () => {
		renderWithProviders(<Sidebar {...baseProps} />);
		const nav = screen.getByRole("navigation", { name: "Main navigation" });
		// The nav's scroll container must be the first child of the
		// <aside> — no branding header precedes it, so the navigation
		// fills the space the branding previously occupied.
		const aside = nav.closest("aside");
		expect(nav.parentElement).toBe(aside?.children[0]);
	});

	it("SIDEBAR-BRANDING: no theme switch renders inside the sidebar (moved to the TitleBar)", () => {
		renderWithProviders(<Sidebar {...baseProps} />);
		expect(screen.queryByLabelText(/^Current theme:/)).toBeNull();
	});
});
