/**
 * Contract tests for `configHotkeyLabels` — the single helper that
 * computes the user-facing dictation + repaste hotkey labels from the
 * app config. App.tsx feeds it the config selectors and passes the
 * results to the Help overlay; the defaults it falls back to must
 * match the backend's canonical defaults (see the lockstep comments in
 * `hotkey-utils.ts` / `pages/onboarding/lib/constants.ts`).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
	configHotkeyLabels,
	HOTKEY_DEFAULT,
	REPASTE_HOTKEY_DEFAULT,
} from "@/components/hotkey/hotkey-utils";
import { HOTKEY_DEFAULT as ONBOARDING_HOTKEY_DEFAULT } from "@/pages/onboarding/lib/constants";

// formatHotkey renders macOS glyph forms (⌃⌥V) on macOS, so these
// label assertions are platform-dependent. Pin a Windows UA so the
// expected labels ("Caps Lock", "Ctrl+Alt+V") are deterministic on
// every CI OS — same stub pattern as shortcuts.test.ts.
beforeEach(() => {
	vi.stubGlobal("navigator", {
		userAgent:
			"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
	});
});

afterEach(() => {
	vi.unstubAllGlobals();
});

describe("configHotkeyLabels — config-driven dictation/repaste labels", () => {
	it("falls back to the canonical defaults when config fields are unset", () => {
		expect(configHotkeyLabels({})).toEqual({
			dictationLabel: "Caps Lock",
			repasteLabel: "Ctrl+Alt+V",
		});
		// Null fields (config not yet loaded / cleared) behave the same.
		expect(configHotkeyLabels({ hotkey: null, repaste_hotkey: null })).toEqual({
			dictationLabel: "Caps Lock",
			repasteLabel: "Ctrl+Alt+V",
		});
	});

	it("formats configured hotkeys through the canonical formatter", () => {
		expect(
			configHotkeyLabels({
				hotkey: "<f4>",
				repaste_hotkey: "<ctrl>+<shift>+v",
			}),
		).toEqual({
			dictationLabel: "F4",
			repasteLabel: "Ctrl+Shift+V",
		});
	});

	it("keeps the onboarding re-export in lockstep with the hotkey module default", () => {
		// `onboarding/lib/constants.ts` re-exports HOTKEY_DEFAULT from
		// the hotkey module — if that re-export ever regresses to a
		// local copy, the value (and future changes) drift silently.
		expect(ONBOARDING_HOTKEY_DEFAULT).toBe(HOTKEY_DEFAULT);
		expect(HOTKEY_DEFAULT).toBe("<caps_lock>");
		expect(REPASTE_HOTKEY_DEFAULT).toBe("<ctrl>+<alt>+v");
	});
});
