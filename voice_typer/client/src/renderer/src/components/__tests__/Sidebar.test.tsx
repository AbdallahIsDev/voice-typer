/**
 * Tests for the Sidebar component.
 *
 * Sidebar renders the primary navigation: 9 nav items (Home, History,
 * Analytics, Templates, Vocabulary, Models, Microphone, Settings,
 * About & Privacy — the former About and Privacy pages merged into ONE
 * destination). The branding header + the ThemeSwitch moved OUT of
 * the sidebar (the theme control now lives in the TitleBar), so the
 * sidebar is nav-only.
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// Mock the hugeicons runtime wrapper.
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

describe("Sidebar", () => {
	afterEach(() => {
		cleanup();
	});

	const baseProps = {
		currentPage: "home" as const,
		onNavigate: vi.fn(),
	};

	it("renders all 9 navigation items with their labels", () => {
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
			expect(screen.getByText(label)).toBeTruthy();
		}
	});

	it("renders the nav landmark with an accessible name", () => {
		renderWithProviders(<Sidebar {...baseProps} />);
		const nav = screen.getByRole("navigation", {
			name: "Main navigation",
		});
		expect(nav).toBeTruthy();
	});

	it("calls onNavigate with 'home' when the Home item is clicked", () => {
		const onNavigate = vi.fn();
		renderWithProviders(<Sidebar {...baseProps} onNavigate={onNavigate} />);
		fireEvent.click(screen.getByText("Home"));
		expect(onNavigate).toHaveBeenCalledWith("home");
	});

	it("calls onNavigate with 'microphone' when the Microphone item is clicked", () => {
		const onNavigate = vi.fn();
		renderWithProviders(<Sidebar {...baseProps} onNavigate={onNavigate} />);
		fireEvent.click(screen.getByText("Microphone"));
		expect(onNavigate).toHaveBeenCalledWith("microphone");
	});

	it("calls onNavigate with 'aboutAndPrivacy' when the About & Privacy item is clicked", () => {
		const onNavigate = vi.fn();
		renderWithProviders(<Sidebar {...baseProps} onNavigate={onNavigate} />);
		fireEvent.click(screen.getByText("About & Privacy"));
		expect(onNavigate).toHaveBeenCalledWith("aboutAndPrivacy");
	});

	it("marks the active page with aria-current='page'", () => {
		renderWithProviders(<Sidebar {...baseProps} currentPage="vocabulary" />);
		const activeItem = screen.getByText("Vocabulary").closest("button");
		expect(activeItem?.getAttribute("aria-current")).toBe("page");
	});

	it("does not set aria-current on inactive items", () => {
		renderWithProviders(<Sidebar {...baseProps} currentPage="home" />);
		const inactiveItem = screen.getByText("Settings").closest("button");
		expect(inactiveItem?.getAttribute("aria-current")).toBeNull();
	});

	it("renders NO branding header (no logo, no app-name text in the sidebar)", () => {
		const { container } = renderWithProviders(<Sidebar {...baseProps} />);
		// The logo/title header block was removed from the sidebar — the
		// nav is the sidebar's only content now.
		const nav = screen.getByRole("navigation", { name: "Main navigation" });
		expect(nav).toBeTruthy();
		// No element in the sidebar may carry the app name as a label.
		expect(
			[...container.querySelectorAll("[aria-label]")].some((el) =>
				el.getAttribute("aria-label")?.includes(APP_NAME),
			),
		).toBe(false);
	});

	it("renders NO theme switch inside the sidebar (it moved to the TitleBar)", () => {
		renderWithProviders(<Sidebar {...baseProps} />);
		// The ThemeSwitch aria-label pattern ("Current theme: ...") must
		// NOT appear anywhere in the sidebar.
		expect(screen.queryByLabelText(/^Current theme:/)).toBeNull();
	});
});
