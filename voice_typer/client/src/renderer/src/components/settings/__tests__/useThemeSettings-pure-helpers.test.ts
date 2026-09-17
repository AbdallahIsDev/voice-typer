/**
 * Unit tests for the pure helpers already exported from
 * `useThemeSettings`.
 *
 * The large hook body is React state + draft persistence and stays in
 * the hook (further extraction is high-risk / low-value). These two
 * module-level helpers are the pure surface the component JSX calls
 * directly and pin the defensive nameKey accessor + the preview-swatch
 * colour resolution.
 */
import { describe, expect, it } from "vitest";

import {
	_getThemeNameKey,
	getThemePreviewColors,
} from "@/components/settings/useThemeSettings";
import { DEFAULT_CUSTOM_DARK, DEFAULT_CUSTOM_LIGHT, THEMES } from "@/themes";

describe("_getThemeNameKey", () => {
	it("returns a non-empty string nameKey", () => {
		expect(_getThemeNameKey({ nameKey: "theme.preset.amoled" })).toBe(
			"theme.preset.amoled",
		);
	});

	it("returns null when the field is missing, empty, or not a string", () => {
		expect(_getThemeNameKey({})).toBeNull();
		expect(_getThemeNameKey({ nameKey: "" })).toBeNull();
		expect(_getThemeNameKey({ nameKey: 12 })).toBeNull();
		expect(_getThemeNameKey(null)).toBeNull();
		expect(_getThemeNameKey("nope")).toBeNull();
	});
});

describe("getThemePreviewColors", () => {
	it("uses the custom draft primary when the custom preset is selected", () => {
		const result = getThemePreviewColors("custom", false, {
			light: { "--primary": "#112233", "--foreground": "#eeeeee" },
			dark: { "--primary": "#445566", "--foreground": "#111111" },
		});
		expect(result).toEqual({ bg: "#112233", fg: "#eeeeee" });
	});

	it("uses the dark custom draft in dark mode", () => {
		const result = getThemePreviewColors("custom", true, {
			light: { "--primary": "#112233", "--foreground": "#eeeeee" },
			dark: { "--primary": "#445566", "--foreground": "#111111" },
		});
		expect(result).toEqual({ bg: "#445566", fg: "#111111" });
	});

	it("falls back to the hardcoded custom defaults when a custom draft omits primary", () => {
		const light = getThemePreviewColors("custom", false, {
			light: {},
			dark: {},
		});
		expect(light.bg).toBe("#5469d4");
		const dark = getThemePreviewColors("custom", true, {
			light: {},
			dark: {},
		});
		expect(dark.bg).toBe("#6b7fd4");
	});

	it("uses the theme swatch for the default preset", () => {
		const light = getThemePreviewColors("default", false, null);
		const dark = getThemePreviewColors("default", true, null);
		expect(light.bg).toBeTruthy();
		expect(dark.bg).toBeTruthy();
		expect(light.fg).not.toBe(dark.fg);
	});

	it("reads primary/foreground from a built-in theme's var maps", () => {
		const theme = THEMES.find((t) => t.id !== "default" && t.id !== "custom");
		expect(theme).toBeDefined();
		if (!theme) return;
		const light = getThemePreviewColors(theme.id, false, null);
		const dark = getThemePreviewColors(theme.id, true, null);
		expect(light.bg).toBeTruthy();
		expect(dark.bg).toBeTruthy();
	});

	it("returns the hardcoded fallback for unknown preset ids", () => {
		expect(getThemePreviewColors("does-not-exist", false, null)).toEqual({
			bg: "#5469d4",
			fg: "#000000",
		});
	});

	it("treats custom without a draft like the hardcoded fallback path", () => {
		// No draft yet: preview still renders with sensible defaults.
		const result = getThemePreviewColors("custom", false, null);
		expect(result.bg).toBeTruthy();
		expect(result.fg).toBeTruthy();
	});
});

// Keep the imported default maps referenced so tree-shaking comments in
// the production module stay honest about their consumers.
describe("theme default maps", () => {
	it("exposes non-empty light/dark custom defaults", () => {
		expect(Object.keys(DEFAULT_CUSTOM_LIGHT).length).toBeGreaterThan(0);
		expect(Object.keys(DEFAULT_CUSTOM_DARK).length).toBeGreaterThan(0);
	});
});
