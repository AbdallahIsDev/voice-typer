// lib/theme-draft-storage.ts, localStorage draft-backup helpers for
// the custom-theme colour picker (extracted so they can be unit-tested
// independently).
//
// Persists the custom theme color picker draft to localStorage on every
// change.  If the backend save fails (process crash, network blip,
// etc.), the user's unsaved colors are recovered on the next page
// visit.  Cleared when the backend confirms the save.
//
// These are pure functions (no React dependency), extracted from
// ThemeSettingsSection.tsx so they can be unit-tested independently
// and reused by any caller that needs crash-recovery for the
// custom-theme draft.

import type { CustomThemeData } from "@/themes";

// localStorage key under which the custom-theme draft is persisted.
// Kept non-exported: callers interact with the draft only through
// ``saveDraftToLS`` / ``loadDraftFromLS`` / ``clearDraftLS`` and never
// need to know the key.
const LS_DRAFT_KEY = "vt_custom_theme_draft";

/**
 * Persist the custom-theme draft to localStorage. Non-fatal, if
 * localStorage is full or unavailable the backend save still proceeds,
 * we just lose the crash-recovery draft for the next page visit.
 */
export function saveDraftToLS(data: CustomThemeData): void {
	try {
		localStorage.setItem(LS_DRAFT_KEY, JSON.stringify(data));
	} catch (e) {
		// localStorage may be full or unavailable, non-fatal.
		// The backend save will still proceed; we just lose the
		// crash-recovery draft for the next page visit.
		console.warn("[renderer:theme-draft-storage] saveDraftToLS failed:", e);
	}
}

/**
 * Load the persisted custom-theme draft from localStorage. Returns
 * ``null`` when no draft is stored or the stored value is unparseable
 * (corrupted JSON, schema drift, etc.).
 */
export function loadDraftFromLS(): CustomThemeData | null {
	try {
		const raw = localStorage.getItem(LS_DRAFT_KEY);
		if (!raw) return null;
		const parsed = JSON.parse(raw);
		// Validate the parsed structure before casting, the same shape
		// guard the other CustomThemeData cache readers use
		// (theme-bootstrap.ts readLsCustomTheme and
		// hooks/theme/themeStore.ts). A hand-edited devtools payload or a
		// stale schema from an older build must NOT be cast to
		// CustomThemeData: consumers index `light` / `dark` directly, so a
		// missing half crashes them. Invalid shape → null (the function's
		// documented "no value" contract, no sentinel object).
		if (
			parsed &&
			typeof parsed === "object" &&
			"light" in parsed &&
			"dark" in parsed
		) {
			return parsed as CustomThemeData;
		}
	} catch {
		return null;
	}
	return null;
}

/**
 * Remove the persisted draft from localStorage. Non-fatal, a leftover
 * draft will just be overwritten on the next save or rejected as stale
 * on the next load.
 */
export function clearDraftLS(): void {
	try {
		localStorage.removeItem(LS_DRAFT_KEY);
	} catch (e) {
		// non-fatal, a leftover draft will just be overwritten
		// on the next save or rejected as stale on the next load.
		console.warn("[renderer:theme-draft-storage] clearDraftLS failed:", e);
	}
}
