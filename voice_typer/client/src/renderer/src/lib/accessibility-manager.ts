/**
 * AccessibilityManager — settings flag for the deaf-accessibility
 * visual mirror.
 *
 * Extracted from sound-manager.ts (concern split): whether each sound
 * cue should ALSO be rendered as a distinct visual pulse (status-pill /
 * title-bar / tray-icon flash) is a SETTINGS/accessibility concern, not
 * a sound-synthesis concern — this module reads and persists no audio
 * state and has no dependency on the AudioContext. Keeping it separate
 * lets the sound manager stay purely about cue playback.
 *
 * The flag mirrors the sound-feedback-enabled sync flow: an in-memory
 * default (false — the visual mirror is opt-in so sighted+hearing users
 * don't get redundant visual noise) is overridden by the persisted
 * localStorage value whenever present. The ``useSoundFeedback`` hook's
 * ``onVisualCue`` callback path is the production wiring for the
 * visual mirror: the App decides whether to pass the callback (and may
 * gate that decision on ``isVisualFeedbackEnabled()``). A future
 * Settings → Accessibility toggle should call ``setVisualFeedbackEnabled``
 * when ``config.visual_feedback_enabled`` changes.
 *
 * C-DATA-1: this is a pure local-storage write — NO network call.
 */

let _visualEnabled: boolean = false;

const VISUAL_STORAGE_KEY = "vt_visual_feedback_enabled";

/**
 * Update the in-memory visual-feedback-enabled flag and persist to
 * localStorage. Same persistence semantics as the sound-feedback flag:
 * the in-memory flag still works if localStorage is unavailable.
 */
export function setVisualFeedbackEnabled(enabled: boolean): void {
	_visualEnabled = enabled;
	try {
		localStorage.setItem(VISUAL_STORAGE_KEY, enabled ? "1" : "0");
	} catch (e) {
		// localStorage unavailable (e.g. SSR, private browsing) —
		// non-fatal; the in-memory flag still works for this session.
		console.warn(
			"[renderer:sound-manager] setVisualFeedbackEnabled localStorage.setItem failed:",
			e,
		);
	}
}

/**
 * Read the visual-feedback-enabled flag. Returns true when the visual
 * mirror should be active (each sound cue mirrored as a visual pulse
 * for deaf / hard-of-hearing users).
 *
 * Reads localStorage on every call (no IPC round-trip) — same pattern
 * as ``isSoundFeedbackEnabled``. Default: false — the visual mirror is
 * opt-in; deaf / hard-of-hearing users explicitly enable it (or it's
 * auto-enabled when the OS reports a screen reader / captioning
 * preference — future work, not implemented here).
 */
export function isVisualFeedbackEnabled(): boolean {
	try {
		const raw = localStorage.getItem(VISUAL_STORAGE_KEY);
		if (raw === null) return _visualEnabled; // Fall back to in-memory default
		return raw === "1";
	} catch (err) {
		// Log the localStorage read failure at debug so silent
		// visual-flag read failures are visible.
		console.debug(
			"[renderer:sound-manager] isVisualFeedbackEnabled localStorage.getItem failed:",
			err,
		);
		return _visualEnabled;
	}
}

/**
 * Reset all state — used by tests to ensure isolation between cases.
 */
export function _resetAccessibilityManagerForTests(): void {
	_visualEnabled = false;
}
