import { useCallback, useEffect, useRef, useState } from "react";
import { usePythonEvent } from "@/hooks/usePython";
import { setSoundFeedbackEnabled } from "@/lib/sound-manager";
import {
	LS_CUSTOM_THEME,
	LS_TEXT_SIZE,
	LS_THEME_MODE,
	LS_THEME_PRESET,
} from "@/lib/theme-storage-keys";
import { useAppStore } from "@/stores/appStore";
import {
	applyThemeVars,
	type CustomThemeData,
	deriveCustomVars,
	THEMES,
} from "@/themes";
import type { VoiceTyperConfig } from "@/types/config";

//the four ``LS_*`` constants previously lived here (and were
// duplicated in ``theme-bootstrap.ts``). They now live in
// ``lib/theme-storage-keys.ts`` (single source of truth) so the
// bootstrap and the hook cannot drift out of sync — a one-sided key
// rename would previously have caused a silent cache desync (the
// bootstrap reading from the old key while this hook wrote to the new
// one, producing a FOUC on every launch).

function readLsThemeMode(): VoiceTyperConfig["theme_mode"] {
	try {
		const v = localStorage.getItem(LS_THEME_MODE);
		if (v === "light" || v === "dark" || v === "system") return v;
	} catch (e) {
		// localStorage read failure — using default. Common in SSR,
		// sandboxed renderers, or when storage is disabled.
		console.warn("[useTheme] readLsThemeMode failed:", e);
	}
	return "system";
}

function readLsThemePreset(): VoiceTyperConfig["theme_preset"] {
	try {
		const v = localStorage.getItem(LS_THEME_PRESET);
		//validate against the canonical ``THEMES`` list
		// (single source of truth in ``themes/index.ts``) instead
		// of a hand-maintained string-literal chain. Adding a new
		// preset previously required editing BOTH the themes/
		// index.ts array AND the literal chain here; forgetting
		// the latter silently rejected the cached preset on
		// remount (FOUC). The ``THEMES.some(t => t.id === v)``
		// check auto-stays-in-sync as presets are added.
		if (typeof v === "string" && THEMES.some((t) => t.id === v)) {
			return v as VoiceTyperConfig["theme_preset"];
		}
	} catch (e) {
		// localStorage read failure — using default.
		console.warn("[useTheme] readLsThemePreset failed:", e);
	}
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
	} catch (e) {
		// localStorage parse failure — using default.
		console.warn("[useTheme] readLsCustomTheme parse failed:", e);
	}
	return null;
}

function readLsTextSize(): number {
	try {
		const v = localStorage.getItem(LS_TEXT_SIZE);
		if (v) {
			const n = Number.parseInt(v, 10);
			if (Number.isFinite(n) && n >= 10 && n <= 20) return n;
		}
	} catch (e) {
		// localStorage read failure — using default.
		console.warn("[useTheme] readLsTextSize failed:", e);
	}
	return 14;
}

/**
 * Theme hook: manages the active theme mode (light/dark/system), preset,
 * custom colours, and text-size scaling.  Applies the theme to the
 * document via CSS variables and persists changes to the backend config
 * with a 300ms debounce.
 *
 * @param call  The Python bridge `call` function (from usePython).
 *
 * ── dual-instance, deferred ──────────────────────────────────
 *
 * ``useTheme`` is called from BOTH ``App.tsx`` (always-mounted) AND
 * ``Settings.tsx`` (lazy-mounted when the user opens Settings). Each
 * call instantiates an INDEPENDENT React state (``themeMode``,
 * ``themePreset``, ``customTheme``, ``textSize``) plus:
 *
 *   - one ``reloadThemeFromConfig`` mount effect → 1 extra
 *     ``get_config`` IPC call per Settings open
 *   - one ``config_changed`` ``usePythonEvent`` subscription → 2
 *     subscriptions app-wide (each updates its OWN state)
 *   - one ``beforeunload`` flush listener → 2 listeners app-wide
 *     (idempotent: both flush the same pending payload, the second
 *     is a no-op because ``pendingThemeUpdatesRef`` is cleared on
 *     first flush)
 *   - one ``localStorage`` sync effect → 2 writes per state change
 *     (idempotent: both write the same value)
 *
 * State IS eventually consistent across the two instances because:
 *
 *   1. Both initialise from the same ``localStorage`` keys
 *      (``readLsThemeMode`` / ``readLsThemePreset`` /
 *      ``readLsCustomTheme`` / ``readLsTextSize``).
 *   2. Both receive ``config_changed`` events from the backend and
 *      update their local state from the same payload.
 *
 * So the user-visible behaviour is correct; the cost is duplicate
 * (idempotent) IPC traffic and duplicate (idempotent) listeners.
 *
 * The proper fix is to extract the state + side effects into a
 * module-level singleton store (e.g. ``useSyncExternalStore`` with a
 * module-level ``listeners`` set + ``getSnapshot``, or a tiny Zustand
 * store) so both callers READ from the same source and the
 * ``reloadThemeFromConfig`` / ``config_changed`` / ``beforeunload``
 * effects run EXACTLY ONCE per page load.
 *
 * This refactor is deferred because:
 *
 *  - The 519-line hook has tightly-coupled debounce + flush logic
 *    that depends on React lifecycle (``useRef`` for the timer,
 *    ``useEffect`` cleanup for the flush). Moving it to a module-level
 *    store requires re-implementing the debounce queue outside React
 *    (or guarding the effects with a module-level ``initOnce`` flag
 *    so only the FIRST ``useTheme`` caller actually runs them).
 *
 *  - The existing comment in ``Settings.tsx`` (line ~85-90) documents
 *    that the dual-instance pattern is "safe because theme state is
 *    synchronised across instances via the config_changed event
 *    subscription and localStorage cache" — confirming the team
 *    consciously accepted this trade-off.
 *
 *  - A minimal "initOnce" guard would prevent the duplicate
 *    ``reloadThemeFromConfig`` IPC and duplicate ``config_changed``
 *    subscription without converting the whole hook to an external
 *    store, but it would also break Settings.tsx's initial state
 *    (its ``themeMode`` wouldn't get the backend's authoritative
 *    value on first mount — only on the NEXT ``config_changed``
 *    event). Doing this correctly requires the singleton-store
 *    approach above.
 *
 * When the singleton refactor is done, ``useTheme`` should become a
 * thin wrapper around ``useSyncExternalStore(themeSubscribe,
 * themeGetSnapshot)`` returning the current state + stable setters
 * (the setters update the singleton, which notifies all subscribers).
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
	//the underlying useState setters are renamed to
	// ``*State`` so the public-facing names (``setThemePreset``,
	// ``setCustomTheme``, ``setTextSize``) can be wrapped in
	// debounce-and-save callbacks below. The internal callers that
	// receive backend-pushed values (``reloadThemeFromConfig``, the
	// ``config_changed`` event handler) use the ``*State`` setters
	// directly so they DON'T re-trigger a backend save (the change
	// came FROM the backend, not from the user — round-tripping it
	// would be a no-op at best and a feedback loop at worst).
	const [themePreset, setThemePresetState] = useState<
		VoiceTyperConfig["theme_preset"]
	>(readLsThemePreset());
	const [customTheme, setCustomThemeState] = useState<CustomThemeData | null>(
		readLsCustomTheme(),
	);
	const [textSize, setTextSizeState] = useState(readLsTextSize());

	//FLASH-FIX: tracks whether the first
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

	//Apply text_size as a CSS custom property so the entire UI
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
	//removed the ``if (!isReady) return`` guard — it was
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
					setThemePresetState(cfg.theme_preset);
				}
				if (cfg?.custom_theme) {
					localStorage.setItem(
						LS_CUSTOM_THEME,
						JSON.stringify(cfg.custom_theme),
					);
					setCustomThemeState(cfg.custom_theme);
				} else if (cfg?.theme_preset && cfg.theme_preset !== "custom") {
					// Backend confirmed a non-custom preset — clear
					// any stale custom-theme cache so the bootstrap
					// doesn't try to derive custom vars from it.
					localStorage.removeItem(LS_CUSTOM_THEME);
				}
				if (cfg?.text_size) {
					localStorage.setItem(LS_TEXT_SIZE, String(cfg.text_size));
					setTextSizeState(cfg.text_size);
				}
			} catch (e) {
				// localStorage may be unavailable — non-fatal.
				// State setters below still fire so the UI
				// reflects the backend values for this session.
				console.warn("[useTheme] localStorage cache write failed:", e);
				if (cfg?.theme_mode) setThemeMode(cfg.theme_mode);
				if (cfg?.theme_preset) setThemePresetState(cfg.theme_preset);
				if (cfg?.custom_theme) setCustomThemeState(cfg.custom_theme);
				if (cfg?.text_size) setTextSizeState(cfg.text_size);
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
		} catch (e) {
			console.warn("[useTheme] get_config failed:", e);
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
		} catch (e) {
			// localStorage may be unavailable
			console.warn("[useTheme] localStorage sync failed:", e);
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
					setTextSizeState(data.text_size);
				}
				if (typeof data.theme_mode === "string") {
					setThemeMode(data.theme_mode as VoiceTyperConfig["theme_mode"]);
				}
				if (typeof data.theme_preset === "string") {
					setThemePresetState(
						data.theme_preset as VoiceTyperConfig["theme_preset"],
					);
				}
				if (data.custom_theme && typeof data.custom_theme === "object") {
					setCustomThemeState(data.custom_theme as CustomThemeData);
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
	//
	//the debounce + flush-on-unmount pattern was previously
	// applied ONLY to ``theme_mode`` (via ``handleThemeChange``).
	// ``setThemePreset`` / ``setCustomTheme`` / ``setTextSize`` were
	// bare ``useState`` setters — external callers (Settings page,
	// keyboard shortcuts) had to do their own ``set_config`` round-
	// trip, which they often forgot or did non-idempotently. The
	// unified ``scheduleThemeSave`` helper below merges all four
	// theme-related config keys into a single debounce queue + a
	// single flush path, so every theme-affecting setter persists
	// its change with the same 300ms debounce and the same
	// quit-flush guarantee.
	const themeSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
	//unified pending-updates object. Each entry is a partial
	// ``set_config`` payload keyed by the field name. The flush path
	// sends the merged object in a single IPC call.
	const pendingThemeUpdatesRef = useRef<Partial<
		Pick<
			VoiceTyperConfig,
			"theme_mode" | "theme_preset" | "custom_theme" | "text_size"
		>
	> | null>(null);

	const flushPendingThemeSave = useCallback(() => {
		if (themeSaveTimerRef.current) {
			clearTimeout(themeSaveTimerRef.current);
			themeSaveTimerRef.current = null;
		}
		const pending = pendingThemeUpdatesRef.current;
		if (pending) {
			pendingThemeUpdatesRef.current = null;
			// Fire-and-forget — the renderer may be tearing down, so we
			// can't await. The IPC layer queues the write before the
			// process exits. The Promise's rejection MUST be handled
			// here (via `.catch`) — `void call(...)` alone discards the
			// Promise without installing a rejection handler, which
			// surfaces as an "unhandled promise rejection" warning in
			// Electron (and can crash the renderer in strict modes).
			// Theme is local-only if backend unavailable — the warn is
			// the entire recovery path.
			void call("set_config", pending).catch((e) => {
				console.warn("[useTheme] set_config (flush) failed:", e);
			});
		}
	}, [call]);

	const scheduleThemeSave = useCallback(
		(
			updates: Partial<
				Pick<
					VoiceTyperConfig,
					"theme_mode" | "theme_preset" | "custom_theme" | "text_size"
				>
			>,
		): void => {
			// Merge into the pending payload so successive rapid
			// changes (e.g. typing into a custom-colour picker)
			// coalesce into a single backend write.
			pendingThemeUpdatesRef.current = {
				...pendingThemeUpdatesRef.current,
				...updates,
			};
			// Cancel any pending save and schedule a new one.
			if (themeSaveTimerRef.current) {
				clearTimeout(themeSaveTimerRef.current);
			}
			themeSaveTimerRef.current = setTimeout(async () => {
				themeSaveTimerRef.current = null;
				const pending = pendingThemeUpdatesRef.current;
				pendingThemeUpdatesRef.current = null;
				if (!pending) return;
				try {
					await call("set_config", pending);
				} catch (e) {
					// Theme is local-only if backend unavailable
					console.warn("[useTheme] set_config (debounced) failed:", e);
				}
			}, 300);
		},
		[call],
	);

	const handleThemeChange = useCallback(
		async (mode: VoiceTyperConfig["theme_mode"]): Promise<void> => {
			setThemeMode(mode);
			scheduleThemeSave({ theme_mode: mode });
		},
		[scheduleThemeSave],
	);

	//public-facing setters for theme_preset / custom_theme /
	// text_size. Each updates the local state immediately (so the UI
	// reflects the change without waiting for the backend round-trip)
	// AND schedules a debounced save. The localStorage-sync effect
	// below fires on every state change so the cache stays fresh
	// for the next mount regardless of whether the backend save
	// completes first.
	const setThemePreset = useCallback(
		(preset: VoiceTyperConfig["theme_preset"]): void => {
			setThemePresetState(preset);
			scheduleThemeSave({ theme_preset: preset });
		},
		[scheduleThemeSave],
	);

	const setCustomTheme = useCallback(
		(custom: CustomThemeData | null): void => {
			setCustomThemeState(custom);
			// ``custom_theme`` may be ``null`` (user cleared the
			// custom colours); the backend accepts ``null`` as
			// "revert to preset". The scheduleThemeSave helper
			// merges the value as-is into the pending payload.
			scheduleThemeSave({ custom_theme: custom });
		},
		[scheduleThemeSave],
	);

	const setTextSize = useCallback(
		(size: number): void => {
			setTextSizeState(size);
			scheduleThemeSave({ text_size: size });
		},
		[scheduleThemeSave],
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
