/**
 * themeBridge.ts — the singleton IPC-bridge slots shared by the theme
 * sync (config read) and persist (config write) paths. Split out of
 * ``hooks/useTheme.ts`` so the sync and persist modules can both read
 * the current bridge without importing each other (no module cycle).
 *
 * The ``call`` and ``mergeConfig`` references are refreshed on every
 * ``ensureThemeSideEffects`` call (every consumer mount — see
 * ``themeSync.ts``). In practice these are stable across the app's
 * lifetime (they come from ``usePython`` and ``useAppStore``, both of
 * which return stable references), so refreshing is a no-op for the
 * second caller.
 *
 * The slots MUST be read at use time (not captured in closures) so the
 * debounced save timer fires against the LATEST bridge and the flush
 * path works even when the registering consumer has since unmounted.
 */
import type { VoiceTyperConfig } from "@/types/config";

/** Type alias for the public ``call`` function shape accepted by
 * ``useTheme``. Used to type the module-level ``activeCall`` slot. */
export type ThemeCallFn = <T = unknown>(
	type: string,
	data?: Record<string, unknown>,
) => Promise<T>;

/** The ``mergeConfig`` action shape (from the appStore) used to merge
 * backend-pushed config partials into the app-level config cache. */
export type ThemeMergeConfigFn = (updates: Partial<VoiceTyperConfig>) => void;

let activeCall: ThemeCallFn | null = null;
let activeMergeConfig: ThemeMergeConfigFn | null = null;

export function getActiveCall(): ThemeCallFn | null {
	return activeCall;
}

export function setActiveCall(call: ThemeCallFn): void {
	activeCall = call;
}

/** Refresh BOTH singleton references — used by the initOnce setup in
 * ``themeSync.ensureThemeSideEffects``. */
export function setActiveBridge(
	call: ThemeCallFn,
	mergeConfig: ThemeMergeConfigFn,
): void {
	activeCall = call;
	activeMergeConfig = mergeConfig;
}

export function getActiveMergeConfig(): ThemeMergeConfigFn | null {
	return activeMergeConfig;
}

/** Null both slots — used by the ``_resetThemeStoreForTest`` seam. */
export function clearActiveBridge(): void {
	activeCall = null;
	activeMergeConfig = null;
}
