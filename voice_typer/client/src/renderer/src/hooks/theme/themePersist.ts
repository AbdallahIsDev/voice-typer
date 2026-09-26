import { createDebouncedCallback } from "@/hooks/useDebounce";
import {
	LS_CUSTOM_THEME,
	LS_TEXT_SIZE,
	LS_THEME_MODE,
	LS_THEME_PRESET,
} from "@/lib/theme-storage-keys";
import type { CustomThemeData } from "@/themes";
import type { LausuConfig } from "@/types/config";
import { getActiveCall } from "./themeBridge";

/** The config fields this subsystem owns, as accepted by ``set_config``. */
export type ThemeSaveUpdates = Partial<
	Pick<
		LausuConfig,
		"theme_mode" | "theme_preset" | "custom_theme" | "text_size"
	>
>;

let pendingThemeUpdates: ThemeSaveUpdates | null = null;

// Shared debounce timer; the payload merge lives above so rapid
// changes coalesce, the bridge resolves at fire time (registered
// after schedule), and flush keeps its own sync warn path.
const themeSaver = createDebouncedCallback(() => {
	const pending = pendingThemeUpdates;
	pendingThemeUpdates = null;
	const activeCall = getActiveCall();
	if (!pending || !activeCall) return;
	activeCall("set_config", pending).catch((e) => {
		// Theme is local-only if backend unavailable
		console.warn("[renderer:useTheme] set_config (debounced) failed:", e);
	});
}, 300);

export function flushPendingThemeSave(): void {
	themeSaver.cancel();
	const pending = pendingThemeUpdates;
	if (pending) {
		pendingThemeUpdates = null;
		const activeCall = getActiveCall();
		if (activeCall) {
			// Fire-and-forget, the renderer may be tearing down, so we
			// can't await. The IPC layer queues the write before the
			// process exits. The Promise's rejection MUST be handled
			// here (via `.catch`), `void call(...)` alone discards the
			// Promise without installing a rejection handler, which
			// surfaces as an "unhandled promise rejection" warning in
			// predecessor (and can crash the renderer in strict modes).
			// Theme is local-only if backend unavailable, the warn is
			// the entire recovery path.
			void activeCall("set_config", pending).catch((e) => {
				console.warn("[renderer:useTheme] set_config (flush) failed:", e);
			});
		}
	}
}

export function scheduleThemeSave(updates: ThemeSaveUpdates): void {
	// Merge into the pending payload so successive rapid
	// changes (e.g. typing into a custom-colour picker)
	// coalesce into a single backend write.
	pendingThemeUpdates = {
		...pendingThemeUpdates,
		...updates,
	};
	themeSaver.debounced();
}

export function syncThemeCacheToLocalStorage(
	themeMode: LausuConfig["theme_mode"],
	themePreset: LausuConfig["theme_preset"],
	customTheme: CustomThemeData | null,
	textSize: number,
): void {
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
}

/** Install the single app-wide ``beforeunload`` flush listener. Called
 * from ``themeSync.ensureThemeSideEffects`` (exactly once per app load). */
export function installBeforeUnloadFlush(): void {
	window.addEventListener("beforeunload", flushPendingThemeSave);
}

/** Remove the ``beforeunload`` flush listener, used by the
 * ``_resetThemeStoreForTest`` seam. */
export function removeBeforeUnloadFlush(): void {
	if (typeof window !== "undefined") {
		window.removeEventListener("beforeunload", flushPendingThemeSave);
	}
}

/** Drop any pending save + cancel the debounce timer, used by the
 * ``_resetThemeStoreForTest`` seam. */
export function resetThemePersistState(): void {
	themeSaver.cancel();
	pendingThemeUpdates = null;
}
