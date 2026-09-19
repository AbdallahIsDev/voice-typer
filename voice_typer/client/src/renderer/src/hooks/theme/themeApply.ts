import {
	applyThemeVars,
	type CustomThemeData,
	deriveCustomVars,
} from "@/themes";

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

export function applyTextScale(textSize: number): void {
	const scale = textSize / 14;
	document.documentElement.style.setProperty("--font-scale", String(scale));
}
