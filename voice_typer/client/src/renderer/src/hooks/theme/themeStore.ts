import { create } from "zustand";
import {
	LS_CUSTOM_THEME,
	LS_TEXT_SIZE,
	LS_THEME_MODE,
	LS_THEME_PRESET,
} from "@/lib/theme-storage-keys";
import { type CustomThemeData, THEMES } from "@/themes";
import type { LausuConfig } from "@/types/config";

// duplicated in ``theme-bootstrap.ts``). They now live in
// ``lib/theme-storage-keys.ts`` (single source of truth) so the
// bootstrap and the hook cannot drift out of sync, a one-sided key
// one, producing a FOUC on every launch).

export function readLsThemeMode(): LausuConfig["theme_mode"] {
	try {
		const v = localStorage.getItem(LS_THEME_MODE);
		if (v === "light" || v === "dark" || v === "system") return v;
	} catch (e) {
		// localStorage read failure, using default. Common in SSR,
		// sandboxed renderers, or when storage is disabled.
		console.warn("[renderer:useTheme] readLsThemeMode failed:", e);
	}
	return "system";
}

export function readLsThemePreset(): LausuConfig["theme_preset"] {
	try {
		const v = localStorage.getItem(LS_THEME_PRESET);
		//validate against the canonical ``THEMES`` list
		// (single source of truth in ``themes/index.ts``) instead
		// of a hand-maintained string-literal chain. Adding a new
		// index.ts array AND the literal chain here; forgetting
		// the latter silently rejected the cached preset on
		// remount (FOUC). The ``THEMES.some(t => t.id === v)``
		// check auto-stays-in-sync as presets are added.
		if (typeof v === "string" && THEMES.some((t) => t.id === v)) {
			return v as LausuConfig["theme_preset"];
		}
	} catch (e) {
		// localStorage read failure, using default.
		console.warn("[renderer:useTheme] readLsThemePreset failed:", e);
	}
	return "default";
}

export function readLsCustomTheme(): CustomThemeData | null {
	try {
		const raw = localStorage.getItem(LS_CUSTOM_THEME);
		if (raw) {
			const parsed = JSON.parse(raw);
			if (
				parsed &&
				typeof parsed === "object" &&
				"light" in parsed &&
				"dark" in parsed
			) {
				return parsed as CustomThemeData;
			}
		}
	} catch (e) {
		// localStorage parse failure, using default.
		console.warn("[renderer:useTheme] readLsCustomTheme parse failed:", e);
	}
	return null;
}

export function readLsTextSize(): number {
	try {
		const v = localStorage.getItem(LS_TEXT_SIZE);
		if (v) {
			const n = Number.parseInt(v, 10);
			if (Number.isFinite(n) && n >= 10 && n <= 20) return n;
		}
	} catch (e) {
		// localStorage read failure, using default.
		console.warn("[renderer:useTheme] readLsTextSize failed:", e);
	}
	return 14;
}

export interface ThemeState {
	themeMode: LausuConfig["theme_mode"];
	themePreset: LausuConfig["theme_preset"];
	customTheme: CustomThemeData | null;
	textSize: number;
	// FLASH-FIX: tracks whether the first ``reloadThemeFromConfig``
	// call has completed. Until it has, the theme-application effect
	// in the hook is suppressed, the pre-React ``theme-bootstrap.ts``
	// already applied the cached localStorage state to the DOM, so
	// re-applying here would either be a no-op (when localStorage
	// matches the bootstrap state) or a visible flash (when
	// ``reloadThemeFromConfig`` resolves with backend values that
	// differ from the cached localStorage, triggering a state change
	// that re-runs this effect). By suppressing until the first
	// reload completes, we ensure the backend confirmation produces
	// at most ONE theme application rather than two (cached → backend).
	hasInitialReloadCompleted: boolean;

	// Internal setters (state-only, NO backend save).
	setThemeModeState: (mode: LausuConfig["theme_mode"]) => void;
	setThemePresetState: (preset: LausuConfig["theme_preset"]) => void;
	setCustomThemeState: (custom: CustomThemeData | null) => void;
	setTextSizeState: (size: number) => void;
	setHasInitialReloadCompleted: (value: boolean) => void;
}

export const useThemeStore = create<ThemeState>()((set) => ({
	themeMode: readLsThemeMode(),
	themePreset: readLsThemePreset(),
	customTheme: readLsCustomTheme(),
	textSize: readLsTextSize(),
	hasInitialReloadCompleted: false,
	setThemeModeState: (mode) => set({ themeMode: mode }),
	setThemePresetState: (preset) => set({ themePreset: preset }),
	setCustomThemeState: (custom) => set({ customTheme: custom }),
	setTextSizeState: (size) => set({ textSize: size }),
	setHasInitialReloadCompleted: (value) =>
		set({ hasInitialReloadCompleted: value }),
}));

export function resetThemeStoreToCachedState(): void {
	useThemeStore.setState({
		themeMode: readLsThemeMode(),
		themePreset: readLsThemePreset(),
		customTheme: readLsCustomTheme(),
		textSize: readLsTextSize(),
		hasInitialReloadCompleted: false,
	});
}
