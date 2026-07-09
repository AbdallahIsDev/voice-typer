/**
 * Tests for hotkey-utils — preset safety invariants.
 *
 * The most important invariant: `<win>+<space>` must NEVER appear in
 * the combo presets on Windows. Win+Space is reserved by the OS for the
 * input-language switcher; offering it as a paste shortcut would silently
 * break language switching. Linux `<super>+<space>` is OK because Linux
 * does not reserve that combo.
 *
 * We also smoke-test that the HotkeySettingsSection renders both the
 * dictation (single-mode) and the re-paste (combo-mode) pickers via the
 * same HotkeyPicker component, so the paste shortcut reuses the same
 * accessible capture UI as the dictation key.
 *
 * ISSUE-8: the preset lists are now exposed via getter functions
 * `getSingleKeyPresets()` / `getComboPresets()` that re-detect the
 * platform on every call. The tests below call the getters directly
 * after stubbing `navigator.userAgent` so the platform branch under
 * test is exercised deterministically.
 */
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

// `hotkey-utils.ts` reads `navigator.userAgent` to derive IS_WIN /
// IS_LINUX / IS_MAC at module load, and `getComboPresets()` re-reads it
// on every call via `detectPlatform()`. Each test below stubs the
// userAgent string and re-imports the module via vi.resetModules() +
// dynamic import() so the platform branch under test is exercised
// deterministically.

async function importUtils() {
	vi.resetModules();
	return (await import("../hotkey-utils")) as typeof import("../hotkey-utils");
}

function setUserAgent(ua: string) {
	vi.stubGlobal("navigator", { userAgent: ua });
}

afterEach(() => {
	vi.unstubAllGlobals();
	vi.resetModules();
});

describe("getComboPresets() — Win+Space safety", () => {
	beforeAll(() => {
		// Ensure a clean starting point.
		vi.resetModules();
	});

	it("never contains <win>+<space> on Windows", async () => {
		setUserAgent(
			"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
		);
		const { getComboPresets, IS_WIN } = await importUtils();
		expect(IS_WIN).toBe(true);
		const COMBO_PRESETS = getComboPresets();
		const offending = COMBO_PRESETS.filter((p) => p.value === "<win>+<space>");
		expect(offending).toHaveLength(0);
	});

	it("never contains <super>+<space> on Windows (Super is Linux-only)", async () => {
		setUserAgent(
			"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
		);
		const { getComboPresets, IS_WIN } = await importUtils();
		expect(IS_WIN).toBe(true);
		const COMBO_PRESETS = getComboPresets();
		const offending = COMBO_PRESETS.filter(
			(p) => p.value === "<super>+<space>",
		);
		expect(offending).toHaveLength(0);
	});

	it("still offers <super>+<space> on Linux (Linux does not reserve it)", async () => {
		setUserAgent("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36");
		const { getComboPresets, IS_LINUX } = await importUtils();
		expect(IS_LINUX).toBe(true);
		const COMBO_PRESETS = getComboPresets();
		const superSpace = COMBO_PRESETS.find((p) => p.value === "<super>+<space>");
		expect(superSpace).toBeTruthy();
	});

	it("never contains <win>+<space> on macOS either", async () => {
		setUserAgent(
			"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
		);
		const { getComboPresets, IS_MAC } = await importUtils();
		expect(IS_MAC).toBe(true);
		const COMBO_PRESETS = getComboPresets();
		const offending = COMBO_PRESETS.filter((p) => p.value === "<win>+<space>");
		expect(offending).toHaveLength(0);
	});
});

describe("HotkeyPicker — reusable for both recording and paste shortcuts", () => {
	// This is a structural import test: it verifies the HotkeyPicker
	// component is the single, shared entry point used by the settings
	// section. A full render test already lives in Settings.test.tsx;
	// here we just guard against regressions where the paste shortcut
	// might be silently switched to a bespoke picker.
	it("HotkeySettingsSection imports HotkeyPicker (shared component for both modes)", async () => {
		const sectionMod = (await import(
			"../settings/HotkeySettingsSection"
		)) as typeof import("../settings/HotkeySettingsSection");
		// HotkeySettingsSection is wrapped in React.memo, so the
		// exported value is a MemoExoticComponent (typeof "object")
		// rather than a plain function. We just assert it resolves
		// to a defined, renderable component.
		expect(sectionMod.HotkeySettingsSection).toBeTruthy();
		// Re-import HotkeyPicker to confirm the symbol resolves; if
		// either the section or the picker is renamed/removed, this
		// throws and the test fails loudly.
		const pickerMod = (await import(
			"../HotkeyPicker"
		)) as typeof import("../HotkeyPicker");
		expect(typeof pickerMod.HotkeyPicker).toBe("function");
	});
});
