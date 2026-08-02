/**
 * Bubble overlay package — `useThemeSync` hook.
 *
 * Keeps the bubble's `<html>` in sync with the main app's theme so
 * Tailwind `dark:` variants resolve correctly, and the `dir` attribute
 * tracks the user's UI locale so RTL locales (currently Arabic) flip
 * the pill's logical-property utilities (`ms-*`, `me-*`) on the
 * always-on-top bubble window.
 *
 * The bubble renderer is a SEPARATE `BrowserWindow` from the main
 * app, so `setLocale()` running in the main renderer does NOT
 * propagate `document.documentElement.dir` (or `.lang`) here — the
 * bubble must apply its own `dir` from the same locale signal the
 * main window uses. The inline script in `bubble.html` does the
 * first-paint read from localStorage so the bubble's initial render
 * is in the correct writing direction; this hook keeps `dir` in sync
 * at runtime when the user changes locale from the Settings page
 * (which fires a `bubble:config` push via the Python backend).
 *
 * Theme handling: previously this hook honored ONLY the OS
 * `prefers-color-scheme` media query and an optional `theme_mode`
 * field from `bubble:config`. It never learned the user's
 * `theme_preset` (e.g. "nord", "dracula") or `custom_theme` CSS-var
 * map, so the bubble rendered with the default palette while the
 * main app rendered with the user's chosen preset. Now the hook
 * also reads `theme_preset` and `custom_theme` from the
 * `bubble:config` payload and calls `applyThemeVars()` after
 * toggling `.dark` so the bubble inherits the same preset-derived
 * CSS vars as the main app.
 *
 * First-paint race (theme + dir sync): the `bubble:config` event is
 * fired by the Python backend over the WS bridge and is only
 * received AFTER the React tree mounts + the bubble preload wires
 * `onConfig`. So the very first paint of the bubble uses the
 * inline-script-applied `dir` / `lang` / `.dark` (read from
 * localStorage) — and if those localStorage keys are stale or
 * missing (e.g. first run, fresh install, cleared prefs), the
 * bubble paints with browser defaults until the first
 * `bubble:config` arrives ~50-200ms later. The proper fix is for
 * the main process to push `bubble:config` in the bubble window's
 * `did-finish-load` listener (so the config arrives before the
 * React tree mounts) — that work is owned by the `main/windows/`
 * sub-agent. Until then, the inline-script + this hook's runtime
 * sync provide a best-effort first paint + correct steady state.
 */
import { useCallback, useEffect, useRef } from "react";
import { type Locale, SUPPORTED_LOCALES } from "@/i18n/locale";
import { isRtlLocale } from "@/i18n/rtl";
import { applyThemeVars, CUSTOM_THEME_ID } from "@/themes";
import { useBubbleBridge } from "./useBubbleBridge";

// Type guard for the `locale` field of the `bubble:config` payload.
// The payload is typed as `Record<string, unknown>`, so a runtime
// check is required before treating `cfg.locale` as a `Locale`.
function isLocaleValue(v: unknown): v is Locale {
	return (
		typeof v === "string" &&
		(SUPPORTED_LOCALES as readonly string[]).includes(v)
	);
}

export function useThemeSync() {
	const bridge = useBubbleBridge();
	const themeModeRef = useRef<"light" | "dark" | "system" | null>(null);
	const themePresetRef = useRef<string | null>(null);
	const customThemeRef = useRef<{
		light?: Record<string, string>;
		dark?: Record<string, string>;
	} | null>(null);

	const applyTheme = useCallback(() => {
		const prefersDark = window.matchMedia("(prefers-color-scheme: dark)");
		const mode = themeModeRef.current;
		const isDark =
			mode === "dark" ? true : mode === "light" ? false : prefersDark.matches; // mode === "system" || mode === null
		document.documentElement.classList.toggle("dark", isDark);
		// Re-apply theme-preset CSS vars AFTER toggling `.dark` so the
		// bubble picks up the correct light/dark variant of the preset.
		// `applyThemeVars` is a no-op for the "default" preset.
		const preset = themePresetRef.current ?? "default";
		const customVars =
			preset === CUSTOM_THEME_ID && customThemeRef.current
				? ((isDark
						? customThemeRef.current.dark
						: customThemeRef.current.light) ?? null)
				: null;
		try {
			applyThemeVars(preset, isDark, customVars);
		} catch (e) {
			// A corrupted custom_theme payload could throw inside
			// applyThemeVars; swallow so the bubble doesn't crash over
			// a cosmetic error. The .dark class is already toggled.
			console.warn("[bubble] applyThemeVars failed:", e);
		}
	}, []);

	// OS prefers-color-scheme listener.
	useEffect(() => {
		const prefersDark = window.matchMedia("(prefers-color-scheme: dark)");
		applyTheme();
		prefersDark.addEventListener("change", applyTheme);
		return () => prefersDark.removeEventListener("change", applyTheme);
	}, [applyTheme]);

	// `bubble:config` listener for theme_mode / theme_preset /
	// custom_theme / locale.
	//
	// `locale` sync: the bubble renderer's `<html dir>` must match the
	// user's UI locale so RTL locales (Arabic) flip the pill's
	// logical-property utilities. The bubble is sandboxed and has no
	// `get_locale`, so the locale MUST come via `bubble:config` —
	// currently the Python backend's `_push_bubble_config` (in
	// `voice_typer/server/waveform_bubble_wiring.py`) does NOT include
	// `locale` in the payload. Until that's added on the backend side,
	// the `dir` attribute set by `bubble.html`'s inline first-paint
	// script (read from localStorage) is the only source of truth —
	// this hook's `dir` sync will be a no-op until the backend starts
	// pushing `locale`. The defensive `isLocaleValue` check below
	// ensures we don't set `dir` from an unknown payload value.
	useEffect(() => {
		if (!bridge) return;

		const off = bridge.on("config", (cfg) => {
			const mode = cfg.theme_mode;
			if (mode === "light" || mode === "dark" || mode === "system") {
				themeModeRef.current = mode;
			} else {
				themeModeRef.current = null;
			}
			// Accept theme_preset (id string) and custom_theme
			// ({light, dark} map). The backend doesn't push these yet,
			// but the bubble can react when it does.
			const preset = cfg.theme_preset;
			if (typeof preset === "string") {
				themePresetRef.current = preset;
			}
			const custom = cfg.custom_theme;
			if (custom && typeof custom === "object") {
				customThemeRef.current = custom as {
					light?: Record<string, string>;
					dark?: Record<string, string>;
				};
			}
			// `dir` sync from cfg.locale. The inline first-paint script
			// in `bubble.html` already set `dir` from localStorage, so
			// this only fires when the locale changes at runtime AND
			// the backend pushes the new locale in the config event.
			const localeVal = cfg.locale;
			if (isLocaleValue(localeVal)) {
				try {
					document.documentElement.dir = isRtlLocale(localeVal) ? "rtl" : "ltr";
				} catch (e) {
					// `document` may be unavailable in some test contexts.
					console.warn("[bubble] dir sync failed:", e);
				}
			}
			applyTheme();
		});
		return off;
	}, [applyTheme, bridge]);
}
