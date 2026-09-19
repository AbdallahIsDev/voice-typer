/**
 * AccessibilityManager, settings flag for the deaf-accessibility
 * visual mirror.
 * cue should ALSO be rendered as a distinct visual pulse (status-pill /
 * C-DATA-1: this is a pure local-storage write, NO network call.
 */

let _visualEnabled: boolean = false;

const VISUAL_STORAGE_KEY = "vt_visual_feedback_enabled";

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

export function _resetAccessibilityManagerForTests(): void {
	_visualEnabled = false;
}
