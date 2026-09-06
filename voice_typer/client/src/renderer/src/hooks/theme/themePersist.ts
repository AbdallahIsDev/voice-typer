/**
 * themePersist.ts — the persistence concern of the theme subsystem,
 * split out of ``hooks/useTheme.ts``. Owns the single debounced backend
 * write path (``scheduleThemeSave``), the quit-time flush
 * (``flushPendingThemeSave``), and the localStorage cache sync.
 *
 * ── debounced backend save ───────────────────────────────────────────
 *
 * PERF: debounce the backend write so rapid theme toggling (e.g.
 * user clicking through light → dark → system quickly) doesn't
 * fire 3 separate set_config IPC calls. The local UI updates
 * immediately (via the store); the backend save is deferred 300ms
 * and only the LAST selected mode is persisted.
 *
 * QUIT-FLUSH-FIX: previously, if the user changed the theme and
 * then closed the app (close-to-tray → tray Quit, or window close)
 * during the 300ms debounce window, the pending save was dropped
 * and the next launch loaded the old theme from the backend. The
 * ``beforeunload`` listener (installed once via
 * ``ensureThemeSideEffects``) + the per-instance unmount cleanup
 * (in the hook body) both call ``flushPendingThemeSave`` so the
 * pending save fires synchronously before the renderer tears down.
 *
 * The bridge (``call``) is read via ``getActiveCall()`` at fire/flush
 * time — never captured — so the write always targets the latest
 * registered bridge even if the consumer that scheduled the save has
 * since unmounted.
 */
import {
	LS_CUSTOM_THEME,
	LS_TEXT_SIZE,
	LS_THEME_MODE,
	LS_THEME_PRESET,
} from "@/lib/theme-storage-keys";
import type { CustomThemeData } from "@/themes";
import type { VoiceTyperConfig } from "@/types/config";
import { getActiveCall } from "./themeBridge";

/** The config fields this subsystem owns, as accepted by ``set_config``. */
export type ThemeSaveUpdates = Partial<
	Pick<
		VoiceTyperConfig,
		"theme_mode" | "theme_preset" | "custom_theme" | "text_size"
	>
>;

let themeSaveTimer: ReturnType<typeof setTimeout> | null = null;
let pendingThemeUpdates: ThemeSaveUpdates | null = null;

export function flushPendingThemeSave(): void {
	if (themeSaveTimer) {
		clearTimeout(themeSaveTimer);
		themeSaveTimer = null;
	}
	const pending = pendingThemeUpdates;
	if (pending) {
		pendingThemeUpdates = null;
		const activeCall = getActiveCall();
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
	// Cancel any pending save and schedule a new one.
	if (themeSaveTimer) {
		clearTimeout(themeSaveTimer);
	}
	themeSaveTimer = setTimeout(async () => {
		themeSaveTimer = null;
		const pending = pendingThemeUpdates;
		pendingThemeUpdates = null;
		const activeCall = getActiveCall();
		if (!pending || !activeCall) return;
		try {
			await activeCall("set_config", pending);
		} catch (e) {
			// Theme is local-only if backend unavailable
			console.warn("[renderer:useTheme] set_config (debounced) failed:", e);
		}
	}, 300);
}

/**
 * Mirror the theme state into localStorage so the pre-React
 * ``theme-bootstrap.ts`` (and the next mount's store hydration) start
 * from the freshest cache. KEPT per-instance by the hook (idempotent —
 * both consumers write the same keys with the same values).
 */
export function syncThemeCacheToLocalStorage(
	themeMode: VoiceTyperConfig["theme_mode"],
	themePreset: VoiceTyperConfig["theme_preset"],
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

/** Remove the ``beforeunload`` flush listener — used by the
 * ``_resetThemeStoreForTest`` seam. */
export function removeBeforeUnloadFlush(): void {
	if (typeof window !== "undefined") {
		window.removeEventListener("beforeunload", flushPendingThemeSave);
	}
}

/** Drop any pending save + cancel the debounce timer — used by the
 * ``_resetThemeStoreForTest`` seam. */
export function resetThemePersistState(): void {
	if (themeSaveTimer) {
		clearTimeout(themeSaveTimer);
		themeSaveTimer = null;
	}
	pendingThemeUpdates = null;
}
