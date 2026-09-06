/**
 * themeApply.ts — DOM application of the resolved theme. Split out of
 * ``hooks/useTheme.ts`` (the theme-apply concern): pure, side-effecting
 * helpers that write the resolved theme onto ``document.documentElement``
 * — the ``.dark`` class, the preset/custom CSS variable overrides, and
 * the ``--font-scale`` text-size property.
 *
 * The per-instance ``useEffect`` wiring (matchMedia subscription, effect
 * dependency arrays) stays in the hook; only the application BODY lives
 * here so it can be unit-tested without rendering a consumer.
 */
import {
	applyThemeVars,
	type CustomThemeData,
	deriveCustomVars,
} from "@/themes";

/**
 * Apply one resolved theme to the document:
 *
 * 1. Toggle the ``dark`` class based on the mode + system preference.
 * 2. Apply theme preset CSS variable overrides on top of light/dark.
 *    For custom themes, derive the full var set from the 6 core colours.
 *
 * @param mode              Effective theme mode ("light" | "dark" | "system").
 * @param themePreset       Active preset id (validated upstream).
 * @param customTheme       Custom colour map (only used when preset is "custom").
 * @param prefersDarkMatches Current system dark preference (``prefersDark.matches``),
 *                          passed in by the caller so the system preference is
 *                          read at invocation time, exactly as the pre-split
 *                          closure did.
 */
export function applyThemeToDocument(
	mode: string,
	themePreset: string,
	customTheme: CustomThemeData | null,
	prefersDarkMatches: boolean,
): void {
	let isDark: boolean;
	if (mode === "dark") {
		isDark = true;
	} else if (mode === "light") {
		isDark = false;
	} else {
		isDark = prefersDarkMatches;
	}
	document.documentElement.classList.toggle("dark", isDark);

	applyThemeVars(
		themePreset,
		isDark,
		themePreset === "custom" && customTheme
			? isDark
				? deriveCustomVars(customTheme.dark, true)
				: deriveCustomVars(customTheme.light, false)
			: null,
	);
}

/**
 * Apply text_size as a CSS custom property so the entire UI scales
 * proportionally. text_size=14 is the default (scale=1.0). The
 * ``--font-scale`` variable is consumed by index.css to set the root
 * font-size. This gives users a "Large Text" accessibility toggle
 * without requiring OS-level DPI changes.
 */
export function applyTextScale(textSize: number): void {
	const scale = textSize / 14;
	document.documentElement.style.setProperty("--font-scale", String(scale));
}
