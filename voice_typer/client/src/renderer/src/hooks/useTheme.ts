/**
 * Theme hook: manages the active theme mode (light/dark/system), preset,
 * custom colours, and text-size scaling.  Applies the theme to the
 * document via CSS variables and persists changes to the backend config
 * with a 300ms debounce.
 *
 * @param call  The Python bridge `call` function (from usePython).
 *
 * ── singleton store + initOnce side-effect guard ───────────────
 *
 * ``useTheme`` is called from BOTH ``App.tsx`` (always-mounted) AND
 * ``Settings.tsx`` (lazy-mounted when the user opens Settings).
 * Previously each call instantiated an INDEPENDENT React state
 * (``themeMode``, ``themePreset``, ``customTheme``, ``textSize``) plus:
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
 * The fix mirrors ``useNavigation.ts``'s singleton-store pattern:
 *
 *   1. Theme state lives in a module-level Zustand store
 *      (``useThemeStore``). Both callers READ from the same store via
 *      ``useShallow``, so a state change in one caller's setter
 *      re-renders BOTH callers.
 *
 *   2. Side-effecting logic (``reloadThemeFromConfig``, the
 *      ``config_changed`` subscription handler, the
 *      ``beforeunload`` flush listener, the debounced
 *      ``scheduleThemeSave`` + ``flushPendingThemeSave``) is moved to
 *      MODULE-LEVEL functions guarded by an ``initOnce`` flag
 *      (``themeInitStarted``). The first ``useTheme`` caller's mount
 *      effect calls ``ensureThemeSideEffects(call, mergeConfig)``,
 *      which sets the flag and runs the side effects EXACTLY ONCE.
 *      Subsequent callers' mount effects are no-ops.
 *
 *   3. The ``usePythonEvent("config_changed", handler)`` call is
 *      kept inside the hook body (rules-of-hooks), but the handler
 *      is a STABLE module-level singleton (``configChangedHandler``)
 *      that updates the shared store. The dispatcher in
 *      ``usePython.ts`` already deduplicates the underlying
 *      ``api.onEvent`` subscription, so N callers share ONE IPC
 *      listener; the per-caller handlers all update the same store
 *      and Zustand's ``set`` deduplicates same-value writes (the
 *      second caller's handler invocation is a no-op).
 *
 *   4. The per-instance theme-application effect (CSS variables) and
 *      the per-instance ``localStorage`` sync effect are KEPT
 *      per-instance — they are idempotent (apply the same values,
 *      write the same keys) and cheap, so deduplicating them would
 *      add complexity without meaningful perf gain.
 *
 *   5. The unmount cleanup that calls ``flushPendingThemeSave()`` is
 *      KEPT per-instance — when the FIRST caller unmounts (e.g.
 *      Settings closes), any pending save is flushed. The second
 *      caller's unmount cleanup is a no-op (no pending). The
 *      module-level ``beforeunload`` listener (installed once via
 *      ``ensureThemeSideEffects``) stays for app lifetime.
 *
 * ``_resetThemeStoreForTest`` is the test seam (mirrors
 * ``_resetNavigationForTest``): it re-reads localStorage into the
 * store + resets the ``initOnce`` flag so a test can mount a fresh
 * ``useTheme`` consumer deterministically.
 */
import { useCallback, useEffect, useRef } from "react";
import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
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
		console.warn("[renderer:useTheme] readLsThemeMode failed:", e);
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
		console.warn("[renderer:useTheme] readLsThemePreset failed:", e);
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
		console.warn("[renderer:useTheme] readLsCustomTheme parse failed:", e);
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
		console.warn("[renderer:useTheme] readLsTextSize failed:", e);
	}
	return 14;
}

// ── Module-level Zustand store ───────────────────────────────────────
//
// Mirrors the ``useNavigation`` pattern: theme state is a module-level
// singleton so every ``useTheme`` caller reads from the SAME source.
// A state change in one caller's setter re-renders ALL callers via
// Zustand's subscriber-notification mechanism.
//
// The "internal" setters (``setThemeModeState`` etc.) update state
// WITHOUT scheduling a backend save — they're used by backend-pushed
// paths (``reloadThemeFromConfig``, ``config_changed`` handler) where
// the change came FROM the backend, so round-tripping it would be a
// feedback loop. The public-facing setters (in the hook body, below)
// wrap these + add the debounced ``scheduleThemeSave`` call.

interface ThemeState {
	themeMode: VoiceTyperConfig["theme_mode"];
	themePreset: VoiceTyperConfig["theme_preset"];
	customTheme: CustomThemeData | null;
	textSize: number;
	// FLASH-FIX: tracks whether the first ``reloadThemeFromConfig``
	// call has completed. Until it has, the theme-application effect
	// in the hook is suppressed — the pre-React ``theme-bootstrap.ts``
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
	setThemeModeState: (mode: VoiceTyperConfig["theme_mode"]) => void;
	setThemePresetState: (preset: VoiceTyperConfig["theme_preset"]) => void;
	setCustomThemeState: (custom: CustomThemeData | null) => void;
	setTextSizeState: (size: number) => void;
	setHasInitialReloadCompleted: (value: boolean) => void;
}

const useThemeStore = create<ThemeState>()((set) => ({
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

// ── Singleton side-effect state ──────────────────────────────────────
//
// All previously per-instance side-effect state (the ``call`` function
// reference, ``mergeConfig`` from the appStore, the debounce timer ref,
// the pending-updates ref) is now module-level. The first ``useTheme``
// caller's mount effect calls ``ensureThemeSideEffects(call,
// mergeConfig)`` which sets ``themeInitStarted = true`` and runs the
// side effects EXACTLY ONCE. Subsequent callers' mount effects are
// no-ops.
//
// The ``call`` and ``mergeConfig`` references are refreshed on every
// ``ensureThemeSideEffects`` call (every consumer mount). In practice
// these are stable across the app's lifetime (they come from
// ``usePython`` and ``useAppStore``, both of which return stable
// references), so refreshing is a no-op for the second caller.

let themeInitStarted = false;
let activeCall:
	| ((type: string, data?: Record<string, unknown>) => Promise<unknown>)
	| null = null;
let activeMergeConfig: ((updates: Partial<VoiceTyperConfig>) => void) | null =
	null;
let themeSaveTimer: ReturnType<typeof setTimeout> | null = null;
let pendingThemeUpdates: Partial<
	Pick<
		VoiceTyperConfig,
		"theme_mode" | "theme_preset" | "custom_theme" | "text_size"
	>
> | null = null;

// Type alias for the public ``call`` function shape accepted by
// ``useTheme``. Used to type the module-level ``activeCall`` slot.
type ThemeCallFn = <T = unknown>(
	type: string,
	data?: Record<string, unknown>,
) => Promise<T>;

// ── reloadThemeFromConfig (module-level singleton) ───────────────────
//
// Pulled out of the hook body so it can run EXACTLY ONCE per app load
// (via ``ensureThemeSideEffects``). Previously each ``useTheme`` caller
// ran its own mount effect that called ``reloadThemeFromConfig``, so
// opening Settings fired a second ``get_config`` IPC round-trip. Now
// only the first caller triggers the reload; subsequent callers read
// the already-populated store.
function reloadThemeFromConfigImpl(): Promise<void> {
	if (!activeCall) return Promise.resolve();
	const call = activeCall as ThemeCallFn;
	return call<VoiceTyperConfig>("get_config")
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
function handleConfigChanged(
	data: Record<string, unknown> | undefined,
): (() => void) | undefined {
	if (!data) return undefined;
	// Merge into the store's config cache.
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

// ── scheduleThemeSave / flushPendingThemeSave (module-level) ─────────
//
// PERF: debounce the backend write so rapid theme toggling (e.g.
// user clicking through light → dark → system quickly) doesn't
// fire 3 separate set_config IPC calls. The local UI updates
// immediately (via the store); the backend save is deferred 300ms
// and only the LAST selected mode is persisted.
//
// QUIT-FLUSH-FIX: previously, if the user changed the theme and
// then closed the app (close-to-tray → tray Quit, or window close)
// during the 300ms debounce window, the pending save was dropped
// and the next launch loaded the old theme from the backend. The
// ``beforeunload`` listener (installed once via
// ``ensureThemeSideEffects``) + the per-instance unmount cleanup
// (in the hook body, below) both call ``flushPendingThemeSave`` so
// the pending save fires synchronously before the renderer tears
// down.

function flushPendingThemeSave(): void {
	if (themeSaveTimer) {
		clearTimeout(themeSaveTimer);
		themeSaveTimer = null;
	}
	const pending = pendingThemeUpdates;
	if (pending) {
		pendingThemeUpdates = null;
		if (activeCall) {
			// Fire-and-forget — the renderer may be tearing down, so we
			// can't await. The IPC layer queues the write before the
			// process exits. The Promise's rejection MUST be handled
			// here (via `.catch`) — `void call(...)` alone discards the
			// Promise without installing a rejection handler, which
			// surfaces as an "unhandled promise rejection" warning in
			// Electron (and can crash the renderer in strict modes).
			// Theme is local-only if backend unavailable — the warn is
			// the entire recovery path.
			void (activeCall as ThemeCallFn)("set_config", pending).catch((e) => {
				console.warn("[renderer:useTheme] set_config (flush) failed:", e);
			});
		}
	}
}

function scheduleThemeSave(
	updates: Partial<
		Pick<
			VoiceTyperConfig,
			"theme_mode" | "theme_preset" | "custom_theme" | "text_size"
		>
	>,
): void {
	// Merge into the pending payload so successive rapid
	// changes (e.g. typing into a custom-colour picker)
	// coalesce into a single backend write.
	pendingThemeUpdates = {
		...pendingThemeUpdates,
		...updates,
	};
	// Cancel any pending save and schedule a new one.
	if (themeSaveTimer) {
		clearTimeout(themeSaveTimer);
	}
	themeSaveTimer = setTimeout(async () => {
		themeSaveTimer = null;
		const pending = pendingThemeUpdates;
		pendingThemeUpdates = null;
		if (!pending || !activeCall) return;
		try {
			await (activeCall as ThemeCallFn)("set_config", pending);
		} catch (e) {
			// Theme is local-only if backend unavailable
			console.warn("[renderer:useTheme] set_config (debounced) failed:", e);
		}
	}, 300);
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
function ensureThemeSideEffects(
	call: ThemeCallFn,
	mergeConfig: (updates: Partial<VoiceTyperConfig>) => void,
): void {
	// Always refresh the singleton references — they're stable in
	// practice (from ``usePython`` / ``useAppStore``), but refreshing
	// is cheap and makes the singleton robust to test-environment
	// bridge swaps.
	activeCall = call;
	activeMergeConfig = mergeConfig;

	if (themeInitStarted) return;
	if (typeof window === "undefined") return; // SSR guard
	themeInitStarted = true;

	// 1. Initial reload from config (single ``get_config`` IPC call
	//    app-wide, was previously 2 with dual-instance pattern).
	void reloadThemeFromConfigImpl();

	// 2. ``beforeunload`` flush listener (single listener app-wide,
	//    was previously 2 — the second was an idempotent no-op but
	//    still consumed an event-listener slot).
	window.addEventListener("beforeunload", flushPendingThemeSave);
}

// ── Test seam ────────────────────────────────────────────────────────
//
// Mirrors ``_resetNavigationForTest``: re-reads localStorage into the
// shared store + resets the ``initOnce`` flag so a test can mount a
// fresh ``useTheme`` consumer deterministically. @internal
export function _resetThemeStoreForTest(): void {
	useThemeStore.setState({
		themeMode: readLsThemeMode(),
		themePreset: readLsThemePreset(),
		customTheme: readLsCustomTheme(),
		textSize: readLsTextSize(),
		hasInitialReloadCompleted: false,
	});
	themeInitStarted = false;
	activeCall = null;
	activeMergeConfig = null;
	if (themeSaveTimer) {
		clearTimeout(themeSaveTimer);
		themeSaveTimer = null;
	}
	pendingThemeUpdates = null;
	if (typeof window !== "undefined") {
		window.removeEventListener("beforeunload", flushPendingThemeSave);
	}
}

// ── Hook ─────────────────────────────────────────────────────────────

export function useTheme(
	call: <T = unknown>(
		type: string,
		data?: Record<string, unknown>,
	) => Promise<T>,
) {
	const mergeConfig = useAppStore((s) => s.mergeConfig);

	// callRef mirror (Home.tsx pattern): the singleton init effect below
	// must not depend on the `call` identity — stable in production, but
	// a test mock handing out a fresh `call` per render would re-fire
	// it every render (OOM loop class). The mirror keeps the ref fresh;
	// ``ensureThemeSideEffects``'s initOnce guard makes re-runs no-ops.
	const callRef = useRef(call);
	useEffect(() => {
		callRef.current = call;
	}, [call]);

	// ── Read state from the singleton store ────────────────────────
	//
	// ``useShallow`` collapses the 5 value reads into ONE selector
	// run + ONE shallow-equal check per ``set()``. The selector
	// returns a fresh object on every run, but ``useShallow``
	// returns the CACHED object reference when the shallow-equal
	// check passes (i.e. none of the 5 values changed), so unrelated
	// state changes (none currently, but defensive) don't trigger
	// re-renders.
	const {
		themeMode,
		themePreset,
		customTheme,
		textSize,
		hasInitialReloadCompleted,
	} = useThemeStore(
		useShallow((s) => ({
			themeMode: s.themeMode,
			themePreset: s.themePreset,
			customTheme: s.customTheme,
			textSize: s.textSize,
			hasInitialReloadCompleted: s.hasInitialReloadCompleted,
		})),
	);

	// Stable internal setters (Zustand store actions — never change
	// identity). Used by the public-facing setters below + by the
	// theme-application effect.
	const setThemeModeState = useThemeStore((s) => s.setThemeModeState);
	const setThemePresetState = useThemeStore((s) => s.setThemePresetState);
	const setCustomThemeState = useThemeStore((s) => s.setCustomThemeState);
	const setTextSizeState = useThemeStore((s) => s.setTextSizeState);

	// ── Singleton side-effect init (initOnce guard) ────────────────
	//
	// The FIRST ``useTheme`` caller's mount effect triggers
	// ``ensureThemeSideEffects``, which sets ``themeInitStarted = true``
	// and runs: (1) ``reloadThemeFromConfig`` (single ``get_config``
	// IPC), (2) the ``beforeunload`` flush listener (single app-wide
	// listener). Subsequent callers' mount effects are no-ops (the
	// flag short-circuits). The ``call`` reference is read via
	// ``callRef.current`` (mirrored above) so the effect deps stay
	// identity-free; ``mergeConfig`` remains a dep because a stale
	// config-merge closure would apply outdated theme defaults.
	useEffect(() => {
		ensureThemeSideEffects(callRef.current, mergeConfig);
	}, [mergeConfig]);

	// ── Theme detection & application (per-instance, idempotent) ────
	//
	// KEPT per-instance rather than moved to the singleton — it's
	// idempotent (applies the same CSS vars) and cheap. Both
	// consumers' effects fire on every state change, but they write
	// the same values to the DOM.
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

	// Public-facing reload function — exposed so App.tsx's
	// onboarding-completion handler can re-trigger a full theme reload
	// after the wizard applies the user's choices (the onboarding_apply
	// IPC route doesn't reliably emit a config_changed event, so we
	// explicitly re-fetch the config).
	//
	// Wraps the module-level ``reloadThemeFromConfigImpl`` so the
	// public API stays the same (``reloadThemeFromConfig()`` returns a
	// Promise). The ``call`` reference is refreshed from the singleton
	// so the latest caller's bridge is used.
	const reloadThemeFromConfig = useCallback(async () => {
		// Read the freshest bridge from the mirrored ref (see the
		// callRef mirror above) so this callback keeps a STABLE identity
		// ([] deps) — its consumer (App.tsx → useConnectionToasts) lists
		// it in an effect dep array, and an identity churn under test
		// mocks would re-fire that effect on every render.
		activeCall = callRef.current;
		await reloadThemeFromConfigImpl();
	}, []);

	// ── Sync theme state to localStorage on every change ────────────
	//
	// KEPT per-instance — idempotent (writes the same value twice).
	// Both consumers' effects fire on every state change, but they
	// write the same keys with the same values.
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
			console.warn("[renderer:useTheme] localStorage sync failed:", e);
		}
	}, [themeMode, themePreset, customTheme, textSize]);

	// ── Config changed push (live UI updates) ───────────────────────
	//
	// ``usePythonEvent`` is called per-consumer (rules-of-hooks), but
	// the handler is the STABLE module-level ``handleConfigChanged``
	// singleton. The dispatcher in ``usePython.ts`` already
	// deduplicates the underlying ``api.onEvent`` subscription, so N
	// consumers share ONE IPC listener. Both consumers' handler
	// invocations update the SAME Zustand store, and Zustand's ``set``
	// skips notification when the new value is ``Object.is``-equal to
	// the old — so the second invocation is a no-op.
	usePythonEvent("config_changed", handleConfigChanged);

	// ── Theme change handler (save to config) ─────────────────────
	//
	// Public-facing setters: each updates the store immediately (so
	// the UI reflects the change without waiting for the backend
	// round-trip) AND schedules a debounced save via the module-level
	// ``scheduleThemeSave``. The localStorage-sync effect above fires
	// on every store change so the cache stays fresh for the next
	// mount regardless of whether the backend save completes first.
	const handleThemeChange = useCallback(
		async (mode: VoiceTyperConfig["theme_mode"]): Promise<void> => {
			setThemeModeState(mode);
			scheduleThemeSave({ theme_mode: mode });
		},
		[setThemeModeState],
	);

	const setThemePreset = useCallback(
		(preset: VoiceTyperConfig["theme_preset"]): void => {
			setThemePresetState(preset);
			scheduleThemeSave({ theme_preset: preset });
		},
		[setThemePresetState],
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
		[setCustomThemeState],
	);

	const setTextSize = useCallback(
		(size: number): void => {
			setTextSizeState(size);
			scheduleThemeSave({ text_size: size });
		},
		[setTextSizeState],
	);

	// QUIT-FLUSH-FIX: flush the pending theme save on unmount. The
	// ``beforeunload`` listener (installed once via
	// ``ensureThemeSideEffects``) handles app-quit; this per-instance
	// unmount cleanup handles the case where the FIRST caller unmounts
	// (e.g. Settings closes) while a save is pending. The second
	// caller's unmount cleanup is a no-op (no pending after the first
	// flush). Idempotent.
	//
	// The empty dep array means this effect runs ONCE per mount with
	// a stable cleanup closure. ``flushPendingThemeSave`` is a
	// module-level function (stable identity), so the cleanup always
	// references the current singleton state.
	useEffect(() => {
		return () => {
			flushPendingThemeSave();
		};
	}, []);

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
