import type { ThemePreset } from "../themes";

export const customTheme: Omit<ThemePreset, "nameKey"> = {
	id: "custom",
	name: "Custom",
	swatch: "oklch(0.6 0.15 280)", // gradient-like purple to hint at "customisable"
	light: {}, // handled via custom_theme data
	dark: {},
};
