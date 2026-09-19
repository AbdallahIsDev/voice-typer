import type { ThemePreset } from "../themes";

export const defaultTheme: Omit<ThemePreset, "nameKey"> = {
	id: "default",
	name: "Default",
	swatch: "oklch(0.488 0.243 264.376)", // primary blue
	light: {}, // no overrides → use what's in the stylesheet
	dark: {},
};
