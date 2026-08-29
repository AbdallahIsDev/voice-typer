/**
 * Tests for the accessibility-manager module (visual-feedback flag for
 * the deaf-accessibility mirror).
 *
 * These tests moved with the flag when it was extracted from
 * sound-manager.ts — same coverage, same localStorage semantics, same
 * "[renderer:sound-manager]" log prefix (the flags belong to the sound
 * feedback subsystem from an operator's perspective).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { stubGlobalLocalStorage } from "./helpers/local-storage-stub";

describe("AccessibilityManager — visual feedback flag (deaf mirror)", () => {
	beforeEach(() => {
		vi.resetModules();
		localStorage.clear();
	});

	afterEach(() => {
		vi.restoreAllMocks();
	});

	it("setVisualFeedbackEnabled persists to localStorage under the visual key", async () => {
		const { setVisualFeedbackEnabled } = await import(
			"@/lib/accessibility-manager"
		);
		setVisualFeedbackEnabled(true);
		expect(localStorage.getItem("vt_visual_feedback_enabled")).toBe("1");
		setVisualFeedbackEnabled(false);
		expect(localStorage.getItem("vt_visual_feedback_enabled")).toBe("0");
	});

	it("isVisualFeedbackEnabled defaults to false on a fresh reset", async () => {
		const { isVisualFeedbackEnabled, _resetAccessibilityManagerForTests } =
			await import("@/lib/accessibility-manager");
		_resetAccessibilityManagerForTests();
		expect(isVisualFeedbackEnabled()).toBe(false);
	});

	it("isVisualFeedbackEnabled reflects the persisted value after setVisualFeedbackEnabled(true)", async () => {
		const {
			isVisualFeedbackEnabled,
			setVisualFeedbackEnabled,
			_resetAccessibilityManagerForTests,
		} = await import("@/lib/accessibility-manager");
		_resetAccessibilityManagerForTests();
		setVisualFeedbackEnabled(true);
		expect(isVisualFeedbackEnabled()).toBe(true);
	});

	it("isVisualFeedbackEnabled falls back to in-memory default when localStorage is empty", async () => {
		const { isVisualFeedbackEnabled, _resetAccessibilityManagerForTests } =
			await import("@/lib/accessibility-manager");
		_resetAccessibilityManagerForTests();
		// No localStorage entry — must fall back to the in-memory default (false).
		localStorage.clear();
		expect(isVisualFeedbackEnabled()).toBe(false);
	});

	it("setVisualFeedbackEnabled does NOT throw when localStorage.setItem fails", async () => {
		const { setVisualFeedbackEnabled, _resetAccessibilityManagerForTests } =
			await import("@/lib/accessibility-manager");
		_resetAccessibilityManagerForTests();

		const setItemSpy = vi.fn(() => {
			throw new DOMException("quota exceeded");
		});
		stubGlobalLocalStorage({ setItem: setItemSpy });
		const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});

		try {
			// Must NOT throw — the in-memory flag still updates.
			expect(() => setVisualFeedbackEnabled(true)).not.toThrow();
			expect(setItemSpy).toHaveBeenCalled();
			// The warning surfaces the localStorage failure to operators.
			expect(warnSpy).toHaveBeenCalled();
		} finally {
			vi.unstubAllGlobals();
			warnSpy.mockRestore();
		}
	});

	it("isVisualFeedbackEnabled logs debug message when localStorage.getItem throws", async () => {
		const { isVisualFeedbackEnabled, _resetAccessibilityManagerForTests } =
			await import("@/lib/accessibility-manager");
		_resetAccessibilityManagerForTests();

		const getItemSpy = vi.fn(() => {
			throw new DOMException("SecurityError");
		});
		stubGlobalLocalStorage({ getItem: getItemSpy });
		const debugSpy = vi.spyOn(console, "debug").mockImplementation(() => {});

		try {
			const result = isVisualFeedbackEnabled();
			// Default in-memory flag is false on a fresh reset.
			expect(result).toBe(false);
			expect(debugSpy).toHaveBeenCalled();
			const debugMsg = debugSpy.mock.calls[0]?.[0] ?? "";
			expect(String(debugMsg)).toContain("[renderer:sound-manager]");
		} finally {
			vi.unstubAllGlobals();
			debugSpy.mockRestore();
		}
	});

	it("uses a SEPARATE localStorage key from the sound-feedback flag", async () => {
		const { setSoundFeedbackEnabled } = await import("@/lib/sound-manager");
		const { setVisualFeedbackEnabled } = await import(
			"@/lib/accessibility-manager"
		);
		setSoundFeedbackEnabled(true);
		setVisualFeedbackEnabled(false);
		// Sound is enabled, visual is disabled — the two flags are independent.
		expect(localStorage.getItem("vt_sound_feedback_enabled")).toBe("1");
		expect(localStorage.getItem("vt_visual_feedback_enabled")).toBe("0");
	});
});
