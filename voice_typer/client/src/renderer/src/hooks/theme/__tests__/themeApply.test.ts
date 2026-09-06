/**
 * Tests for hooks/theme/themeApply — the DOM application concern
 * extracted from useTheme.ts.
 *
 * Covers:
 *   1. ``applyThemeToDocument`` toggles the ``dark`` class correctly for
 *      explicit dark/light modes AND for the system mode (driven by the
 *      caller-supplied ``prefersDark.matches``).
 *   2. Preset CSS var overrides are forwarded to ``applyThemeVars`` —
 *      ``null`` custom vars for non-custom presets, derived vars from
 *      the custom colour map for the custom preset (dark vs light core
 *      maps selected by the resolved isDark).
 *   3. ``applyTextScale`` writes ``--font-scale`` (size / 14).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
	applyThemeVars: vi.fn(),
	deriveCustomVars: vi.fn(() => ({ "--background": "#derived" })),
}));

vi.mock("@/themes", () => ({
	applyThemeVars: mocks.applyThemeVars,
	deriveCustomVars: mocks.deriveCustomVars,
	THEMES: [{ id: "default", name: "Default" }],
}));

import { applyTextScale, applyThemeToDocument } from "../themeApply";

const customTheme = {
	light: { "--background": "#light-bg" },
	dark: { "--background": "#dark-bg" },
};

describe("themeApply — applyThemeToDocument", () => {
	beforeEach(() => {
		mocks.applyThemeVars.mockClear();
		mocks.deriveCustomVars.mockClear();
		document.documentElement.className = "";
	});

	it("adds the dark class for mode 'dark'", () => {
		applyThemeToDocument("dark", "default", null, false);
		expect(document.documentElement.classList.contains("dark")).toBe(true);
	});

	it("removes the dark class for mode 'light'", () => {
		document.documentElement.classList.add("dark");
		applyThemeToDocument("light", "default", null, true);
		expect(document.documentElement.classList.contains("dark")).toBe(false);
	});

	it("resolves mode 'system' from the caller-supplied system preference", () => {
		applyThemeToDocument("system", "default", null, true);
		expect(document.documentElement.classList.contains("dark")).toBe(true);
		applyThemeToDocument("system", "default", null, false);
		expect(document.documentElement.classList.contains("dark")).toBe(false);
	});

	it("passes null custom vars for non-custom presets", () => {
		applyThemeToDocument("dark", "nord", null, true);
		expect(mocks.applyThemeVars).toHaveBeenCalledWith("nord", true, null);
		expect(mocks.deriveCustomVars).not.toHaveBeenCalled();
	});

	it("derives custom vars from the dark core map when resolved dark", () => {
		applyThemeToDocument("dark", "custom", customTheme, false);
		expect(mocks.deriveCustomVars).toHaveBeenCalledWith(customTheme.dark, true);
		expect(mocks.applyThemeVars).toHaveBeenCalledWith("custom", true, {
			"--background": "#derived",
		});
	});

	it("derives custom vars from the light core map when resolved light", () => {
		applyThemeToDocument("light", "custom", customTheme, true);
		expect(mocks.deriveCustomVars).toHaveBeenCalledWith(
			customTheme.light,
			false,
		);
	});

	it("passes null custom vars when preset is custom but no custom map exists", () => {
		applyThemeToDocument("dark", "custom", null, true);
		expect(mocks.deriveCustomVars).not.toHaveBeenCalled();
		expect(mocks.applyThemeVars).toHaveBeenCalledWith("custom", true, null);
	});
});

describe("themeApply — applyTextScale", () => {
	it("writes --font-scale as size/14", () => {
		applyTextScale(14);
		expect(
			document.documentElement.style.getPropertyValue("--font-scale"),
		).toBe("1");

		applyTextScale(21);
		expect(
			document.documentElement.style.getPropertyValue("--font-scale"),
		).toBe("1.5");
	});
});
