/**
 * I18N-3: RTL `dir` attribute tests.
 *
 * Validates that {@link setLocale} updates `document.documentElement.dir` to
 * `"rtl"` for right-to-left locales (currently only Arabic) and `"ltr"` for
 * all other supported locales. This is the F-4 / A11Y-7 contract: switching
 * the UI language to Arabic must flip the entire layout horizontally via
 * the HTML `dir` attribute, which in turn cascades through Tailwind's
 * logical-property utilities (`ps-*`, `pe-*`, `ms-*`, `me-*`, etc.) so the
 * whole UI mirrors correctly without per-component RTL overrides.
 *
 * Also exercises {@link isRtlLocale} — the pure function used by both
 * `setLocale` and any component that needs to know the current direction
 * without touching the DOM.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { isRtlLocale, setLocale } from "@/i18n/i18n";

describe("I18N-3: RTL dir attribute", () => {
	const originalDir =
		typeof document !== "undefined" ? document.documentElement.dir : "";

	afterEach(() => {
		// Restore the original dir so this test file cannot leak RTL state
		// into sibling test files that happen to share the jsdom instance.
		if (typeof document !== "undefined") {
			document.documentElement.dir = originalDir;
		}
		// Reset the i18n module state to English so the next test starts
		// from a known baseline (setLocale also clears the persisted
		// localStorage key to "en").
		setLocale("en");
	});

	beforeEach(() => {
		// Belt-and-suspenders: ensure each test starts in LTR/English so
		// the assertion is only about the act of calling setLocale, not
		// about leftover state from a previous test.
		setLocale("en");
	});

	it("sets dir=rtl when locale is Arabic", () => {
		setLocale("ar");
		expect(document.documentElement.dir).toBe("rtl");
		expect(isRtlLocale("ar")).toBe(true);
	});

	it("sets dir=ltr for non-RTL locales", () => {
		const ltrLocales = ["en", "es", "fr", "de", "ru", "zh", "hi"] as const;
		for (const loc of ltrLocales) {
			setLocale(loc);
			expect(document.documentElement.dir).toBe("ltr");
			expect(isRtlLocale(loc)).toBe(false);
		}
	});

	it("isRtlLocale returns false for non-RTL locales and true for ar", () => {
		// Defensive: isRtlLocale is a pure lookup; it must never throw on
		// the typed Locale union, even for values not in RTL_LOCALES.
		expect(isRtlLocale("en")).toBe(false);
		expect(isRtlLocale("zh")).toBe(false);
		expect(isRtlLocale("ar")).toBe(true);
	});

	it("flips dir back to ltr when switching from ar to en", () => {
		setLocale("ar");
		expect(document.documentElement.dir).toBe("rtl");
		setLocale("en");
		expect(document.documentElement.dir).toBe("ltr");
	});
});
