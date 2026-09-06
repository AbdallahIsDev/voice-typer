/**
 * themeSync.ts — the backend→store sync concern of the theme subsystem,
 * split out of ``hooks/useTheme.ts``. Owns:
 *
 * 1. ``reloadThemeFromConfig`` — the initial (and on-demand) config
 *    read that seeds the store + localStorage from the backend.
 * 2. ``handleConfigChanged`` — the stable ``config_changed`` push
 *    handler shared by every ``useTheme`` consumer.
 * 3. ``ensureThemeSideEffects`` — the initOnce guard that runs the
 *    side-effecting setup (initial reload + ``beforeunload`` flush
 *    listener) EXACTLY ONCE per app load, no matter how many
 *    ``useTheme`` callers mount.
 *
 * All state updates go through the shared store in ``themeStore.ts``
 * (the internal state-only setters — the change came FROM the backend,
 * so round-tripping it would be a feedback loop).
 */
import { setSoundFeedbackEnabled } from "@/lib/sound-manager";
import {
	LS_CUSTOM_THEME,
	LS_TEXT_SIZE,
	LS_THEME_MODE,
	LS_THEME_PRESET,
} from "@/lib/theme-storage-keys";
import type { CustomThemeData } from "@/themes";
import type { VoiceTyperConfig } from "@/types/config";
import {
	getActiveCall,
	getActiveMergeConfig,
	setActiveBridge,
	type ThemeCallFn,
} from "./themeBridge";
import { installBeforeUnloadFlush } from "./themePersist";
import { useThemeStore } from "./themeStore";

let themeInitStarted = false;

// ── reloadThemeFromConfig (module-level singleton) ───────────────────
//
// Pulled out of the hook body so it can run EXACTLY ONCE per app load
// (via ``ensureThemeSideEffects``). Previously each ``useTheme`` caller
// ran its own mount effect that called ``reloadThemeFromConfig``, so
// opening Settings fired a second ``get_config`` IPC round-trip. Now
// only the first caller triggers the reload; subsequent callers read
// the already-populated store.
export function reloadThemeFromConfig(): Promise<void> {
	const activeCall = getActiveCall();
	if (!activeCall) return Promise.resolve();
	return activeCall<VoiceTyperConfig>("get_config")
		.then((cfg) => {
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
					useThemeStore.getState().setThemeModeState(cfg.theme_mode);
				}
				if (cfg?.theme_preset) {
					localStorage.setItem(LS_THEME_PRESET, cfg.theme_preset);
					useThemeStore.getState().setThemePresetState(cfg.theme_preset);
				}
				if (cfg?.custom_theme) {
					localStorage.setItem(
						LS_CUSTOM_THEME,
						JSON.stringify(cfg.custom_theme),
					);
					useThemeStore.getState().setCustomThemeState(cfg.custom_theme);
				} else if (cfg?.theme_preset && cfg.theme_preset !== "custom") {
					// Backend confirmed a non-custom preset — clear
					// any stale custom-theme cache so the bootstrap
					// doesn't try to derive custom vars from it.
					localStorage.removeItem(LS_CUSTOM_THEME);
				}
				if (cfg?.text_size) {
					localStorage.setItem(LS_TEXT_SIZE, String(cfg.text_size));
					useThemeStore.getState().setTextSizeState(cfg.text_size);
				}
			} catch (e) {
				// localStorage may be unavailable — non-fatal.
				// State setters below still fire so the UI
				// reflects the backend values for this session.
				console.warn("[renderer:useTheme] localStorage cache write failed:", e);
				if (cfg?.theme_mode)
					useThemeStore.getState().setThemeModeState(cfg.theme_mode);
				if (cfg?.theme_preset)
					useThemeStore.getState().setThemePresetState(cfg.theme_preset);
				if (cfg?.custom_theme)
					useThemeStore.getState().setCustomThemeState(cfg.custom_theme);
				if (cfg?.text_size)
					useThemeStore.getState().setTextSizeState(cfg.text_size);
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
		})
		.catch((e) => {
			console.warn("[renderer:useTheme] get_config failed:", e);
		})
		.finally(() => {
			// FLASH-FIX: regardless of success/failure, flip the
			// guard so the theme-application effect can run.
			// On failure we keep the cached localStorage state
			// (already applied by ``theme-bootstrap.ts``) —
			// flipping the flag here lets the effect take over
			// for subsequent state changes (e.g. when the user
			// toggles the theme via the sidebar).
			useThemeStore.getState().setHasInitialReloadCompleted(true);
		});
}

// ── config_changed handler (module-level singleton) ──────────────────
//
// The handler invoked by the ``usePythonEvent("config_changed", ...)``
// subscription. Kept as a module-level STABLE function reference so
// the ``usePythonEvent`` hook's internal ``handlerRef`` always points
// at the same identity (no re-subscription needed when consumers
// re-render).
//
// Both ``useTheme`` callers register their own ``usePythonEvent``
// entry in the dispatcher's ``typeSubscribers`` Map, but the
// dispatcher holds a SINGLE ``api.onEvent`` subscription (it
// deduplicates the underlying IPC listener). Both entries' handlers
// are invoked per event — but the handler here updates the SHARED
// Zustand store, and Zustand's ``set`` skips notification when the
// new value is ``Object.is``-equal to the old. So the second
// invocation is a no-op (cheap state-entry lookup, no subscriber
// fan-out).
export function handleConfigChanged(
	data: Record<string, unknown> | undefined,
): (() => void) | undefined {
	if (!data) return undefined;
	// Merge into the store's config cache.
	const activeMergeConfig = getActiveMergeConfig();
	if (activeMergeConfig) {
		// The backend's ``config_changed`` payload is a partial config
		// object — cast to ``Partial<VoiceTyperConfig>`` for the
		// ``mergeConfig`` call (the cast is safe because the backend
		// only sends config-typed fields; unknown fields are silently
		// ignored by ``mergeConfig``'s merge implementation).
		activeMergeConfig(data as Partial<VoiceTyperConfig>);
	}
	const store = useThemeStore.getState();
	if (typeof data.text_size === "number") {
		store.setTextSizeState(data.text_size);
	}
	if (typeof data.theme_mode === "string") {
		store.setThemeModeState(data.theme_mode as VoiceTyperConfig["theme_mode"]);
	}
	if (typeof data.theme_preset === "string") {
		store.setThemePresetState(
			data.theme_preset as VoiceTyperConfig["theme_preset"],
		);
	}
	if (data.custom_theme && typeof data.custom_theme === "object") {
		store.setCustomThemeState(data.custom_theme as CustomThemeData);
	}
	// SOUND-FIX-REWRITE: keep localStorage in sync
	// when the sound_feedback_enabled flag changes
	// via ANY path (Settings toggle, config import,
	// CLI tool, etc.) — not just the Settings UI.
	if (typeof data.sound_feedback_enabled === "boolean") {
		setSoundFeedbackEnabled(data.sound_feedback_enabled);
	}
	return undefined;
}

// ── ensureThemeSideEffects (initOnce guard) ──────────────────────────
//
// Called from the ``useTheme`` hook's mount ``useEffect``. Sets
// ``themeInitStarted = true`` on the first call and runs the
// side-effecting setup (initial ``reloadThemeFromConfig``,
// ``beforeunload`` listener). Subsequent calls are no-ops (the flag
// short-circuits), but they STILL refresh the ``activeCall`` /
// ``activeMergeConfig`` references — in practice these are stable
// across the app's lifetime, but refreshing is cheap and protects
// against the (theoretical) case where a caller passes a different
// ``call`` function (e.g. in tests with a mocked bridge).
export function ensureThemeSideEffects(
	call: ThemeCallFn,
	mergeConfig: (updates: Partial<VoiceTyperConfig>) => void,
): void {
	// Always refresh the singleton references — they're stable in
	// practice (from ``usePython`` / ``useAppStore``), but refreshing
	// is cheap and makes the singleton robust to test-environment
	// bridge swaps.
	setActiveBridge(call, mergeConfig);

	if (themeInitStarted) return;
	if (typeof window === "undefined") return; // SSR guard
	themeInitStarted = true;

	// 1. Initial reload from config (single ``get_config`` IPC call
	//    app-wide, was previously 2 with dual-instance pattern).
	void reloadThemeFromConfig();

	// 2. ``beforeunload`` flush listener (single listener app-wide,
	//    was previously 2 — the second was an idempotent no-op but
	//    still consumed an event-listener slot).
	installBeforeUnloadFlush();
}

/** Reset the initOnce flag — used by the ``_resetThemeStoreForTest``
 * seam so a test can mount a fresh ``useTheme`` consumer. */
export function resetThemeSyncSingletons(): void {
	themeInitStarted = false;
}
