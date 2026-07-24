/**
 * FIX-15 vitest suite — covers UX-16, PROD-7, PROD-9, PROD-14
 * for the Sidebar component.
 *
 * - UX-16: active nav item uses a 2px left accent bar + soft accent
 *   background (replacing the weak full-border treatment).
 * - PROD-7: nav items are grouped (Main / Power features / System)
 *   with visible section labels and <hr> dividers between groups.
 * - PROD-9: aria-keyshortcuts is exposed on Home ("Control+h") and
 *   Settings ("Control+,") since App.tsx binds those shortcuts.
 *   Items without a shortcut omit the attribute entirely.
 * - PROD-14: when collapsed, the Logo is wrapped in a <button> with
 *   aria-label={APP_NAME} so AT users still get the app name.
 *
 * The existing `components/__tests__/Sidebar.test.tsx` covers the
 * basic nav-label + aria-current behavior; this suite focuses on the
 * new fixes only and avoids duplicating those assertions.
 */
import { cleanup, render, screen } from "@testing-library/react";
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

import { APP_NAME } from "@/branding";
import { Sidebar } from "@/components/layout/Sidebar";

describe("Sidebar — FIX-15 (UX-16, PROD-7, PROD-9, PROD-14)", () => {
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

	// ── UX-16: active nav item visual hierarchy ───────────────────────

	it("UX-16: active nav item carries the 2px left accent bar + soft accent background classes", () => {
		render(<Sidebar {...baseProps} currentPage="settings" />);
		const activeButton = screen.getByText("Settings").closest("button");
		expect(activeButton).toBeTruthy();
		const cls = activeButton?.className ?? "";
		// 2px inline-start accent bar via before:bg-accent pseudo-element.
		expect(cls).toContain("border-s-2");
		expect(cls).toContain("before:bg-accent");
		// Soft accent background (vs the old solid --bg).
		expect(cls).toContain("bg-(--accent-soft)");
		// Text color + weight bumped to primary/medium.
		expect(cls).toContain("text-(--text-primary)");
		expect(cls).toContain("font-medium");
	});

	it("UX-16: inactive nav items do NOT carry the accent border/background", () => {
		render(<Sidebar {...baseProps} currentPage="home" />);
		const inactiveButton = screen.getByText("Settings").closest("button");
		expect(inactiveButton).toBeTruthy();
		const cls = inactiveButton?.className ?? "";
		// Inactive uses a transparent border-s-2 so heights stay aligned
		// (no layout shift when the active item changes), but the bar
		// itself is transparent, NOT --accent.
		expect(cls).toContain("border-s-2");
		expect(cls).toContain("border-s-transparent");
		expect(cls).not.toContain("border-s-(--accent)");
		expect(cls).not.toContain("bg-(--accent-soft)");
		expect(cls).not.toContain("font-medium");
	});

	// ── PROD-7: nav grouping ──────────────────────────────────────────

	it("PROD-7: renders three group labels (Main, Power features, System)", () => {
		render(<Sidebar {...baseProps} />);
		expect(screen.getByText("Main")).toBeTruthy();
		expect(screen.getByText("Power features")).toBeTruthy();
		expect(screen.getByText("System")).toBeTruthy();
	});

	it("PROD-7: groups are rendered as <section> elements with aria-label matching the group label", () => {
		render(<Sidebar {...baseProps} />);
		const mainSection = screen.getByText("Home").closest("section");
		const powerSection = screen.getByText("Templates").closest("section");
		const systemSection = screen.getByText("Settings").closest("section");
		expect(mainSection?.getAttribute("aria-label")).toBe("Main");
		expect(powerSection?.getAttribute("aria-label")).toBe("Power features");
		expect(systemSection?.getAttribute("aria-label")).toBe("System");
	});

	it("PROD-7: Home/History/Analytics are in the Main group; Templates/Vocabulary/Models/Microphone in Power features; Settings/About in System", () => {
		render(<Sidebar {...baseProps} />);
		const groupOf = (label: string) =>
			screen.getByText(label).closest("section")?.getAttribute("aria-label");

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
		render(<Sidebar {...baseProps} />);
		const nav = screen.getByRole("navigation", { name: "Main navigation" });
		const dividers = nav.querySelectorAll("hr");
		expect(dividers.length).toBe(2);
	});

	it("PROD-7: still renders all 9 nav item labels (grouping does not drop items)", () => {
		render(<Sidebar {...baseProps} />);
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
			expect(screen.getByText(label)).toBeTruthy();
		}
	});

	// ── PROD-9: aria-keyshortcuts on nav items ────────────────────────

	it("PROD-9: Home nav item exposes aria-keyshortcuts='Control+h' (App.tsx binds Ctrl+H)", () => {
		render(<Sidebar {...baseProps} />);
		const homeButton = screen.getByText("Home").closest("button");
		expect(homeButton?.getAttribute("aria-keyshortcuts")).toBe("Control+h");
	});

	it("PROD-9: Settings nav item exposes aria-keyshortcuts='Control+,' (App.tsx binds Ctrl+,)", () => {
		render(<Sidebar {...baseProps} />);
		const settingsButton = screen.getByText("Settings").closest("button");
		expect(settingsButton?.getAttribute("aria-keyshortcuts")).toBe("Control+,");
	});

	it("PROD-9: nav items without a keyboard shortcut omit aria-keyshortcuts entirely", () => {
		render(<Sidebar {...baseProps} />);
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
			const btn = screen.getByText(label).closest("button");
			expect(btn?.hasAttribute("aria-keyshortcuts")).toBe(false);
		}
	});

	// ── PROD-14: collapsed-state logo accessibility ───────────────────

	it("PROD-14: when collapsed, the Logo is wrapped in a <button> with aria-label={APP_NAME}", () => {
		render(<Sidebar {...baseProps} collapsed />);
		const logoButton = screen.getByLabelText(APP_NAME);
		expect(logoButton.tagName).toBe("BUTTON");
	});

	it("PROD-14: when expanded, the Logo is NOT wrapped in a <button> (the visible <span> already carries the name)", () => {
		render(<Sidebar {...baseProps} collapsed={false} />);
		// APP_NAME appears once as the visible <span>{APP_NAME}</span>
		// (text content), but should NOT be the aria-label of a button.
		const buttons = document.querySelectorAll("button");
		const labeledButtons = Array.from(buttons).filter(
			(b) => b.getAttribute("aria-label") === APP_NAME,
		);
		expect(labeledButtons.length).toBe(0);
	});
});
