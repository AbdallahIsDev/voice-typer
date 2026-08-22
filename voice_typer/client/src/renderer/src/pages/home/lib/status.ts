//status helpers extracted from Home.tsx.
//
// `statusLabelFor` resolves at render time so the pill honours the
// current locale on every render (not just at module-import time). The
// `RecordingState` union from `@/types/ipc` is the source of truth for
// the input string — the helpers gracefully fall back to the "ready"
// label for any unrecognised state (defensive against future backend
// additions).

import { t } from "@/i18n/i18n";
import type { RecordingState } from "@/types/ipc";

/**
 * Strip the `<` / `>` brackets from a backend hotkey token (e.g.
 * `"<caps_lock>"` → `"CAPS_LOCK"`) and uppercase it for display in the
 * hotkey chip on the Home page.
 */
export function normalizeHotkey(raw: string): string {
	return raw.replace(/[<>]/g, "").toUpperCase();
}

/**
 * Resolve the visible label for a recording-status key. Re-resolves the
 * `t()` call on every render so locale switches take effect immediately.
 */
export function statusLabelFor(key: string): string {
	switch (key) {
		case "recording":
			return t("home.recording");
		case "transcribing":
			return t("home.transcribing");
		case "loading":
			return t("home.loading");
		case "cancelling":
			return t("home.cancelling");
		case "error":
			return t("home.error");
		default:
			return t("home.ready");
	}
}

/**
 * Map a `RecordingState` (+ error flag) onto the lowercase status key
 * used to look up `STATUS_COLORS` / `statusLabelFor`. The `error` state
 * only surfaces when there is also a non-empty `lastError` — without an
 * error message the user can't act on, the pill falls back to the
 * ready/idle key so it always agrees with the dynamic description line
 * below the mic button (which shows the normal dictate hint whenever
 * `lastError` is empty).
 *
 * PILL/DESCRIPTION INVARIANT: the pill and the description are two views
 * of the same authoritative `{recordingState, lastError}` pair — they
 * must be derived from the same predicate, never from independent
 * sources (an ERROR pill above a "Press <hotkey> or click to dictate"
 * hint is the divergence bug this guards against). The renderer-side
 * sync contract that keeps `lastError` populated on every error path
 * lives in `useConnection.ts` (`applyStatusWithReason`).
 */
export function statusKeyFor(state: RecordingState, hasError: boolean): string {
	if (state === "error") {
		return hasError ? "error" : "idle";
	}
	return state;
}
