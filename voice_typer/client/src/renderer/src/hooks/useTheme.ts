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

// ─── localStorage cache keys for theme persistence ────────────────────
// THEME-CACHE-FIX: persist the last-known theme state in localStorage so
// the theme is restored immediately on remount (e.g. after a restart)
// without waiting for the backend to connect.  The cache is updated every
// time config is successfully loaded from the backend, and read on mount
// in the ``useState`` initializers below.
const LS_THEME_MODE = "voice-typer-theme-mode";
const LS_THEME_PRESET = "voice-typer-theme-preset";
const LS_CUSTOM_THEME = "voice-typer-custom-theme";
const LS_TEXT_SIZE = "voice-typer-text-size";

function readLsThemeMode(): VoiceTyperConfig["theme_mode"] {
	try {
		const v = localStorage.getItem(LS_THEME_MODE);
		if (v === "light" || v === "dark" || v === "system") return v;
	} catch {}
	return "system";
}

function readLsThemePreset(): VoiceTyperConfig["theme_preset"] {
	try {
		const v = localStorage.getItem(LS_THEME_PRESET);
		if (
			v === "default" ||
			v === "amoled" ||
			v === "nord" ||
			v === "dracula" ||
			v === "sepia" ||
			v === "solarized" ||
			v === "monokai" ||
			v === "ayu" ||
			v === "github" ||
			v === "catppuccin" ||
			v === "tokyo-night" ||
			v === "custom"
		)
			return v;
	} catch {}
	return "default";
}

function readLsCustomTheme(): CustomThemeData | null {
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
	} catch {}
	return null;
}

function readLsTextSize(): number {
	try {
		const v = localStorage.getItem(LS_TEXT_SIZE);
		if (v) {
			const n = Number.parseInt(v, 10);
			if (Number.isFinite(n) && n >= 10 && n <= 20) return n;
		}
	} catch {}
	return 14;
}

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

	// THEME-CACHE-FIX: seed from localStorage so the theme is immediately
	// restored on remount (e.g. after restart) without waiting for the
	// backend.  ``reloadThemeFromConfig`` updates the cache after the
	// backend connects.
	const [themeMode, setThemeMode] = useState<VoiceTyperConfig["theme_mode"]>(
		readLsThemeMode(),
	);
	const [themePreset, setThemePreset] = useState<
		VoiceTyperConfig["theme_preset"]
	>(readLsThemePreset());
	const [customTheme, setCustomTheme] = useState<CustomThemeData | null>(
		readLsCustomTheme(),
	);
	const [textSize, setTextSize] = useState(readLsTextSize());

	// PVT-018 / FLASH-FIX: tracks whether the first
	// ``reloadThemeFromConfig`` call has completed on this mount.
	// Until it has, the theme-application effect below is suppressed
	// — the pre-React ``theme-bootstrap.ts`` already applied the
	// cached localStorage state to the DOM, so re-applying here
	// would either be a no-op (when localStorage matches the
	// bootstrap state, which it always does) or a visible flash
	// (when ``reloadThemeFromConfig`` resolves with backend values
	// that differ from the cached localStorage, triggering a
	// state change that re-runs this effect).  By suppressing
	// until the first reload completes, we ensure the backend
	// confirmation produces at most ONE theme application rather
	// than two (cached → backend).
	//
	// The flag is a ``useState`` (not a ref) so toggling it
	// triggers a re-render and re-runs the theme-application
	// effect with the now-confirmed values.
	const [hasInitialReloadCompleted, setHasInitialReloadCompleted] =
		useState(false);

	// ── Theme detection & application ────────────────────────────

	useEffect(() => {
		// FLASH-FIX: skip until the backend has confirmed the
		// theme state on first mount.  The bootstrap already
		// applied the cached localStorage theme, so there's
		// nothing to do here until ``reloadThemeFromConfig``
		// resolves (which flips ``hasInitialReloadCompleted``
		// to true and re-triggers this effect).
		if (!hasInitialReloadCompleted) return;

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
	}, [themeMode, themePreset, customTheme, hasInitialReloadCompleted]);

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
			// FLASH-FIX: write the backend-confirmed values back
			// to localStorage immediately so the NEXT mount
			// starts with the authoritative state (the
			// ``theme-bootstrap.ts`` module reads from the same
			// keys).  Without this, a stale localStorage value
			// could flash on every launch until the user
			// manually changes the theme.
			try {
				if (cfg?.theme_mode) {
					localStorage.setItem(LS_THEME_MODE, cfg.theme_mode);
					setThemeMode(cfg.theme_mode);
				}
				if (cfg?.theme_preset) {
					localStorage.setItem(LS_THEME_PRESET, cfg.theme_preset);
					setThemePreset(cfg.theme_preset);
				}
				if (cfg?.custom_theme) {
					localStorage.setItem(
						LS_CUSTOM_THEME,
						JSON.stringify(cfg.custom_theme),
					);
					setCustomTheme(cfg.custom_theme);
				} else if (cfg?.theme_preset && cfg.theme_preset !== "custom") {
					// Backend confirmed a non-custom preset — clear
					// any stale custom-theme cache so the bootstrap
					// doesn't try to derive custom vars from it.
					localStorage.removeItem(LS_CUSTOM_THEME);
				}
				if (cfg?.text_size) {
					localStorage.setItem(LS_TEXT_SIZE, String(cfg.text_size));
					setTextSize(cfg.text_size);
				}
			} catch {
				// localStorage may be unavailable — non-fatal.
				// State setters below still fire so the UI
				// reflects the backend values for this session.
				if (cfg?.theme_mode) setThemeMode(cfg.theme_mode);
				if (cfg?.theme_preset) setThemePreset(cfg.theme_preset);
				if (cfg?.custom_theme) setCustomTheme(cfg.custom_theme);
				if (cfg?.text_size) setTextSize(cfg.text_size);
			}
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
		} catch {
		} finally {
			// FLASH-FIX: regardless of success/failure, flip the
			// guard so the theme-application effect can run.
			// On failure we keep the cached localStorage state
			// (already applied by ``theme-bootstrap.ts``) —
			// flipping the flag here lets the effect take over
			// for subsequent state changes (e.g. when the user
			// toggles the theme via the sidebar).
			setHasInitialReloadCompleted(true);
		}
	}, [call]);

	// Load theme from config on mount
	useEffect(() => {
		reloadThemeFromConfig();
	}, [reloadThemeFromConfig]);

	// ── Sync theme state to localStorage on every change ────────────────
	// THEME-CACHE-FIX: keep the localStorage cache in sync whenever the
	// theme state changes (via handleThemeChange, setThemePreset,
	// setCustomTheme, setTextSize, or config_changed events). This
	// ensures the cache is always fresh regardless of how the theme was
	// changed, so the next remount (e.g. after restart) immediately
	// restores the last-known theme without waiting for the backend.
	useEffect(() => {
		try {
			localStorage.setItem(LS_THEME_MODE, themeMode);
			localStorage.setItem(LS_THEME_PRESET, themePreset);
			if (customTheme) {
				localStorage.setItem(LS_CUSTOM_THEME, JSON.stringify(customTheme));
			} else {
				localStorage.removeItem(LS_CUSTOM_THEME);
			}
			localStorage.setItem(LS_TEXT_SIZE, String(textSize));
		} catch {
			// localStorage may be unavailable
		}
	}, [themeMode, themePreset, customTheme, textSize]);

	// ── Config changed push (live UI updates) ───────────────────────────
	// When the Python backend processes a set_config command, it pushes a
	// config_changed event with the validated fields.  We update local UI
	// state (text_size, theme_mode, etc.) immediately so the user sees the
	// change without restarting the app.  We also merge the updates into
	// the appStore's config snapshot so other components see the change.
	usePythonEvent(
		"config_changed",
		useCallback(
			(data): (() => void) | undefined => {
				if (!data) return undefined;
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
				return undefined;
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
	//
	// QUIT-FLUSH-FIX: previously, if the user changed the theme and
	// then closed the app (close-to-tray → tray Quit, or window close)
	// during the 300ms debounce window, the pending save was dropped
	// and the next launch loaded the old theme from the backend. Added
	// a flush-on-unmount effect + a `beforeunload` listener so the
	// pending save fires synchronously before the renderer tears down.
	const themeSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
	const pendingThemeModeRef = useRef<VoiceTyperConfig["theme_mode"] | null>(
		null,
	);

	const flushPendingThemeSave = useCallback(() => {
		if (themeSaveTimerRef.current) {
			clearTimeout(themeSaveTimerRef.current);
			themeSaveTimerRef.current = null;
		}
		const mode = pendingThemeModeRef.current;
		if (mode !== null) {
			pendingThemeModeRef.current = null;
			// Fire-and-forget — the renderer may be tearing down, so we
			// can't await. The IPC layer queues the write before the
			// process exits.
			try {
				void call("set_config", { theme_mode: mode });
			} catch {
				// Theme is local-only if backend unavailable
			}
		}
	}, [call]);

	const handleThemeChange = useCallback(
		async (mode: VoiceTyperConfig["theme_mode"]): Promise<void> => {
			setThemeMode(mode);
			pendingThemeModeRef.current = mode;
			// Cancel any pending save and schedule a new one.
			if (themeSaveTimerRef.current) {
				clearTimeout(themeSaveTimerRef.current);
			}
			themeSaveTimerRef.current = setTimeout(async () => {
				themeSaveTimerRef.current = null;
				pendingThemeModeRef.current = null;
				try {
					await call("set_config", { theme_mode: mode });
				} catch {
					// Theme is local-only if backend unavailable
				}
			}, 300);
		},
		[call],
	);

	// QUIT-FLUSH-FIX: flush the pending theme save on unmount + before
	// the renderer unloads (close-to-tray, window close, app quit).
	useEffect(() => {
		const onBeforeUnload = () => flushPendingThemeSave();
		window.addEventListener("beforeunload", onBeforeUnload);
		return () => {
			window.removeEventListener("beforeunload", onBeforeUnload);
			flushPendingThemeSave();
		};
	}, [flushPendingThemeSave]);

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
