/**
 * Tests for the Sidebar component.
 *
 * Sidebar renders the primary navigation: 9 nav items (Home, History,
 * Analytics, Templates, Vocabulary, Models, Microphone, Settings,
 * About), the Logo + title, and a ThemeSwitch at the bottom.
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

// Stub every icon used by Sidebar + ThemeSwitch with `{ name }` so the
// HugeiconsIcon mock can surface which icon was rendered via data-name.
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
		ModernTvIcon: make("ModernTvIcon"),
		Moon02Icon: make("Moon02Icon"),
		Settings03Icon: make("Settings03Icon"),
		Sun01Icon: make("Sun01Icon"),
	};
});

import { Sidebar } from "@/components/Sidebar";

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

	it("renders all 9 navigation items with their labels", () => {
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

	it("renders the nav landmark with an accessible name", () => {
		render(<Sidebar {...baseProps} />);
		const nav = screen.getByRole("navigation", {
			name: "Main navigation",
		});
		expect(nav).toBeTruthy();
	});

	it("calls onNavigate with 'home' when the Home item is clicked", () => {
		const onNavigate = vi.fn();
		render(<Sidebar {...baseProps} onNavigate={onNavigate} />);
		fireEvent.click(screen.getByText("Home"));
		expect(onNavigate).toHaveBeenCalledWith("home");
	});

	it("calls onNavigate with 'microphone' when the Microphone item is clicked", () => {
		const onNavigate = vi.fn();
		render(<Sidebar {...baseProps} onNavigate={onNavigate} />);
		fireEvent.click(screen.getByText("Microphone"));
		expect(onNavigate).toHaveBeenCalledWith("microphone");
	});

	it("calls onNavigate with 'about' when the About item is clicked", () => {
		const onNavigate = vi.fn();
		render(<Sidebar {...baseProps} onNavigate={onNavigate} />);
		fireEvent.click(screen.getByText("About"));
		expect(onNavigate).toHaveBeenCalledWith("about");
	});

	it("marks the active page with aria-current='page'", () => {
		render(<Sidebar {...baseProps} currentPage="vocabulary" />);
		const activeItem = screen.getByText("Vocabulary").closest("button");
		expect(activeItem?.getAttribute("aria-current")).toBe("page");
	});

	it("does not set aria-current on inactive items", () => {
		render(<Sidebar {...baseProps} currentPage="home" />);
		const inactiveItem = screen.getByText("Settings").closest("button");
		expect(inactiveItem?.getAttribute("aria-current")).toBeNull();
	});

	it("renders the ThemeSwitch and forwards onThemeChange when clicked", () => {
		const onThemeChange = vi.fn();
		render(
			<Sidebar
				{...baseProps}
				themeMode="light"
				onThemeChange={onThemeChange}
			/>,
		);
		// ThemeSwitch exposes its current mode via aria-label.
		const themeButton = screen.getByLabelText(
			"Current theme: Light. Click to switch.",
		);
		expect(themeButton).toBeTruthy();
		fireEvent.click(themeButton);
		expect(onThemeChange).toHaveBeenCalledWith("dark");
	});
});
