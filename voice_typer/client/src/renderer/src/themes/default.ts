/**
 * "Default" theme preset — no CSS variable overrides.
 *
 * The ``default`` preset is a no-op: it means "use whatever is in the
 * stylesheet" (no overrides needed).  See ``themes.ts`` for the
 * ``ThemePreset`` interface and how presets are consumed.
 */
import type { ThemePreset } from "../themes";

export const defaultTheme: Omit<ThemePreset, "nameKey"> = {
	id: "default",
	name: "Default",
	swatch: "oklch(0.488 0.243 264.376)", // primary blue
	light: {}, // no overrides → use what's in the stylesheet
	dark: {},
};
