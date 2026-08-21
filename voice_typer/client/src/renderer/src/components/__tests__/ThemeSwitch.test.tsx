/**
 * Tests for the ThemeSwitch component.
 *
 * ThemeSwitch is a small icon-only button that cycles between three
 * theme modes (light → dark → system → light). It renders NO visible
 * text label — the current mode's icon is the only on-screen content.
 * The current mode + the mode clicking will switch to are exposed to
 * assistive tech via aria-label (and to sighted hoverers via title).
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// Mock the hugeicons runtime wrapper so we don't pull in the real SVG
// renderer (which depends on browser-only APIs and is heavy).
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

import { ThemeSwitch } from "@/components/layout/ThemeSwitch";

describe("ThemeSwitch", () => {
	afterEach(() => {
		cleanup();
	});

	it("renders NO visible text label in any mode (icon-only by design)", () => {
		render(<ThemeSwitch themeMode="light" onThemeChange={vi.fn()} />);
		expect(screen.queryByText("Light")).toBeNull();
		expect(screen.queryByText("Dark")).toBeNull();
		expect(screen.queryByText("System")).toBeNull();
	});

	it("renders the current mode's icon (data-name matches the mode)", () => {
		const { rerender } = render(
			<ThemeSwitch themeMode="light" onThemeChange={vi.fn()} />,
		);
		// HugeiconsIcon mock renders <span data-name={icon.name}>.
		const iconSpan = screen.getByTestId("hugeicon");
		expect(iconSpan.getAttribute("data-name")).toBe("Sun01Icon");

		rerender(<ThemeSwitch themeMode="dark" onThemeChange={vi.fn()} />);
		expect(screen.getByTestId("hugeicon").getAttribute("data-name")).toBe(
			"Moon02Icon",
		);

		rerender(<ThemeSwitch themeMode="system" onThemeChange={vi.fn()} />);
		expect(screen.getByTestId("hugeicon").getAttribute("data-name")).toBe(
			"ModernTvIcon",
		);
	});

	it("calls onThemeChange with 'dark' when clicked in light mode", () => {
		const onThemeChange = vi.fn();
		render(<ThemeSwitch themeMode="light" onThemeChange={onThemeChange} />);
		fireEvent.click(screen.getByRole("button"));
		expect(onThemeChange).toHaveBeenCalledTimes(1);
		expect(onThemeChange).toHaveBeenCalledWith("dark");
	});

	it("calls onThemeChange with 'system' when clicked in dark mode", () => {
		const onThemeChange = vi.fn();
		render(<ThemeSwitch themeMode="dark" onThemeChange={onThemeChange} />);
		fireEvent.click(screen.getByRole("button"));
		expect(onThemeChange).toHaveBeenCalledTimes(1);
		expect(onThemeChange).toHaveBeenCalledWith("system");
	});

	it("calls onThemeChange with 'light' when clicked in system mode", () => {
		const onThemeChange = vi.fn();
		render(<ThemeSwitch themeMode="system" onThemeChange={onThemeChange} />);
		fireEvent.click(screen.getByRole("button"));
		expect(onThemeChange).toHaveBeenCalledTimes(1);
		expect(onThemeChange).toHaveBeenCalledWith("light");
	});

	it("exposes the current mode via aria-label for screen readers", () => {
		const { rerender } = render(
			<ThemeSwitch themeMode="light" onThemeChange={vi.fn()} />,
		);
		expect(
			screen.getByLabelText("Current theme: Light. Click to switch to Dark."),
		).toBeTruthy();

		rerender(<ThemeSwitch themeMode="dark" onThemeChange={vi.fn()} />);
		expect(
			screen.getByLabelText("Current theme: Dark. Click to switch to System."),
		).toBeTruthy();

		rerender(<ThemeSwitch themeMode="system" onThemeChange={vi.fn()} />);
		expect(
			screen.getByLabelText("Current theme: System. Click to switch to Light."),
		).toBeTruthy();
	});

	//title attribute includes the next mode (mirror aria-label) ──

	it("ZU-42: title attribute includes the next mode so sighted mouse users see the same context as SR users", () => {
		const { rerender } = render(
			<ThemeSwitch themeMode="light" onThemeChange={vi.fn()} />,
		);
		// The title attribute used to show only the current mode
		// ("Light mode — click to switch") which left sighted mouse
		// users with less context than SR users got from the
		// aria-label. The title mirrors the aria-label so both
		// audiences see the "current → next" preview.
		expect(screen.getByRole("button").getAttribute("title")).toBe(
			"Current theme: Light. Click to switch to Dark.",
		);

		rerender(<ThemeSwitch themeMode="dark" onThemeChange={vi.fn()} />);
		expect(screen.getByRole("button").getAttribute("title")).toBe(
			"Current theme: Dark. Click to switch to System.",
		);

		rerender(<ThemeSwitch themeMode="system" onThemeChange={vi.fn()} />);
		expect(screen.getByRole("button").getAttribute("title")).toBe(
			"Current theme: System. Click to switch to Light.",
		);
	});

	it("accepts a host className and merges it over the base icon button styling", () => {
		render(
			<ThemeSwitch
				themeMode="dark"
				onThemeChange={vi.fn()}
				className="no-drag h-8 w-8 rounded text-(--text-muted)"
			/>,
		);
		const cls = screen.getByRole("button").className;
		// Host overrides win via tailwind-merge.
		expect(cls).toContain("h-8");
		expect(cls).toContain("w-8");
		expect(cls).toContain("rounded");
		expect(cls).toContain("no-drag");
		expect(cls).toContain("text-(--text-muted)");
	});
});
