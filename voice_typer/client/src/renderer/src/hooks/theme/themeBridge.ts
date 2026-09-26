import type { PythonCall } from "@/hooks/usePython";
import type { LausuConfig } from "@/types/config";

/** Type alias for the public ``call`` function shape accepted by
 * ``useTheme``. The canonical declaration is the bridge's
 * ``PythonCall`` (``lib/python-bridge/usePython.ts``), single-sourced
 * here instead of re-declared structurally, so the slot typing can
 * module-level ``activeCall`` slot. */
export type ThemeCallFn = PythonCall;

export type ThemeMergeConfigFn = (updates: Partial<LausuConfig>) => void;

let activeCall: ThemeCallFn | null = null;
let activeMergeConfig: ThemeMergeConfigFn | null = null;

export function getActiveCall(): ThemeCallFn | null {
	return activeCall;
}

export function setActiveCall(call: ThemeCallFn): void {
	activeCall = call;
}

/** Refresh BOTH singleton references, used by the initOnce setup in
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

/** Null both slots, used by the ``_resetThemeStoreForTest`` seam. */
export function clearActiveBridge(): void {
	activeCall = null;
	activeMergeConfig = null;
}
