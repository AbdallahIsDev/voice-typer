/**
 * Tests for the Sidebar component.
 *
 * Sidebar renders the primary navigation: 10 nav items (Home, History,
 * Analytics, Templates, Vocabulary, Models, Microphone, Settings,
 * About, Privacy), the Logo + title, and a ThemeSwitch at the bottom.
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
		themeMode: "light" as const,
		onThemeChange: vi.fn(),
	};

	it("renders all 10 navigation items with their labels", () => {
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
			"Privacy",
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

	it("calls onNavigate with 'about' when the About item is clicked", () => {
		const onNavigate = vi.fn();
		renderWithProviders(<Sidebar {...baseProps} onNavigate={onNavigate} />);
		fireEvent.click(screen.getByText("About"));
		expect(onNavigate).toHaveBeenCalledWith("about");
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

	it("renders the ThemeSwitch and forwards onThemeChange when clicked", () => {
		const onThemeChange = vi.fn();
		renderWithProviders(
			<Sidebar
				{...baseProps}
				themeMode="light"
				onThemeChange={onThemeChange}
			/>,
		);
		// ThemeSwitch exposes its current mode via aria-label.
		// en.json: theme.switchAriaLabel = "Current theme: {mode}. Click to switch to {next}."
		// For themeMode="light", next is "dark", so the label is
		// "Current theme: Light. Click to switch to Dark."
		const themeButton = screen.getByLabelText(
			"Current theme: Light. Click to switch to Dark.",
		);
		expect(themeButton).toBeTruthy();
		fireEvent.click(themeButton);
		expect(onThemeChange).toHaveBeenCalledWith("dark");
	});
});
