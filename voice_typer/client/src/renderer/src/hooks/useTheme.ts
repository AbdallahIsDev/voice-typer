import { useCallback, useEffect, useRef, useState } from "react";
import { usePythonEvent } from "@/hooks/usePython";
import { setSoundFeedbackEnabled } from "@/lib/sound-manager";
import { useAppStore } from "@/stores/appStore";
import {
	applyThemeVars,
	type CustomThemeData,
	deriveCustomVars,
} from "@/themes";
import type { VoiceTyperConfig } from "@/types/config";

/**
 * Theme hook: manages the active theme mode (light/dark/system), preset,
 * custom colours, and text-size scaling.  Applies the theme to the
 * document via CSS variables and persists changes to the backend config
 * with a 300ms debounce.
 *
 * @param call  The Python bridge `call` function (from usePython).
 */
export function useTheme(
	call: <T = unknown>(
		type: string,
		data?: Record<string, unknown>,
	) => Promise<T>,
) {
	const mergeConfig = useAppStore((s) => s.mergeConfig);
	const [themeMode, setThemeMode] =
		useState<VoiceTyperConfig["theme_mode"]>("system");
	// Theme preset — a built-in colour-scheme layer on top of the current mode.
	const [themePreset, setThemePreset] =
		useState<VoiceTyperConfig["theme_preset"]>("default");
	// Custom theme colours — used only when themePreset === 'custom'
	const [customTheme, setCustomTheme] = useState<CustomThemeData | null>(null);
	// PLAT-017: text size state for UI scaling. Fetched from config on mount.
	const [textSize, setTextSize] = useState(14);

	// ── Theme detection & application ────────────────────────────

	useEffect(() => {
		const prefersDark = window.matchMedia("(prefers-color-scheme: dark)");

		const applyTheme = (mode: string) => {
			let isDark: boolean;
			if (mode === "dark") {
				isDark = true;
			} else if (mode === "light") {
				isDark = false;
			} else {
				isDark = prefersDark.matches;
			}
			document.documentElement.classList.toggle("dark", isDark);

			// Apply theme preset CSS variable overrides on top of light/dark.
			// For custom themes, derive full var set from the 6 core colours.
			const customVars =
				themePreset === "custom" && customTheme
					? isDark
						? deriveCustomVars(customTheme.dark, true)
						: deriveCustomVars(customTheme.light, false)
					: null;
			applyThemeVars(themePreset, isDark, customVars);
		};

		// Apply current theme
		applyTheme(themeMode);

		// Listen for system changes when in 'system' mode
		const handler = () => {
			if (themeMode === "system") {
				applyTheme("system");
			}
		};
		prefersDark.addEventListener("change", handler);
		return () => prefersDark.removeEventListener("change", handler);
	}, [themeMode, themePreset, customTheme]);

	// PLAT-017: Apply text_size as a CSS custom property so the entire UI
	// scales proportionally. text_size=14 is the default (scale=1.0).
	// The --font-scale variable is consumed by index.css to set the
	// root font-size. This gives users a "Large Text" accessibility
	// toggle without requiring OS-level DPI changes.
	useEffect(() => {
		const scale = textSize / 14;
		document.documentElement.style.setProperty("--font-scale", String(scale));
	}, [textSize]);

	// Load theme from config.  Extracted as a reusable callback so the
	// onboarding-completion handler (in App.tsx) can re-trigger a full
	// reload after the user finishes the wizard.
	// NEW-TS-015: removed the ``if (!isReady) return`` guard — it was
	// dead code (``isReady`` was always ``true`` because the preload
	// installs ``window.python`` before React mounts).  The actual
	// backend-readiness check is ``connectionStatus === 'connected'``,
	// which is set by the connection lifecycle effect in useConnection.
	const reloadThemeFromConfig = useCallback(async () => {
		try {
			const cfg = await call<VoiceTyperConfig>("get_config");
			if (cfg?.theme_mode) setThemeMode(cfg.theme_mode);
			// Theme preset
			if (cfg?.theme_preset) setThemePreset(cfg.theme_preset);
			// Custom theme colours
			if (cfg?.custom_theme) setCustomTheme(cfg.custom_theme);
			// PLAT-017: load text_size from config for UI scaling
			if (cfg?.text_size) setTextSize(cfg.text_size);
			// SOUND-FIX-REWRITE: sync the sound_feedback_enabled
			// flag from config to localStorage on every config
			// load.  Previously the localStorage flag was only
			// written when the user toggled the switch in
			// Settings, which caused drift on fresh installs
			// and after clearing localStorage.  Now the flag
			// is always in sync with the actual config value.
			if (typeof cfg?.sound_feedback_enabled === "boolean") {
				setSoundFeedbackEnabled(cfg.sound_feedback_enabled);
			}
		} catch {}
	}, [call]);

	// Load theme from config on mount
	useEffect(() => {
		reloadThemeFromConfig();
	}, [reloadThemeFromConfig]);

	// ── Config changed push (live UI updates) ───────────────────────────
	// When the Python backend processes a set_config command, it pushes a
	// config_changed event with the validated fields.  We update local UI
	// state (text_size, theme_mode, etc.) immediately so the user sees the
	// change without restarting the app.  We also merge the updates into
	// the appStore's config snapshot so other components see the change.
	usePythonEvent(
		"config_changed",
		useCallback(
			(data) => {
				if (!data) return;
				// Merge into the store's config cache
				mergeConfig(data);
				if (typeof data.text_size === "number") {
					setTextSize(data.text_size);
				}
				if (typeof data.theme_mode === "string") {
					setThemeMode(data.theme_mode as VoiceTyperConfig["theme_mode"]);
				}
				if (typeof data.theme_preset === "string") {
					setThemePreset(data.theme_preset as VoiceTyperConfig["theme_preset"]);
				}
				if (data.custom_theme && typeof data.custom_theme === "object") {
					setCustomTheme(data.custom_theme as CustomThemeData);
				}
				// SOUND-FIX-REWRITE: keep localStorage in sync
				// when the sound_feedback_enabled flag changes
				// via ANY path (Settings toggle, config import,
				// CLI tool, etc.) — not just the Settings UI.
				if (typeof data.sound_feedback_enabled === "boolean") {
					setSoundFeedbackEnabled(data.sound_feedback_enabled);
				}
			},
			[mergeConfig],
		),
	);

	// ── Theme change handler (save to config) ─────────────────────
	// PERF: debounce the backend write so rapid theme toggling (e.g.
	// user clicking through light → dark → system quickly) doesn't
	// fire 3 separate set_config IPC calls. The local UI updates
	// immediately (setThemeMode); the backend save is deferred 300ms
	// and only the LAST selected mode is persisted.
	const themeSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
	const handleThemeChange = useCallback(
		async (mode: VoiceTyperConfig["theme_mode"]): Promise<void> => {
			setThemeMode(mode);
			// Cancel any pending save and schedule a new one.
			if (themeSaveTimerRef.current) {
				clearTimeout(themeSaveTimerRef.current);
			}
			themeSaveTimerRef.current = setTimeout(async () => {
				themeSaveTimerRef.current = null;
				try {
					await call("set_config", { theme_mode: mode });
				} catch {
					// Theme is local-only if backend unavailable
				}
			}, 300);
		},
		[call],
	);

	return {
		themeMode,
		themePreset,
		customTheme,
		textSize,
		setThemePreset,
		setCustomTheme,
		setTextSize,
		handleThemeChange,
		// Exposed so App.tsx's onboarding-completion handler can re-trigger
		// a full theme reload after the wizard applies the user's choices
		// (the onboarding_apply IPC route doesn't reliably emit a
		// config_changed event, so we explicitly re-fetch the config).
		reloadThemeFromConfig,
	};
}
