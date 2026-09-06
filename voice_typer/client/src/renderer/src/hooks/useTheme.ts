/**
 * Theme hook: manages the active theme mode (light/dark/system), preset,
 * custom colours, and text-size scaling.  Applies the theme to the
 * document via CSS variables and persists changes to the backend config
 * with a 300ms debounce.
 *
 * @param call  The Python bridge `call` function (from usePython).
 *
 * ── composition root for the theme subsystem ────────────────────
 *
 * This file is now the composition root only — the concern modules live
 * in ``hooks/theme/`` (each imported once, no cycles):
 *
 *   - ``theme/themeStore.ts``   — the singleton Zustand store + the
 *     localStorage hydration readers (state concern).
 *   - ``theme/themeApply.ts``   — DOM application of the resolved theme
 *     (``.dark`` class, preset/custom CSS vars, ``--font-scale``).
 *   - ``theme/themePersist.ts`` — the single debounced backend write
 *     path (``scheduleThemeSave``), the quit-time flush
 *     (``flushPendingThemeSave``), and the localStorage cache sync.
 *   - ``theme/themeSync.ts``    — backend→store sync: the initial
 *     config reload, the stable ``config_changed`` handler, and the
 *     initOnce side-effect guard (``ensureThemeSideEffects``).
 *   - ``theme/themeBridge.ts``  — the singleton IPC-bridge slots shared
 *     by the sync and persist paths (read at use time, never captured).
 *
 * ── singleton store + initOnce side-effect guard (rationale) ─────
 *
 * ``useTheme`` is called from BOTH ``App.tsx`` (always-mounted) AND
 * ``Settings.tsx`` (lazy-mounted when the user opens Settings), so all
 * state lives in the shared module-level store and every side-effecting
 * piece of setup runs EXACTLY ONCE via the ``ensureThemeSideEffects``
 * initOnce guard: one ``get_config`` reload, one ``beforeunload`` flush
 * listener, one ``config_changed`` push handler (the dispatcher in
 * ``usePython.ts`` deduplicates the underlying ``api.onEvent``
 * subscription, and the shared store makes the second handler
 * invocation a no-op).
 *
 * The per-instance effects (theme application, localStorage cache sync,
 * unmount flush) are KEPT per-instance — they are idempotent (apply the
 * same values, write the same keys) and cheap, so deduplicating them
 * would add complexity without meaningful perf gain.
 *
 * ``_resetThemeStoreForTest`` is the test seam (mirrors
 * ``_resetNavigationForTest``): it re-reads localStorage into the
 * store + resets the ``initOnce`` flag so a test can mount a fresh
 * ``useTheme`` consumer deterministically.
 */
import { useCallback, useEffect } from "react";
import { useShallow } from "zustand/react/shallow";
import { useLatestRef } from "@/hooks/useLatestRef";
import { usePythonEvent } from "@/hooks/usePython";
import { useAppStore } from "@/stores/appStore";
import type { CustomThemeData } from "@/themes";
import type { VoiceTyperConfig } from "@/types/config";
import { applyTextScale, applyThemeToDocument } from "./theme/themeApply";
import { clearActiveBridge, setActiveCall } from "./theme/themeBridge";
import {
	flushPendingThemeSave,
	removeBeforeUnloadFlush,
	resetThemePersistState,
	scheduleThemeSave,
	syncThemeCacheToLocalStorage,
} from "./theme/themePersist";
import {
	resetThemeStoreToCachedState,
	useThemeStore,
} from "./theme/themeStore";
import {
	ensureThemeSideEffects,
	handleConfigChanged,
	reloadThemeFromConfig,
	resetThemeSyncSingletons,
} from "./theme/themeSync";

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
	const callRef = useLatestRef(call);

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
	// biome-ignore lint/correctness/useExhaustiveDependencies: callRef is a useLatestRef mirror: reading .current in a stale closure is the hook's documented contract — .current must NOT become a dep
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

		// Apply current theme.
		applyThemeToDocument(
			themeMode,
			themePreset,
			customTheme,
			prefersDark.matches,
		);

		// Listen for system changes when in 'system' mode
		const handler = () => {
			if (themeMode === "system") {
				applyThemeToDocument(
					"system",
					themePreset,
					customTheme,
					prefersDark.matches,
				);
			}
		};
		prefersDark.addEventListener("change", handler);
		return () => prefersDark.removeEventListener("change", handler);
	}, [themeMode, themePreset, customTheme, hasInitialReloadCompleted]);

	useEffect(() => {
		applyTextScale(textSize);
	}, [textSize]);

	// Public-facing reload function — exposed so App.tsx's
	// onboarding-completion handler can re-trigger a full theme reload
	// after the wizard applies the user's choices (the onboarding_apply
	// IPC route doesn't reliably emit a config_changed event, so we
	// explicitly re-fetch the config).
	//
	// Wraps the module-level ``reloadThemeFromConfig`` (themeSync) so the
	// public API stays the same (``reloadThemeFromConfig()`` returns a
	// Promise). The ``call`` reference is refreshed from the singleton
	// so the latest caller's bridge is used.
	// biome-ignore lint/correctness/useExhaustiveDependencies: callRef is a useLatestRef mirror: reading .current in a stale closure is the hook's documented contract — .current must NOT become a dep
	const reloadThemeFromConfigStable = useCallback(async () => {
		// Read the freshest bridge from the mirrored ref (see the
		// callRef mirror above) so this callback keeps a STABLE identity
		// ([] deps) — its consumer (App.tsx → useConnectionToasts) lists
		// it in an effect dep array, and an identity churn under test
		// mocks would re-fire that effect on every render.
		setActiveCall(callRef.current);
		await reloadThemeFromConfig();
	}, []);

	// ── Sync theme state to localStorage on every change ────────────
	//
	// KEPT per-instance — idempotent (writes the same value twice).
	// Both consumers' effects fire on every state change, but they
	// write the same keys with the same values.
	useEffect(() => {
		syncThemeCacheToLocalStorage(themeMode, themePreset, customTheme, textSize);
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
		reloadThemeFromConfig: reloadThemeFromConfigStable,
	};
}

// ── Test seam ────────────────────────────────────────────────────────
//
// Mirrors ``_resetNavigationForTest``: re-reads localStorage into the
// shared store + resets the ``initOnce`` flag so a test can mount a
// fresh ``useTheme`` consumer deterministically. Composed from the
// per-concern reset helpers (store / sync / persist / bridge).
// @internal
export function _resetThemeStoreForTest(): void {
	resetThemeStoreToCachedState();
	resetThemeSyncSingletons();
	clearActiveBridge();
	resetThemePersistState();
	removeBeforeUnloadFlush();
}
