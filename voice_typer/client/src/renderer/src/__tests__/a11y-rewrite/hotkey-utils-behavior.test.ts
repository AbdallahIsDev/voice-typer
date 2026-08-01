/**
 *  vitest rewrite — behavioral tests for `hotkey-utils.ts`.
 *
 * Replaces the following string-pattern Python tests from
 * `tests/test_hotkeys.py`:
 *   - TestHotkeyUtilsFormatLabel::test_formats_single_key
 *   - TestHotkeyUtilsFormatLabel::test_formats_combo
 *   - TestHotkeyUtilsValidate::test_validate_rejects_empty
 *   - TestHotkeyUtilsValidate::test_validate_rejects_modifiers_only_in_combo
 *   - TestHotkeyUtilsValidate::test_validate_rejects_multi_key_in_single_mode
 *   - TestDictationKeySupportsExpandedPresets::test_single_key_presets_include_beyond_f12
 *
 * The Python tests asserted on substring presence inside the source file
 * (e.g. `"function formatHotkeyLabel" in utils`, `'"Caps Lock"' in utils`).
 * These are brittle: they pass even when the function silently returns
 * the wrong value, and they fail on innocent refactors (renaming the
 * function, switching quote style, extracting constants).  The vitest
 * versions below call the actual functions and assert on the returned
 * value, so a refactor that preserves the contract still passes and a
 * behavioral regression fails.
 *
 * The corresponding Python tests are skipped via `@pytest.mark.skip`
 * with a pointer back to this file.  They are NOT deleted — they
 * remain as a fallback until CI verifies the vitest versions pass on
 * all platforms.
 */
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

async function importUtils() {
	vi.resetModules();
	return (await import(
		"@/components/hotkey/hotkey-utils"
	)) as typeof import("@/components/hotkey/hotkey-utils");
}

describe("formatHotkeyLabel — RW-0 rewrite of test_formats_single_key", () => {
	beforeAll(() => {
		vi.resetModules();
	});
	afterEach(() => {
		vi.unstubAllGlobals();
		vi.resetModules();
	});

	it("formats a single Ctrl modifier", async () => {
		vi.stubGlobal("navigator", {
			userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
		});
		const { formatHotkeyLabel } = await importUtils();
		// Original Python invariant: `"Ctrl"` appears as a label.
		// Behavioral: formatHotkeyLabel("<ctrl>") returns "Ctrl".
		expect(formatHotkeyLabel("<ctrl>")).toBe("Ctrl");
	});

	it("formats Caps Lock with a human-readable label", async () => {
		vi.stubGlobal("navigator", {
			userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
		});
		const { formatHotkeyLabel } = await importUtils();
		// Original Python invariant: `"Caps Lock"` is a label.
		// Behavioral: formatHotkeyLabel("<caps_lock>") returns "Caps Lock".
		expect(formatHotkeyLabel("<caps_lock>")).toBe("Caps Lock");
	});

	it("formats Space with a human-readable label", async () => {
		vi.stubGlobal("navigator", {
			userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
		});
		const { formatHotkeyLabel } = await importUtils();
		// Original Python invariant: `"Space"` is a label.
		// Behavioral: formatHotkeyLabel("<space>") returns "Space".
		expect(formatHotkeyLabel("<space>")).toBe("Space");
	});
});

describe("formatHotkeyLabel — RW-0 rewrite of test_formats_combo", () => {
	beforeAll(() => {
		vi.resetModules();
	});
	afterEach(() => {
		vi.unstubAllGlobals();
		vi.resetModules();
	});

	it("joins combo parts with +", async () => {
		vi.stubGlobal("navigator", {
			userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
		});
		const { formatHotkeyLabel } = await importUtils();
		// Original Python invariant: `.split("+")` and `.join("+")` are
		// both present in source.  Behavioral: formatHotkeyLabel joins
		// every part of a multi-part combo with "+".
		expect(formatHotkeyLabel("<ctrl>+<alt>+v")).toBe("Ctrl+Alt+V");
		expect(formatHotkeyLabel("<shift>+<space>")).toBe("Shift+Space");
	});
});

describe("validateHotkey — RW-0 rewrite of test_validate_rejects_empty", () => {
	beforeAll(() => {
		vi.resetModules();
	});
	afterEach(() => {
		vi.unstubAllGlobals();
		vi.resetModules();
	});

	it("returns a non-null error for an empty hotkey in single mode", async () => {
		vi.stubGlobal("navigator", {
			userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
		});
		const { validateHotkey } = await importUtils();
		// Original Python invariant: the string "Hotkey is empty"
		// appears in the source.  Behavioral: validateHotkey("")
		// returns a non-null error string mentioning the empty case.
		const result = validateHotkey("", "single");
		expect(result).not.toBeNull();
		expect(result?.toLowerCase()).toMatch(/empty/);
	});

	it("returns a non-null error for a whitespace-only hotkey in combo mode", async () => {
		vi.stubGlobal("navigator", {
			userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
		});
		const { validateHotkey } = await importUtils();
		const result = validateHotkey("   ", "combo");
		expect(result).not.toBeNull();
	});
});

describe("validateHotkey — RW-0 rewrite of test_validate_rejects_modifiers_only_in_combo", () => {
	beforeAll(() => {
		vi.resetModules();
	});
	afterEach(() => {
		vi.unstubAllGlobals();
		vi.resetModules();
	});

	it("rejects a mixed combo that ends with a modifier", async () => {
		vi.stubGlobal("navigator", {
			userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
		});
		const { validateHotkey } = await importUtils();
		// Original Python invariant: the string
		// "must end with a non-modifier key" is in source.
		// Behavioral: a mixed combo ending with a modifier
		// (e.g. <ctrl>+<alt>+<v>+<shift>) is rejected.
		const result = validateHotkey("<ctrl>+<alt>+<v>+<shift>", "combo");
		expect(result).not.toBeNull();
		expect(result?.toLowerCase()).toMatch(/must end with a non-modifier/);
	});

	it("allows a pure-modifier combo (HOTKEY-MULTIKEY-001)", async () => {
		vi.stubGlobal("navigator", {
			userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
		});
		const { validateHotkey } = await importUtils();
		// Pure-modifier combos (no non-modifier) are now allowed;
		// the "must end with non-modifier" rule only applies to
		// MIXED combos.  This is a defense-in-depth check.
		const result = validateHotkey("<ctrl>+<shift>", "combo");
		expect(result).toBeNull();
	});
});

describe("validateHotkey — RW-0 rewrite of test_validate_rejects_multi_key_in_single_mode", () => {
	beforeAll(() => {
		vi.resetModules();
	});
	afterEach(() => {
		vi.unstubAllGlobals();
		vi.resetModules();
	});

	it("rejects a multi-key combo in single mode", async () => {
		vi.stubGlobal("navigator", {
			userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
		});
		const { validateHotkey } = await importUtils();
		// Original Python invariant: the string
		// "must be a single key" is in source.
		// Behavioral: passing two keys in single mode
		// returns a non-null error.
		const result = validateHotkey("<ctrl>+<alt>", "single");
		expect(result).not.toBeNull();
		expect(result?.toLowerCase()).toMatch(/single key/);
	});

	it("accepts a single key in single mode", async () => {
		vi.stubGlobal("navigator", {
			userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
		});
		const { validateHotkey } = await importUtils();
		const result = validateHotkey("<caps_lock>", "single");
		expect(result).toBeNull();
	});
});

describe("hotkey-utils — RW-0 rewrite of test_single_key_presets_include_beyond_f12", () => {
	beforeAll(() => {
		vi.resetModules();
	});
	afterEach(() => {
		vi.unstubAllGlobals();
		vi.resetModules();
	});

	it("KEY_CODE_TO_PYNPUT maps extended keys beyond F2-F12", async () => {
		vi.stubGlobal("navigator", {
			userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
		});
		const { KEY_CODE_TO_PYNPUT } = await importUtils();
		// Original Python invariant: hotkey-utils.ts contains
		// the strings "caps_lock", "print_screen", "scroll_lock",
		// "pause", "insert", "home", "page_up", "page_down".
		// Behavioral: the key-code → pynput-name map resolves
		// each extended key code to its pynput name.  A future
		// refactor that drops one of these entries would
		// break capture for that key (regression).
		expect(KEY_CODE_TO_PYNPUT.CapsLock).toBe("caps_lock");
		expect(KEY_CODE_TO_PYNPUT.PrintScreen).toBe("print_screen");
		expect(KEY_CODE_TO_PYNPUT.ScrollLock).toBe("scroll_lock");
		expect(KEY_CODE_TO_PYNPUT.Pause).toBe("pause");
		expect(KEY_CODE_TO_PYNPUT.Insert).toBe("insert");
		expect(KEY_CODE_TO_PYNPUT.Home).toBe("home");
		expect(KEY_CODE_TO_PYNPUT.PageUp).toBe("page_up");
		expect(KEY_CODE_TO_PYNPUT.PageDown).toBe("page_down");
	});
});
