/**
 * Tests for the ThemeSwitch component.
 *
 * ThemeSwitch is a small button that cycles between three theme modes
 * (light → dark → system → light) and exposes the current mode to
 * assistive tech via aria-label.
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

// Mock the icon definitions as plain tagged objects. Each named export
// used by ThemeSwitch is stubbed with `{ name }` so the HugeiconsIcon
// mock can surface which icon was rendered via data-name.
vi.mock("@hugeicons/core-free-icons", () => {
	const make = (name: string) => ({ name });
	return {
		ModernTvIcon: make("ModernTvIcon"),
		Moon02Icon: make("Moon02Icon"),
		Sun01Icon: make("Sun01Icon"),
	};
});

import { ThemeSwitch } from "@/components/layout/ThemeSwitch";

describe("ThemeSwitch", () => {
	afterEach(() => {
		cleanup();
	});

	it("renders the Light label when themeMode is light", () => {
		render(<ThemeSwitch themeMode="light" onThemeChange={vi.fn()} />);
		expect(screen.getByText("Light")).toBeTruthy();
	});

	it("renders the Dark label when themeMode is dark", () => {
		render(<ThemeSwitch themeMode="dark" onThemeChange={vi.fn()} />);
		expect(screen.getByText("Dark")).toBeTruthy();
	});

	it("renders the System label when themeMode is system", () => {
		render(<ThemeSwitch themeMode="system" onThemeChange={vi.fn()} />);
		expect(screen.getByText("System")).toBeTruthy();
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
			screen.getByLabelText("Current theme: Light. Click to switch."),
		).toBeTruthy();

		rerender(<ThemeSwitch themeMode="dark" onThemeChange={vi.fn()} />);
		expect(
			screen.getByLabelText("Current theme: Dark. Click to switch."),
		).toBeTruthy();

		rerender(<ThemeSwitch themeMode="system" onThemeChange={vi.fn()} />);
		expect(
			screen.getByLabelText("Current theme: System. Click to switch."),
		).toBeTruthy();
	});

	it("still exposes the aria-label when collapsed (label text is hidden)", () => {
		const onThemeChange = vi.fn();
		render(
			<ThemeSwitch themeMode="dark" onThemeChange={onThemeChange} collapsed />,
		);
		// The visible text label is hidden via CSS max-width/opacity, but the
		// button's accessible name must remain so screen-reader users still
		// know which mode is active.
		expect(
			screen.getByLabelText("Current theme: Dark. Click to switch."),
		).toBeTruthy();
		// The button is still clickable in collapsed mode.
		fireEvent.click(screen.getByRole("button"));
		expect(onThemeChange).toHaveBeenCalledWith("system");
	});
});
