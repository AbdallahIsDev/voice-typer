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
import { useCallback, useEffect, useRef, useState } from "react";
import { setLocale } from "@/i18n/i18n";
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
	const textSizeRef = useRef<number | null>(null);
	// Bumped when a locale-change push arrives so the component that
	// called `useThemeSync` (via `useBubbleLifecycle`) re-renders and
	// the bubble's module-level `t()` labels re-resolve in the new
	// locale. See the `localeChanged` effect below.
	const [, setLocaleRenderTick] = useState(0);

	// Mirror of the main window's text-size scaling
	// (`useTheme.ts` sets `--font-scale = textSize / 14` on
	// `document.documentElement`; `index.css` consumes it for the root
	// font-size). The bubble applies the same formula from the
	// `text_size` field of the `bubble:config` push so the pill's text
	// scales with the user's UI text-size setting. `null` (setting
	// never pushed) leaves the CSS default (1) untouched.
	const applyTextSize = useCallback(() => {
		const size = textSizeRef.current;
		if (size === null) return;
		try {
			document.documentElement.style.setProperty(
				"--font-scale",
				String(size / 14),
			);
		} catch (e) {
			// `document` may be unavailable in some test contexts.
			console.warn("[renderer:useThemeSync] text-size sync failed:", e);
		}
	}, []);

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
			console.warn("[renderer:useThemeSync] applyThemeVars failed:", e);
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
	// custom_theme / locale / text_size.
	//
	// `locale` sync: the bubble renderer's `<html dir>` must match the
	// user's UI locale so RTL locales (Arabic) flip the pill's
	// logical-property utilities. The `bubble:config` payload's
	// `locale` field (when present) keeps `dir`/`lang` correct after a
	// config change; the dedicated `bubble:locale-changed` push below
	// is the primary live path. The defensive `isLocaleValue` check
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
					// Also sync `lang` so screen readers announce the pill's
					// labels in the new locale (matches setLocale's behavior
					// in the main window).
					document.documentElement.lang = localeVal;
				} catch (e) {
					// `document` may be unavailable in some test contexts.
					console.warn("[renderer:useThemeSync] dir sync failed:", e);
				}
			}
			// Text-size sync: the backend pushes `text_size` in the
			// `bubble:config` payload (same push the theme triplet rides).
			const size = cfg.text_size;
			if (typeof size === "number" && Number.isFinite(size) && size > 0) {
				textSizeRef.current = size;
			}
			applyTextSize();
			applyTheme();
		});
		return off;
	}, [applyTheme, applyTextSize, bridge]);

	// Runtime locale-change push (`bubble:locale-changed` — the main
	// process forwards the locale whenever the user switches app
	// language; `notifyBubbleLocaleChanged` in windows/bubble/
	// lifecycle.ts). This is the LIVE path — the `cfg.locale` branch
	// above only fires when the config push happens to carry a locale.
	// The payload is the bare locale code ("en" / "ar" / …); the
	// `isLocaleValue` guard rejects unknown values so `dir` is never
	// set from a hostile/garbled payload.
	//
	// The handler routes through the PUBLIC `setLocale` (the same
	// orchestrator the main window uses) so the bubble's i18n runtime
	// switches wholesale: `_currentLocale`, dynamic translation-table
	// load, `dir`/`lang`, and subscriber notification — without
	// duplicating that choreography here. Its IPC pushes are no-ops in
	// the sandboxed bubble (`window.window_` / `window.python` are not
	// exposed here), so no echo loop with the main process is possible.
	// The state bump forces a re-render because the bubble's labels
	// resolve via the module-level `t()` at render time — unlike
	// `useT()` subscribers they are not notified by the locale change.
	useEffect(() => {
		if (!bridge) return;
		const off = bridge.on("localeChanged", (locale) => {
			if (!isLocaleValue(locale)) return;
			try {
				setLocale(locale);
			} catch (e) {
				console.warn("[renderer:useThemeSync] locale switch failed:", e);
			}
			setLocaleRenderTick((t) => t + 1);
		});
		return off;
	}, [bridge]);
}
