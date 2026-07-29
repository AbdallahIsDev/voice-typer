/**
 * Bubble overlay package — `useThemeSync` hook.
 *
 * Keeps the bubble's `<html>` in sync with the main app's theme so
 * Tailwind dark: variants resolve correctly.
 *
 * Extracted from the former `bubble-components.tsx` monolith (PVT-067 /
 * DR-16).
 *
 * PVT-017: previously this hook honored ONLY the OS
 * `prefers-color-scheme` media query (and, since CR-056, an optional
 * `theme_mode` field from `bubble:config`). It never learned the
 * user's `theme_preset` (e.g. "nord", "dracula") or `custom_theme`
 * CSS-var map, so the bubble rendered with the default palette while
 * the main app rendered with the user's chosen preset. Now the hook
 * also reads `theme_preset` and `custom_theme` from the `bubble:config`
 * payload and calls `applyThemeVars()` after toggling `.dark` so the
 * bubble inherits the same preset-derived CSS vars as the main app.
 *
 * Forward-compatible: until the Python backend's `_push_bubble_config`
 * (in voice_typer/server/waveform_bubble_wiring.py) is updated to
 * include `theme_preset` / `custom_theme` in the payload, both refs
 * stay `null`/`"default"` and the bubble keeps deferring to the
 * stylesheet defaults — preserving the pre-fix behavior.
 */
import { useCallback, useEffect, useRef } from "react";
import { applyThemeVars, CUSTOM_THEME_ID } from "@/themes";

export function useThemeSync() {
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
		// PVT-017: re-apply theme-preset CSS vars AFTER toggling `.dark`
		// so the bubble picks up the correct light/dark variant of the
		// preset. `applyThemeVars` is a no-op for the "default" preset.
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

	// OS prefers-color-scheme listener (preserved from CR-056).
	useEffect(() => {
		const prefersDark = window.matchMedia("(prefers-color-scheme: dark)");
		applyTheme();
		prefersDark.addEventListener("change", applyTheme);
		return () => prefersDark.removeEventListener("change", applyTheme);
	}, [applyTheme]);

	// bubble:config listener for theme_mode / theme_preset / custom_theme.
	useEffect(() => {
		const api = window.bubble as
			| import("@/types/ipc").BubbleWindowBubble
			| undefined;
		if (!api?.onConfig) return;

		const off = api.onConfig((cfg) => {
			const mode = cfg.theme_mode;
			if (mode === "light" || mode === "dark" || mode === "system") {
				themeModeRef.current = mode;
			} else {
				themeModeRef.current = null;
			}
			// PVT-017: accept theme_preset (id string) and custom_theme
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
			applyTheme();
		});
		return off;
	}, [applyTheme]);
}
