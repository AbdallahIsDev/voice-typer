/**
 * "Custom" theme preset — placeholder for user-defined colours.
 *
 * The ``light`` / ``dark`` maps are empty because the actual variable
 * overrides are computed at runtime from ``custom_theme`` config data
 * (see ``deriveCustomVars`` in ``themes.ts``).
 *
 * See ``themes.ts`` for the ``ThemePreset`` interface and how presets
 * are consumed.
 */
import type { ThemePreset } from "../themes";

export const customTheme: Omit<ThemePreset, "nameKey"> = {
	id: "custom",
	name: "Custom",
	swatch: "oklch(0.6 0.15 280)", // gradient-like purple to hint at "customisable"
	light: {}, // handled via custom_theme data
	dark: {},
};
